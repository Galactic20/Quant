# bot_telegram.py v3.0
# Estrategia por defecto (ESTRATEGIA_BOT=cartera): cartera modelo 50% SPY +
# 50% estrategia B (momentum entre 5 ETFs). Ver cartera.py.
#   CIERRE (3:55 PM NY): rebalanceo el ultimo dia habil del mes y resumen
#                        los viernes (o en ejecucion manual).
#   APERTURA:            sin acciones en este modo.
# Con ESTRATEGIA_BOT=quant vuelve el escaner de swing trading v2.3:
#   APERTURA (8:45 AM NY): Revisa gaps pre-market y variacion del Chandelier.
#   CIERRE   (3:55 PM NY): Escaner completo. Unico momento valido para COMPRAR.
#
# El modo se decide asi (en orden):
#   1. MODO_MANUAL=apertura|cierre (input de workflow_dispatch)
#   2. CRON_PROGRAMADO (github.event.schedule): el workflow tiene crons para
#      horario de verano (EDT) y de invierno (EST); el que no corresponde a la
#      fecha actual se salta solo, asi no hay que editar el YAML en nov/mar.
#   3. Hora local de Nueva York (ejecucion local sin variables).

import json
import os
import sys
import pandas as pd
import yfinance as yf
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from calendario import (
    feriados_nyse, es_dia_habil_nyse, es_ultimo_dia_habil_mes,
)
from quant_core import (
    cargar_posiciones_detalle, descargar_datos_globales, detectar_regimen,
    escanear_universo, calcular_indicadores, formatear_alerta, formatear_pnl,
    enviar_telegram, _normalizar_columnas, _pnl,
)

VERSION = "v3.0"
ESTRATEGIA_BOT = os.getenv("ESTRATEGIA_BOT", "cartera").strip().lower()
NY = ZoneInfo("America/New_York")

# Archivo donde se guarda el Chandelier del dia anterior
CHANDELIER_FILE = "chandelier_prev.json"

# Hora UTC de cada cron del workflow -> (modo, es_horario_de_verano)
CRONES = {
    12: ("APERTURA", True),  13: ("APERTURA", False),
    19: ("CIERRE",   True),  20: ("CIERRE",   False),
}


def guardar_chandelier(valores: dict):
    with open(CHANDELIER_FILE, "w") as f:
        json.dump(valores, f, indent=2)

def cargar_chandelier_previo() -> dict:
    try:
        with open(CHANDELIER_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ======================================================================
# MODO DE EJECUCION
# ======================================================================

def resolver_modo(ahora_utc: datetime = None,
                  cron: str = None, manual: str = None) -> str | None:
    """Devuelve 'APERTURA', 'CIERRE' o None si esta ejecucion debe saltarse."""
    ahora_utc = ahora_utc or datetime.now(timezone.utc)
    cron      = (cron   if cron   is not None else os.getenv("CRON_PROGRAMADO", "")).strip()
    manual    = (manual if manual is not None else os.getenv("MODO_MANUAL", "")).strip().lower()
    ahora_ny  = ahora_utc.astimezone(NY)
    print("Hora UTC: " + ahora_utc.strftime("%H:%M")
          + " | Hora NY: " + ahora_ny.strftime("%Y-%m-%d %H:%M %Z"))

    if manual in ("apertura", "cierre"):
        return manual.upper()

    if cron:
        try:
            hora = int(cron.split()[1])
        except (IndexError, ValueError):
            hora = None
        if hora in CRONES:
            modo, cron_verano = CRONES[hora]
            es_verano = ahora_ny.utcoffset() == timedelta(hours=-4)
            if cron_verano != es_verano:
                print("Cron '" + cron + "' es del otro horario (EDT/EST). Se omite.")
                return None
            if not es_dia_habil_nyse(ahora_ny.date()):
                print("Mercado cerrado hoy (fin de semana o feriado NYSE). Se omite.")
                return None
            return modo

    return "APERTURA" if ahora_ny.hour < 12 else "CIERRE"


# ======================================================================
# APERTURA
# ======================================================================

def obtener_precio_premarket(ticker: str) -> float | None:
    """Ultimo precio pre-market de hoy (NY), o None si no hay datos de hoy."""
    try:
        df = yf.Ticker(ticker).history(period="1d", interval="1m", prepost=True)
        df = df.dropna(subset=["Close"])
        if df.empty:
            return None
        ultimo = df.index[-1]
        if ultimo.tz_convert(NY).date() != datetime.now(NY).date():
            return None
        return float(df["Close"].iloc[-1])
    except Exception as e:
        print("Sin pre-market para " + ticker + ": " + str(e))
        return None

def ejecutar_apertura():
    # Revisa gaps criticos y variacion del Chandelier vs dia anterior.
    print("MODO APERTURA - Revisando posiciones abiertas...")
    posiciones = cargar_posiciones_detalle()
    if not posiciones:
        enviar_telegram("APERTURA - Sin posiciones abiertas que monitorear.")
        return
    tickers_cartera = list(posiciones.keys())
    print("Posiciones activas: " + ", ".join(tickers_cartera))

    hoy_ny = datetime.now(NY).date()
    datos_cartera = {}
    sin_datos     = []
    for ticker in tickers_cartera:
        try:
            df = yf.download(ticker, period="1y", progress=False, auto_adjust=True)
            df = _normalizar_columnas(df).dropna()
            # Solo velas cerradas: descarta una posible vela parcial de hoy
            if len(df) and df.index[-1].date() >= hoy_ny:
                df = df.iloc[:-1]
            if len(df) >= 200:
                datos_cartera[ticker] = calcular_indicadores(df)
            else:
                sin_datos.append(ticker)
        except Exception as e:
            print("Error descargando " + ticker + ": " + str(e))
            sin_datos.append(ticker)

    regimen, contexto = detectar_regimen()
    regimen_txt = regimen.replace("_", " ")   # evita error Markdown en Telegram
    chandelier_previo = cargar_chandelier_previo()
    chandelier_hoy    = {}
    alertas_apertura  = []
    resumen_cartera   = []

    for ticker, df in datos_cartera.items():
        try:
            last       = df.iloc[-1]
            cierre_ant = float(last["Close"])
            rsi        = float(last["RSI"])
            chandelier = float(last["Chandelier_Exit"])
            sma200     = float(last["SMA_200"])
            info       = posiciones[ticker]
            cant       = info["unidades"]
            sl         = info.get("stop_loss")

            premarket = obtener_precio_premarket(ticker)
            precio    = premarket if premarket is not None else cierre_ant
            fuente    = "Pre-market" if premarket is not None else "Cierre anterior"
            gap_pct   = (precio / cierre_ant - 1) * 100
            estado    = "OK"
            alerta    = None

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

            detalle = (fuente + " $" + str(round(precio, 2))
                       + " (gap " + format(gap_pct, "+.2f") + "%)")

            # Deteccion de gaps criticos
            if sl and precio <= sl:
                estado = "BAJO STOP LOSS"
                alerta = ("GAP - STOP LOSS: " + ticker + "\n"
                    + detalle + " | Stop Loss $" + str(round(sl, 2)) + "\n"
                    + "El precio esta bajo tu Stop Loss fijo.\n"
                    + "Tienes " + format(cant, "g") + " acciones. Evalua vender en apertura.")
            elif precio < sma200:
                estado = "BAJO SMA200"
                alerta = ("GAP BAJISTA - VENTA URGENTE: " + ticker + "\n"
                    + detalle + " | SMA200 $" + str(round(sma200, 2)) + "\n"
                    + "Precio bajo la SMA 200. Tendencia rota.\n"
                    + "Tienes " + format(cant, "g") + " acciones. Evalua vender en apertura.")
            elif precio < chandelier:
                estado = "BAJO CHANDELIER"
                alerta = ("GAP - STOP DINAMICO: " + ticker + "\n"
                    + detalle + " | Chandelier $" + str(round(chandelier, 2)) + "\n"
                    + "Precio bajo el soporte dinamico.\n"
                    + "Tienes " + format(cant, "g") + " acciones. Evalua ejecutar el stop.")

            pnl = _pnl(precio, info)
            pnl_txt = ""
            if pnl:
                pnl_txt = (" | PnL " + format(pnl["pnl_pct"], "+.2f") + "% ($"
                           + format(pnl["pnl_usd"], "+.2f") + ")")

            resumen_cartera.append(
                estado + " " + ticker
                + " | " + detalle
                + " | RSI " + str(round(rsi, 1))
                + " | Chandelier $" + str(round(chandelier, 2)) + ch_cambio
                + pnl_txt
            )
            if alerta:
                alertas_apertura.append(alerta)

        except Exception as e:
            print("Error en " + ticker + ": " + str(e))
            sin_datos.append(ticker)

    for ticker in sin_datos:
        resumen_cartera.append("SIN DATOS " + ticker + " | revisalo manualmente")

    # Guardar Chandelier de hoy para la proxima apertura (sin perder los
    # valores de tickers que hoy no se pudieron descargar)
    guardar_chandelier({**{t: v for t, v in chandelier_previo.items()
                           if t in posiciones}, **chandelier_hoy})

    sep = "-" * 38
    encabezado = ("APERTURA DE MERCADO - Quant Bot " + VERSION + "\n"
        + "Regimen: " + regimen_txt + "\n"
        + "SPY $" + str(contexto.get("SPY", "N/A")) + " | VIX " + str(contexto.get("VIX", "N/A")) + "\n"
        + sep + "\nEstado de tu cartera:\n" + "\n".join(resumen_cartera) + "\n" + sep + "\n")
    mensaje = encabezado + ("\n\n".join(alertas_apertura) if alertas_apertura else "Sin gaps criticos. Cartera en orden.")
    enviar_telegram(mensaje)
    print("Apertura: " + str(len(alertas_apertura)) + " alerta(s) enviada(s).")


# ======================================================================
# CIERRE
# ======================================================================

def resumen_cartera_cierre(df_senales: pd.DataFrame, posiciones: dict) -> str:
    """Una linea por posicion abierta sin alerta (MANTENER) o sin datos."""
    lineas = []
    vistos = set()
    if not df_senales.empty:
        for _, fila in df_senales.iterrows():
            s = fila.to_dict()
            vistos.add(s.get("ticker"))
            if s.get("tipo") != "MANTENER":
                continue
            linea = ("💎 `" + s["ticker"] + "` $" + str(s["precio"])
                     + " | RSI " + str(s["rsi"])
                     + " | Chandelier $" + str(s["chandelier"]))
            pnl = formatear_pnl(s)
            if pnl:
                linea += "\n    " + pnl
            lineas.append(linea)
    for ticker in posiciones:
        if ticker not in vistos:
            lineas.append("❓ `" + ticker + "` sin datos, revisalo manualmente")
    if not lineas:
        return ""
    return "Cartera:\n" + "\n".join(lineas) + "\n" + "-" * 38 + "\n\n"

def ejecutar_cierre():
    # Escaner completo con datos definitivos del dia.
    # Unico momento valido para senales de COMPRA.
    print("MODO CIERRE - Escaner completo...")
    posiciones = cargar_posiciones_detalle()
    datos = descargar_datos_globales(periodo="3y")
    regimen, contexto = detectar_regimen()
    regimen_txt = regimen.replace("_", " ")   # evita error Markdown en Telegram
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
    encabezado = ("CIERRE DE MERCADO - Quant Bot " + VERSION + "\n"
        + contexto["emoji"] + " Regimen: " + regimen_txt + "\n"
        + "SPY $" + str(contexto.get("SPY", "N/A"))
        + " | SMA50 $" + str(contexto.get("SPY_SMA50", "N/A"))
        + " | VIX " + str(contexto.get("VIX", "N/A")) + "\n"
        + contexto["accion"] + "\n" + sep + "\n\n"
        + resumen_cartera_cierre(df_senales, posiciones))
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
    print("QUANT BOT " + VERSION + " - INICIO (estrategia: " + ESTRATEGIA_BOT + ")")
    print("=" * 55)
    modo = resolver_modo()
    if modo is None:
        print("Nada que hacer en esta ejecucion.")
        sys.exit(0)
    print("Modo: " + modo)
    if ESTRATEGIA_BOT == "quant":
        if modo == "APERTURA":
            ejecutar_apertura()
        else:
            ejecutar_cierre()
    else:
        import cartera
        manual = os.getenv("MODO_MANUAL", "").strip() != ""
        if modo == "CIERRE" or manual:
            cartera.ejecutar(datetime.now(NY).date(), manual=manual)
        else:
            print("Apertura: sin acciones en modo cartera.")
    print("Proceso finalizado.")
