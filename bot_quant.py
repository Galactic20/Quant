import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import warnings

# Limpieza de warnings para que el log de GitHub sea legible
warnings.simplefilter(action='ignore', category=FutureWarning)

# --- CONFIGURACIÓN DE SEGURIDAD (GitHub Secrets) ---
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
RIESGO_USD = 100 

def calcular_estrategia_final(ticker, riesgo_usd):
    try:
        # Descargamos 3 años para asegurar que la SMA 200 esté bien calculada
        df = yf.download(ticker, period="3y", auto_adjust=True, progress=False)
        if df.empty or len(df) < 200: return None

        # 1. SMA 200 (Tendencia de largo plazo)
        df['SMA_200'] = df['Close'].rolling(window=200).mean()
        
        # 2. RSI Wilder (Momentum)
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        df['RSI'] = 100 - (100 / (1 + (gain / loss)))

        # 3. ATR (Volatilidad para el Stop Loss)
        hl = df['High'] - df['Low']
        h_pc = abs(df['High'] - df['Close'].shift())
        l_pc = abs(df['Low'] - df['Close'].shift())
        df['TR'] = pd.concat([hl, h_pc, l_pc], axis=1).max(axis=1)
        df['ATR'] = df['TR'].rolling(window=14).mean()
        
        # Valores Actuales
        last = df.iloc[-1]
        precio = float(last['Close'].item())
        rsi = float(last['RSI'].item())
        sma = float(last['SMA_200'].item())
        atr = float(last['ATR'].item())
        max_14 = float(df['High'].rolling(window=14).max().iloc[-1])

        # --- LÓGICA DE STOP LOSS DINÁMICO ---
        # Si el precio cayó muy rápido (caso Apple), ajustamos el SL
        stop_teorico = max_14 - (atr * 3)
        if stop_teorico >= precio:
            stop_loss = precio - (atr * 1.5)
        else:
            stop_loss = stop_teorico

        # Gestión de Riesgo (Risk/Reward 1:2)
        distancia = precio - stop_loss
        tp = precio + (distancia * 2)
        unidades = riesgo_usd / distancia if distancia > 0 else 0

        # Determinación de Señal
        senal = "ESPERAR"
        if precio > sma:
            if rsi < 40: senal = "🟢 COMPRA"
            elif rsi > 70: senal = "🔴 VENTA"
        else:
            senal = "⚪ TEND. BAJISTA"

        return {
            "Ticker": ticker.replace("-USD", ""),
            "Precio": round(precio, 2),
            "RSI": round(rsi, 1),
            "SL": round(stop_loss, 2),
            "TP": round(tp, 2),
            "Cant": round(unidades, 2) if unidades > 0 else 0,
            "SEÑAL": senal
        }
    except Exception as e:
        print(f"Error analizando {ticker}: {e}")
        return None

def enviar_telegram(mensaje):
    if not TOKEN or not CHAT_ID:
        print("Error: No se configuraron el TOKEN o CHAT_ID en los Secrets.")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

def ejecutar_bot():
    tickers = ['QQQ', 'NVDA', 'AAPL', 'MSFT', 'BTC-USD', 'ETH-USD', 'TSLA', 'AMD', 'VTI', 'ON', 'TSM', 'META', 'IEF', 'GUSH', 'GOOG', 'SHOP', 'ASML', 'GLD']
    print(f"🚀 Iniciando escaneo...")

    for t in tickers:
        data = calcular_estrategia_final(t, RIESGO_USD)
        
        if data and data['SEÑAL'] == "🟢 COMPRA":
            mensaje = (f"🎯 *¡SEÑAL DE COMPRA QUANT!*\n\n"
                       f"📈 *Activo:* {data['Ticker']}\n"
                       f"💰 *Precio:* ${data['Precio']}\n"
                       f"📊 *RSI:* {data['RSI']}\n\n"
                       f"🛡️ *Stop Loss:* {data['SL']}\n"
                       f"🚀 *Take Profit:* {data['TP']}\n"
                       f"⚖️ *Cantidad:* {data['Cant']} unidades")
            enviar_telegram(mensaje)
            print(f"✅ Alerta enviada para {t}")

if __name__ == "__main__":
    ejecutar_bot()
