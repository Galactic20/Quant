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

    # Índices / Macro / Alternativos
    'QQQ': 'Índices (ETF)', 'VTI': 'Índices (ETF)', 'IEF': 'Bonos', 'GLD': 'Oro/Refugio',
    'BTC-USD': 'Crypto', 'ETH-USD': 'Crypto',

    # Big Tech / Mega Caps
    'AAPL': 'Big Tech', 'MSFT': 'Big Tech', 'META': 'Big Tech',
    'GOOG': 'Big Tech', 'GOOGL': 'Big Tech', 'AMZN': 'Big Tech',

    # Semiconductores
    'NVDA': 'Semiconductores', 'AMD': 'Semiconductores', 'TSM': 'Semiconductores',
    'AVGO': 'Semiconductores', 'ASML': 'Semiconductores', 'ON': 'Semiconductores',
    'INTC': 'Semiconductores', 'LRCX': 'Semiconductores', 'AMAT': 'Semiconductores',
    'KLAC': 'Semiconductores', 'MU': 'Semiconductores', 'SWKS': 'Semiconductores',
    'MCHP': 'Semiconductores', 'QRVO': 'Semiconductores', 'TER': 'Semiconductores',

    # Software / IT / Hardware
    'CRM': 'Software/SaaS', 'SNOW': 'Software/SaaS', 'ADBE': 'Software/SaaS',
    'ORCL': 'Software/SaaS', 'INTU': 'Software', 'ACN': 'Servicios IT',
    'CSCO': 'Networking', 'NTAP': 'Almacenamiento',
    'SNDK': 'Computer Hardware', 'DELL': 'Hardware', 'HPQ': 'Hardware', 'KD': 'Tech',

    # Internet / Plataformas / Ecommerce
    'MELI': 'E-commerce', 'SHOP': 'E-commerce', 'Etsy': 'E-commerce',
    'BABA': 'China Tech', 'UBER': 'Movilidad',

    # Ciberseguridad
    'NET': 'Ciberseguridad', 'PANW': 'Ciberseguridad',

    # Comunicación / Media / Entretenimiento
    'NFLX': 'Streaming', 'DIS': 'Medios', 'CMCSA': 'Telecom/Media',

    # Automotriz / Transporte / Aeroespacial
    'TSLA': 'Automotriz/Tech', 'BA': 'Aeroespacial', 'LUV': 'Aerolíneas', 'UPS': 'Logística',

    # Finanzas
    'JPM': 'Finanzas', 'MA': 'Finanzas', 'V': 'Finanzas',
    'BAC': 'Finanzas', 'WFC': 'Finanzas', 'C': 'Finanzas',
    'GS': 'Finanzas', 'MS': 'Finanzas', 'AIG': 'Finanzas', 'CME': 'Finanzas',

    # Salud / Farma / Biotech
    'JNJ': 'Salud', 'UNH': 'Salud', 'PFE': 'Salud', 'MRK': 'Salud',
    'LLY': 'Salud', 'ABT': 'Salud', 'BMY': 'Salud', 'AMGN': 'Biotech',

    # Energía / Renovables / Nuclear
    'CVX': 'Energía', 'OXY': 'Energía', 'XOM': 'Energía', 'COP': 'Energía',
    'SLB': 'Servicios petroleros', 'HAL': 'Servicios petroleros',
    'PSX': 'Refinación', 'VST': 'Energía', 'CEG': 'Energía',
    'ENPH': 'Energía', 'GUSH': 'Energía (Apal)', 'CCJ': 'Mineria-Uranio',

    # Utilities
    'NEE': 'Utilities', 'DUK': 'Utilities', 'SO': 'Utilities', 'AEE': 'Utilities',

    # Industriales / Construcción / Maquinaria
    'CAT': 'Industriales', 'DE': 'Maquinaria', 'GE': 'Industriales',
    'HON': 'Industriales', 'MMM': 'Industriales', 'FLR': 'Construcción',

    # Materiales / Químicos / Minería
    'DD': 'Materiales', 'FCX': 'Minería', 'ECL': 'Químicos', 'APD': 'Químicos',

    # Consumo Cíclico
    'HD': 'Consumo cíclico', 'NKE': 'Consumo cíclico', 'LOW': 'Retail',
    'SBUX': 'Restaurantes', 'MCD': 'Restaurantes', 'GIL': 'Consumo cíclico',

    # Consumo Defensivo
    'KO': 'Consumo defensivo', 'PEP': 'Consumo defensivo',
    'WMT': 'Retail defensivo', 'PG': 'Consumo defensivo',
    'COST': 'Retail defensivo', 'MO': 'Tabaco', 'ABEV': 'Consumo defensivo',

    # Real Estate / REITs
    'PLD': 'REIT Industrial', 'AMT': 'REIT Telecom',
    'EQIX': 'REIT Data Centers', 'SPG': 'REIT Retail', 'GRBK': 'Inmoviliario'

 


}
# --- TU CARTERA REAL ---
MIS_POSICIONES = { # Ejemplo: tienes 2 acciones
       
"AAPL": 0,
"ABEV": 0,
"ABT": 0,
"ACN": 0,
"ADBE": 0,
"AEE": 0,
"AIG": 0,
"AMAT": 0,
"AMD": 0.63,
"AMGN": 0,
"AMT": 0,
"AMZN": 0,
"APD": 0,
"ASML": 0,
"AVGO": 1.0,
"BA": 0,
"BABA": 2.09,
"BAC": 0,
"BMY": 0,
"BTC-USD": 0,
"C": 0,
"CAT": 0,
"CCJ": 0,
"CEG": 0,
"CME": 0,
"CMCSA": 0,
"COP": 0,
"COST": 0,
"CRM": 0,
"CSCO": 0,
"CVX": 0,
"DD": 0,
"DE": 0,
"DELL": 0,
"DIS": 0,
"DUK": 0,
"ECL": 0,
"ENPH": 0,
"EQIX": 0,
"ETH-USD": 0,
"Etsy": 0,
"FCX": 0,
"FLR": 0,
"GE": 0,
"GIL": 0,
"GLD": 0,
"GOOG": 0.86,
"GOOGL": 0,
"GRBK": 0,
"GS": 0,
"GUSH": 0,
"HAL": 0,
"HD": 0,
"HON": 0,
"HPQ": 0,
"IEF": 0,
"INTC": 0,
"INTU": 0,
"JNJ": 0,
"JPM": 1.27,
"KD": 0,
"KLAC": 0,
"KO": 0,
"LRCX": 0,
"LOW": 0,
"LUV": 0,
"MA": 0,
"MCD": 0,
"MCHP": 0,
"MELI": 0,
"META": 0,
"MMM": 0,
"MO": 0,
"MRK": 0,
"MS": 0,
"MSFT": 0,
"MU": 0,
"NEE": 0,
"NET": 0,
"NFLX": 0,
"NKE": 0,
"NTAP": 0,
"NVDA": 0,
"ON": 0,
"ORCL": 0,
"OXY": 0,
"PANW": 0,
"PEP": 0,
"PFE": 0,
"PG": 0,
"PLD": 0,
"PSX": 0,
"QQQ": 0.85,
"QRVO": 0,
"SBUX": 0,
"SHOP": 0,
"SLB": 0,
"SNOW": 0,
"SO": 0,
"SPG": 0,
"SNDK": 0,
"SWKS": 0,
"TER": 0,
"TSLA": 0.23019,
"TSM": 0,
"UBER": 0,
"UNH": 0,
"UPS": 0,
"V": 0,
"VST": 0,
"VTI": 0,
"WFC": 0,
"WMT": 0,
"XOM": 0,
"LLY": 0


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
  
