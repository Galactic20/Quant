# -*- coding: utf-8 -*-
"""
CARTERA MODELO — 50% SPY + 50% estrategia B (momentum entre 5 ETFs)

El bot mantiene una cartera modelo en datos/cartera_modelo.json que se
actualiza sola (el workflow la guarda en el repo). Tu cartera virtual de
eToro debería replicarla: cada fin de mes el bot te dice exactamente qué
comprar y vender.

  - Último día hábil del mes: aporte mensual + rebalanceo a la asignación
    objetivo (50% SPY + 50% del ETF que elija B).
  - Viernes (o ejecución manual): resumen de la cartera frente a SPY.
  - Primera ejecución: crea la cartera con el capital inicial.

Los precios son los del cierre (el bot corre a las 3:55 PM NY). Ejecuta las
órdenes al cierre de ese día o en la apertura del siguiente.
"""

import json
import os
from datetime import date

import estrategias as es
import quant_core as qc
from calendario import es_ultimo_dia_habil_mes

MODELO_FILE = "datos/cartera_modelo.json"

CONFIG = {
    "PESO_SPY":        0.5,     # el resto va a la elección de la estrategia B
    "APORTE_MENSUAL":  100.0,   # USD que agregas cada fin de mes
    "CAPITAL_INICIAL": qc.PARAMETROS["CAPITAL_INICIAL"],
    "ORDEN_MINIMA":    10.0,    # USD; diferencias menores no se operan (mínimo eToro)
    "COSTE_PCT":       es.COSTE_PCT_ETF,
}


# ======================================================================
# ESTADO
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

def nuevo_modelo(fecha: date, spy: float, cfg: dict = CONFIG) -> dict:
    capital = cfg["CAPITAL_INICIAL"]
    return {
        "estrategia": f"{int(cfg['PESO_SPY'] * 100)}% SPY + {int(round((1 - cfg['PESO_SPY']) * 100))}% B",
        "inicio": fecha.isoformat(),
        "efectivo": capital,
        "unidades": {},
        "aportado": capital,
        "participaciones": capital,          # valor por participación inicial = 1
        "referencia_spy_unidades": capital / spy,
        "asignacion": {},
        "ultimo_rebalanceo": None,
        "ultimo_cierre_mes": None,   # "AAAA-MM" del último aporte + rebalanceo mensual
        "historial": [],
    }

def valor(modelo: dict, precios: dict) -> float:
    return modelo["efectivo"] + sum(u * precios[t] for t, u in modelo["unidades"].items())

def valor_referencia(modelo: dict, precios: dict) -> float:
    """SPY comprar y mantener con los mismos aportes (para comparar)."""
    return modelo["referencia_spy_unidades"] * precios["SPY"]


# ======================================================================
# OPERACIONES
# ======================================================================

def aportar(modelo: dict, monto: float, precios: dict):
    if monto <= 0:
        return
    nav = valor(modelo, precios) / modelo["participaciones"]
    modelo["efectivo"] += monto
    modelo["aportado"] += monto
    modelo["participaciones"] += monto / nav
    modelo["referencia_spy_unidades"] += monto / precios["SPY"]

def rebalancear(modelo: dict, objetivo: dict, precios: dict, fecha: date,
                cfg: dict = CONFIG) -> list:
    """Lleva la cartera a `objetivo` (pesos). Devuelve las órdenes, ventas primero."""
    total = valor(modelo, precios) * (1 - cfg["COSTE_PCT"])   # reserva para el spread
    tickers = set(modelo["unidades"]) | set(objetivo)
    ordenes = []
    for t in tickers:
        actual = modelo["unidades"].get(t, 0.0) * precios[t]
        dif = total * objetivo.get(t, 0.0) - actual
        if objetivo.get(t, 0.0) == 0 and actual > 0:
            dif = -actual                                   # cerrar del todo
        elif abs(dif) < cfg["ORDEN_MINIMA"]:
            continue
        ordenes.append({"ticker": t, "accion": "COMPRAR" if dif > 0 else "VENDER",
                        "usd": round(abs(dif), 2), "precio": round(precios[t], 2),
                        "unidades": round(abs(dif) / precios[t], 4)})
    ordenes.sort(key=lambda o: (o["accion"] != "VENDER", o["ticker"]))
    for o in ordenes:
        signo = 1 if o["accion"] == "COMPRAR" else -1
        unidades = signo * o["usd"] / precios[o["ticker"]]
        nuevas = modelo["unidades"].get(o["ticker"], 0.0) + unidades
        if abs(nuevas) * precios[o["ticker"]] < 0.01:
            modelo["unidades"].pop(o["ticker"], None)
        else:
            modelo["unidades"][o["ticker"]] = nuevas
        modelo["efectivo"] -= signo * o["usd"] + o["usd"] * cfg["COSTE_PCT"]
        modelo["historial"].append({"fecha": fecha.isoformat(), **o})
    modelo["asignacion"] = objetivo
    modelo["ultimo_rebalanceo"] = fecha.isoformat()
    return ordenes


# ======================================================================
# MENSAJES
# ======================================================================

def _usd(x: float) -> str:
    return f"${x:,.2f}"

def texto_senal(objetivo: dict, puntajes: dict) -> str:
    orden = sorted(puntajes, key=puntajes.get, reverse=True)
    lineas = ["Momentum (prom. 3/6/12 meses):"]
    for i, t in enumerate(orden):
        marca = " <- elegido por B" if i == 0 else ""
        lineas.append(f"  {t}: {puntajes[t] * 100:+.1f}%{marca}")
    asign = " + ".join(f"{w * 100:.0f}% {t}" for t, w in
                       sorted(objetivo.items(), key=lambda kv: -kv[1]))
    lineas.append(f"Asignacion objetivo: {asign}")
    return "\n".join(lineas)

def texto_estado(modelo: dict, precios: dict) -> str:
    v, ref = valor(modelo, precios), valor_referencia(modelo, precios)
    nav = v / modelo["participaciones"]
    lineas = [f"Valor cartera: {_usd(v)} (aportado {_usd(modelo['aportado'])})",
              f"Rendimiento: {(nav - 1) * 100:+.2f}% desde {modelo['inicio']}",
              f"SPY con los mismos aportes: {_usd(ref)} ({(v - ref):+,.2f} vs SPY)",
              "Posiciones:"]
    for t, u in sorted(modelo["unidades"].items()):
        lineas.append(f"  {t}: {u:.4f} u. = {_usd(u * precios[t])} ({u * precios[t] / v * 100:.0f}%)")
    if modelo["efectivo"] > 0.5:
        lineas.append(f"  Efectivo: {_usd(modelo['efectivo'])}")
    return "\n".join(lineas)

def texto_ordenes(ordenes: list) -> str:
    if not ordenes:
        return "Sin cambios: la cartera ya esta en su asignacion objetivo."
    lineas = ["Ordenes a ejecutar en eToro (vende primero):"]
    for o in ordenes:
        lineas.append(f"  {o['accion']} {o['ticker']}: {_usd(o['usd'])} "
                      f"(~{o['unidades']:.4f} u. a {_usd(o['precio'])})")
    return "\n".join(lineas)


# ======================================================================
# EJECUCIÓN DIARIA (la llama bot_telegram.py en el modo CIERRE)
# ======================================================================

def ejecutar(hoy: date, manual: bool = False, enviar=qc.enviar_telegram,
             precios_hist=None, cfg: dict = CONFIG, ruta: str = MODELO_FILE) -> str | None:
    """Decide qué hacer hoy, actualiza el modelo y envía el mensaje. Devuelve el texto."""
    hist = precios_hist if precios_hist is not None else es.descargar_etfs(periodo="2y")
    precios = {t: float(hist[t].iloc[-1]) for t in hist.columns}
    puntajes = es.puntajes_momentum(hist)
    objetivo = es.regla_spy_mas_b(cfg["PESO_SPY"])(hist)
    sep = "-" * 34
    modelo = cargar_modelo(ruta)

    if modelo is None:
        modelo = nuevo_modelo(hoy, precios["SPY"], cfg)
        ordenes = rebalancear(modelo, objetivo, precios, hoy, cfg)
        if es_ultimo_dia_habil_mes(hoy):
            # El capital inicial ya cuenta como el movimiento de este mes
            modelo["ultimo_cierre_mes"] = hoy.isoformat()[:7]
        guardar_modelo(modelo, ruta)
        texto = "\n".join([f"CARTERA {modelo['estrategia']} - INICIO", sep,
                           texto_senal(objetivo, puntajes), sep,
                           texto_ordenes(ordenes), sep, texto_estado(modelo, precios)])
    elif es_ultimo_dia_habil_mes(hoy) and modelo.get("ultimo_cierre_mes") != hoy.isoformat()[:7]:
        aportar(modelo, cfg["APORTE_MENSUAL"], precios)
        ordenes = rebalancear(modelo, objetivo, precios, hoy, cfg)
        modelo["ultimo_cierre_mes"] = hoy.isoformat()[:7]
        guardar_modelo(modelo, ruta)
        texto = "\n".join([f"REBALANCEO MENSUAL - {modelo['estrategia']}", sep,
                           f"1. Deposita tu aporte de {_usd(cfg['APORTE_MENSUAL'])}.",
                           "2. Ejecuta al cierre de hoy o en la apertura de manana:", "",
                           texto_ordenes(ordenes), sep, texto_senal(objetivo, puntajes), sep,
                           texto_estado(modelo, precios)])
    elif hoy.weekday() == 4 or manual:
        texto = "\n".join([f"RESUMEN SEMANAL - {modelo['estrategia']}", sep,
                           texto_estado(modelo, precios), sep,
                           "Senal actual (se aplica a fin de mes):",
                           texto_senal(objetivo, puntajes)])
    else:
        print("Cartera: nada que informar hoy.")
        return None

    enviar(texto)
    print(texto)
    return texto
