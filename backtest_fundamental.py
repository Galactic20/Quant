# -*- coding: utf-8 -*-
"""
BACKTEST DEL SATÉLITE FUNDAMENTAL — datos históricos de SimFin

Aplica las MISMAS reglas que satelite.py / fundamental.py en el pasado:
  - Cada fin de trimestre, solo con la información PUBLICADA hasta esa
    fecha (columna "Publish Date" de SimFin): filtros, puntaje y selección
    (5 acciones, 1 por sector, se mantienen mientras sigan en el top 15).
  - Stop loss / take profit por volatilidad, vigilados cada cierre; el SL
    solo sube en cada revisión.
  - Comisión eToro $1 por lado + spread.
Compara contra SPY con el mismo capital, y contra variantes (sin stops y
20 acciones) para separar el efecto del filtro del de los stops.

Limitaciones (decirlas siempre al leer el resultado):
  - Solo acciones de EE. UU. (cobertura de SimFin); sin ADRs internacionales.
  - SimFin entrega cifras reexpresadas: si una empresa corrigió un reporte,
    se usa la cifra corregida con la fecha de publicación original.
  - Las empresas que dejaron de cotizar se valoran a su último precio
    conocido hasta la siguiente revisión.
  - El plan gratuito tiene ~12 meses de retraso.

Uso:  SIMFIN_API_KEY=... python backtest_fundamental.py
"""

import argparse
import os
import re

import numpy as np
import pandas as pd

import backtest as bt
import fundamental as fu
import satelite as sa

EXCLUIR_SECTOR = re.compile(r"financ|bank|insur|real estate|reit|utilit", re.I)

COL = {  # nombres de columnas de SimFin
    "ticker": "Ticker", "report": "Report Date", "publish": "Publish Date",
    "revenue": "Revenue", "op_income": "Operating Income (Loss)",
    "net_income": "Net Income (Common)", "cfo": "Net Cash from Operating Activities",
    "capex": "Change in Fixed Assets & Intangibles", "equity": "Total Equity",
    "st_debt": "Short Term Debt", "lt_debt": "Long Term Debt",
    "date": "Date", "close": "Close", "adj_close": "Adj. Close",
    "shares": "Shares Outstanding",
}

VARIANTES = {
    "Satelite (5 acc., SL/TP)":   {"n": 5,  "max_sector": 1, "stops": True},
    "5 acciones sin SL/TP":       {"n": 5,  "max_sector": 1, "stops": False},
    "20 acciones sin SL/TP":      {"n": 20, "max_sector": 4, "stops": False},
}


# ======================================================================
# DATOS PUNTO EN EL TIEMPO
# ======================================================================

def _ultimo(df: pd.DataFrame, k: int = 0) -> pd.DataFrame:
    """Fila k-ésima desde la última (k=0 la más reciente) de cada ticker."""
    g = df.groupby(COL["ticker"], sort=False)
    return g.nth(-1 - k).set_index(COL["ticker"])

def _sin_duplicados(s: pd.Series) -> pd.Series:
    """SimFin repite algunos tickers (p. ej. clases de acción): se usa el primero."""
    return s[~s.index.duplicated(keep="first")]

def fundamentales_en(fecha, ttm: pd.DataFrame, bal: pd.DataFrame,
                     cap: pd.Series, precio: pd.Series, sectores: pd.Series) -> pd.DataFrame:
    """
    Tabla con el formato de fundamental.obtener_fundamentales usando SOLO
    reportes publicados hasta `fecha`. ttm/bal deben venir ordenados por
    (Ticker, Report Date).
    """
    fecha = pd.Timestamp(fecha)
    t = ttm[ttm[COL["publish"]] <= fecha]
    b = bal[bal[COL["publish"]] <= fecha]
    if t.empty or b.empty:
        return pd.DataFrame()
    ult = _ultimo(t)
    # Solo empresas que siguen reportando (último reporte de hace < 15 meses)
    ult = ult[ult[COL["report"]] >= fecha - pd.DateOffset(months=15)]
    fcf = lambda d: d[COL["cfo"]] + d[COL["capex"]].fillna(0)
    hist = pd.concat({k: fcf(_ultimo(t, k)) for k in (0, 4, 8, 12)}, axis=1)
    rev_prev = _ultimo(t, 4)[COL["revenue"]]
    bu = _ultimo(b)
    bu = bu[~bu.index.duplicated(keep="last")]
    ult = ult[~ult.index.duplicated(keep="last")]
    cap = _sin_duplicados(cap)
    sectores = _sin_duplicados(sectores)
    equity = bu[COL["equity"]].reindex(ult.index)
    deuda = (bu[COL["st_debt"]].fillna(0) + bu[COL["lt_debt"]].fillna(0)).reindex(ult.index)
    ni = ult[COL["net_income"]]
    mcap = cap.reindex(ult.index)

    df = pd.DataFrame({
        "shortName": ult.index,
        "marketCap": mcap,
        "currentPrice": precio.reindex(ult.index),
        "returnOnEquity": ni / equity.where(equity > 0),
        "operatingMargins": ult[COL["op_income"]] / ult[COL["revenue"]],
        "freeCashflow": fcf(ult),
        "totalRevenue": ult[COL["revenue"]],
        "debtToEquity": deuda / equity.where(equity > 0) * 100,
        "trailingPE": mcap / ni,
        "forwardPE": np.nan,
        "revenueGrowth": ult[COL["revenue"]] / rev_prev.reindex(ult.index) - 1,
        "financialCurrency": "USD",
        "sector": sectores.reindex(ult.index).fillna("Otros"),
    }, index=ult.index)
    df["fcf_hist"] = [[v for v in hist.loc[i].tolist() if pd.notna(v)] if i in hist.index else []
                      for i in df.index]
    df = df[~df["sector"].str.contains(EXCLUIR_SECTOR)]
    return df[df["marketCap"].notna() & df["currentPrice"].notna()]


# ======================================================================
# SIMULACIÓN
# ======================================================================

def fechas_revision(indice: pd.DatetimeIndex, inicio) -> list:
    """Último día hábil de marzo, junio, septiembre y diciembre."""
    s = pd.Series(indice, index=indice)
    s = s[s >= pd.Timestamp(inicio)]
    fin_mes = s.groupby(s.index.to_period("M")).max()
    return [d for d in fin_mes if d.month in (3, 6, 9, 12)]

def simular(datos: dict, inicio, n: int = 5, max_sector: int = 1, stops: bool = True,
            capital: float = 1000.0, cfg: dict = None, verbose: bool = False) -> dict:
    """
    datos = {"ttm", "bal", "adj" (precios ajustados, fechas x tickers),
             "cap" {fecha: Serie cap. bursátil}, "sectores"}.
    """
    cfg = {**sa.CONFIG, **(cfg or {})}
    adj = datos["adj"]
    revisiones = set(fechas_revision(adj.index, inicio))
    fechas = adj.index[adj.index >= min(revisiones)] if revisiones else adj.index[:0]
    cash = capital
    pos: dict = {}          # ticker -> {"u", "sl", "tp", "precio"}
    curva, ops, calificadas = [], [], []
    ultimo_precio = adj.ffill()

    def vender(t, p, fecha, motivo):
        nonlocal cash
        x = pos.pop(t)
        cash += x["u"] * p * (1 - cfg["COSTE_PCT"]) - cfg["COMISION"]
        ops.append({"fecha": fecha, "ticker": t, "motivo": motivo,
                    "ret_pct": round((p / x["precio"] - 1) * 100, 2)})

    for fecha in fechas:
        precios_hoy = ultimo_precio.loc[fecha]
        # Vigilancia diaria de SL / TP
        if stops:
            for t in list(pos):
                p = adj.at[fecha, t] if t in adj.columns else np.nan
                if pd.isna(p):
                    continue
                if p <= pos[t]["sl"]:
                    vender(t, p, fecha, "STOP_LOSS")
                elif p >= pos[t]["tp"]:
                    vender(t, p, fecha, "TAKE_PROFIT")
        # Revisión trimestral
        if fecha in revisiones:
            cache = datos.setdefault("_tablas", {})
            if fecha not in cache:   # igual para todas las variantes
                cache[fecha] = fundamentales_en(fecha, datos["ttm"], datos["bal"],
                                                datos["cap"].get(fecha, pd.Series(dtype=float)),
                                                precios_hoy, datos["sectores"])
            tabla = cache[fecha]
            ranking = fu.puntuar(tabla) if len(tabla) else pd.DataFrame()
            calificadas.append(len(ranking))
            if len(ranking):
                elegidas = fu.seleccionar(ranking, n, max_sector, list(pos), cfg["BUFFER"])
                for t in [t for t in pos if t not in elegidas]:
                    vender(t, precios_hoy[t], fecha, "REVISION")
                nuevas = [t for t in elegidas if t not in pos and pd.notna(precios_hoy.get(t))]
                if nuevas:
                    monto = cash / len(nuevas)
                    for t in nuevas:
                        p = float(precios_hoy[t])
                        hist = adj[t].loc[:fecha].dropna().iloc[-253:]
                        vol = float(np.log(hist).diff().dropna().std()) if len(hist) > 20 else cfg["VOL_DEFECTO"]
                        nv = sa.niveles(p, vol, cfg)
                        u = (monto - cfg["COMISION"]) * (1 - cfg["COSTE_PCT"]) / p
                        pos[t] = {"u": u, "sl": nv["stop_loss"], "tp": nv["take_profit"], "precio": p}
                        cash -= monto
                # El SL de las que se mantienen solo sube
                for t in pos:
                    if t in nuevas:
                        continue
                    hist = adj[t].loc[:fecha].dropna().iloc[-253:]
                    vol = float(np.log(hist).diff().dropna().std()) if len(hist) > 20 else cfg["VOL_DEFECTO"]
                    nv = sa.niveles(float(precios_hoy[t]), vol, cfg)
                    pos[t]["sl"] = max(pos[t]["sl"], nv["stop_loss"])
                    pos[t]["tp"] = max(pos[t]["tp"], nv["take_profit"])
            if verbose:
                print(f"  {fecha.date()}: califican {len(ranking)} | cartera {sorted(pos)}")
        curva.append((fecha, cash + sum(x["u"] * precios_hoy[t] for t, x in pos.items())))

    eq = pd.Series(dict(curva))
    return {"equity": eq, "operaciones": pd.DataFrame(ops),
            "calificadas_prom": round(float(np.mean(calificadas)), 1) if calificadas else 0,
            "cartera_final": sorted(pos)}


# ======================================================================
# CARGA DE SIMFIN Y REPORTE
# ======================================================================

def cargar_simfin(directorio: str = "~/simfin_data") -> dict:
    import simfin as sf
    sf.set_api_key(os.environ.get("SIMFIN_API_KEY", "free"))
    sf.set_data_dir(os.path.expanduser(directorio))
    inc = sf.load_income(variant="ttm", market="us").reset_index()
    cf = sf.load_cashflow(variant="ttm", market="us").reset_index()
    bal = sf.load_balance(variant="quarterly", market="us").reset_index()
    precios = sf.load_shareprices(variant="daily", market="us").reset_index()
    comp = sf.load_companies(market="us")
    ind = sf.load_industries()

    k = [COL["ticker"], COL["report"]]
    ttm = inc[k + [COL["publish"], COL["revenue"], COL["op_income"], COL["net_income"]]].merge(
        cf[k + [COL["publish"], COL["cfo"], COL["capex"]]], on=k, suffixes=("", "_cf"))
    ttm[COL["publish"]] = ttm[[COL["publish"], COL["publish"] + "_cf"]].max(axis=1)
    ttm = ttm.drop(columns=[COL["publish"] + "_cf"]).sort_values(k)
    bal = bal[k + [COL["publish"], COL["equity"], COL["st_debt"], COL["lt_debt"]]].sort_values(k)

    adj = precios.pivot_table(index=COL["date"], columns=COL["ticker"], values=COL["adj_close"])
    revisiones = fechas_revision(adj.index, adj.index[0])
    snap = precios[precios[COL["date"]].isin(revisiones)]
    snap = snap.assign(cap=snap[COL["close"]] * snap[COL["shares"]])
    cap = {f: _sin_duplicados(g.set_index(COL["ticker"])["cap"]) for f, g in snap.groupby(COL["date"])}
    sectores = comp["IndustryId"].map(ind["Sector"]) if "IndustryId" in comp else pd.Series(dtype=str)
    sectores = _sin_duplicados(sectores)
    return {"ttm": ttm, "bal": bal, "adj": adj, "cap": cap, "sectores": sectores}

def main():
    ap = argparse.ArgumentParser(description="Backtest del satelite fundamental (SimFin)")
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--anios-calentamiento", type=float, default=3.0,
                    help="años de historia antes de la primera revisión (FCF de varios años)")
    ap.add_argument("--salida", default="resultados")
    args = ap.parse_args()

    print("Descargando datos de SimFin...")
    datos = cargar_simfin()
    primer_reporte = datos["ttm"][COL["publish"]].min()
    inicio = max(primer_reporte + pd.DateOffset(years=int(args.anios_calentamiento)),
                 datos["adj"].index[0] + pd.DateOffset(days=260))
    print(f"Precios {datos['adj'].index[0].date()} -> {datos['adj'].index[-1].date()} | "
          f"{datos['adj'].shape[1]} tickers | inicio del backtest {inicio.date()}")

    spy = bt.qc._normalizar_columnas(bt.qc.yf.download("SPY", period="max", progress=False,
                                                       auto_adjust=True))["Close"]
    filas, curvas, detalles = [], {}, []
    for nombre, kw in VARIANTES.items():
        print(f"\n{nombre}:")
        r = simular(datos, inicio, capital=args.capital, verbose=(nombre.startswith("Satelite")), **kw)
        eq = r["equity"]
        curvas[nombre] = eq / eq.iloc[0]
        m = bt.metricas(eq)
        ops = r["operaciones"]
        filas.append({"Variante": nombre, "Capital final ($)": round(float(eq.iloc[-1]), 0),
                      "CAGR %": m["cagr_pct"], "Máx. caída %": m["max_drawdown_pct"],
                      "Sharpe": m["sharpe"], "Ventas": len(ops),
                      "Stops": int((ops["motivo"] == "STOP_LOSS").sum()) if len(ops) else 0,
                      "Take profits": int((ops["motivo"] == "TAKE_PROFIT").sum()) if len(ops) else 0,
                      "Califican (prom.)": r["calificadas_prom"]})
        detalles.append(f"- {nombre}: cartera final {', '.join(r['cartera_final'])}")
        rango = eq.index
    s = spy.reindex(rango, method="ffill").dropna()
    mb = bt.metricas(s / s.iloc[0] * args.capital)
    filas.append({"Variante": "SPY (comprar y mantener)",
                  "Capital final ($)": round(args.capital * float(s.iloc[-1] / s.iloc[0]), 0),
                  "CAGR %": mb["cagr_pct"], "Máx. caída %": mb["max_drawdown_pct"],
                  "Sharpe": mb["sharpe"], "Ventas": 0, "Stops": 0, "Take profits": 0,
                  "Califican (prom.)": None})
    curvas["SPY"] = s / s.iloc[0]
    from estrategias import retornos_anuales
    anual = retornos_anuales(curvas)

    texto = "\n".join([
        "# Backtest del satélite fundamental (SimFin)",
        f"Periodo: {rango[0].date()} → {rango[-1].date()} · capital ${args.capital:,.0f} · "
        "revisión trimestral con datos publicados a esa fecha · comisión $1/lado + spread", "",
        pd.DataFrame(filas).to_markdown(index=False), "",
        "### Retorno por año calendario (%)", "", anual.to_markdown(), "",
        *detalles, "",
        "Limitaciones: solo EE. UU.; cifras reexpresadas por SimFin; empresas deslistadas "
        "valoradas a su último precio; ~12 meses de retraso en el plan gratuito."])
    os.makedirs(args.salida, exist_ok=True)
    with open(os.path.join(args.salida, "fundamental_backtest.md"), "w", encoding="utf-8") as f:
        f.write(texto)
    print("\n" + texto)


if __name__ == "__main__":
    main()
