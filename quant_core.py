# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║              QUANT CORE — FUENTE ÚNICA DE VERDAD               ║
║   Módulo compartido entre el Notebook (Colab) y el Bot          ║
║   de Telegram. Garantiza que el backtest y las señales en       ║
║   vivo usen exactamente la misma lógica y parámetros.           ║
╚══════════════════════════════════════════════════════════════════╝

USO:
    En el Notebook (Colab):
        from quant_core import *

    En el Bot (GitHub Actions):
        from quant_core import (
            PARAMETROS, SECTORES, cargar_posiciones,
            analizar_mercado_sano, motor_quant, formatear_alerta
        )
"""

import yfinance as yf
import pandas as pd
import numpy as np
import json
import math
import os
import warnings
import requests
from datetime import datetime, timedelta
from tabulate import tabulate

warnings.simplefilter(action='ignore', category=FutureWarning)
pd.options.mode.chained_assignment = None


# ==================================================================
# 1. PARÁMETROS CENTRALES DE LA ESTRATEGIA
#    Cambia aquí y se actualiza en TODOS lados automáticamente.
# ==================================================================
PARAMETROS = {
    # --- Condiciones de Entrada ---
    "RSI_ENTRADA":        40,     # RSI máximo para señal de compra
    "RV_MIN":            1.5,     # Volumen Relativo mínimo (Mano Fuerte)
    "SMA_PERIODO":       200,     # Período de la media móvil de tendencia
    "VELA_VERDE":        True,    # Exigir que Close > Open en entrada

    # --- Gestión de Riesgo ---
    "ATR_PERIODO":        14,     # Período del ATR
    "ATR_SL":            2.5,     # Multiplicador ATR para Stop Loss inicial
    "ATR_TP":            5.5,     # Multiplicador ATR para Take Profit
    "CHANDELIER_MULT":     3,     # Multiplicador ATR para Chandelier Exit
    "CHANDELIER_PERIODO": 14,     # Períodoo de máximos para Chandelier
    "RR_MINIMO":         2.0,     # Ratio Riesgo/Recompensa mínimo para operar
    "RSI_SOBRECOMPRA":    75,     # RSI para alerta de toma de ganancias

    # --- Filtro de Mercado ---
    "VIX_MAX":            28,     # VIX máximo (sobre este nivel = pánico, no comprar)
    "RV_VOLUMEN":         20,     # Período para calcular volumen promedio

    # --- Backtest ---
    "DIAS_REVISION":      10,     # Horizonte de retorno forward (días)
    "MIN_SEÑALES":         3,     # Mínimo de señales para considerar ADN válido
    "WIN_RATE_MINIMO":   0.60,    # Win rate mínimo para mostrar en el escáner

    # --- Capital ---
    "CAPITAL_INICIAL":  2380,
    "RIESGO_PCT":       0.01,     # 1% del capital por operación
}

# Derivados (no tocar)
PARAMETROS["RIESGO_USD"] = PARAMETROS["CAPITAL_INICIAL"] * PARAMETROS["RIESGO_PCT"]


# ==================================================================
# 2. UNIVERSO DE ACTIVOS Y SECTORES
# ==================================================================
SECTORES = {
    # Índices / Macro / Alternativos
    'QQQ': 'Índices (ETF)', 'VTI': 'Índices (ETF)', 'IEF': 'Bonos',
    'GLD': 'Oro/Refugio', 'BTC-USD': 'Crypto', 'ETH-USD': 'Crypto',

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

    # Comunicación / Media
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
    'ENPH': 'Energía', 'GUSH': 'Energía (Apal.)', 'CCJ': 'Minería-Uranio',

    # Utilities
    'NEE': 'Utilities', 'DUK': 'Utilities', 'SO': 'Utilities', 'AEE': 'Utilities',

    # Industriales / Maquinaria
    'CAT': 'Industriales', 'DE': 'Maquinaria', 'GE': 'Industriales',
    'HON': 'Industriales', 'MMM': 'Industriales', 'FLR': 'Construcción',

    # Materiales / Minería
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
    'EQIX': 'REIT Data Centers', 'SPG': 'REIT Retail', 'GRBK': 'Inmobiliario',
}


# ==================================================================
# 3. GESTIÓN DE POSICIONES (archivo JSON compartido)
#    En vez de hardcodear el dict, ambos scripts leen y escriben
#    el mismo archivo posiciones.json.
# ==================================================================
POSICIONES_FILE = "posiciones.json"

def cargar_posiciones() -> dict:
    """
    Lee las posiciones abiertas desde posiciones.json.
    Si el archivo no existe, devuelve un dict vacío.
    """
    try:
        with open(POSICIONES_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def guardar_posiciones(posiciones: dict):
    """Escribe las posiciones al archivo JSON."""
    with open(POSICIONES_FILE, 'w') as f:
        json.dump(posiciones, f, indent=4)
    print(f"✅ posiciones.json actualizado: {len(posiciones)} posiciones activas.")

def abrir_posicion(ticker: str, cantidad: float):
    """Agrega o actualiza una posición abierta."""
    posiciones = cargar_posiciones()
    posiciones[ticker] = cantidad
    guardar_posiciones(posiciones)

def cerrar_posicion(ticker: str):
    """Elimina una posición del archivo (al vender)."""
    posiciones = cargar_posiciones()
    if ticker in posiciones:
        del posiciones[ticker]
        guardar_posiciones(posiciones)
        print(f"🗑️  Posición {ticker} eliminada de posiciones.json.")
    else:
        print(f"⚠️  {ticker} no encontrado en posiciones.json.")


# ==================================================================
# 4. INDICADORES TÉCNICOS — FUNCIONES ÚNICAS
#    Garantizan que el notebook y el bot calculen igual.
# ==================================================================

def _normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    """Aplana MultiIndex de columnas (fix para yfinance reciente)."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

def calcular_rsi(series: pd.Series, periodo: int = 14) -> pd.Series:
    """
    RSI Exponencial de Wilder.
    Usa EWM (alpha=1/periodo) para coincidir exactamente con
    la mayoría de plataformas de trading profesionales.
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0).ewm(alpha=1/periodo, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/periodo, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calcular_atr(df: pd.DataFrame, periodo: int = 14) -> pd.Series:
    """Average True Range estándar."""
    hl  = df['High'] - df['Low']
    hcp = abs(df['High'] - df['Close'].shift())
    lcp = abs(df['Low']  - df['Close'].shift())
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    return tr.rolling(periodo).mean()

def calcular_chandelier(df: pd.DataFrame, atr: pd.Series,
                        periodo: int = 14, mult: float = 3) -> pd.Series:
    """
    Chandelier Exit alcista.
    Nivel dinámico de soporte = máximo de 'periodo' días - mult*ATR.
    Si el precio cae por debajo, la tendencia alcista está rota.
    """
    max_high = df['High'].rolling(periodo).max()
    return max_high - (mult * atr)

def calcular_rv(df: pd.DataFrame, periodo: int = 20) -> pd.Series:
    """Volumen Relativo: vol de hoy / promedio de 'periodo' días."""
    return df['Volume'] / df['Volume'].rolling(periodo).mean()

def calcular_indicadores(df: pd.DataFrame, p: dict = PARAMETROS) -> pd.DataFrame:
    """
    Calcula TODOS los indicadores de la estrategia sobre un DataFrame
    de precios OHLCV. Devuelve el mismo DataFrame con columnas añadidas.
    """
    df = _normalizar_columnas(df.copy())

    df['SMA_200']         = df['Close'].rolling(p["SMA_PERIODO"]).mean()
    df['SMA_50']          = df['Close'].rolling(50).mean()
    df['RSI']             = calcular_rsi(df['Close'], p["ATR_PERIODO"])
    df['ATR']             = calcular_atr(df, p["ATR_PERIODO"])
    df['RV']              = calcular_rv(df, p["RV_VOLUMEN"])
    df['Chandelier_Exit'] = calcular_chandelier(
        df, df['ATR'], p["CHANDELIER_PERIODO"], p["CHANDELIER_MULT"]
    )
    # Retorno forward a 10 días (para backtest — no disponible en tiempo real)
    df['Ret_10d'] = df['Close'].shift(-p["DIAS_REVISION"]) / df['Close'] - 1

    return df


# ==================================================================
# 5. LÓGICA DE SEÑALES — FUNCIÓN ÚNICA Y COMPARTIDA
#    Esta es la "regla de oro" que debe ser IDÉNTICA en el backtest
#    y en el bot. Si quieres cambiar los criterios de entrada,
#    cámbialo solo aquí.
# ==================================================================

def es_senal_compra(row: pd.Series, p: dict = PARAMETROS) -> bool:
    """
    Condición de ENTRADA LARGA unificada.
    Recibe la última fila del DataFrame con indicadores ya calculados.

    Criterios:
      1. RSI en zona de sobreventa (< RSI_ENTRADA)
      2. Mano Fuerte confirmada (RV > RV_MIN)
      3. Vela de rechazo alcista (Close > Open)
      4. Tendencia alcista (precio sobre SMA 200)
      5. Precio sobre soporte dinámico Chandelier Exit
    """
    try:
        return (
            float(row['RSI'])             <  p["RSI_ENTRADA"]       and
            float(row['RV'])              >  p["RV_MIN"]            and
            float(row['Close'])           >  float(row['Open'])     and  # Vela verde
            float(row['Close'])           >  float(row['SMA_200'])  and  # Tendencia alcista
            float(row['Close'])           >  float(row['Chandelier_Exit'])  # Soporte dinámico
        )
    except (KeyError, TypeError, ValueError):
        return False

def es_senal_venta_urgente(row: pd.Series, p: dict = PARAMETROS) -> bool:
    """Precio rompe la SMA 200 → tendencia rota."""
    return float(row['Close']) < float(row['SMA_200'])

def es_senal_stop_dinamico(row: pd.Series, p: dict = PARAMETROS) -> bool:
    """Precio cae bajo el Chandelier Exit → stop dinámico activado."""
    return float(row['Close']) < float(row['Chandelier_Exit'])

def es_senal_sobrecompra(row: pd.Series, p: dict = PARAMETROS) -> bool:
    """RSI en zona de sobrecompra → ajustar stop manualmente."""
    return float(row['RSI']) > p["RSI_SOBRECOMPRA"]

def calcular_gestion_riesgo(precio: float, atr: float,
                             p: dict = PARAMETROS) -> dict:
    """
    Calcula Stop Loss, Take Profit, unidades y R:R ratio.
    Usa math.floor para fracciones seguras (no comprar más de lo que cabe).
    """
    dist_sl       = atr * p["ATR_SL"]
    stop_loss     = precio - dist_sl
    take_profit   = precio + (atr * p["ATR_TP"])
    unidades      = math.floor(p["RIESGO_USD"] / dist_sl) if dist_sl > 0 else 0
    riesgo_real   = unidades * dist_sl
    inversion     = unidades * precio
    rr_ratio      = (take_profit - precio) / dist_sl if dist_sl > 0 else 0

    return {
        "stop_loss":   round(stop_loss,   2),
        "take_profit": round(take_profit, 2),
        "unidades":    unidades,
        "riesgo_real": round(riesgo_real, 2),
        "inversion":   round(inversion,   2),
        "rr_ratio":    round(rr_ratio,    2),
    }


# ==================================================================
# 6. FILTRO DE MERCADO (con VIX)
# ==================================================================

def analizar_mercado_sano(p: dict = PARAMETROS) -> tuple[bool, dict]:
    """
    Evalúa la salud del mercado usando SPY (tendencia) y VIX (pánico).
    Devuelve (mercado_ok: bool, contexto: dict).
    """
    try:
        raw = yf.download(["SPY", "^VIX"], period="1y", progress=False, auto_adjust=True)
        raw = _normalizar_columnas(raw)

        spy_close = raw['Close']['SPY'].dropna()
        vix_close = raw['Close']['^VIX'].dropna()

        spy_actual    = float(spy_close.iloc[-1])
        spy_sma200    = float(spy_close.rolling(200).mean().iloc[-1])
        vix_actual    = float(vix_close.iloc[-1])

        spy_alcista    = spy_actual > spy_sma200
        vix_controlado = vix_actual < p["VIX_MAX"]
        mercado_ok     = spy_alcista and vix_controlado

        contexto = {
            "SPY":          round(spy_actual, 2),
            "SPY_SMA200":   round(spy_sma200, 2),
            "VIX":          round(vix_actual, 2),
            "SPY_alcista":  spy_alcista,
            "VIX_ok":       vix_controlado,
            "mercado_ok":   mercado_ok,
            "emoji":        "🟢" if mercado_ok else "🔴",
            "descripcion":  (
                "Sano (SPY alcista + VIX bajo)"
                if mercado_ok else
                "Riesgo (SPY bajista o VIX elevado — No comprar)"
            ),
        }
        return mercado_ok, contexto

    except Exception as e:
        print(f"⚠️ Error analizando mercado: {e}")
        return True, {"descripcion": "Desconocido (error de datos)", "emoji": "⚠️"}


# ==================================================================
# 7. MOTOR QUANT UNIFICADO
#    Recibe el DataFrame ya calculado (con indicadores) y devuelve
#    un diccionario estandarizado. Sin llamadas a yfinance aquí.
# ==================================================================

def motor_quant(ticker: str, df: pd.DataFrame,
                mercado_ok: bool,
                win_rate_hist: float = None,
                p: dict = PARAMETROS) -> dict | None:
    """
    Analiza un ticker con su DataFrame de precios+indicadores.
    Devuelve un dict con señal, niveles de riesgo y contexto,
    o None si no hay señal relevante.

    Parámetros:
        ticker       : Símbolo del activo
        df           : DataFrame con columnas calculadas por calcular_indicadores()
        mercado_ok   : Resultado de analizar_mercado_sano()
        win_rate_hist: Win rate histórico del backtest (opcional, enriquece la señal)
        p            : Diccionario de parámetros de la estrategia
    """
    try:
        if df.empty or len(df) < p["SMA_PERIODO"]:
            return None

        last      = df.iloc[-1]
        precio    = float(last['Close'])
        rsi       = float(last['RSI'])
        rv        = float(last['RV'])
        sma200    = float(last['SMA_200'])
        atr       = float(last['ATR'])
        chandelier = float(last['Chandelier_Exit'])

        posiciones   = cargar_posiciones()
        tengo        = posiciones.get(ticker, 0)
        en_cartera   = tengo > 0
        sector       = SECTORES.get(ticker, "Otros")

        riesgo = calcular_gestion_riesgo(precio, atr, p)

        # ── Señales de SALIDA (prioridad si ya estás en el activo) ──
        if en_cartera:
            if es_senal_venta_urgente(last, p):
                return {
                    "tipo":    "VENTA_URGENTE",
                    "ticker":  ticker,
                    "sector":  sector,
                    "precio":  round(precio, 2),
                    "rsi":     round(rsi, 1),
                    "rv":      round(rv, 2),
                    "motivo":  f"Precio (${round(precio,2)}) rompió SMA 200 (${round(sma200,2)}). Tendencia bajista confirmada.",
                    "accion":  "🚨 VENDER TODO",
                    "chandelier": round(chandelier, 2),
                    "unidades_en_cartera": tengo,
                }

            if es_senal_stop_dinamico(last, p):
                return {
                    "tipo":    "STOP_DINAMICO",
                    "ticker":  ticker,
                    "sector":  sector,
                    "precio":  round(precio, 2),
                    "rsi":     round(rsi, 1),
                    "rv":      round(rv, 2),
                    "motivo":  f"Precio bajo Chandelier Exit (${round(chandelier,2)}). Soporte dinámico perdido.",
                    "accion":  "💰 VENTA POR STOP",
                    "chandelier": round(chandelier, 2),
                    "unidades_en_cartera": tengo,
                }

            if es_senal_sobrecompra(last, p):
                return {
                    "tipo":    "SOBRECOMPRA",
                    "ticker":  ticker,
                    "sector":  sector,
                    "precio":  round(precio, 2),
                    "rsi":     round(rsi, 1),
                    "rv":      round(rv, 2),
                    "motivo":  f"RSI ({round(rsi,1)}) en zona de sobrecompra (>{p['RSI_SOBRECOMPRA']}).",
                    "accion":  f"⚠️ AJUSTAR STOP a ${round(chandelier,2)}",
                    "chandelier": round(chandelier, 2),
                    "unidades_en_cartera": tengo,
                }

            # Sin señal relevante para posición abierta
            return {
                "tipo":    "MANTENER",
                "ticker":  ticker,
                "sector":  sector,
                "precio":  round(precio, 2),
                "rsi":     round(rsi, 1),
                "rv":      round(rv, 2),
                "accion":  "💎 MANTENER",
                "chandelier": round(chandelier, 2),
                "unidades_en_cartera": tengo,
            }

        # ── Señal de ENTRADA (solo si no estás en el activo) ──
        if es_senal_compra(last, p) and mercado_ok:
            # Filtro adicional: R:R mínimo
            if riesgo["rr_ratio"] < p["RR_MINIMO"]:
                return None
            if riesgo["unidades"] == 0:
                return None

            return {
                "tipo":         "COMPRA",
                "ticker":       ticker,
                "sector":       sector,
                "precio":       round(precio, 2),
                "rsi":          round(rsi, 1),
                "rv":           round(rv, 2),
                "sma200":       round(sma200, 2),
                "chandelier":   round(chandelier, 2),
                "stop_loss":    riesgo["stop_loss"],
                "take_profit":  riesgo["take_profit"],
                "unidades":     riesgo["unidades"],
                "invers