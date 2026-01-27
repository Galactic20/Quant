import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import warnings
from datetime import datetime

# Limpieza de warnings para logs legibles en GitHub
warnings.simplefilter(action='ignore', category=FutureWarning)

# --- CONFIGURACIÓN QUANT PROFESIONAL ---
# Se obtienen de GitHub Settings > Secrets > Actions
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Gestión de Riesgo Basada en Capital (Regla del 1%)
CAPITAL_TOTAL = 2000
PORCENTAJE_RIESGO = 0.01 
RIESGO_USD = CAPITAL_TOTAL * PORCENTAJE_RIESGO # Resultado: $20 USD

def calcular_estrategia_quant(ticker, riesgo_permitido):
    try:
        # Descarga de datos (3 años para SMA 200 estable)
        df = yf.download(ticker, period="3y", auto_adjust=True, progress=False)
        if df.empty or len(df) < 200: return None

        # 1. Media Móvil Simple (Tendencia)
        df['SMA_200'] = df['Close'].rolling(window=200).mean()

        # 2. RSI Wilder (Momentum/Sobreventa)
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        df['RSI'] = 100 - (100 / (1 + (gain / loss)))

        # 3. ATR (Volatilidad para Stop Loss)
        hl = df['High'] - df['Low']
        h_pc = abs(df['High'] - df['Close'].shift())
        l_pc = abs(df['Low'] - df['Close'].shift())
        df['TR'] = pd.concat([hl, h_pc, l_pc], axis=1).max(axis=1)
        df['ATR'] = df['TR'].rolling(window=14).mean()

        # Valores del cierre más reciente
        last = df.iloc[-1]
        precio = float(last['Close'].item())
        rsi = float(last['RSI'].item())
        sma = float(last['SMA_200'].item())
        atr = float(last['ATR'].item())
        max_14 = float(df['High'].rolling(window=14).max().iloc[-1])

        # --- LÓGICA DE STOP LOSS DINÁMICO ---
        # Si el precio cae muy rápido, el Chandelier Exit (max - 3*ATR) falla.
        # En ese caso, usamos un SL adaptativo de 1.5*ATR desde el precio actual.
        stop_teorico = max_14 - (atr * 3)
        if stop_teorico >= precio:
            stop_loss = precio - (atr * 1.5)
            tipo_sl = "Adaptativo (Volatilidad Alta)"
        else:
            stop_loss = stop_teorico
            tipo_sl = "Estándar (Chandelier)"

        # --- CÁLCULO DE TAMAÑO DE POSICIÓN ---
        distancia_riesgo = precio - stop_loss
        take_profit = precio + (distancia_riesgo * 2) # Ratio 1:2
        
        # Cantidad necesaria para perder exactamente el riesgo_permitido ($20)
        unidades = riesgo_permitido / distancia_riesgo if distancia_riesgo > 0 else 0
        inversion_total = unidades * precio

        # --- FILTROS DE SEÑAL ---
        senal = "ESPERAR"
        if precio > sma: # Filtro de tendencia alcista
            if rsi < 40: # Filtro de sobreventa
                senal = "🟢 COMPRA"
            elif rsi > 70:
                senal = "🔴 VENTA"
        else:
            senal = "⚪ TEND. BAJISTA"

        return {
            "Ticker": ticker.replace("-USD", ""),
            "Precio": round(precio, 2),
            "RSI": round(rsi, 1),
            "SL": round(stop_loss, 2),
            "TP": round(take_profit, 2),
            "Cant": round(unidades, 2),
            "Inversion": round(inversion_total, 2),
            "SEÑAL": senal
        }
    except Exception as e:
        print(f"Error en {ticker}: {e}")
        return None

def enviar_telegram(mensaje):
    if not TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

def ejecutar_escaneo():
    tickers = ['QQQ', 'NVDA', 'AAPL', 'MSFT', 'BTC-USD', 'ETH-USD', 'TSLA', 'AMD', 'VTI', 'ON', 'TSM', 'META', 'IEF', 'GUSH', 'GOOG', 'SHOP', 'ASML', 'GLD', 'BA', 'CVX', 'PANW', 'AMZN', 'OXY', 'JNJ', 'CRM', 'INTC', 'JPM', 'LUV', 'MELI', 'BABA', 'MA', 'NET', 'AVGO', 'NFLX', 'LRCX']
    
    print(f"🚀 Escaneo iniciado: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"💰 Riesgo por operación: ${RIESGO_USD} (1% de {CAPITAL_TOTAL})")
    
    resultados_lista = []

    for t in tickers:
        data = calcular_estrategia_quant(t, RIESGO_USD)
        if data:
            resultados_lista.append(data)
            if data['SEÑAL'] == "🟢 COMPRA":
                msg = (f"🎯 *SEÑAL DE COMPRA QUANT*\n\n"
                       f"📈 *Activo:* {data['Ticker']}\n"
                       f"💰 *Entrada:* ${data['Precio']}\n"
                       f"📊 *RSI:* {data['RSI']}\n\n"
                       f"🛡️ *Stop Loss:* {data['SL']}\n"
                       f"🚀 *Take Profit:* {data['TP']}\n"
                       f"⚖️ *Cantidad:* {data['Cant']} acciones\n"
                       f"💸 *Inversión:* ${data['Inversion']}\n\n"
                       f"_Pérdida máxima controlada: ${RIESGO_USD}_")
                enviar_telegram(msg)
    
    # Mostrar tabla resumen en el log de GitHub
    df_resumen = pd.DataFrame(resultados_lista)
    print("\n" + df_resumen.to_string(index=False))

if __name__ == "__main__":
    ejecutar_escaneo()
