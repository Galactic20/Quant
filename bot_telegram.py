# bot_telegram.py v2.2
# APERTURA (8:45 AM NY): Revisa gaps y variacion del Chandelier vs dia anterior.
# CIERRE   (3:55 PM NY): Escaner completo. Unico momento valido para COMPRAR.

import json
import os
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone
from quant_core import (
    cargar_posiciones, descargar_datos_globales, detectar_regimen,
    escanear_universo, calcular_indicadores, formatear_alerta,
    enviar_telegram, _normalizar_columnas,
)

# Archivo donde se guarda el Chandelier del dia anterior
CHANDELIER_FILE = "chandelier_prev.json"

def guardar_chandelier(valores: dict):
    with open(CHANDELIER_FILE, "w") as f:
        json.dump(valores, f, indent=2)

def cargar_chandelier_previo() -> dict:
    try:
        with open(CHANDELIER_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def detectar_modo():
    hora_utc = datetime.now(timezone.utc).hour
    # EDT (verano): apertura ~12:45 UTC, cierre ~19:55 UTC
    # EST (invierno): apertura ~13:45 UTC, cierre ~20:55 UTC
    # Umbral 16 cubre ambos horarios correctamente
    modo = "APERTURA" if hora_utc < 16 else "CIERRE"
    print("Hora UTC: " + datetime.now(timezone.utc).strftime("%H:%M") + " -> Modo: " + modo)
    return modo


def ejecutar_apertura():
    # Revisa gaps criticos y variacion del Chandelier vs dia anterior.
    print("MODO APERTURA - Revisando posiciones abiertas...")
    posiciones = cargar_posiciones()
    if not posiciones:
        enviar_telegram("APERTURA - Sin posiciones abiertas que monitorear.")
        return
    tickers_cartera = list(posiciones.keys())
    print("Posiciones activas: " + ", ".join(tickers_cartera))

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
    regimen_txt = regimen.replace("_", " ")   # evita error Markdown en Telegram
    chandelier_previo = cargar_chandelier_previo()
    chandelier_hoy    = {}
    alertas_apertura  = []
    resumen_cartera   = []

    for ticker, df in datos_cartera.items():
        try:
            last       = df.iloc[-1]
            precio     = float(last["Close"])
            rsi        = float(last["RSI"])
            chandelier = float(last["Chandelier_Exit"])
            sma200     = float(last["SMA_200"])
            cant       = posiciones.get(ticker, 0)
            estado     = "OK"
            alerta     = None

            # Guardar Chandelier de hoy para comparar manana
            chandelier_hoy[ticker] = round(chandelier, 2)

            # Comparativa Chandelier vs dia anterior
            ch_prev   = chandelier_previo.get(ticker)
            ch_cambio = ""
            if ch_prev is not None:
                diff = round(chandelier - ch_prev, 2)
                if diff > 0.05:
                    ch_cambio = " (+" + str(diff) + " subio - ajusta trailing stop)"
                elif diff < -0.05:
                    ch_cambio = " (" + str(diff) + " bajo)"

            # Deteccion de gaps criticos
            if precio < sma200:
                estado = "BAJO SMA200"
                alerta = ("GAP BAJISTA - VENTA URGENTE: " + ticker + "\n"
                    + "Apertura $" + str(round(precio,2)) + " | SMA200 $" + str(round(sma200,2)) + "\n"
                    + "Precio abrio bajo la SMA 200. Tendencia rota.\n"
                    + "Tienes " + str(cant) + " acciones. Evalua vender en apertura.")
            elif precio < chandelier:
                estado = "BAJO CHANDELIER"
                alerta = ("GAP - STOP DINAMICO: " + ticker + "\n"
                    + "Apertura $" + str(round(precio,2)) + " | Chandelier $" + str(round(chandelier,2)) + "\n"
                    + "Precio abrio bajo el soporte dinamico.\n"
                    + "Tienes " + str(cant) + " acciones. Evalua ejecutar el stop.")

            resumen_cartera.append(
                estado + " " + ticker
                + " | $" + str(round(precio,2))
                + " | RSI " + str(round(rsi,1))
                + " | Chandelier $" + str(round(chandelier,2)) + ch_cambio
            )
            if alerta:
                alertas_apertura.append(alerta)

        except Exception as e:
            print("Error en " + ticker + ": " + str(e))

    # Guardar Chandelier de hoy para la proxima apertura
    guardar_chandelier(chandelier_hoy)

    sep = "-" * 38
    encabezado = ("APERTURA DE MERCADO - Quant Bot\n"
        + "Regimen: " + regimen_txt + "\n"
        + "SPY $" + str(contexto.get("SPY","N/A")) + " | VIX " + str(contexto.get("VIX","N/A")) + "\n"
        + sep + "\nEstado de tu cartera:\n" + "\n".join(resumen_cartera) + "\n" + sep + "\n")
    mensaje = encabezado + ("\n".join(alertas_apertura) if alertas_apertura else "Sin gaps criticos. Cartera en orden.")
    enviar_telegram(mensaje)
    print("Apertura: " + str(len(alertas_apertura)) + " alerta(s) enviada(s).")


def ejecutar_cierre():
    # Escaner completo con datos definitivos del dia.
    # Unico momento valido para senales de COMPRA.
    print("MODO CIERRE - Escaner completo...")
    datos = descargar_datos_globales(periodo="3y")
    regimen, contexto = detectar_regimen()
    regimen_txt = regimen.replace("_", " ")   # evita error Markdown en Telegram
    print(contexto["emoji"] + " Regimen: " + regimen)
    print("   " + contexto["descripcion"])
    print("   Accion: " + contexto["accion"])
    db_hist = None
    try:
        db_hist = pd.read_csv("mi_universo_quant.csv")
        print("Base historica: " + str(len(db_hist)) + " registros.")
    except FileNotFoundError:
        print("Sin base historica - usando universo completo.")
    df_senales = escanear_universo(datos_globales=datos, db_historica=db_hist)
    alertas = []
    if not df_senales.empty:
        for _, fila in df_senales.iterrows():
            msg = formatear_alerta(fila.to_dict())
            if msg:
                alertas.append(msg)
    sep = "-" * 38
    encabezado = ("CIERRE DE MERCADO - Quant Bot v2.2\n"
        + contexto["emoji"] + " Regimen: " + regimen_txt + "\n"
        + "SPY $" + str(contexto.get("SPY","N/A"))
        + " | SMA50 $" + str(contexto.get("SPY_SMA50","N/A"))
        + " | VIX " + str(contexto.get("VIX","N/A")) + "\n"
        + contexto["accion"] + "\n" + sep + "\n\n")
    if alertas:
        bloque = encabezado
        for alerta in alertas:
            if len(bloque) + len(alerta) + 2 > 3900:
                enviar_telegram(bloque)
                bloque = ""
            bloque += alerta + "\n\n"
        if bloque.strip():
            enviar_telegram(bloque)
        print("Cierre: " + str(len(alertas)) + " alerta(s) enviada(s).")
    else:
        enviar_telegram(encabezado + "Sin senales de accion para hoy.\nEl sistema continua monitoreando el mercado.")
        print("Heartbeat de cierre enviado.")


if __name__ == "__main__":
    print("=" * 55)
    print("QUANT BOT v2.1 - INICIO")
    print(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    print("=" * 55)
    modo = detectar_modo()
    if modo == "APERTURA":
        ejecutar_apertura()
    else:
        ejecutar_cierre()
    print("Proceso finalizado.")
