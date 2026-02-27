import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import warnings
import math
from datetime import datetime

warnings.simplefilter(action='ignore', category=FutureWarning)
pd.options.mode.chained_assignment = None

# --- CONFIGURACIÓN DE NOTIFICACIONES ---
TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# --- CONFIGURACIÓN DE ESTRATEGIA ---
CAPITAL_INICIAL = 2380
RIESGO_USD = CAPITAL_INICIAL * 0.01 # $23.80 USD de riesgo por operación

# --- DICCIONARIO COMPLETO ---
SECTORES = {
    'QQQ': 'Índices', 'VTI': 'Índices', 'IEF': 'Bonos', 'GLD': 'Oro', 'BTC-USD': 'Crypto', 'ETH-USD': 'Crypto',
    'AAPL': 'Tech', 'MSFT': 'Tech', 'META': 'Tech', 'GOOG': 'Tech', 'GOOGL': 'Tech', 'AMZN': 'Tech',
    'NVDA': 'Semi', 'AMD': 'Semi', 'TSM': 'Semi', 'AVGO': 'Semi', 'ASML': 'Semi', 'ON': 'Semi',
    'INTC': 'Semi', 'LRCX': 'Semi', 'AMAT': 'Semi', 'KLAC': 'Semi', 'MU': 'Semi', 'SWKS': 'Semi',
    'MCHP': 'Semi', 'QRVO': 'Semi', 'TER': 'Semi', 'CRM': 'SaaS', 'SNOW': 'SaaS', 'ADBE': 'SaaS',
    'ORCL': 'SaaS', 'INTU': 'Software', 'ACN': 'IT', 'CSCO': 'Net', 'NTAP': 'Storage',
    'SNDK': 'Hardware', 'DELL': 'Hardware', 'HPQ': 'Hardware', 'KD': 'Tech',
    'MELI': 'E-com', 'SHOP': 'E-com', 'Etsy': 'E-com', 'BABA': 'China', 'UBER': 'Movilidad',
    'NET': 'Ciber', 'PANW': 'Ciber', 'NFLX': 'Stream', 'DIS': 'Medios', 'CMCSA': 'Media',
    'TSLA': 'Auto', 'BA': 'Aero', 'LUV': 'Aero', 'UPS': 'Log', 'JPM': 'Fin', 'MA': 'Fin', 'V': 'Fin',
    'BAC': 'Fin', 'WFC': 'Fin', 'C': 'Fin', 'GS': 'Fin', 'MS': 'Fin', 'AIG': 'Fin', 'CME': 'Fin',
    'JNJ': 'Salud', 'UNH': 'Salud', 'PFE': 'Salud', 'MRK': 'Salud', 'LLY': 'Salud', 'ABT': 'Salud', 
    'BMY': 'Salud', 'AMGN': 'Bio', 'CVX': 'Energía', 'OXY': 'Energía', 'XOM': 'Energía', 'COP': 'Energía',
    'SLB': 'Petrol', 'HAL': 'Petrol', 'PSX': 'Refin', 'VST': 'Energía', 'CEG': 'Energía',
    'ENPH': 'Energía', 'GUSH': 'Energía', 'CCJ': 'Uranio', 'NEE': 'Util', 'DUK': 'Util', 'SO': 'Util', 
    'AEE': 'Util', 'CAT': 'Ind', 'DE': 'Maq', 'GE': 'Ind', 'HON': 'Ind', 'MMM': 'Ind', 'FLR': 'Const',
    'DD': 'Mat', 'FCX': 'Min', 'ECL': 'Quím', 'APD': 'Quím', 'HD': 'Cons', 'NKE': 'Cons', 'LOW': 'Ret',
    'SBUX': 'Rest', 'MCD': 'Rest', 'GIL': 'Cons', 'KO': 'Defensivo', 'PEP': 'Defensivo',
    'WMT': 'Ret', 'PG': 'Defensivo', 'COST': 'Ret', 'MO': 'Tabaco', 'ABEV': 'Defensivo',
    'PLD': 'REIT', 'AMT': 'REIT', 'EQIX': 'REIT', 'SPG': 'REIT', 'GRBK': 'Inm'
}

# --- TU CARTERA REAL ---
# (Recuerda actualizar esto si decides comprar BA o BAC hoy)
MIS_POSICIONES = {
    "AMD": 0.63, 
    "AVGO": 1.0, 
    "BA": 1.0, 
    "BAC": 5.0,
    "GOOG": 0.86,  
    "QQQ": 0.85, 
    "TSLA": 0.23019
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
        df_mercado = yf.download(["SPY", "^VIX"], period="1y", progress=False, auto_adjust=True)
        if isinstance(df_mercado.columns, pd.MultiIndex):
            df_mercado.columns = df_mercado.columns.get_level_values(0)
            
        spy_close = df_mercado['SPY']
        vix_close = df_mercado['^VIX']
        
        spy_alcista = spy_close.iloc[-1] > spy_close.rolling(200).mean().iloc[-1]
        vix_controlado = vix_close.iloc[-1] < 28 # Si el VIX > 28, bloqueamos compras
        return spy_alcista and vix_controlado
    except: 
        return True

# Descarga en bloque (1 año para calcular SMA 200 sin demoras)
print("Descargando datos globales para el Bot...")
tickers_lista = list(SECTORES.keys())
datos_globales = yf.download(tickers_lista, period="1y", progress=False, group_by="ticker", auto_adjust=True)

def motor_quant_cloud(ticker, mercado_sano):
    try:
        df = datos_globales[ticker].dropna() if len(tickers_lista) > 1 else datos_globales.dropna()
        if len(df) < 200: return None
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # 1. Indicadores Base
        df['SMA_200'] = df['Close'].rolling(window=200).mean()
        df['RV'] = df['Volume'] / df['Volume'].rolling(20).mean()
        
        # 2. RSI Exacto
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        df['RSI'] = 100 - (100 / (1 + (gain / loss)))
        
        # 3. ATR y Chandelier Exit (Stop Loss Dinámico)
        df['TR'] = pd.concat([(df['High']-df['Low']), abs(df['High']-df['Close'].shift()), abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1)
        df['ATR'] = df['TR'].rolling(window=14).mean()
        df['Max_High_14'] = df['High'].rolling(window=14).max()
        df['Chandelier_Exit'] = df['Max_High_14'] - (3 * df['ATR'])

        # Valores de Hoy
        last = df.iloc[-1]
        p = float(last['Close'])
        o = float(last['Open'])
        rsi = float(last['RSI'])
        sma = float(last['SMA_200'])
        atr = float(last['ATR'])
        vol_rel = float(last['RV'])
        sl_quant = float(last['Chandelier_Exit'])
        
        # Stop Loss y Take Profit inicial para nuevas compras
        dist_sl = atr * 2.5
        sl_inicial = p - dist_sl
        tp_inicial = p + (atr * 6)
        
        tengo = MIS_POSICIONES.get(ticker, 0)
        
        # --- LÓGICA DE SALIDA (Para acciones en tu cartera) ---
        if tengo > 0:
            if p < sma: 
                return f"🚨 *VENTA URGENTE*: {ticker}\nPrecio: ${round(p,2)}\n*Motivo*: Rompió SMA 200 (Tendencia bajista)."
            elif p < sl_quant:
                return f"💰 *VENTA POR STOP DINÁMICO*: {ticker}\nPrecio: ${round(p,2)}\n*Motivo*: Perdió soporte del Chandelier Exit (${round(sl_quant,2)})."
            elif rsi > 75:
                return f"⚠️ *ALERTA SOBRECOMPRA*: {ticker}\nPrecio: ${round(p,2)}\n*Acción*: Ajusta tu Stop Loss manualmente a ${round(sl_quant,2)}."
                
        # --- LÓGICA DE ENTRADA ESTRICTA (Solo si no la tienes) ---
        elif tengo == 0 and mercado_sano:
            if (rsi < 42) and (vol_rel > 1.2) and (p > o) and (p > sma) and (p > sl_quant):
                cant = math.floor(RIESGO_USD / dist_sl)
                if cant > 0:
                    riesgo_real = cant * dist_sl
                    return f"🛒 *NUEVA COMPRA ESTRICTA*: {ticker}\nPrecio: ${round(p,2)}\nCant. Sugerida: {cant} acciones\nRiesgo: ${round(riesgo_real,2)}\nSL: ${round(sl_inicial,2)} | TP: ${round(tp_inicial,2)}"
        
        return None
    except Exception as e:
        return None

# --- EJECUCIÓN PRINCIPAL ----
mercado_ok = analizar_mercado_sano()
alertas = []

for t in tickers_lista:
    res = motor_quant_cloud(t, mercado_ok)
    if res: alertas.append(res)

if alertas:
    mensaje_final = "🤖 *REPORTE QUANT DIARIO*\n\n" + "\n\n".join(alertas)
    enviar_telegram(mensaje_final)
else:
    estado = "Sano (VIX bajo y SPY Alcista)" if mercado_ok else "Volátil/Bajista (Precaución)"
    enviar_telegram(f"✅ *Sistema Quant Online*\nEstado del Mercado: {estado}\nSin señales de acción bajo filtros estrictos para hoy.")
