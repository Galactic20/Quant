# -*- coding: utf-8 -*-
"""
CARTERA SATÉLITE — acciones elegidas por análisis fundamental

MODO VIRTUAL (aprendizaje): se opera solo en la cartera virtual de eToro. El
backtest con SimFin (2021-2025) rindió muy por debajo de SPY; se reevalúa
con los resultados reales de la cartera virtual.

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

Stop loss / take profit (se ponen en eToro al comprar):
  distancia = 2 x volatilidad esperada en 3 meses (de la volatilidad diaria
  del último año), acotada entre 15% y 35%. SL = precio - distancia;
  TP = precio + 2 x distancia (relación 2:1). En cada revisión trimestral el
  SL de las que se mantienen solo sube (stop móvil trimestral). El bot revisa
  cada cierre si se tocó algún nivel y te avisa; el efectivo liberado se
  reinvierte en la siguiente revisión trimestral.

Costes: acciones en eToro ~$1 al abrir y ~$1 al cerrar + spread.
Estado en datos/satelite_modelo.json.
"""

import json
import os
from datetime import date

import numpy as np
import pandas as pd

import fundamental as fu
import quant_core as qc
from calendario import es_ultimo_dia_habil_mes

MODELO_FILE = "datos/satelite_modelo.json"

CONFIG = {
    "CAPITAL":        1000.0,  # capital virtual del satélite
    "N_ACCIONES":     5,       # 5 x $200: con menos de $200 la comisión supera el 1%
    "MAX_POR_SECTOR": 1,       # con 5 acciones, una por sector para diversificar
    "BUFFER":         15,      # se mantiene mientras siga en el top 15
    "COMISION":       1.0,     # USD por lado (eToro)
    "COSTE_PCT":      0.0005,  # spread por lado
    "MESES_REVISION": (3, 6, 9, 12),
    "SL_MULT_VOL":    2.0,     # distancia del SL = 2 x volatilidad en 3 meses
    "SL_MIN":         0.15,
    "SL_MAX":         0.35,
    "TP_RATIO":       2.0,     # TP a 2 veces la distancia del SL
    "VOL_DEFECTO":    0.02,    # volatilidad diaria si no hay historial
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

def precios_actuales(tickers: list) -> tuple[dict, dict]:
    """(último cierre, volatilidad diaria del último año) de cada ticker."""
    precios, vols = {}, {}
    for t in tickers:
        try:
            df = qc._normalizar_columnas(
                qc.yf.download(t, period="1y", progress=False, auto_adjust=True))
            c = df["Close"].dropna()
            precios[t] = float(c.iloc[-1])
            vols[t] = float(np.log(c).diff().dropna().std())
        except Exception as e:
            print(f"Sin precio para {t}: {e}")
    return precios, vols

def niveles(precio: float, vol_diaria: float, cfg: dict = CONFIG) -> dict:
    """Stop loss y take profit según la volatilidad de la acción."""
    dist = cfg["SL_MULT_VOL"] * vol_diaria * np.sqrt(63)
    dist = float(min(max(dist, cfg["SL_MIN"]), cfg["SL_MAX"]))
    return {"stop_loss": round(precio * (1 - dist), 2),
            "take_profit": round(precio * (1 + cfg["TP_RATIO"] * dist), 2),
            "distancia_pct": round(dist * 100, 1)}

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

def _comprar(modelo, t, usd, precio, fecha, ranking, cfg, vol):
    neto = usd - cfg["COMISION"]
    u = neto * (1 - cfg["COSTE_PCT"]) / precio
    modelo["unidades"][t] = modelo["unidades"].get(t, 0.0) + u
    modelo["efectivo"] -= usd
    nv = niveles(precio, vol, cfg)
    modelo["tesis"][t] = {"fecha": fecha.isoformat(), "precio": round(precio, 2),
                          **nv, **fu.resumen_accion(ranking, t)}
    orden = {"fecha": fecha.isoformat(), "accion": "COMPRAR", "ticker": t,
             "usd": round(usd, 2), "precio": round(precio, 2), "unidades": round(u, 4), **nv}
    modelo["historial"].append(orden)
    return orden

def revisar(modelo: dict, ranking: pd.DataFrame, precios: dict, fecha: date,
            cfg: dict = CONFIG, vols: dict = None) -> list:
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
            ordenes.append(_comprar(modelo, t, monto, precios[t], fecha, ranking, cfg,
                                    (vols or {}).get(t, cfg["VOL_DEFECTO"])))
    return ordenes

def subir_stops(modelo: dict, precios: dict, vols: dict, cfg: dict = CONFIG) -> list:
    """Revisión trimestral: el SL de las que se mantienen solo sube."""
    ajustes = []
    for t in modelo["unidades"]:
        te = modelo["tesis"].get(t, {})
        nv = niveles(precios[t], vols.get(t, cfg["VOL_DEFECTO"]), cfg)
        if te.get("stop_loss") is None or nv["stop_loss"] > te["stop_loss"]:
            te["stop_loss"] = nv["stop_loss"]
            te["take_profit"] = max(nv["take_profit"], te.get("take_profit") or 0)
            ajustes.append(t)
    return ajustes

def vigilar_niveles(modelo: dict, precios: dict, vols: dict, fecha: date,
                    cfg: dict = CONFIG) -> tuple[list, list]:
    """
    Cada cierre: asigna niveles a posiciones que no los tengan y vende las
    que tocaron su SL o TP. Devuelve (ordenes, tickers_con_niveles_nuevos).
    """
    nuevos, ordenes = [], []
    for t in list(modelo["unidades"]):
        te = modelo["tesis"].setdefault(t, {})
        if te.get("stop_loss") is None:
            base = te.get("precio") or precios[t]
            te.update(niveles(base, vols.get(t, cfg["VOL_DEFECTO"]), cfg))
            nuevos.append(t)
        p = precios.get(t)
        if p is None:
            continue
        if p <= te["stop_loss"]:
            ordenes.append(_vender(modelo, t, p, fecha, f"STOP LOSS (${te['stop_loss']})", cfg))
        elif p >= te["take_profit"]:
            ordenes.append(_vender(modelo, t, p, fecha, f"TAKE PROFIT (${te['take_profit']})", cfg))
    return ordenes, nuevos


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
        if o["accion"] == "COMPRAR" and o.get("stop_loss"):
            extra = (f"\n      SL {_usd(o['stop_loss'])} (-{o['distancia_pct']}%) | "
                     f"TP {_usd(o['take_profit'])} (+{o['distancia_pct'] * CONFIG['TP_RATIO']:.1f}%)")
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

def texto_niveles(modelo: dict, tickers: list, titulo: str) -> str:
    lineas = [titulo]
    for t in tickers:
        te = modelo["tesis"].get(t, {})
        lineas.append(f"  {t}: SL {_usd(te['stop_loss'])} | TP {_usd(te['take_profit'])}")
    return "\n".join(lineas)


def ejecutar(hoy: date, manual: bool = False, enviar=qc.enviar_telegram,
             fundamentales: pd.DataFrame = None, precios: dict = None,
             resultados: dict = None, cfg: dict = CONFIG,
             ruta: str = MODELO_FILE, vols: dict = None) -> str | None:
    modelo = cargar_modelo(ruta)
    fin_mes = es_ultimo_dia_habil_mes(hoy)
    mes = f"{hoy.year}-{hoy.month:02d}"
    revision = fin_mes and hoy.month in cfg["MESES_REVISION"]
    sep = "-" * 34
    necesita_ranking = (modelo is None or (fin_mes and modelo.get("ultimo_cierre_mes") != mes))
    hay_posiciones = bool(modelo and modelo["unidades"])

    if not (necesita_ranking or hay_posiciones or hoy.weekday() == 4 or manual):
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
        precios, vols_desc = precios_actuales(sorted(tickers))
        vols = {**vols_desc, **(vols or {})}
    vols = vols or {}
    if ranking is not None:   # precio de respaldo del propio filtro
        for t in ranking.index:
            if t not in precios and pd.notna(ranking.at[t, "currentPrice"]):
                precios[t] = float(ranking.at[t, "currentPrice"])

    # ── Vigilancia diaria de stop loss / take profit ──
    aviso = []
    if modelo is not None:
        disparadas, nuevos = vigilar_niveles(modelo, precios, vols, hoy, cfg)
        if disparadas:
            aviso += ["SATELITE VIRTUAL - NIVEL ALCANZADO (eToro debio cerrar la posicion)",
                      texto_ordenes(disparadas),
                      "El efectivo se reinvierte en la proxima revision trimestral.", sep]
        if nuevos:
            aviso += [texto_niveles(modelo, nuevos,
                                    "SATELITE VIRTUAL - Pon estos niveles en eToro (posiciones actuales):"), sep]
        if disparadas or nuevos:
            guardar_modelo(modelo, ruta)

    if modelo is None:
        modelo = {"inicio": hoy.isoformat(), "capital_inicial": cfg["CAPITAL"],
                  "efectivo": cfg["CAPITAL"], "unidades": {}, "tesis": {},
                  "referencia_spy_unidades": cfg["CAPITAL"] / precios["SPY"],
                  "ultimo_cierre_mes": mes if fin_mes else None, "historial": []}
        ordenes = revisar(modelo, ranking, precios, hoy, cfg, vols)
        guardar_modelo(modelo, ruta)
        partes = [f"SATELITE VIRTUAL (aprendizaje) - INICIO ({_usd(cfg['CAPITAL'])} virtuales)", sep,
                  texto_ordenes(ordenes), sep,
                  texto_tesis(modelo, list(modelo["unidades"])), sep,
                  fu.texto_ranking(ranking, 10, list(modelo["unidades"]))]
    elif ranking is not None and revision:
        ordenes = revisar(modelo, ranking, precios, hoy, cfg, vols)
        nuevas = [o["ticker"] for o in ordenes if o["accion"] == "COMPRAR"]
        ajustes = [t for t in subir_stops(modelo, precios, vols, cfg) if t not in nuevas]
        modelo["ultimo_cierre_mes"] = mes
        guardar_modelo(modelo, ruta)
        partes = ["SATELITE VIRTUAL - REVISION TRIMESTRAL", sep, texto_ordenes(ordenes)]
        if nuevas:
            partes += [sep, texto_tesis(modelo, nuevas)]
        if ajustes:
            partes += [sep, texto_niveles(modelo, ajustes, "Sube el stop loss en eToro:")]
        partes += [sep, texto_estado(modelo, precios), sep,
                   fu.texto_ranking(ranking, 10, list(modelo["unidades"]))]
    elif ranking is not None:
        modelo["ultimo_cierre_mes"] = mes
        guardar_modelo(modelo, ruta)
        fuera = [t for t in modelo["unidades"] if t not in list(ranking.index[:cfg["BUFFER"]])]
        partes = ["SATELITE VIRTUAL - REPORTE MENSUAL (sin operar; la revision es trimestral)", sep,
                  fu.texto_ranking(ranking, 10, list(modelo["unidades"]))]
        if fuera:
            partes += [sep, "Atencion: fuera del top " + str(cfg["BUFFER"]) + ": " + ", ".join(fuera)
                       + ". Se venderian en la proxima revision si siguen asi."]
        partes += [sep, texto_estado(modelo, precios)]
    elif hoy.weekday() == 4 or manual:
        fechas = resultados if resultados is not None else proximos_resultados(list(modelo["unidades"]))
        partes = ["SATELITE VIRTUAL - RESUMEN SEMANAL", sep, texto_estado(modelo, precios)]
        if modelo["unidades"]:
            partes += [sep, texto_niveles(modelo, sorted(modelo["unidades"]), "Niveles vigentes:")]
        if fechas:
            partes += [sep, "Proximos resultados trimestrales:"]
            partes += [f"  {t}: {f}" for t, f in sorted(fechas.items(), key=lambda kv: kv[1])]
    else:
        partes = []

    texto = "\n".join(aviso + partes).strip()
    if not texto:
        print("Satelite: nada que informar hoy.")
        return None
    enviar(texto)
    print(texto)
    return texto
