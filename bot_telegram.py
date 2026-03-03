# -*- coding: utf-8 -*-
"""
bot_telegram.py v2.0
Orquesta el sistema de señales diarias usando quant_core v2.
Diseñado para correr en GitHub Actions una vez al día.
"""

from quant_core import (
    PARAMETROS,
    descargar_datos_globales,
    detectar_regimen, REGIMENES,
    escanear_universo,
    formatear_alerta,
    enviar_telegram,
)
import pandas as pd

if __name__ == "__main__":

    print("=" * 55)
    print("🤖 QUANT BOT v2.0 — INICIO")
    print("=" * 55)

    # ── 1. Descarga masiva única (activos + ETFs de referencia) ──
    datos = descargar_datos_globales(periodo="3y")

    # ── 2. Detectar régimen ──
    regimen, contexto = detectar_regimen()
    print(f"\n{contexto['emoji']} Régimen: {regimen}")
    print(f"   {contexto['descripcion']}")
    print(f"   Acción: {contexto['accion']}\n")

    # ── 3. Cargar base histórica (si existe en el repo) ──
    db_hist = None
    try:
        db_hist = pd.read_csv("mi_universo_quant.csv")
        print(f"📂 Base histórica cargada: {len(db_hist):,} registros.")
    except FileNotFoundError:
        print("⚠️  Sin base histórica — se usará universo completo.")

    # ── 4. Escanear con todas las mejoras ──
    df_señales = escanear_universo(
        datos_globales=datos,
        db_historica=db_hist,
    )

    # ── 5. Construir y enviar mensajes ──
    alertas = []
    if not df_señales.empty:
        for _, fila in df_señales.iterrows():
            msg = formatear_alerta(fila.to_dict())
            if msg:
                alertas.append(msg)

    encabezado = (
        f"🤖 *REPORTE QUANT v2.0*\n"
        f"{contexto['emoji']} Régimen: `{regimen}`\n"
        f"SPY: `${contexto.get('SPY','N/A')}` | "
        f"SMA50: `${contexto.get('SPY_SMA50','N/A')}` | "
        f"VIX: `{contexto.get('VIX','N/A')}`\n"
        f"📌 {contexto['accion']}\n"
        f"{'─'*38}\n\n"
    )

    if alertas:
        bloque = encabezado
        for alerta in alertas:
            if len(bloque) + len(alerta) + 2 > 3900:
                enviar_telegram(bloque)
                bloque = ""
            bloque += alerta + "\n\n"
        if bloque.strip():
            enviar_telegram(bloque)
        print(f"📨 {len(alertas)} alerta(s) enviada(s).")
    else:
        enviar_telegram(
            encabezado +
            "Sin señales de acción para hoy.\n"
            "El sistema sigue monitoreando tu cartera."
        )
        print("✅ Heartbeat enviado (sin señales).")

    print("✅ Proceso finalizado.")
