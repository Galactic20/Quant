import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import warnings
from datetime import datetime

warnings.simplefilter(action='ignore', category=FutureWarning)

# --- CONFIGURACIÓN DE NOTIFICACIONES ---
TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# --- CONFIGURACIÓN DE ESTRATEGIA ---
CAPITAL_INICIAL = 2380
RIESGO_USD = CAPITAL_INICIAL * 0.01 # $20 USD por operación

# --- DICCIONARIO COMPLETO (Asegúrate de incluir tus 35 activos) ---
SECTORES = {
    'QQQ': 'Índices (ETF)', 'VTI': 'Índices (ETF)', 'IEF': 'Bonos', 'GLD': 'Oro/Refugio',
    'BTC-USD': 'Crypto', 'ETH-USD': 'Crypto',
    'NVDA': 'Semiconductores', 'AMD': 'Semiconductores', 'TSM': 'Semiconductores',
    'AVGO': 'Semiconductores', 'ASML': 'Semiconductores', 'ON': 'Semiconductores',
    'INTC': 'Semiconductores', 'LRCX': 'Semiconductores',
    'AAPL': 'Big Tech', 'MSFT': 'Big Tech', 'META': 'Big Tech', 'GOOG': 'Big Tech',
    'AMZN': 'Big Tech', 'NFLX': 'Big Tech', 'CRM': 'Software/SaaS',
    'TSLA': 'Automotriz/Tech', 'BA': 'Aeroespacial', 'LUV': 'Aerolíneas',
    'JPM': 'Finanzas', 'MA': 'Finanzas', 'MELI': 'E-commerce', 'SHOP': 'E-commerce', 'Etsy': 'E-commerce',
    'NET': 'Ciberseguridad', 'PANW': 'Ciberseguridad', 'BABA': 'China Tech',
    'CVX': 'Energía', 'OXY': 'Energía', 'GUSH': 'Energía (Apal)',
    'ENPH': 'Energía', 'JNJ': 'Salud', 'GIL': 'Consumo cíclico', 'ABEV': 'Consumo defensivo', 'FLR': 'Construcción', 'GRBK': 'Inmoviliario', 'KD': 'Tech', 
    'CEG': 'Energía', 'CCJ': 'Mineria-Uranio', 'VST': 'Energía'
    # Agrega aquí el resto de tus tickers...
}

# --- TU CARTERA REAL ---
MIS_POSICIONES = { # Ejemplo: tienes 2 acciones
    "AAPL": 0,      
    "ABEV": 0,
    "AMD": 0.63,
    "AMZN": 0,
    "ASML": 0,
    "AVGO": 1.00,
    "BA": 0,
    "BABA": 0,
    "BTC-USD": 0,
    "CEG": 0,
    "CCJ": 0,
    "CRM": 0,
    "CVX": 0,
    "ENPH": 0,
    "Etsy": 0,
    "ETH-USD": 0,
    "GIL": 0,
    "GLD": 0,
    "GOOG": 0,
    "GUSH": 0,
    "IEF": 0,
    "INTC": 0,
    "JNJ": 0,
    "JPM": 0,
    "LRCX": 0,
    "LUV": 0,
    "MA": 0,
    "MELI": 0,
    "META": 0,
    "MSFT": 0,
    "NET": 0,
    "NFLX": 0,
    "NVDA": 0,
    "ON": 0,
    "OXY": 0,
    "PANW": 0,
    "QQQ": 0.85,
    "SHOP": 0,
    "TSLA": 0,
    "TSM": 0,
    "VTI": 0,
    "FLR": 0,
    "GRBK": 0,
    "KD": 0,
    "VST": 0

}
def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error enviando Telegram: {e}")

def analizar_mercado_sano():
    try:
        spy = yf.download("SPY", period="1y", progress=False)
        spy.columns = [c[0] if isinstance(c, tuple) else c for c in spy.columns]
        return bool(spy['Close'].iloc[-1] > spy['Close'].rolling(200).mean().iloc[-1])
    except: return True

def motor_quant_cloud(ticker, mercado_sano):
    try:
        df = yf.download(ticker, period="3y", progress=False)
        if df.empty or len(df) < 200: return None
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

        # Indicadores
        df['SMA_200'] = df['Close'].rolling(window=200).mean()
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        df['RSI'] = 100 - (100 / (1 + (gain / loss)))
        df['TR'] = pd.concat([(df['High']-df['Low']), abs(df['High']-df['Close'].shift()), abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1)
        df['ATR'] = df['TR'].rolling(window=14).mean()

        last = df.iloc[-1]
        p, rsi, sma, atr = float(last['Close']), float(last['RSI']), float(last['SMA_200']), float(last['ATR'])
        
        # SL/TP Dinámicos
        dist_sl = atr * 2.5
        sl = p - dist_sl
        tp = p + (atr * 6)
        
        # Señal
        senal = "🟢 COMPRA" if p > sma and rsi < 40 else "🔴 VENTA" if rsi > 70 else "⚪ BAJISTA" if p < sma else "👍 ALCISTA"
        tengo = MIS_POSICIONES.get(ticker, 0)
        
        # Filtro de Alertas Críticas
        if tengo > 0:
            if senal == "⚪ BAJISTA": return f"🚨 *VENTA URGENTE*: {ticker}\nPrecio: {round(p,2)}\n*Motivo*: Tendencia de largo plazo rota."
            if senal == "🔴 VENTA": return f"💰 *TOMAR GANANCIAS*: {ticker}\nPrecio: {round(p,2)}\n*Motivo*: Sobrecompra (RSI: {round(rsi,1)})"
        elif senal == "🟢 COMPRA" and mercado_sano:
            cant = round(RIESGO_USD / dist_sl, 2)
            return f"🛒 *NUEVA COMPRA*: {ticker}\nPrecio: {round(p,2)}\nCant. Sugerida: {cant}\nSL: {round(sl,2)} | TP: {round(tp,2)}"
        
        return None
    except: return None

# --- EJECUCIÓN PRINCIPAL ----
mercado_ok = analizar_mercado_sano()
alertas = []

for t in SECTORES.keys():
    res = motor_quant_cloud(t, mercado_ok)
    if res: alertas.append(res)

if alertas:
    mensaje_final = "🤖 *REPORTE QUANT DIARIO*\n\n" + "\n\n".join(alertas)
    enviar_telegram(mensaje_final)
else:
    # Mensaje de Heartbeat: Confirma que el bot funciona aunque no haya trades
    enviar_telegram("✅ *Sistema Quant Online*\nMercado analizado. Sin señales de acción para hoy.")
  
