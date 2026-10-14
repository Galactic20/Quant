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


# --- Propuestas de protección (estudio "proteccion") -------------------
EFECTIVO = "EFECTIVO"   # fondo de dinero (SGOV/BIL) simulado con la letra del Tesoro a 3 meses

def regla_momentum_absoluto(universo: list = None, refugio: str = EFECTIVO):
    """Propuesta 1: B, pero si incluso el mejor ETF tiene puntaje negativo → refugio."""
    universo = universo or UNIVERSO_MOMENTUM
    def regla(hist: pd.DataFrame) -> dict:
        puntaje = puntajes_momentum(hist, universo)
        if not puntaje:
            return {"IEF": 1.0}
        mejor = max(puntaje, key=puntaje.get)
        return {mejor: 1.0} if puntaje[mejor] > 0 else {refugio: 1.0}
    return regla

PROPUESTAS = {
    "Actual: 50% SPY + 50% B":
        regla_spy_mas_b(0.5),
    "P1: 50% SPY + 50% B con filtro":
        regla_mezcla([(0.5, regla_comprar_mantener("SPY")), (0.5, regla_momentum_absoluto())]),
    "P2: 50% A + 50% B":
        regla_mezcla([(0.5, regla_tendencia("SPY", "IEF", 200)), (0.5, regla_momentum())]),
    "P1+P2: 50% A + 50% B con filtro":
        regla_mezcla([(0.5, regla_tendencia("SPY", "IEF", 200)), (0.5, regla_momentum_absoluto())]),
    "SPY (referencia)":
        regla_comprar_mantener("SPY"),
}


# ======================================================================
# SIMULADOR DE CARTERA CON REBALANCEO MENSUAL
# ======================================================================

def simular(precios: pd.DataFrame, regla, capital: float = 2380,
            aporte_mensual: float = 0.0, coste_pct: float = COSTE_PCT_ETF,
            inicio=None, stop_pct: float = None, tp_pct: float = None) -> dict:
    """
    Devuelve {"valor": Serie en USD, "nav": valor por participación (base 1),
    "operaciones": n, "costes": USD, "aportado": USD, "stops": n, "tps": n}.

    stop_pct: stop loss móvil (desde el máximo de cierre desde la compra).
    tp_pct:   take profit desde el precio de compra.
    Si se tocan (al cierre), la posición pasa a efectivo hasta el siguiente
    rebalanceo mensual, que vuelve a comprarla si la regla la sigue eligiendo.
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
    n_stops, n_tps = 0, 0
    entrada: dict = {}
    pico: dict = {}
    forzar = False
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
        for t in list(entrada):
            if t not in unidades:
                entrada.pop(t); pico.pop(t, None)
        for t in unidades:
            if t not in entrada:
                entrada[t] = pico[t] = precios.at[fecha, t]

    def vender_todo(fecha, t):
        nonlocal cash, n_ops, costes
        bruto = unidades.pop(t) * precios.at[fecha, t]
        cash += bruto * (1 - coste_pct)
        costes += bruto * coste_pct
        n_ops += 1
        entrada.pop(t, None); pico.pop(t, None)

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
                if pendiente != objetivo or forzar:
                    objetivo = pendiente
                    rebalancear(fecha, objetivo)
                    forzar = False
                pendiente = None
        # Stop loss móvil / take profit al cierre
        for t in list(unidades):
            p = precios.at[fecha, t]
            pico[t] = max(pico.get(t, p), p)
            if stop_pct and p <= pico[t] * (1 - stop_pct):
                vender_todo(fecha, t); n_stops += 1; forzar = True
            elif tp_pct and p >= entrada.get(t, p) * (1 + tp_pct):
                vender_todo(fecha, t); n_tps += 1; forzar = True
        if fecha in fin_mes:
            pendiente = regla(precios.loc[:fecha])
        v = valor_cartera(fecha)
        valores.append(v)
        navs.append(v / participaciones)

    return {"valor": pd.Series(valores, index=fechas),
            "nav": pd.Series(navs, index=fechas),
            "operaciones": n_ops, "costes": round(costes, 2),
            "aportado": round(aportado, 2), "stops": n_stops, "tps": n_tps}


# ======================================================================
# COMPARACIÓN Y REPORTE
# ======================================================================

VARIANTES_STOP = {
    "Sin stop":            {},
    "Stop movil 10%":      {"stop_pct": 0.10},
    "Stop movil 15%":      {"stop_pct": 0.15},
    "Stop movil 20%":      {"stop_pct": 0.20},
    "Stop movil 25%":      {"stop_pct": 0.25},
    "Take profit 20%":     {"tp_pct": 0.20},
    "Stop 15% + TP 30%":   {"stop_pct": 0.15, "tp_pct": 0.30},
}

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

def serie_efectivo(indice: pd.DatetimeIndex, periodo: str = "max") -> pd.Series:
    """Índice de un fondo de dinero: acumula a diario el rendimiento de la letra
    del Tesoro a 13 semanas (^IRX), neto de 0.10% anual de gastos."""
    irx = qc._normalizar_columnas(
        qc.yf.download("^IRX", period=periodo, progress=False, auto_adjust=True))["Close"]
    tasa = (irx.reindex(indice).ffill().bfill() / 100 - 0.001).clip(lower=0)
    return 100 * (1 + tasa / 252).cumprod()

def ventanas_caida(nav: pd.Series, eventos: dict) -> dict:
    """Retorno de la estrategia en cada ventana {nombre: (inicio, fin)}."""
    out = {}
    for nombre, (a, b) in eventos.items():
        tramo = nav[(nav.index >= pd.Timestamp(a)) & (nav.index <= pd.Timestamp(b))]
        out[nombre] = round((tramo.iloc[-1] / tramo.iloc[0] - 1) * 100, 1) if len(tramo) > 1 else None
    return out

EVENTOS = {
    "Crisis 2008 (oct-07 a mar-09)":   ("2007-10-09", "2009-03-09"),
    "Rebote 2009 (mar-09 a dic-09)":   ("2009-03-09", "2009-12-31"),
    "COVID (feb-20 a mar-20)":         ("2020-02-19", "2020-03-23"),
    "Rebote COVID (mar-20 a dic-20)":  ("2020-03-23", "2020-12-31"),
    "2022 (acciones y bonos caen)":    ("2022-01-03", "2022-12-30"),
}

def reporte_proteccion(precios: pd.DataFrame, capital: float, aporte: float) -> str:
    """Backtest de las propuestas 1 y 2 frente a la cartera actual."""
    precios = precios.copy()
    precios[EFECTIVO] = serie_efectivo(precios.index)
    inicio_largo = precios.index[0] + pd.DateOffset(days=380)
    txt = ["## 5. Propuestas de protección",
           "- **P1**: la mitad B pasa a un fondo de dinero (SGOV/BIL; simulado con la letra "
           "del Tesoro a 3 meses menos 0.10% de gastos) si incluso el mejor de los 5 ETFs "
           "tiene momentum negativo.",
           "- **P2**: la mitad SPY sigue la regla de 200 días (estrategia A: SPY sobre su "
           "SMA200, si no IEF).",
           "- Parámetros clásicos, sin optimizar.", ""]
    diez = precios.index[-1] - pd.DateOffset(years=10)
    periodos = [("Historia larga, sin aportes", inicio_largo, 0.0),
                ("Últimos 10 años, sin aportes", diez, 0.0)]
    if aporte > 0:
        periodos.append((f"Últimos 10 años, aporte ${aporte:,.0f}/mes", diez, aporte))
    resultados = {}
    for etiqueta, ini, ap in periodos:
        res = {n: simular(precios, r, capital, ap, inicio=ini) for n, r in PROPUESTAS.items()}
        if not resultados:
            resultados = res
        filas = [resumen(n, r) for n, r in res.items()]
        for f in filas:
            f["% meses protegido"] = round(_tiempo_refugio(precios, PROPUESTAS[f["Estrategia"]], ini))
        txt += [f"### {etiqueta}: {pd.Timestamp(ini).date()} → {precios.index[-1].date()}", "",
                pd.DataFrame(filas).to_markdown(index=False), ""]
    eventos = pd.DataFrame({n: ventanas_caida(r["nav"], EVENTOS) for n, r in resultados.items()})
    txt += ["### Caídas y rebotes concretos (retorno % en cada ventana, historia larga)", "",
            eventos.to_markdown(), "",
            "### Retorno por año calendario, historia larga (%)", "",
            retornos_anuales({n: r["nav"] for n, r in resultados.items()}).to_markdown(), ""]
    return "\n".join(txt)

def _tiempo_refugio(precios: pd.DataFrame, regla, inicio) -> float:
    """% de meses en que la regla pone algo en IEF o EFECTIVO por protección
    (excluye cuando B elige IEF por ser el más fuerte)."""
    fechas = precios.index[precios.index >= pd.Timestamp(inicio)]
    fin_mes = pd.Series(fechas, index=fechas).groupby(fechas.to_period("M")).max()
    base = regla_spy_mas_b(0.5)
    n = 0
    for f in fin_mes:
        hist = precios.loc[:f]
        a, b = regla(hist), base(hist)
        if a.get(EFECTIVO, 0) > 0 or a.get("IEF", 0) > b.get("IEF", 0) + 1e-9:
            n += 1
    return 100 * n / max(len(fin_mes), 1)

def main():
    ap = argparse.ArgumentParser(description="Comparar estrategias alternativas")
    ap.add_argument("--periodo", default="max")
    ap.add_argument("--capital", type=float, default=qc.PARAMETROS["CAPITAL_INICIAL"])
    ap.add_argument("--aporte", type=float, default=100.0)
    ap.add_argument("--salida", default="resultados")
    ap.add_argument("--solo-proteccion", action="store_true",
                    help="Solo la sección 5 (propuestas de protección)")
    args = ap.parse_args()
    os.makedirs(args.salida, exist_ok=True)

    precios = descargar_etfs(periodo=args.periodo)
    if args.solo_proteccion:
        texto = "\n".join(["# Propuestas de protección para la cartera 50/50",
                           f"Capital inicial ${args.capital:,.0f} · spread ETFs "
                           f"{COSTE_PCT_ETF*100:.2f}% por lado · decisión mensual, ejecución "
                           "al cierre del día hábil siguiente", "",
                           reporte_proteccion(precios, args.capital, args.aporte)])
        with open(os.path.join(args.salida, "proteccion.md"), "w", encoding="utf-8") as f:
            f.write(texto)
        print(texto)
        return
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

    # 4. Stop loss / take profit sobre la cartera del bot (50/50)
    filas = []
    for nombre, kw in VARIANTES_STOP.items():
        for etiqueta, ini in (("larga", inicio_largo), ("bot", inicio_bot)):
            r = simular(precios, regla_spy_mas_b(0.5), args.capital, 0.0, inicio=ini, **kw)
            m = bt.metricas(r["nav"])
            filas.append({"Variante": nombre, "Periodo": etiqueta, "CAGR %": m["cagr_pct"],
                          "Máx. caída %": m["max_drawdown_pct"], "Sharpe": m["sharpe"],
                          "Stops": r["stops"], "Take profits": r["tps"],
                          "Capital final ($)": round(float(r["valor"].iloc[-1]), 0)})
    tabla_sl = pd.DataFrame(filas).sort_values(["Periodo", "Variante"])
    txt += ["## 4. Stop loss / take profit en la cartera 50% SPY + 50% B (sin aportes)",
            "Stop móvil desde el máximo; take profit desde la compra; al tocarse se pasa a "
            "efectivo hasta el siguiente rebalanceo mensual.", "",
            tabla_sl.to_markdown(index=False), ""]

    txt += ["### Retorno por año calendario, historia larga (%)", "", anual.to_markdown(), ""]
    txt += [reporte_proteccion(precios, args.capital, args.aporte)]
    texto = "\n".join(txt)
    with open(os.path.join(args.salida, "estrategias.md"), "w", encoding="utf-8") as f:
        f.write(texto)
    print(texto)


if __name__ == "__main__":
    main()
