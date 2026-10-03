# -*- coding: utf-8 -*-
"""
BACKTEST — simulación día a día de la estrategia de quant_core.py

Usa las MISMAS funciones que el bot (régimen, señal de compra, fuerza
relativa, calidad, tamaño de posición, filtro por sector y salidas), así que
lo que se mide es exactamente lo que el bot recomienda.

Supuestos (ver BT_DEFAULTS):
  - Las compras se ejecutan al cierre del día de la señal (el bot avisa 3:55 PM).
  - Stop Loss y Take Profit se evalúan con el High/Low del día siguiente en
    adelante; si el día abre más allá del nivel (gap), se ejecuta en la
    apertura. Si en un mismo día se tocan ambos, se asume el Stop Loss.
  - VENTA_URGENTE (cierre < SMA200) y STOP_DINAMICO (Chandelier) salen al cierre.
  - Costes de eToro por lado: comisión fija (acciones ~$1, ETFs $0) + spread
    (COSTE_PCT); cripto paga COSTE_PCT_CRIPTO. Ver costes_lado().
  - Sin win rate histórico (igual que el bot en GitHub, que no tiene la base).
  - Sesgo de supervivencia: el universo son los activos de HOY.

Uso desde consola:
    python backtest.py --periodo 10y                 # backtest + SPY
    python backtest.py --periodo 10y --walk-forward  # + validación fuera de muestra
"""

import argparse
import itertools
import math
import os

import numpy as np
import pandas as pd

import quant_core as qc
from quant_core import PARAMETROS, SECTORES, SECTOR_ETFS

BT_DEFAULTS = {
    # Costes observados en eToro (cuenta en USD, sin conversión de divisa):
    # acciones ~$1 al abrir y ~$1 al cerrar; ETFs sin comisión.
    "COMISION_ACCION":  1.0,     # USD por lado
    "COMISION_ETF":     0.0,     # USD por lado
    # Acciones y ETFs reales (sin apalancamiento): eToro no añade margen al
    # spread de mercado; en valores líquidos de EE. UU. suele ser < 0.05%.
    # La tabla de spreads de CFD de eToro solo aplica a posiciones CFD.
    "COSTE_PCT":        0.0005,  # spread de mercado estimado, 0.05% por lado
    "COSTE_PCT_CRIPTO": 0.01,    # cripto: 1% por lado (verificado con ETH)
    "SALIDA_TP":   True,     # Cerrar al tocar el Take Profit
    "COMPONER":    True,     # Tamaño según el capital actual (no el inicial)
}

ORDEN_CALIDAD = {"ALTA": 0, "NORMAL": 1, "DEBIL": 2}

def costes_lado(ticker: str, bt: dict) -> tuple[float, float]:
    """(porcentaje, fijo_usd) que se paga en cada compra o venta del activo."""
    if ticker.endswith("-USD"):
        return bt["COSTE_PCT_CRIPTO"], 0.0
    if ticker in qc.ETFS:
        return bt["COSTE_PCT"], bt["COMISION_ETF"]
    return bt["COSTE_PCT"], bt["COMISION_ACCION"]


# ======================================================================
# PREPARACIÓN
# ======================================================================

def _candidatos_por_fecha(datos: dict, tickers: list, regimen: pd.Series,
                          p: dict) -> dict:
    """{fecha: [ticker, ...]} con las señales de compra (incluye filtro RS)."""
    por_fecha: dict = {}
    for t in tickers:
        df = datos[t]
        mask = qc.mascara_compra(df, regimen, p, t)
        etf = SECTOR_ETFS.get(SECTORES.get(t, "Otros"))
        if etf and etf in datos:
            rs = qc.serie_fuerza_relativa(df, datos[etf], p)
            mask &= ~(rs <= p["RS_MIN"])   # NaN no bloquea (igual que el bot)
        for fecha in df.index[mask.values]:
            por_fecha.setdefault(fecha, []).append(t)
    return por_fecha


# ======================================================================
# MOTOR
# ======================================================================

def backtest(datos: dict, spy_close: pd.Series, vix_close: pd.Series,
             p: dict = PARAMETROS, inicio=None, fin=None,
             capital: float = None, tickers: list = None,
             bt: dict = None) -> dict:
    """
    Simula la estrategia y devuelve
    {"equity": Serie, "operaciones": DataFrame, "metricas": dict}.
    `datos` es la salida de qc.descargar_datos_globales().
    """
    bt = {**BT_DEFAULTS, **(bt or {})}
    capital = float(capital or p["CAPITAL_INICIAL"])
    if tickers is None:
        tickers = [t for t in SECTORES if t in datos]
    tickers = [t for t in tickers if t in datos]

    regimen = qc.serie_regimen(spy_close, vix_close, p)
    fechas = spy_close.index
    if inicio is not None:
        fechas = fechas[fechas >= pd.Timestamp(inicio)]
    if fin is not None:
        fechas = fechas[fechas <= pd.Timestamp(fin)]

    candidatos = _candidatos_por_fecha(datos, tickers, regimen, p)
    urgente = {t: qc.mascara_venta_urgente(datos[t]) for t in tickers}
    stop_din = {t: qc.mascara_stop_dinamico(datos[t], p) for t in tickers}

    cash = capital
    abiertas: dict = {}          # ticker -> posición
    ultimo_cierre: dict = {}
    operaciones = []
    equity = []

    def cerrar(t, fecha, precio, motivo):
        nonlocal cash
        pos = abiertas.pop(t)
        pct, fijo = costes_lado(t, bt)
        neto = pos["unidades"] * precio * (1 - pct) - fijo
        cash += neto
        pnl = neto - pos["coste_total"]
        operaciones.append({
            "ticker": t, "sector": SECTORES.get(t, "Otros"),
            "calidad": pos["calidad"],
            "fecha_entrada": pos["fecha"], "precio_entrada": pos["precio"],
            "fecha_salida": fecha, "precio_salida": round(precio, 4),
            "unidades": pos["unidades"], "invertido": round(pos["coste_total"], 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl / pos["coste_total"] * 100, 2),
            "r_multiple": round(pnl / pos["riesgo_usd"], 2) if pos["riesgo_usd"] else None,
            "dias": (fecha - pos["fecha"]).days, "motivo": motivo,
        })

    for fecha in fechas:
        # ── 1. Salidas de posiciones abiertas ──
        for t in list(abiertas):
            df = datos[t]
            if fecha not in df.index:
                continue
            pos = abiertas[t]
            bar = df.loc[fecha]
            o, h, l, c = (float(bar["Open"]), float(bar["High"]),
                          float(bar["Low"]), float(bar["Close"]))
            ultimo_cierre[t] = c
            if l <= pos["stop_loss"]:
                cerrar(t, fecha, min(o, pos["stop_loss"]), "STOP_LOSS")
            elif bt["SALIDA_TP"] and h >= pos["take_profit"]:
                cerrar(t, fecha, max(o, pos["take_profit"]), "TAKE_PROFIT")
            elif urgente[t].get(fecha, False):
                cerrar(t, fecha, c, "VENTA_URGENTE")
            elif stop_din[t].get(fecha, False):
                cerrar(t, fecha, c, "STOP_DINAMICO")

        valor_abiertas = sum(pos["unidades"] * ultimo_cierre.get(t, pos["precio"])
                             for t, pos in abiertas.items())
        equity_hoy = cash + valor_abiertas

        # ── 2. Entradas al cierre ──
        señales = []
        for t in candidatos.get(fecha, []):
            if t in abiertas:
                continue
            row = datos[t].loc[fecha]
            precio, atr = float(row["Close"]), float(row["ATR"])
            base = qc.calcular_gestion_riesgo(precio, atr, "NORMAL", capital, p)
            calidad = qc.clasificar_calidad_senal(
                float(row["RSI"]), float(row["RV"]), base["rr_ratio"], None, p)
            señales.append({"ticker": t, "precio": precio, "atr": atr,
                            "calidad": calidad, "rv": float(row["RV"]),
                            "rr": base["rr_ratio"]})
        señales.sort(key=lambda s: (ORDEN_CALIDAD[s["calidad"]], -s["rv"], -s["rr"]))

        por_sector: dict = {}
        for t in abiertas:
            sec = SECTORES.get(t, "Otros")
            por_sector[sec] = por_sector.get(sec, 0) + 1

        base_capital = equity_hoy if bt["COMPONER"] else capital
        for s in señales:
            sec = SECTORES.get(s["ticker"], "Otros")
            if por_sector.get(sec, 0) >= p["MAX_POR_SECTOR"]:
                continue
            r = qc.calcular_gestion_riesgo(s["precio"], s["atr"], s["calidad"],
                                           base_capital, p)
            if r["rr_ratio"] < p["RR_MINIMO"] or r["unidades"] <= 0:
                continue
            unidades = r["unidades"]
            pct, fijo = costes_lado(s["ticker"], bt)
            coste = unidades * s["precio"] * (1 + pct) + fijo
            if coste > cash:
                # Sin efectivo suficiente: compra lo que alcance
                unidades = (cash - fijo) / (s["precio"] * (1 + pct))
                if p.get("FRACCIONES", False):
                    f = 10 ** p.get("DECIMALES_UNIDADES", 4)
                    unidades = math.floor(unidades * f) / f
                else:
                    unidades = math.floor(unidades)
                if unidades <= 0 or unidades * s["precio"] < p.get("INVERSION_MINIMA", 0):
                    continue
                coste = unidades * s["precio"] * (1 + pct) + fijo
            cash -= coste
            abiertas[s["ticker"]] = {
                "fecha": fecha, "precio": s["precio"], "unidades": unidades,
                "stop_loss": r["stop_loss"], "take_profit": r["take_profit"],
                "calidad": s["calidad"], "coste_total": coste,
                "riesgo_usd": unidades * s["atr"] * p["ATR_SL"],
            }
            ultimo_cierre[s["ticker"]] = s["precio"]
            por_sector[sec] = por_sector.get(sec, 0) + 1

        valor_abiertas = sum(pos["unidades"] * ultimo_cierre.get(t, pos["precio"])
                             for t, pos in abiertas.items())
        equity.append((fecha, cash + valor_abiertas, len(abiertas)))

    # Cerrar lo que quede abierto al último precio conocido
    if len(fechas):
        for t in list(abiertas):
            cerrar(t, fechas[-1], ultimo_cierre.get(t, abiertas[t]["precio"]), "FIN")

    eq = pd.DataFrame(equity, columns=["fecha", "equity", "posiciones"]).set_index("fecha")
    ops = pd.DataFrame(operaciones)
    return {"equity": eq["equity"], "posiciones": eq["posiciones"],
            "operaciones": ops,
            "metricas": metricas(eq["equity"], ops, eq["posiciones"])}


# ======================================================================
# MÉTRICAS
# ======================================================================

def _r(x, n=2):
    """round() que devuelve float de Python (no np.float64)."""
    return round(float(x), n)

def estadisticas_operaciones(ops: pd.DataFrame, col_pnl: str = "pnl") -> dict:
    """Estadísticas de una lista de operaciones (backtest o reales)."""
    if ops is None or len(ops) == 0:
        return {"operaciones": 0}
    pnl = ops[col_pnl].astype(float)
    gan, per = pnl[pnl > 0], pnl[pnl <= 0]
    pf = gan.sum() / -per.sum() if per.sum() < 0 else float("inf")
    est = {
        "operaciones":     int(len(pnl)),
        "ganadoras":       int(len(gan)),
        "win_rate_pct":    _r(len(gan) / len(pnl) * 100, 1),
        "pnl_total":       _r(pnl.sum(), 2),
        "ganancia_prom":   _r(gan.mean(), 2) if len(gan) else 0.0,
        "perdida_prom":    _r(per.mean(), 2) if len(per) else 0.0,
        "profit_factor":   _r(pf, 2) if math.isfinite(pf) else pf,
        "esperanza_trade": _r(pnl.mean(), 2),
    }
    if "dias" in ops:
        est["dias_prom"] = _r(float(ops["dias"].mean()), 1)
    if "r_multiple" in ops:
        est["r_prom"] = _r(float(ops["r_multiple"].dropna().mean()), 2)
    return est

def cargar_operaciones_reales(ruta: str = "datos/operaciones_reales.json") -> pd.DataFrame:
    """Operaciones reales cerradas (registradas en el repo)."""
    import json
    with open(ruta, encoding="utf-8") as f:
        return pd.DataFrame(json.load(f)["operaciones"])

def resumen_operaciones_reales(ruta: str = "datos/operaciones_reales.json") -> pd.DataFrame:
    """Estadísticas separando las del bot de las previas al sistema."""
    ops = cargar_operaciones_reales(ruta)
    col = "pnl_neto" if "pnl_neto" in ops else "pnl"   # neto de comisiones
    filas = [{"grupo": g, **estadisticas_operaciones(d, col),
              "comisiones": round(float(d.get("comision", pd.Series(0.0)).sum()), 2)}
             for g, d in ops.groupby("origen")]
    filas.append({"grupo": "total", **estadisticas_operaciones(ops, col),
                  "comisiones": round(float(ops.get("comision", pd.Series(0.0)).sum()), 2)})
    return pd.DataFrame(filas).set_index("grupo")

def metricas(equity: pd.Series, ops: pd.DataFrame = None,
             posiciones: pd.Series = None) -> dict:
    if equity is None or len(equity) < 2:
        return {}
    ret = equity.pct_change().dropna()
    años = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    dd = equity / equity.cummax() - 1
    m = {
        "capital_inicial":  _r(float(equity.iloc[0]), 2),
        "capital_final":    _r(float(equity.iloc[-1]), 2),
        "retorno_total_pct": _r(total * 100, 2),
        "cagr_pct":         _r(((1 + total) ** (1 / años) - 1) * 100, 2),
        "max_drawdown_pct": _r(float(dd.min()) * 100, 2),
        "sharpe":           _r(float(ret.mean() / ret.std() * np.sqrt(252)), 2)
                            if ret.std() > 0 else 0.0,
        "volatilidad_pct":  _r(float(ret.std() * np.sqrt(252)) * 100, 2),
    }
    if posiciones is not None:
        m["exposicion_pct"] = _r(float((posiciones > 0).mean()) * 100, 1)
    if ops is not None:
        m.update(estadisticas_operaciones(ops))
    return m

def benchmark(precio: pd.Series, capital: float, inicio=None, fin=None,
              bt: dict = None) -> dict:
    """Comprar y mantener (p. ej. SPY) con el mismo capital y coste de entrada."""
    bt = {**BT_DEFAULTS, **(bt or {})}
    s = precio.dropna()
    if inicio is not None:
        s = s[s.index >= pd.Timestamp(inicio)]
    if fin is not None:
        s = s[s.index <= pd.Timestamp(fin)]
    unidades = (capital - bt["COMISION_ETF"]) / (s.iloc[0] * (1 + bt["COSTE_PCT"]))
    eq = unidades * s
    return {"equity": eq, "metricas": metricas(eq)}


# ======================================================================
# WALK-FORWARD (validación fuera de muestra)
# ======================================================================

GRID_DEFECTO = {
    "RSI_ENTRADA": [40, 45, 50],
    "RV_MIN":      [1.2, 1.5],
    "ATR_SL":      [2.0, 2.5, 3.0],
}

def walk_forward(datos: dict, spy_close: pd.Series, vix_close: pd.Series,
                 grid: dict = None, años_entrenamiento: int = 3,
                 años_prueba: int = 1, criterio: str = "sharpe",
                 p: dict = PARAMETROS, capital: float = None,
                 bt: dict = None, verbose: bool = True) -> pd.DataFrame:
    """
    Para cada ventana: elige los parámetros con mejor `criterio` en los
    años de entrenamiento y los mide en el año siguiente (que no vio).
    Compara contra los parámetros actuales y contra SPY en esa misma ventana.
    """
    grid = grid or GRID_DEFECTO
    combos = [dict(zip(grid, v)) for v in itertools.product(*grid.values())]
    # Primer día con SMA200 de SPY y de los activos disponible
    inicio_datos = spy_close.index[0] + pd.DateOffset(days=300)
    fin_datos = spy_close.index[-1]
    filas = []
    ini_test = inicio_datos + pd.DateOffset(years=años_entrenamiento)
    while ini_test < fin_datos:
        fin_test = min(ini_test + pd.DateOffset(years=años_prueba), fin_datos)
        ini_train = ini_test - pd.DateOffset(years=años_entrenamiento)
        mejor, mejor_valor = None, -np.inf
        for c in combos:
            pc = {**p, **c}
            m = backtest(datos, spy_close, vix_close, pc, ini_train,
                         ini_test - pd.Timedelta(days=1), capital, bt=bt)["metricas"]
            valor = m.get(criterio, -np.inf)
            if m.get("operaciones", 0) >= 10 and valor > mejor_valor:
                mejor, mejor_valor = c, valor
        fila = {"prueba_inicio": ini_test.date(), "prueba_fin": fin_test.date(),
                "params_elegidos": mejor}
        if mejor is not None:
            m_opt = backtest(datos, spy_close, vix_close, {**p, **mejor},
                             ini_test, fin_test, capital, bt=bt)["metricas"]
            fila.update({f"opt_{k}": m_opt.get(k) for k in
                         ("retorno_total_pct", "max_drawdown_pct", "sharpe", "operaciones")})
        m_act = backtest(datos, spy_close, vix_close, p, ini_test, fin_test,
                         capital, bt=bt)["metricas"]
        fila.update({f"actual_{k}": m_act.get(k) for k in
                     ("retorno_total_pct", "max_drawdown_pct", "sharpe", "operaciones")})
        m_spy = benchmark(spy_close, capital or p["CAPITAL_INICIAL"],
                          ini_test, fin_test, bt)["metricas"]
        fila.update({f"spy_{k}": m_spy.get(k) for k in
                     ("retorno_total_pct", "max_drawdown_pct", "sharpe")})
        filas.append(fila)
        if verbose:
            print(f"  Ventana {fila['prueba_inicio']} → {fila['prueba_fin']}: "
                  f"elegidos {mejor} | opt {fila.get('opt_retorno_total_pct')}% | "
                  f"actual {fila['actual_retorno_total_pct']}% | "
                  f"SPY {fila['spy_retorno_total_pct']}%")
        ini_test = fin_test + pd.Timedelta(days=1)
    return pd.DataFrame(filas)


# ======================================================================
# ADN POR ACTIVO (reemplaza al "Ret 10 días" del notebook)
# ======================================================================

def adn_por_activo(datos: dict, spy_close: pd.Series, vix_close: pd.Series,
                   tickers: list = None, p: dict = PARAMETROS,
                   bt: dict = None, inicio=None, fin=None) -> pd.DataFrame:
    """Backtest de cada activo por separado con las reglas reales de salida."""
    tickers = tickers or [t for t in SECTORES if t in datos]
    filas = []
    for t in tickers:
        if t not in datos:
            continue
        r = backtest(datos, spy_close, vix_close, p, inicio, fin,
                     tickers=[t], bt=bt)
        e = estadisticas_operaciones(r["operaciones"])
        filas.append({"ticker": t, "sector": SECTORES.get(t, "Otros"), **e})
    df = pd.DataFrame(filas)
    if "profit_factor" in df:
        df = df.sort_values(["esperanza_trade"], ascending=False, na_position="last")
    return df


# ======================================================================
# DATOS Y REPORTE
# ======================================================================

def descargar(periodo: str = "10y") -> tuple:
    """(datos, spy_close, vix_close) listos para backtest()."""
    datos = qc.descargar_datos_globales(periodo=periodo)
    vix = qc._normalizar_columnas(
        qc.yf.download("^VIX", period=periodo, progress=False, auto_adjust=True))
    if "SPY" not in datos:
        raise RuntimeError("No se pudo descargar SPY")
    return datos, datos["SPY"]["Close"], vix["Close"].dropna()

def reporte(res: dict, bench: dict, wf: pd.DataFrame = None) -> str:
    m, b = res["metricas"], bench["metricas"]
    filas = [
        ("Capital final ($)",     "capital_final"),
        ("Retorno total (%)",     "retorno_total_pct"),
        ("CAGR (%)",              "cagr_pct"),
        ("Máx. drawdown (%)",     "max_drawdown_pct"),
        ("Sharpe",                "sharpe"),
        ("Volatilidad anual (%)", "volatilidad_pct"),
    ]
    txt = ["# Backtest Quant Bot",
           f"Periodo: {res['equity'].index[0].date()} → {res['equity'].index[-1].date()}",
           "", "| Métrica | Estrategia | SPY (comprar y mantener) |", "|---|---|---|"]
    txt += [f"| {n} | {m.get(k)} | {b.get(k)} |" for n, k in filas]
    txt += ["", "## Operaciones", "| Métrica | Valor |", "|---|---|"]
    for k in ("operaciones", "win_rate_pct", "profit_factor", "ganancia_prom",
              "perdida_prom", "esperanza_trade", "r_prom", "dias_prom",
              "exposicion_pct"):
        txt.append(f"| {k} | {m.get(k)} |")
    ops = res["operaciones"]
    if len(ops):
        txt += ["", "## Salidas por motivo", "| Motivo | N | PnL |", "|---|---|---|"]
        for motivo, g in ops.groupby("motivo"):
            txt.append(f"| {motivo} | {len(g)} | {round(g['pnl'].sum(), 2)} |")
        txt += ["", "## Por calidad de señal", "| Calidad | N | Win rate % | PnL |",
                "|---|---|---|---|"]
        for cal, g in ops.groupby("calidad"):
            txt.append(f"| {cal} | {len(g)} | {round((g['pnl'] > 0).mean() * 100, 1)} "
                       f"| {round(g['pnl'].sum(), 2)} |")
    if wf is not None and len(wf):
        txt += ["", "## Walk-forward (fuera de muestra)", "", wf.to_markdown(index=False)]
    return "\n".join(txt)

COLOR_ESTRATEGIA = "#2a78d6"   # azul  (paleta validada para daltonismo)
COLOR_SPY        = "#eb6834"   # naranja

def graficar(res: dict, bench: dict, titulo: str = "Estrategia vs SPY"):
    """Capital y drawdown de la estrategia frente a comprar y mantener SPY."""
    import matplotlib.pyplot as plt
    series = [("Estrategia", res["equity"], COLOR_ESTRATEGIA),
              ("SPY", bench["equity"], COLOR_SPY)]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1.3]})
    for nombre, eq, color in series:
        ax1.plot(eq.index, eq.values, color=color, linewidth=2, label=nombre)
        ax1.annotate(f"{nombre} ${eq.iloc[-1]:,.0f}", (eq.index[-1], eq.iloc[-1]),
                     xytext=(6, 0), textcoords="offset points", va="center",
                     fontsize=9, color="#333333")
        dd = (eq / eq.cummax() - 1) * 100
        ax2.plot(dd.index, dd.values, color=color, linewidth=1.5, label=nombre)
    ax1.set_title(titulo, loc="left", fontsize=13)
    ax1.set_ylabel("Capital ($)")
    ax2.set_ylabel("Drawdown (%)")
    ax1.legend(loc="upper left", frameon=False)
    for ax in (ax1, ax2):
        ax.grid(True, color="#e6e6e3", linewidth=0.8)
        ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    plt.show()

def main():
    ap = argparse.ArgumentParser(description="Backtest de la estrategia Quant")
    ap.add_argument("--periodo", default="10y")
    ap.add_argument("--capital", type=float, default=None)
    ap.add_argument("--walk-forward", action="store_true")
    ap.add_argument("--spread", type=float, default=None,
                    help="Spread por lado en %% (p. ej. 0.15 = tabla CFD de eToro)")
    ap.add_argument("--salida", default="resultados")
    args = ap.parse_args()

    bt_cfg = dict(BT_DEFAULTS)
    if args.spread is not None:
        bt_cfg["COSTE_PCT"] = args.spread / 100
    datos, spy, vix = descargar(args.periodo)
    capital = args.capital or PARAMETROS["CAPITAL_INICIAL"]
    # Primer día con indicadores completos
    inicio = spy.index[0] + pd.DateOffset(days=300)
    res = backtest(datos, spy, vix, capital=capital, inicio=inicio, bt=bt_cfg)
    bench = benchmark(spy, capital, inicio=inicio, bt=bt_cfg)
    wf = None
    if args.walk_forward:
        print("\nWalk-forward:")
        wf = walk_forward(datos, spy, vix, capital=capital, bt=bt_cfg)

    os.makedirs(args.salida, exist_ok=True)
    texto = reporte(res, bench, wf)
    texto = texto.replace("# Backtest Quant Bot",
                          f"# Backtest Quant Bot\nSpread por lado: {bt_cfg['COSTE_PCT'] * 100:.2f}%", 1)
    with open(os.path.join(args.salida, "resumen.md"), "w", encoding="utf-8") as f:
        f.write(texto)
    res["operaciones"].to_csv(os.path.join(args.salida, "operaciones.csv"), index=False)
    pd.DataFrame({"estrategia": res["equity"], "spy": bench["equity"]}).to_csv(
        os.path.join(args.salida, "equity.csv"))
    if wf is not None:
        wf.to_csv(os.path.join(args.salida, "walk_forward.csv"), index=False)
    adn = adn_por_activo(datos, spy, vix, inicio=inicio, bt=bt_cfg)
    adn.to_csv(os.path.join(args.salida, "adn_por_activo.csv"), index=False)
    print("\n" + texto)
    print(f"\nArchivos guardados en {args.salida}/")


if __name__ == "__main__":
    main()
