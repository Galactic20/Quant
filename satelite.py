# -*- coding: utf-8 -*-
"""
CARTERA SATÉLITE — acciones elegidas por análisis fundamental

Complementa a la cartera principal (50% SPY + 50% B). Se mide contra SPY con
el mismo capital: si después de 12-18 meses no le gana, no vale la pena.

  - Primera ejecución: compra las N mejores del filtro fundamental, a partes
    iguales.
  - Último día hábil de marzo, junio, septiembre y diciembre: revisión
    trimestral. Vende las que salieron del top 15 o dejaron de cumplir los
    filtros y compra reemplazos. Las que siguen bien se mantienen, para no
    pagar comisiones de más.
  - Último día hábil de los demás meses: reporte del filtro (sin operar).
  - Viernes o ejecución manual: resumen frente a SPY y próximos resultados
    trimestrales de tus empresas.

Costes: acciones en eToro ~$1 al abrir y ~$1 al cerrar + spread.
Estado en datos/satelite_modelo.json.
"""

import json
import os
from datetime import date

import pandas as pd

import fundamental as fu
import quant_core as qc
from calendario import es_ultimo_dia_habil_mes

MODELO_FILE = "datos/satelite_modelo.json"

CONFIG = {
    "CAPITAL":        1000.0,  # capital virtual del satélite
    "N_ACCIONES":     5,       # 5 x $200: con menos de $200 la comisión supera el 1%
    "MAX_POR_SECTOR": 2,
    "BUFFER":         15,      # se mantiene mientras siga en el top 15
    "COMISION":       1.0,     # USD por lado (eToro)
    "COSTE_PCT":      0.0005,  # spread por lado
    "MESES_REVISION": (3, 6, 9, 12),
}


# ======================================================================
# ESTADO Y PRECIOS
# ======================================================================

def cargar_modelo(ruta: str = MODELO_FILE) -> dict | None:
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def guardar_modelo(modelo: dict, ruta: str = MODELO_FILE):
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(modelo, f, indent=2, ensure_ascii=False)

def precios_actuales(tickers: list) -> dict:
    """Último cierre de cada ticker (yfinance)."""
    precios = {}
    for t in tickers:
        try:
            df = qc._normalizar_columnas(
                qc.yf.download(t, period="5d", progress=False, auto_adjust=True))
            precios[t] = float(df["Close"].dropna().iloc[-1])
        except Exception as e:
            print(f"Sin precio para {t}: {e}")
    return precios

def proximos_resultados(tickers: list) -> dict:
    """Fecha del próximo reporte trimestral (si yfinance la tiene)."""
    fechas = {}
    for t in tickers:
        try:
            cal = qc.yf.Ticker(t).calendar
            fecha = (cal or {}).get("Earnings Date")
            if isinstance(fecha, list):
                fecha = fecha[0] if fecha else None
            if fecha:
                fechas[t] = str(fecha)[:10]
        except Exception:
            pass
    return fechas

def valor(modelo: dict, precios: dict) -> float:
    return modelo["efectivo"] + sum(u * precios[t] for t, u in modelo["unidades"].items())


# ======================================================================
# OPERACIONES
# ======================================================================

def _vender(modelo, t, precio, fecha, motivo, cfg):
    u = modelo["unidades"].pop(t)
    bruto = u * precio
    modelo["efectivo"] += bruto * (1 - cfg["COSTE_PCT"]) - cfg["COMISION"]
    compra = modelo["tesis"].pop(t, {})
    pnl = None
    if compra.get("precio"):
        pnl = round((precio / compra["precio"] - 1) * 100, 2)
    orden = {"fecha": fecha.isoformat(), "accion": "VENDER", "ticker": t,
             "usd": round(bruto, 2), "precio": round(precio, 2),
             "unidades": round(u, 4), "motivo": motivo, "pnl_pct": pnl}
    modelo["historial"].append(orden)
    return orden

def _comprar(modelo, t, usd, precio, fecha, ranking, cfg):
    neto = usd - cfg["COMISION"]
    u = neto * (1 - cfg["COSTE_PCT"]) / precio
    modelo["unidades"][t] = modelo["unidades"].get(t, 0.0) + u
    modelo["efectivo"] -= usd
    modelo["tesis"][t] = {"fecha": fecha.isoformat(), "precio": round(precio, 2),
                          **fu.resumen_accion(ranking, t)}
    orden = {"fecha": fecha.isoformat(), "accion": "COMPRAR", "ticker": t,
             "usd": round(usd, 2), "precio": round(precio, 2), "unidades": round(u, 4)}
    modelo["historial"].append(orden)
    return orden

def revisar(modelo: dict, ranking: pd.DataFrame, precios: dict, fecha: date,
            cfg: dict = CONFIG) -> list:
    """Revisión: vende las que ya no califican y compra reemplazos con el efectivo."""
    actuales = list(modelo["unidades"])
    elegidas = fu.seleccionar(ranking, cfg["N_ACCIONES"], cfg["MAX_POR_SECTOR"],
                              actuales, cfg["BUFFER"])
    descartadas = ranking.attrs.get("descartadas", {})
    ordenes = []
    for t in actuales:
        if t not in elegidas:
            motivo = (f"ya no cumple filtros ({descartadas[t]})" if t in descartadas
                      else f"salio del top {cfg['BUFFER']}")
            ordenes.append(_vender(modelo, t, precios[t], fecha, motivo, cfg))
    nuevas = [t for t in elegidas if t not in modelo["unidades"]]
    if nuevas:
        monto = modelo["efectivo"] / len(nuevas)
        for t in nuevas:
            ordenes.append(_comprar(modelo, t, monto, precios[t], fecha, ranking, cfg))
    return ordenes


# ======================================================================
# MENSAJES
# ======================================================================

def _usd(x):
    return f"${x:,.2f}"

def texto_ordenes(ordenes: list) -> str:
    if not ordenes:
        return "Sin cambios: tus acciones siguen calificando."
    lineas = ["Ordenes en eToro (vende primero):"]
    for o in ordenes:
        extra = ""
        if o["accion"] == "VENDER":
            extra = f" | {o['motivo']}"
            if o.get("pnl_pct") is not None:
                extra += f" | {o['pnl_pct']:+.1f}% desde la compra"
        lineas.append(f"  {o['accion']} {o['ticker']}: {_usd(o['usd'])} (~{o['unidades']:.4f} u.){extra}")
    return "\n".join(lineas)

def texto_tesis(modelo: dict, tickers: list) -> str:
    lineas = ["Por que (metricas al comprar):"]
    for t in tickers:
        m = modelo["tesis"].get(t)
        if m:
            lineas.append(f"  {t} ({m['sector']}): ROE {m['roe_pct']}% | margen op {m['margen_op_pct']}% | "
                          f"FCF yield {m['fcf_yield_pct']}% | P/E {m['pe']} | crec {m['crec_ingresos_pct']}%")
    return "\n".join(lineas)

def texto_estado(modelo: dict, precios: dict) -> str:
    v = valor(modelo, precios)
    ref = modelo["referencia_spy_unidades"] * precios["SPY"]
    lineas = [f"Valor satelite: {_usd(v)} (capital {_usd(modelo['capital_inicial'])}, "
              f"{(v / modelo['capital_inicial'] - 1) * 100:+.2f}% desde {modelo['inicio']})",
              f"SPY con el mismo capital: {_usd(ref)} ({v - ref:+,.2f} vs SPY)"]
    for t, u in sorted(modelo["unidades"].items()):
        compra = modelo["tesis"].get(t, {}).get("precio")
        var = f" ({(precios[t] / compra - 1) * 100:+.1f}%)" if compra else ""
        lineas.append(f"  {t}: {_usd(u * precios[t])}{var}")
    if modelo["efectivo"] > 0.5:
        lineas.append(f"  Efectivo: {_usd(modelo['efectivo'])}")
    return "\n".join(lineas)


# ======================================================================
# EJECUCIÓN (la llama bot_telegram.py en el modo CIERRE)
# ======================================================================

def ejecutar(hoy: date, manual: bool = False, enviar=qc.enviar_telegram,
             fundamentales: pd.DataFrame = None, precios: dict = None,
             resultados: dict = None, cfg: dict = CONFIG,
             ruta: str = MODELO_FILE) -> str | None:
    modelo = cargar_modelo(ruta)
    fin_mes = es_ultimo_dia_habil_mes(hoy)
    mes = f"{hoy.year}-{hoy.month:02d}"
    revision = fin_mes and hoy.month in cfg["MESES_REVISION"]
    sep = "-" * 34

    necesita_ranking = (modelo is None or (fin_mes and modelo.get("ultimo_cierre_mes") != mes))
    if not necesita_ranking and not (hoy.weekday() == 4 or manual):
        print("Satelite: nada que informar hoy.")
        return None

    ranking = None
    if necesita_ranking:
        datos = fundamentales if fundamentales is not None else fu.obtener_fundamentales()
        ranking = fu.puntuar(datos)
        if ranking.empty:
            print("Satelite: sin datos fundamentales suficientes.")
            return None

    tickers = set((modelo or {}).get("unidades", {})) | {"SPY"}
    if ranking is not None:
        tickers |= set(ranking.index[:cfg["BUFFER"]])
    if precios is None:
        precios = precios_actuales(sorted(tickers))
    if ranking is not None:   # precio de respaldo del propio filtro
        for t in ranking.index:
            if t not in precios and pd.notna(ranking.at[t, "currentPrice"]):
                precios[t] = float(ranking.at[t, "currentPrice"])

    if modelo is None:
        modelo = {"inicio": hoy.isoformat(), "capital_inicial": cfg["CAPITAL"],
                  "efectivo": cfg["CAPITAL"], "unidades": {}, "tesis": {},
                  "referencia_spy_unidades": cfg["CAPITAL"] / precios["SPY"],
                  "ultimo_cierre_mes": mes if fin_mes else None, "historial": []}
        ordenes = revisar(modelo, ranking, precios, hoy, cfg)
        guardar_modelo(modelo, ruta)
        texto = "\n".join([f"SATELITE FUNDAMENTAL - INICIO ({_usd(cfg['CAPITAL'])} virtuales)", sep,
                           texto_ordenes(ordenes), sep,
                           texto_tesis(modelo, list(modelo["unidades"])), sep,
                           fu.texto_ranking(ranking, 10, list(modelo["unidades"]))])
    elif ranking is not None and revision:
        ordenes = revisar(modelo, ranking, precios, hoy, cfg)
        modelo["ultimo_cierre_mes"] = mes
        guardar_modelo(modelo, ruta)
        nuevas = [o["ticker"] for o in ordenes if o["accion"] == "COMPRAR"]
        partes = [f"SATELITE - REVISION TRIMESTRAL", sep, texto_ordenes(ordenes)]
        if nuevas:
            partes += [sep, texto_tesis(modelo, nuevas)]
        partes += [sep, texto_estado(modelo, precios), sep,
                   fu.texto_ranking(ranking, 10, list(modelo["unidades"]))]
        texto = "\n".join(partes)
    elif ranking is not None:
        modelo["ultimo_cierre_mes"] = mes
        guardar_modelo(modelo, ruta)
        fuera = [t for t in modelo["unidades"] if t not in list(ranking.index[:cfg["BUFFER"]])]
        partes = ["SATELITE - REPORTE MENSUAL (sin operar; la revision es trimestral)", sep,
                  fu.texto_ranking(ranking, 10, list(modelo["unidades"]))]
        if fuera:
            partes += [sep, "Atencion: fuera del top " + str(cfg["BUFFER"]) + ": " + ", ".join(fuera)
                       + ". Se venderian en la proxima revision si siguen asi."]
        partes += [sep, texto_estado(modelo, precios)]
        texto = "\n".join(partes)
    else:
        fechas = resultados if resultados is not None else proximos_resultados(list(modelo["unidades"]))
        partes = ["SATELITE - RESUMEN SEMANAL", sep, texto_estado(modelo, precios)]
        if fechas:
            partes += [sep, "Proximos resultados trimestrales:"]
            partes += [f"  {t}: {f}" for t, f in sorted(fechas.items(), key=lambda kv: kv[1])]
        texto = "\n".join(partes)

    enviar(texto)
    print(texto)
    return texto
