# -*- coding: utf-8 -*-
"""
FILTRO FUNDAMENTAL — calidad + valoración + crecimiento

Puntúa las acciones del universo con datos fundamentales actuales de
yfinance (no hay histórico "punto en el tiempo" gratuito, así que esto NO se
puede validar con backtest: se valida hacia adelante con la cartera satélite).

  Filtros mínimos (descartan la acción):
    capitalización ≥ $10B · flujo de caja libre > 0 en TODOS los años
    disponibles (hasta 4) · ROE ≥ 10% · deuda/patrimonio ≤ 2x · P/E positivo
    · moneda de reporte convertible a USD (en los ADRs yfinance da los
      estados en moneda local y la capitalización en USD: se convierten al
      tipo de cambio actual antes de calcular las razones)

  Universo: acciones de EE. UU. del bot + empresas internacionales grandes
  que cotizan en EE. UU. como ADR (UNIVERSO_INTERNACIONAL).
    Se excluyen bancos/finanzas, REITs, utilities, ETFs y cripto: sus
    métricas de deuda y flujo de caja no son comparables con el resto.

  Puntaje (percentil dentro del universo, 0 a 1):
    calidad      50%: ROE, margen operativo, margen de flujo libre, poca deuda
    valoración   35%: rendimiento de flujo libre PROMEDIO de varios años
                      (evita que un año récord de una cíclica la haga
                      parecer barata) y rendimiento de ganancias (1/P/E)
    crecimiento  15%: crecimiento de ingresos
"""

import time

import numpy as np
import pandas as pd

import quant_core as qc

SECTORES_EXCLUIDOS = {
    'Finanzas', 'REIT Industrial', 'REIT Telecom', 'REIT Data Centers',
    'REIT Retail', 'Inmobiliario', 'Utilities', 'Índices (ETF)', 'Bonos',
    'Oro/Refugio', 'Crypto', 'Energía (Apal.)',
}

FILTROS = {
    "CAP_MINIMA":     10e9,
    "ROE_MINIMO":     0.10,
    "DEUDA_MAXIMA":   200,     # yfinance da deuda/patrimonio en %: 200 = 2x
}

PESOS = {"calidad": 0.50, "valor": 0.35, "crecimiento": 0.15}

CAMPOS = ["shortName", "marketCap", "currentPrice", "returnOnEquity",
          "operatingMargins", "freeCashflow", "totalRevenue", "debtToEquity",
          "trailingPE", "forwardPE", "revenueGrowth", "financialCurrency",
          "netIncomeToCommon"]
NUMERICOS = [c for c in CAMPOS if c not in ("shortName", "financialCurrency")]

# Montos de los estados financieros (se convierten a USD si hace falta)
MONTOS = ["freeCashflow", "totalRevenue", "netIncomeToCommon"]

# Empresas internacionales rentables que cotizan en EE. UU. (ADR) -> (sector, país)
UNIVERSO_INTERNACIONAL = {
    # Europa
    "NVO":  ("Salud", "Dinamarca"),          "AZN":  ("Salud", "Reino Unido"),
    "NVS":  ("Salud", "Suiza"),              "SAP":  ("Software", "Alemania"),
    "UL":   ("Consumo defensivo", "Reino Unido"), "DEO": ("Bebidas", "Reino Unido"),
    "BTI":  ("Tabaco", "Reino Unido"),       "RELX": ("Servicios profesionales", "Reino Unido"),
    "SHEL": ("Energía", "Reino Unido"),      "TTE":  ("Energía", "Francia"),
    # Asia-Pacífico
    "TM":   ("Automotriz", "Japón"),         "SONY": ("Electrónica", "Japón"),
    "INFY": ("Servicios IT", "India"),       "PDD":  ("E-commerce", "China"),
    "BHP":  ("Minería", "Australia"),        "RIO":  ("Minería", "Reino Unido/Australia"),
    # Canadá
    "CNQ":  ("Energía", "Canadá"),           "CP":   ("Transporte", "Canadá"),
    # Latinoamérica
    "VALE": ("Minería", "Brasil"),           "PBR":  ("Energía", "Brasil"),
    "SQM":  ("Químicos", "Chile"),           "FMX":  ("Bebidas", "México"),
    "AMX":  ("Telecom", "México"),           "KOF":  ("Bebidas", "México"),
}

# País de las internacionales que ya estaban en el universo del bot
PAIS_EXTRA = {"TSM": "Taiwán", "ASML": "Países Bajos", "BABA": "China",
              "MELI": "Argentina/Uruguay", "SHOP": "Canadá", "ABEV": "Brasil"}


def sector_de(t: str) -> str:
    if t in UNIVERSO_INTERNACIONAL:
        return UNIVERSO_INTERNACIONAL[t][0]
    return qc.SECTORES.get(t, "Otros")


def pais_de(t: str) -> str:
    if t in UNIVERSO_INTERNACIONAL:
        return UNIVERSO_INTERNACIONAL[t][1]
    return PAIS_EXTRA.get(t, "EE. UU.")


_CACHE_FX: dict = {}

def tipo_cambio(moneda: str) -> float | None:
    """USD por 1 unidad de `moneda` (yfinance, último cierre)."""
    if not moneda or moneda == "USD":
        return 1.0
    divisor = 1.0
    if moneda == "GBp":                       # peniques
        moneda, divisor = "GBP", 100.0
    if moneda not in _CACHE_FX:
        try:
            df = qc._normalizar_columnas(qc.yf.download(f"{moneda}USD=X", period="5d",
                                                        progress=False, auto_adjust=True))
            _CACHE_FX[moneda] = float(df["Close"].dropna().iloc[-1])
        except Exception as e:
            print(f"Sin tipo de cambio para {moneda}: {e}")
            _CACHE_FX[moneda] = None
    fx = _CACHE_FX[moneda]
    return None if fx is None else fx / divisor


def convertir_a_usd(fila: dict, fx=tipo_cambio) -> dict:
    """Convierte los montos de los estados financieros a USD (ratios no cambian)."""
    moneda = fila.get("financialCurrency") or "USD"
    if moneda == "USD":
        return fila
    tasa = fx(moneda)
    if tasa is None:
        return fila                     # queda en otra moneda → el filtro la descarta
    fila = dict(fila)
    for c in MONTOS:
        if fila.get(c) is not None:
            fila[c] = float(fila[c]) * tasa
    if fila.get("fcf_hist"):
        fila["fcf_hist"] = [float(x) * tasa for x in fila["fcf_hist"]]
    # P/E propio con utilidad convertida (el de yfinance puede mezclar monedas)
    if fila.get("netIncomeToCommon") and fila.get("marketCap"):
        pe = float(fila["marketCap"]) / fila["netIncomeToCommon"]
        fila["trailingPE"] = pe
        fila["forwardPE"] = None
    fila["moneda_reporte"] = moneda
    fila["financialCurrency"] = "USD"
    return fila


# Misma empresa con dos clases de acción: se analiza solo una
DUPLICADOS = {"GOOGL"}

def universo_acciones() -> list:
    eeuu = [t for t, s in qc.SECTORES.items()
            if t not in qc.ETFS and t not in DUPLICADOS and not t.endswith("-USD")
            and s not in SECTORES_EXCLUIDOS]
    return eeuu + [t for t in UNIVERSO_INTERNACIONAL if t not in eeuu]


def obtener_fundamentales(tickers: list = None, pausa: float = 0.3) -> pd.DataFrame:
    """Descarga los datos de yfinance. Los tickers que fallan se omiten."""
    filas = []
    for t in tickers or universo_acciones():
        try:
            tk = qc.yf.Ticker(t)
            info = tk.info
            fila = {"ticker": t, **{c: info.get(c) for c in CAMPOS}}
            try:
                cf = tk.cashflow   # anual, columnas = años (más reciente primero)
                if cf is not None and "Free Cash Flow" in cf.index:
                    fila["fcf_hist"] = [float(x) for x in cf.loc["Free Cash Flow"].dropna().iloc[:4]]
            except Exception:
                pass
            filas.append(convertir_a_usd(fila))
        except Exception as e:
            print(f"Sin datos fundamentales para {t}: {e}")
        time.sleep(pausa)
    return pd.DataFrame(filas).set_index("ticker") if filas else pd.DataFrame()


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def puntuar(df: pd.DataFrame, filtros: dict = FILTROS, pesos: dict = PESOS) -> pd.DataFrame:
    """Agrega métricas, aplica filtros y devuelve el ranking (mejor primero)."""
    d = df.copy()
    for c in NUMERICOS:
        if c in d:
            d[c] = _num(d[c])
    d["sector"] = [sector_de(t) for t in d.index]
    d["pais"] = [pais_de(t) for t in d.index]
    hist = d["fcf_hist"] if "fcf_hist" in d else pd.Series([None] * len(d), index=d.index)
    hist = [h if isinstance(h, (list, tuple)) and len(h) else [f] for h, f in zip(hist, d["freeCashflow"])]
    d["fcf_prom"] = [float(np.mean(h)) if all(pd.notna(h)) else np.nan for h in hist]
    d["fcf_min"] = [float(np.min(h)) if all(pd.notna(h)) else np.nan for h in hist]
    d["fcf_anios"] = [len(h) for h in hist]
    d["fcf_yield"] = d["fcf_prom"] / d["marketCap"]
    d["fcf_margen"] = d["fcf_prom"] / d["totalRevenue"]
    moneda = d["financialCurrency"] if "financialCurrency" in d else pd.Series("USD", index=d.index)
    pe = d["forwardPE"].where(d["forwardPE"] > 0, d["trailingPE"])
    d["pe"] = pe
    d["earnings_yield"] = 1 / pe

    motivo = pd.Series("", index=d.index)
    motivo[d["marketCap"].isna() | (d["marketCap"] < filtros["CAP_MINIMA"])] += "cap. pequeña; "
    motivo[~(d["freeCashflow"] > 0) | ~(d["fcf_min"] > 0)] += "FCF negativo en algun año; "
    motivo[~moneda.fillna("USD").isin(["USD"])] += "reporta en otra moneda; "
    motivo[~(d["returnOnEquity"] >= filtros["ROE_MINIMO"])] += "ROE bajo; "
    motivo[~(d["debtToEquity"].fillna(0) <= filtros["DEUDA_MAXIMA"])] += "deuda alta; "
    motivo[~(pe > 0)] += "P/E no positivo; "
    d["descartada"] = motivo.str.rstrip("; ")

    ok = d[d["descartada"] == ""].copy()
    ok.attrs["descartadas"] = d.loc[d["descartada"] != "", "descartada"].to_dict()
    if ok.empty:
        return ok
    r = lambda s, asc=True: s.rank(pct=True, ascending=asc)
    ok["calidad"] = pd.concat([r(ok["returnOnEquity"]), r(ok["operatingMargins"]),
                               r(ok["fcf_margen"]), r(ok["debtToEquity"].fillna(0), False)],
                              axis=1).mean(axis=1)
    ok["valor"] = pd.concat([r(ok["fcf_yield"]), r(ok["earnings_yield"])], axis=1).mean(axis=1)
    ok["crecimiento"] = r(ok["revenueGrowth"]).fillna(0.5)
    ok["puntaje"] = (pesos["calidad"] * ok["calidad"] + pesos["valor"] * ok["valor"]
                     + pesos["crecimiento"] * ok["crecimiento"])
    ranking = ok.sort_values("puntaje", ascending=False)
    ranking.attrs["descartadas"] = ok.attrs["descartadas"]
    return ranking


def seleccionar(ranking: pd.DataFrame, n: int, max_por_sector: int = 2,
                actuales: list = None, buffer: int = 15) -> list:
    """
    Elige `n` acciones. Las que ya tienes se mantienen mientras sigan dentro
    del top `buffer` (evita rotar por pequeñas diferencias y pagar comisiones).
    """
    actuales = actuales or []
    top_buffer = list(ranking.index[:buffer])
    elegidas = [t for t in actuales if t in top_buffer][:n]
    por_sector: dict = {}
    for t in elegidas:
        s = ranking.at[t, "sector"]
        por_sector[s] = por_sector.get(s, 0) + 1
    for t in ranking.index:
        if len(elegidas) >= n:
            break
        s = ranking.at[t, "sector"]
        if t in elegidas or por_sector.get(s, 0) >= max_por_sector:
            continue
        elegidas.append(t)
        por_sector[s] = por_sector.get(s, 0) + 1
    return elegidas


def resumen_accion(ranking: pd.DataFrame, t: str) -> dict:
    """Métricas clave para registrar la 'tesis' de compra."""
    f = ranking.loc[t]
    v = lambda x, k=1: None if pd.isna(x) else round(float(x) * k, 1)
    return {"nombre": f.get("shortName"), "sector": f["sector"], "pais": f.get("pais", "EE. UU."),
            "puntaje": round(float(f["puntaje"]), 3), "roe_pct": v(f["returnOnEquity"], 100),
            "margen_op_pct": v(f["operatingMargins"], 100), "fcf_yield_pct": v(f["fcf_yield"], 100),
            "pe": v(f["pe"]), "crec_ingresos_pct": v(f["revenueGrowth"], 100),
            "deuda_patrimonio": v(f["debtToEquity"], 0.01)}


def texto_ranking(ranking: pd.DataFrame, n: int = 10, marcadas: list = None) -> str:
    marcadas = set(marcadas or [])
    lineas = [f"Top {n} fundamental (calidad 50% / valor 35% / crec. 15%):"]
    for i, t in enumerate(ranking.index[:n], 1):
        m = resumen_accion(ranking, t)
        marca = " *" if t in marcadas else ""
        lugar = m['sector'] if m['pais'] == "EE. UU." else f"{m['sector']}, {m['pais']}"
        lineas.append(f"{i:2d}. {t}{marca} ({lugar}) punt {m['puntaje']:.2f} | "
                      f"ROE {m['roe_pct']}% | FCF yield {m['fcf_yield_pct']}% | "
                      f"P/E {m['pe']} | crec {m['crec_ingresos_pct']}%")
    if marcadas:
        lineas.append("* = en tu cartera satelite")
    return "\n".join(lineas)


if __name__ == "__main__":
    datos = obtener_fundamentales()
    ranking = puntuar(datos)
    print(f"Con datos: {len(datos)} | Califican: {len(ranking)} | "
          f"Descartadas: {len(ranking.attrs.get('descartadas', {}))}")
    for t, motivo in sorted(ranking.attrs.get("descartadas", {}).items()):
        print(f"  descartada {t}: {motivo}")
    print()
    print(texto_ranking(ranking, 20))
