# -*- coding: utf-8 -*-
"""
ESTRATEGIAS ALTERNATIVAS CON ETFs — comparación contra el bot Quant y SPY

  SPY  : comprar y mantener SPY (referencia).
  A    : Tendencia. Al cierre de cada mes, SPY si cotiza sobre su SMA 200;
         si no, IEF (bonos del Tesoro 7-10 años).
  B    : Momentum. Al cierre de cada mes, el ETF con mejor promedio de
         retorno a 3, 6 y 12 meses entre SPY, QQQ, EFA, GLD e IEF.
  50/50: 50% SPY fijo + 50% en la elección de B (la cartera del bot).

Los parámetros son los clásicos de la literatura y NO se optimizan con
estos datos, para que el resultado no esté sobreajustado.

Supuestos:
  - La decisión se toma con el cierre del último día hábil del mes y se
    ejecuta al cierre del día hábil siguiente (sin mirar el futuro).
  - En eToro los ETFs no pagan comisión: solo spread (COSTE_PCT por lado).
  - Aporte mensual opcional el primer día hábil de cada mes, invertido según
    la asignación vigente. Las métricas se calculan sobre el valor por
    participación, así los aportes no inflan la rentabilidad.

Uso:
    python estrategias.py --periodo max --aporte 100
"""

import argparse
import os

import numpy as np
import pandas as pd

import quant_core as qc
import backtest as bt

UNIVERSO_MOMENTUM = ["SPY", "QQQ", "EFA", "GLD", "IEF"]
COSTE_PCT_ETF = 0.0005   # spread por lado (ETFs sin comisión en eToro)


# ======================================================================
# REGLAS DE ASIGNACIÓN — reciben el histórico hasta la fecha de decisión
# ======================================================================

def regla_comprar_mantener(ticker: str = "SPY"):
    return lambda hist: {ticker: 1.0}

def regla_tendencia(riesgo: str = "SPY", refugio: str = "IEF", sma: int = 200):
    def regla(hist: pd.DataFrame) -> dict:
        serie = hist[riesgo].dropna()
        if len(serie) < sma:
            return {refugio: 1.0}
        return {riesgo: 1.0} if serie.iloc[-1] > serie.iloc[-sma:].mean() else {refugio: 1.0}
    return regla

VENTANAS_MOMENTUM = (63, 126, 252)   # ~3, 6 y 12 meses hábiles

def puntajes_momentum(hist: pd.DataFrame, universo: list = None,
                      ventanas=VENTANAS_MOMENTUM) -> dict:
    """Promedio del retorno a 3, 6 y 12 meses de cada ETF (mayor = más fuerte)."""
    universo = universo or UNIVERSO_MOMENTUM
    if len(hist) <= max(ventanas):
        return {}
    return {t: float(np.mean([hist[t].iloc[-1] / hist[t].iloc[-1 - v] - 1 for v in ventanas]))
            for t in universo if t in hist}

def regla_momentum(universo: list = None, ventanas=VENTANAS_MOMENTUM, top: int = 1):
    universo = universo or UNIVERSO_MOMENTUM
    def regla(hist: pd.DataFrame) -> dict:
        puntaje = puntajes_momentum(hist, universo, ventanas)
        if not puntaje:
            return {"IEF": 1.0}
        elegidos = sorted(puntaje, key=puntaje.get, reverse=True)[:top]
        return {t: 1.0 / len(elegidos) for t in elegidos}
    return regla


def regla_mezcla(partes: list):
    """Combina reglas: partes = [(peso, regla), ...]. Los pesos suman 1."""
    def regla(hist: pd.DataFrame) -> dict:
        total: dict = {}
        for peso, r in partes:
            for t, w in r(hist).items():
                total[t] = total.get(t, 0.0) + peso * w
        return total
    return regla

def regla_spy_mas_b(peso_spy: float = 0.5):
    """Cartera del bot: peso_spy en SPY fijo + el resto en la estrategia B."""
    return regla_mezcla([(peso_spy, regla_comprar_mantener("SPY")),
                         (1 - peso_spy, regla_momentum())])


# ======================================================================
# SIMULADOR DE CARTERA CON REBALANCEO MENSUAL
# ======================================================================

def simular(precios: pd.DataFrame, regla, capital: float = 2380,
            aporte_mensual: float = 0.0, coste_pct: float = COSTE_PCT_ETF,
            inicio=None) -> dict:
    """
    Devuelve {"valor": Serie en USD, "nav": valor por participación (base 1),
    "operaciones": n, "costes": USD, "aportado": USD}.
    """
    precios = precios.dropna()
    fechas = precios.index
    if inicio is not None:
        fechas = fechas[fechas >= pd.Timestamp(inicio)]
    # Último día hábil de cada mes → decisión; se ejecuta el día siguiente
    fin_mes = set(pd.Series(fechas, index=fechas).groupby(fechas.to_period("M")).max())
    primer_dia = set(pd.Series(fechas, index=fechas).groupby(fechas.to_period("M")).min())

    unidades: dict = {}
    cash = capital
    participaciones = capital          # NAV inicial = 1
    objetivo = None
    pendiente = None
    n_ops, costes, aportado = 0, 0.0, capital
    valores, navs = [], []

    def valor_cartera(fecha):
        return cash + sum(u * precios.at[fecha, t] for t, u in unidades.items())

    def rebalancear(fecha, pesos):
        nonlocal cash, n_ops, costes
        total = valor_cartera(fecha)
        actuales = {t: unidades.get(t, 0.0) * precios.at[fecha, t] for t in set(unidades) | set(pesos)}
        for t in actuales:
            dif = total * pesos.get(t, 0.0) - actuales[t]
            if abs(dif) < 1.0:
                continue
            coste = abs(dif) * coste_pct
            unidades[t] = unidades.get(t, 0.0) + dif / precios.at[fecha, t]
            cash -= dif + coste
            costes += coste
            n_ops += 1
        for t in [t for t, u in unidades.items() if abs(u) < 1e-12]:
            del unidades[t]

    for i, fecha in enumerate(fechas):
        if i == 0:
            objetivo = regla(precios.loc[:fecha])
            rebalancear(fecha, objetivo)
        else:
            if fecha in primer_dia and aporte_mensual > 0:
                nav_hoy = valor_cartera(fecha) / participaciones
                cash += aporte_mensual
                aportado += aporte_mensual
                participaciones += aporte_mensual / nav_hoy
                rebalancear(fecha, objetivo)
            if pendiente is not None:
                if pendiente != objetivo:
                    objetivo = pendiente
                    rebalancear(fecha, objetivo)
                pendiente = None
        if fecha in fin_mes:
            pendiente = regla(precios.loc[:fecha])
        v = valor_cartera(fecha)
        valores.append(v)
        navs.append(v / participaciones)

    return {"valor": pd.Series(valores, index=fechas),
            "nav": pd.Series(navs, index=fechas),
            "operaciones": n_ops, "costes": round(costes, 2),
            "aportado": round(aportado, 2)}


# ======================================================================
# COMPARACIÓN Y REPORTE
# ======================================================================

ESTRATEGIAS = {
    "SPY (comprar y mantener)":  regla_comprar_mantener("SPY"),
    "A · Tendencia SMA200":      regla_tendencia("SPY", "IEF", 200),
    "B · Momentum 5 ETFs":       regla_momentum(),
    "50% SPY + 50% B":           regla_spy_mas_b(0.5),
}

def resumen(nombre: str, r: dict) -> dict:
    m = bt.metricas(r["nav"])
    años = max((r["nav"].index[-1] - r["nav"].index[0]).days / 365.25, 1e-9)
    return {"Estrategia": nombre,
            "Capital final ($)": round(float(r["valor"].iloc[-1]), 0),
            "Aportado ($)": r["aportado"],
            "CAGR %": m["cagr_pct"], "Máx. caída %": m["max_drawdown_pct"],
            "Sharpe": m["sharpe"], "Volatilidad %": m["volatilidad_pct"],
            "Operaciones/año": round(r["operaciones"] / años, 1),
            "Costes ($)": r["costes"]}

def retornos_anuales(navs: dict) -> pd.DataFrame:
    """Retorno de cada año calendario (el primero, desde la fecha de inicio)."""
    columnas = {}
    for nombre, nav in navs.items():
        base = pd.concat([nav.iloc[:1], nav.resample("YE").last()])
        r = base.pct_change().dropna() * 100
        r.index = r.index.year
        columnas[nombre] = r[~r.index.duplicated(keep="last")]
    return pd.DataFrame(columnas).round(1)

def comparar(precios: pd.DataFrame, capital: float, aporte: float = 0.0,
             inicio=None, extra: dict = None) -> tuple:
    """Simula todas las estrategias. `extra` = {nombre: Serie de equity} ya calculada."""
    resultados = {n: simular(precios, r, capital, aporte, inicio=inicio)
                  for n, r in ESTRATEGIAS.items()}
    filas = [resumen(n, r) for n, r in resultados.items()]
    navs = {n: r["nav"] for n, r in resultados.items()}
    for nombre, eq in (extra or {}).items():
        eq = eq[eq.index >= resultados["SPY (comprar y mantener)"]["nav"].index[0]]
        m = bt.metricas(eq)
        filas.append({"Estrategia": nombre, "Capital final ($)": round(float(eq.iloc[-1]), 0),
                      "Aportado ($)": capital, "CAGR %": m["cagr_pct"],
                      "Máx. caída %": m["max_drawdown_pct"], "Sharpe": m["sharpe"],
                      "Volatilidad %": m["volatilidad_pct"],
                      "Operaciones/año": None, "Costes ($)": None})
        navs[nombre] = eq / eq.iloc[0]
    return pd.DataFrame(filas), retornos_anuales(navs)

def descargar_etfs(tickers: list = None, periodo: str = "max") -> pd.DataFrame:
    tickers = tickers or UNIVERSO_MOMENTUM
    series = {}
    for t in tickers:
        df = qc._normalizar_columnas(
            qc.yf.download(t, period=periodo, progress=False, auto_adjust=True))
        series[t] = df["Close"]
    return pd.DataFrame(series).dropna()

def main():
    ap = argparse.ArgumentParser(description="Comparar estrategias alternativas")
    ap.add_argument("--periodo", default="max")
    ap.add_argument("--capital", type=float, default=qc.PARAMETROS["CAPITAL_INICIAL"])
    ap.add_argument("--aporte", type=float, default=100.0)
    ap.add_argument("--salida", default="resultados")
    args = ap.parse_args()
    os.makedirs(args.salida, exist_ok=True)

    precios = descargar_etfs(periodo=args.periodo)
    inicio_largo = precios.index[0] + pd.DateOffset(days=380)   # 12 meses de historia
    txt = ["# Estrategias alternativas vs bot Quant",
           f"Capital inicial ${args.capital:,.0f} · spread ETFs {COSTE_PCT_ETF*100:.2f}% por lado · "
           "decisión mensual, ejecución al cierre del día hábil siguiente", ""]

    # 1. Historia larga (incluye 2008 si hay datos)
    tabla, anual = comparar(precios, args.capital, 0.0, inicio_largo)
    txt += [f"## 1. Historia larga: {inicio_largo.date()} → {precios.index[-1].date()} (sin aportes)",
            "", tabla.to_markdown(index=False), ""]

    # 2. Mismo periodo que el backtest del bot, incluyendo al bot
    datos, spy, vix = bt.descargar("10y")
    inicio_bot = spy.index[0] + pd.DateOffset(days=300)
    res_bot = bt.backtest(datos, spy, vix, capital=args.capital, inicio=inicio_bot)
    tabla_bot, anual_bot = comparar(precios, args.capital, 0.0, inicio_bot,
                                    extra={"Bot Quant v2.3": res_bot["equity"]})
    txt += [f"## 2. Periodo del bot: {inicio_bot.date()} → {precios.index[-1].date()} (sin aportes)",
            "", tabla_bot.to_markdown(index=False), "",
            "### Retorno por año calendario (%)", "", anual_bot.to_markdown(), ""]

    # 3. Con aporte mensual
    if args.aporte > 0:
        tabla_ap, _ = comparar(precios, args.capital, args.aporte, inicio_bot)
        txt += [f"## 3. Periodo del bot con aporte de ${args.aporte:,.0f} al mes",
                "", tabla_ap.to_markdown(index=False), ""]

    txt += ["### Retorno por año calendario, historia larga (%)", "", anual.to_markdown(), ""]
    texto = "\n".join(txt)
    with open(os.path.join(args.salida, "estrategias.md"), "w", encoding="utf-8") as f:
        f.write(texto)
    print(texto)


if __name__ == "__main__":
    main()
