# -*- coding: utf-8 -*-
"""
bot_telegram.py v2.1
Dos ejecuciones diarias con lógica diferente:

  8:45 AM NY  → Modo APERTURA: solo revisa gaps en posiciones abiertas.
                Detecta si alguna posición abrió con un gap bajista que
                active el Chandelier Exit o rompa la SMA 200 durante
                la noche / pre-market.

  3:55 PM NY  → Modo CIERRE: escáner completo con datos definitivos del día.
                La vela está cerrada, el RSI y el volumen son comparables
                al backtest. Es el único momento válido para señales de compra.
"""

import pandas as pd
from datetime import datetime, timezone

from quant_core import (
    PARAMETROS, SECTORES,
    cargar_posiciones,
    descargar_datos_globales,
    detectar_regimen,
    escanear_universo,
    motor_quant,
    calcular_indicadores,
    formatear_alerta,
    enviar_telegram,
    _normalizar_columnas,
)


# ======================================================================
# DETECCIÓN DEL MODO DE EJECUCIÓN
# ======================================================================

def detectar_modo() -> str:
    """
    Determina si estamos en la ejecución de apertura o de cierre
    basándose en la hora UTC actual.

      < 15:00 UTC  →  APERTURA  (8:45 AM NY)
      >= 15:00 UTC →  CIERRE    (3:55 PM NY)

    Si se ejecuta manualmente (workflow_dispatch), corre en modo CIERRE.
    """
    hora_utc = datetime.now(timezone.utc).hour
    modo = "APERTURA" if hora_utc < 15 else "CIERRE"
    print(f"🕐 Hora UTC: {datetime.now(timezone.utc).strftime('%H:%M')} → Modo: {modo}")
    return modo


# ======================================================================
# MODO APERTURA — Solo revisar gaps en posiciones abiertas
# Descarga ligera: solo los tickers que tienes en cartera.
# No genera señales de compra, solo alertas de stop y venta urgente.
# ======================================================================

def ejecutar_apertura():
    """
    Revisa si alguna posición abierta sufrió un gap bajista durante
    la noche o el pre-market que active el Chandelier Exit o rompa
    la SMA 200. No genera señales de compra.
    """
    print("\n📋 MODO APERTURA — Revisando posiciones abiertas...")

    posiciones = cargar_posiciones()
    if not posiciones:
        print("   Sin posiciones abiertas. Nada que revisar.")
        enviar_telegram(
            "🌅 *APERTURA — Sistema Quant Online*\n"
            "Sin posiciones abiertas que monitorear."
        )
        return

    tickers_cartera = list(posiciones.keys())
    print(f"   Posiciones activas: {', '.join(tickers_cartera)}")

    # Descarga ligera: solo los tickers en cartera (mucho más rápido)
    import yfinance as yf
    datos_cartera = {}
    for ticker in tickers_cartera:
        try:
            df = yf.download(ticker, period="1y", progress=False, auto_adjust=True)
            df = _normalizar_columnas(df)
            if len(df) >= 200:
                datos_cartera[ticker] = calcular_indicadores(df)
        except Exception:
            continue

    regimen, contexto = detectar_regimen()

    alertas_apertura = []
    resumen_cartera  = []

    for ticker, df in datos_cartera.items():
        try:
            last       = df.iloc[-1]
            precio     = float(last['Close'])
            rsi        = float(last['RSI'])
            chandelier = float(last['Chandelier_Exit'])
            sma200     = float(last['SMA_200'])
            cant       = posiciones.get(ticker, 0)

            estado = "✅ OK"
            alerta = None

            # Gap bajista que rompe SMA 200 → venta urgente
            if precio < sma200:
                estado = "🚨 BAJO SMA200"
                alerta = (
                    f"🚨 *GAP BAJISTA — VENTA URGENTE*: `{ticker}`\n"
                    f"Apertura: `${round(precio,2)}` | SMA200: `${round(sma200,2)}`\n"
                    f"Precio abrió *bajo la SMA 200*. Tendencia rota.\n"
                    f"Tienes `{cant}` acciones. Evalúa vender en la apertura."
                )

            # Gap bajista que rompe Chandelier Exit → stop dinámico
            elif precio < chandelier:
                estado = "⚠️ BAJO CHANDELIER"
                alerta = (
                    f"💰 *GAP — STOP DINÁMICO*: `{ticker}`\n"
                    f"Apertura: `${round(precio,2)}` | Chandelier: `${round(chandelier,2)}`\n"
                    f"Precio abrió *bajo el soporte dinámico*.\n"
                    f"Tienes `{cant}` acciones. Evalúa ejecutar el stop."
                )

            resumen_cartera.append(
                f"  {estado} `{ticker}` — "
                f"${round(precio,2)} | RSI: {round(rsi,1)} | "
                f"Chandelier: ${round(chandelier,2)}"
            )

            if alerta:
                alertas_apertura.append(alerta)

        except Exception as e:
            print(f"   ⚠️ Error procesando {ticker}: {e}")

    # Construir mensaje de apertura
    encabezado = (
        f"🌅 *APERTURA DE MERCADO — Quant Bot*\n"
        f"{contexto['emoji']} Régimen: `{regimen}`\n"
        f"SPY: `${contexto.get('SPY','N/A')}` | "
        f"VIX: `{contexto.get('VIX','N/A')}`\n"
        f"{'─'*38}\n"
        f"*Estado de tu cartera:*\n"
        + "\n".join(resumen_cartera) + "\n"
        f"{'─'*38}\n"
    )

    if alertas_apertura:
        mensaje = encabezado + "\n".join(alertas_apertura)
    else:
        mensaje = encabezado + "✅ Sin gaps críticos. Cartera en orden."

    enviar_telegram(mensaje)
    print(f"📨 Apertura: {len(alertas_apertura)} alerta(s) enviada(s).")


# ======================================================================
# MODO CIERRE — Escáner completo con datos definitivos del día
# Única ejecución válida para señales de COMPRA.
# ======================================================================

def ejecutar_cierre():
    """
    Escáner completo al cierre del mercado.
    Los datos están completos: vela cerrada, RSI y volumen definitivos,
    comparables al backtest construido con precios de cierre.
    """
    print("\n📊 MODO CIERRE — Escáner completo...")

    # 1. Descarga masiva única (activos + ETFs de referencia)
    datos = descargar_datos_globales(periodo="3y")

    # 2. Régimen de mercado
    regimen, contexto = detectar_regimen()
    print(f"\n{contexto['emoji']} Régimen: {regimen}")
    print(f"   {contexto['descripcion']}")
    print(f"   Acción: {contexto['accion']}\n")

    # 3. Base histórica (si existe en el repo)
    db_hist = None
    try:
        db_hist = pd.read_csv("mi_universo_quant.csv")
        print(f"📂 Base histórica: {len(db_hist):,} registros.")
    except FileNotFoundError:
        print("⚠️  Sin base histórica — usando universo completo.")

    # 4. Escaneo con todas las mejoras
    df_senales = escanear_universo(
        datos_globales=datos,
        db_historica=db_hist,
    )

    # 5. Formatear alertas
    alertas = []
    if not df_senales.empty:
        for _, fila in df_senales.iterrows():
            msg = formatear_alerta(fila.to_dict())
            if msg:
                alertas.append(msg)

    encabezado = (
        f"📊 *CIERRE DE MERCADO — Quant Bot v2.1*\n"
        f"{contexto['emoji']} Régimen: `{regimen}`\n"
        f"SPY: `${contexto.get('SPY','N/A')}` | "
        f"SMA50: `${contexto.get('SPY_SMA50','N/A')}` | "
        f"VIX: `{contexto.get('VIX','N/A')}`\n"
        f"📌 {contexto['accion']}\n"
        f"{'─'*38}\n\n"
    )

    # 6. Enviar en bloques (Telegram tiene límite de 4096 chars)
    if alertas:
        bloque = encabezado
        for alerta in alertas:
            if len(bloque) + len(alerta) + 2 > 3900:
                enviar_telegram(bloque)
                bloque = ""
            bloque += alerta + "\n\n"
        if bloque.strip():
            enviar_telegram(bloque)
        print(f"📨 Cierre: {len(alertas)} alerta(s) enviada(s).")
    else:
        enviar_telegram(
            encabezado +
            "Sin señales de acción para hoy.\n"
            "El sistema continúa monitoreando el mercado."
        )
        print("✅ Heartbeat de cierre enviado.")


# ======================================================================
# EJECUCIÓN PRINCIPAL
# ======================================================================

if __name__ == "__main__":

    print("=" * 55)
    print("🤖 QUANT BOT v2.1 — INICIO")
    print(f"   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 55)

    modo = detectar_modo()

    if modo == "APERTURA":
        ejecutar_apertura()
    else:
        ejecutar_cierre()

    print("\n✅ Proceso finalizado.")
