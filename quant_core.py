# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════╗
║           QUANT CORE v2.0 — FUENTE ÚNICA DE VERDAD                 ║
║                                                                      ║
║  MEJORAS v2.0:                                                       ║
║   1. Detección de régimen de mercado (4 estados)                     ║
║   2. Filtro de fuerza relativa vs sector ETF                         ║
║   3. Universo adaptativo: top 30 activos por ADN histórico           ║
║   4. Tamaño de posición variable por calidad de señal                ║
║   5. Filtro de correlación: máx. 1 operación por sector              ║
║   6. Parámetros de entrada ajustados por régimen                     ║
╚══════════════════════════════════════════════════════════════════════╝

ARCHIVOS DEL SISTEMA:
    quant_core.py             ← Este archivo (fuente única de verdad)
    bot_telegram.py           ← Bot de GitHub Actions
    ultimate_quant_colab.py   ← Notebook de Google Colab
    posiciones.json           ← Posiciones abiertas (compartido)
    mi_universo_quant.csv     ← Base de datos del backtest
    mi_bitacora.json          ← Historial de trades
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


# ======================================================================
# 1. PARÁMETROS CENTRALES
#    Un solo lugar para cambiar cualquier valor de la estrategia.
# ======================================================================
PARAMETROS = {
    # ── Condiciones de Entrada (régimen TENDENCIA_ALCISTA) ──
    "RSI_ENTRADA":          40,    # RSI máximo para compra
    "RV_MIN":              1.5,    # Volumen relativo mínimo (Mano Fuerte)
    "SMA_PERIODO":         200,    # Período SMA de tendencia

    # ── Condiciones de Entrada (régimen RECUPERACION — más estricto) ──
    "RSI_ENTRADA_REC":      35,    # RSI más bajo exigido en recuperación
    "RV_MIN_REC":          2.0,    # Más volumen exigido en recuperación

    # ── Gestión de Riesgo ──
    "ATR_PERIODO":          14,
    "ATR_SL":              2.5,    # Multiplicador ATR → Stop Loss
    "ATR_TP":              5.5,    # Multiplicador ATR → Take Profit
    "CHANDELIER_MULT":       3,    # Multiplicador Chandelier Exit
    "CHANDELIER_PERIODO":   14,
    "RR_MINIMO":           2.0,    # R:R mínimo para abrir posición
    "RSI_SOBRECOMPRA":      75,

    # ── Tamaño de Posición Variable (% del capital en riesgo) ──
    "RIESGO_ALTA_CONV":   0.020,   # 2.0% — señal de máxima calidad
    "RIESGO_NORMAL":      0.010,   # 1.0% — señal estándar
    "RIESGO_DEBIL":       0.005,   # 0.5% — señal débil (prudencia)
    # Umbrales para clasificar calidad de señal:
    "WR_ALTA_CONV":        0.70,   # Win rate histórico ≥ 70%
    "WR_NORMAL":           0.60,   # Win rate histórico ≥ 60%
    "RV_ALTA_CONV":        2.0,    # RV ≥ 2.0 para alta convicción
    "RR_ALTA_CONV":        3.0,    # R:R ≥ 3.0 para alta convicción

    # ── Filtro de Mercado ──
    "VIX_PANICO":           35,    # VIX > 35 → régimen PÁNICO
    "VIX_ALERTA":           28,    # VIX > 28 → régimen RECUPERACION
    "RV_VOLUMEN":           20,    # Período para volumen relativo

    # ── Filtro de Fuerza Relativa ──
    "RS_PERIODO":           20,    # Días para calcular fuerza relativa
    "RS_MIN":             0.00,    # Activo debe superar retorno del sector

    # ── Universo Adaptativo ──
    "TOP_ACTIVOS":          30,    # Máximo de activos del universo élite
    "MIN_SEÑALES_ADN":       5,    # Mínimas señales históricas para ADN válido
    "WIN_RATE_MINIMO":     0.60,   # Win rate mínimo para universo élite

    # ── Filtro de Correlación (máx. posiciones por sector) ──
    "MAX_POR_SECTOR":        1,    # Solo 1 operación abierta/nueva por sector

    # ── Filtro de Stop Dinámico (margen para evitar falsas rupturas) ──
    # El precio debe cerrar al menos MARGEN_CHANDELIER × ATR por debajo
    # del Chandelier Exit para activar la alerta. Filtra ruido intradiario
    # en activos de alta volatilidad como AMD o TSLA.
    "MARGEN_CHANDELIER":  0.10,   # 10% del ATR como colchón mínimo

    # ── Breakout (alerta informativa, no señal de compra automática) ──
    # El precio debe romper el máximo de 52 semanas con volumen institucional
    # para generar una alerta de breakout. No reemplaza la señal de compra.
    "PERIODO_MAX_HIST":    252,    # Días para calcular máximo histórico (1 año)
    "RV_BREAKOUT":         2.0,    # Volumen relativo mínimo para confirmar breakout

    # ── Backtest ──
    "DIAS_REVISION":        10,

    # ── Capital ──
    "CAPITAL_INICIAL":    2380,
}
PARAMETROS["RIESGO_USD_BASE"] = (
    PARAMETROS["CAPITAL_INICIAL"] * PARAMETROS["RIESGO_NORMAL"]
)


# ======================================================================
# 2. UNIVERSO COMPLETO DE ACTIVOS
# ======================================================================
SECTORES = {
    'QQQ': 'Índices (ETF)', 'VTI': 'Índices (ETF)', 'IEF': 'Bonos',
    'GLD': 'Oro/Refugio', 'BTC-USD': 'Crypto', 'ETH-USD': 'Crypto',
    'AAPL': 'Big Tech', 'MSFT': 'Big Tech', 'META': 'Big Tech',
    'GOOG': 'Big Tech', 'GOOGL': 'Big Tech', 'AMZN': 'Big Tech',
    'NVDA': 'Semiconductores', 'AMD': 'Semiconductores', 'TSM': 'Semiconductores',
    'AVGO': 'Semiconductores', 'ASML': 'Semiconductores', 'ON': 'Semiconductores',
    'INTC': 'Semiconductores', 'LRCX': 'Semiconductores', 'AMAT': 'Semiconductores',
    'KLAC': 'Semiconductores', 'MU': 'Semiconductores', 'SWKS': 'Semiconductores',
    'MCHP': 'Semiconductores', 'QRVO': 'Semiconductores', 'TER': 'Semiconductores',
    'CRM': 'Software/SaaS', 'SNOW': 'Software/SaaS', 'ADBE': 'Software/SaaS',
    'ORCL': 'Software/SaaS', 'INTU': 'Software', 'ACN': 'Servicios IT',
    'CSCO': 'Networking', 'NTAP': 'Almacenamiento',
    'SNDK': 'Hardware', 'DELL': 'Hardware', 'HPQ': 'Hardware', 'KD': 'Tech',
    'MELI': 'E-commerce', 'SHOP': 'E-commerce', 'Etsy': 'E-commerce',
    'BABA': 'China Tech', 'UBER': 'Movilidad',
    'NET': 'Ciberseguridad', 'PANW': 'Ciberseguridad',
    'NFLX': 'Streaming', 'DIS': 'Medios', 'CMCSA': 'Telecom/Media',
    'TSLA': 'Automotriz/Tech', 'BA': 'Aeroespacial', 'LUV': 'Aerolíneas',
    'UPS': 'Logística',
    'JPM': 'Finanzas', 'MA': 'Finanzas', 'V': 'Finanzas',
    'BAC': 'Finanzas', 'WFC': 'Finanzas', 'C': 'Finanzas',
    'GS': 'Finanzas', 'MS': 'Finanzas', 'AIG': 'Finanzas', 'CME': 'Finanzas',
    'JNJ': 'Salud', 'UNH': 'Salud', 'PFE': 'Salud', 'MRK': 'Salud',
    'LLY': 'Salud', 'ABT': 'Salud', 'BMY': 'Salud', 'AMGN': 'Biotech',
    'CVX': 'Energía', 'OXY': 'Energía', 'XOM': 'Energía', 'COP': 'Energía',
    'XLE': 'Energía',              # ETF macro de seguimiento energía (nuevo)
    'SLB': 'Servicios petroleros', 'HAL': 'Servicios petroleros',
    'PSX': 'Refinación', 'VST': 'Energía', 'CEG': 'Energía',
    'ENPH': 'Energía', 'GUSH': 'Energía (Apal.)', 'CCJ': 'Minería-Uranio',
    'NEE': 'Utilities', 'DUK': 'Utilities', 'SO': 'Utilities', 'AEE': 'Utilities',
    'CAT': 'Industriales', 'DE': 'Maquinaria', 'GE': 'Industriales',
    'HON': 'Industriales', 'MMM': 'Industriales', 'FLR': 'Construcción',
    'DD': 'Materiales', 'FCX': 'Minería', 'ECL': 'Químicos', 'APD': 'Químicos',
    'HD': 'Consumo cíclico', 'NKE': 'Consumo cíclico', 'LOW': 'Retail',
    'SBUX': 'Restaurantes', 'MCD': 'Restaurantes', 'GIL': 'Consumo cíclico',
    'KO': 'Consumo defensivo', 'PEP': 'Consumo defensivo',
    'WMT': 'Retail defensivo', 'PG': 'Consumo defensivo',
    'COST': 'Retail defensivo', 'MO': 'Tabaco', 'ABEV': 'Consumo defensivo',
    'PLD': 'REIT Industrial', 'AMT': 'REIT Telecom',
    'EQIX': 'REIT Data Centers', 'SPG': 'REIT Retail', 'GRBK': 'Inmobiliario',
}

# ETFs representativos por sector para el filtro de fuerza relativa
SECTOR_ETFS = {
    'Big Tech':           'XLK',
    'Semiconductores':    'SOXX',
    'Software/SaaS':      'IGV',
    'Finanzas':           'XLF',
    'Salud':              'XLV',
    'Energía':            'XLE',
    'Consumo cíclico':    'XLY',
    'Consumo defensivo':  'XLP',
    'Industriales':       'XLI',
    'Utilities':          'XLU',
    'Inmobiliario':       'IYR',
    'Crypto':             'QQQ',   # Proxy macro (no hay ETF cripto universal fiable)
    'Índices (ETF)':      'SPY',
    'Bonos':              'AGG',
    'Oro/Refugio':        'GLD',
}


# ======================================================================
# 3. GESTIÓN DE POSICIONES (JSON compartido)
# ======================================================================
POSICIONES_FILE = "posiciones.json"

def cargar_posiciones() -> dict:
    try:
        with open(POSICIONES_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def guardar_posiciones(posiciones: dict):
    with open(POSICIONES_FILE, 'w') as f:
        json.dump(posiciones, f, indent=4)
    print(f"✅ posiciones.json actualizado ({len(posiciones)} posiciones).")

def abrir_posicion(ticker: str, cantidad: float):
    pos = cargar_posiciones()
    pos[ticker] = cantidad
    guardar_posiciones(pos)

def cerrar_posicion(ticker: str):
    pos = cargar_posiciones()
    if ticker in pos:
        del pos[ticker]
        guardar_posiciones(pos)
        print(f"🗑️  {ticker} eliminado de posiciones.json.")
    else:
        print(f"⚠️  {ticker} no estaba en posiciones.json.")


# ======================================================================
# 4. INDICADORES TÉCNICOS (funciones únicas y reutilizables)
# ======================================================================

def _normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

def calcular_rsi(series: pd.Series, periodo: int = 14) -> pd.Series:
    """RSI Exponencial de Wilder — idéntico en bot y backtest."""
    delta = series.diff()
    gain  = delta.where(delta > 0, 0).ewm(alpha=1/periodo, adjust=False).mean()
    loss  = (-delta.where(delta < 0, 0)).ewm(alpha=1/periodo, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calcular_atr(df: pd.DataFrame, periodo: int = 14) -> pd.Series:
    hl  = df['High'] - df['Low']
    hcp = abs(df['High'] - df['Close'].shift())
    lcp = abs(df['Low']  - df['Close'].shift())
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    return tr.rolling(periodo).mean()

def calcular_chandelier(df: pd.DataFrame, atr: pd.Series,
                        periodo: int = 14, mult: float = 3) -> pd.Series:
    return df['High'].rolling(periodo).max() - (mult * atr)

def calcular_rv(df: pd.DataFrame, periodo: int = 20) -> pd.Series:
    return df['Volume'] / df['Volume'].rolling(periodo).mean()

def calcular_indicadores(df: pd.DataFrame, p: dict = PARAMETROS) -> pd.DataFrame:
    """Calcula todos los indicadores sobre un DataFrame OHLCV."""
    df = _normalizar_columnas(df.copy())
    df['SMA_200']         = df['Close'].rolling(p["SMA_PERIODO"]).mean()
    df['SMA_50']          = df['Close'].rolling(50).mean()
    df['RSI']             = calcular_rsi(df['Close'], p["ATR_PERIODO"])
    df['ATR']             = calcular_atr(df, p["ATR_PERIODO"])
    df['RV']              = calcular_rv(df, p["RV_VOLUMEN"])
    df['Chandelier_Exit'] = calcular_chandelier(
        df, df['ATR'], p["CHANDELIER_PERIODO"], p["CHANDELIER_MULT"]
    )
    # Retorno forward (solo válido en backtest, NaN en tiempo real)
    df['Ret_10d'] = df['Close'].shift(-p["DIAS_REVISION"]) / df['Close'] - 1
    return df


# ======================================================================
# 5. MEJORA 1: DETECCIÓN DE RÉGIMEN DE MERCADO
#    Cuatro estados posibles que determinan qué hacer en cada momento.
# ======================================================================

REGIMENES = {
    "TENDENCIA_ALCISTA": {
        "emoji":       "🟢",
        "descripcion": "SPY sobre SMA200 + SMA50 > SMA200 + VIX bajo",
        "accion":      "Estrategia completa activa. Buscar compras.",
    },
    "RECUPERACION": {
        "emoji":       "🟡",
        "descripcion": "SPY sobre SMA200 pero SMA50 < SMA200 o VIX moderado",
        "accion":      "Solo señales de muy alta calidad (RV > 2.0, RSI < 35).",
    },
    "PANICO": {
        "emoji":       "🔴",
        "descripcion": "VIX > 35 — zona de pánico institucional",
        "accion":      "Sin compras. Solo GLD / IEF como refugio.",
    },
    "BAJISTA": {
        "emoji":       "⚫",
        "descripcion": "SPY bajo SMA200 — tendencia bajista confirmada",
        "accion":      "Sin compras largas. Preservar capital.",
    },
}

def detectar_regimen(p: dict = PARAMETROS) -> tuple[str, dict]:
    """
    Descarga SPY y VIX por separado (evita problemas de MultiIndex
    al descargar varios tickers juntos con yfinance), evalúa el estado
    del mercado y devuelve (nombre_regimen: str, contexto: dict).
    """
    try:
        # Descarga individual para evitar MultiIndex
        df_spy = yf.download("SPY",  period="1y", progress=False, auto_adjust=True)
        df_vix = yf.download("^VIX", period="1y", progress=False, auto_adjust=True)

        df_spy = _normalizar_columnas(df_spy)
        df_vix = _normalizar_columnas(df_vix)

        if df_spy.empty or df_vix.empty:
            raise ValueError("yfinance devolvió datos vacíos para SPY o ^VIX")

        spy_close = df_spy['Close'].dropna()
        vix_close = df_vix['Close'].dropna()

        spy       = float(spy_close.iloc[-1])
        sma200    = float(spy_close.rolling(200).mean().iloc[-1])
        sma50     = float(spy_close.rolling(50).mean().iloc[-1])
        vix       = float(vix_close.iloc[-1])

        spy_sobre_200  = spy   > sma200
        golden_cross   = sma50 > sma200
        vix_controlado = vix   < p["VIX_ALERTA"]
        vix_panico     = vix   > p["VIX_PANICO"]

        if vix_panico:
            regimen = "PANICO"
        elif not spy_sobre_200:
            regimen = "BAJISTA"
        elif spy_sobre_200 and golden_cross and vix_controlado:
            regimen = "TENDENCIA_ALCISTA"
        else:
            regimen = "RECUPERACION"

        contexto = {
            "regimen":    regimen,
            "SPY":        round(spy,   2),
            "SPY_SMA200": round(sma200,2),
            "SPY_SMA50":  round(sma50, 2),
            "VIX":        round(vix,   2),
            **REGIMENES[regimen],
        }
        return regimen, contexto

    except Exception as e:
        print(f"⚠️ Error detectando régimen: {e}")
        return "TENDENCIA_ALCISTA", {
            "regimen":     "TENDENCIA_ALCISTA",
            "descripcion": "Desconocido (error de datos)",
            "emoji":       "⚠️",
            "accion":      "Usar parámetros estándar.",
            "SPY":         "N/A",
            "SPY_SMA200":  "N/A",
            "SPY_SMA50":   "N/A",
            "VIX":         "N/A",
        }

# Alias para compatibilidad con código existente
def analizar_mercado_sano(p: dict = PARAMETROS) -> tuple[bool, dict]:
    regimen, ctx = detectar_regimen(p)
    mercado_ok = regimen in ("TENDENCIA_ALCISTA", "RECUPERACION")
    return mercado_ok, ctx


# ======================================================================
# 6. MEJORA 2: FILTRO DE FUERZA RELATIVA (Relative Strength)
#    El activo debe crecer más que su sector ETF en los últimos N días.
#    Elimina los rezagados del universo en entradas.
# ======================================================================

def calcular_fuerza_relativa(ticker: str, sector: str,
                              datos: dict,
                              p: dict = PARAMETROS) -> float | None:
    """
    Compara el retorno del activo vs su ETF de referencia en RS_PERIODO días.
    Devuelve la diferencia (positivo = activo más fuerte que su sector).
    """
    etf = SECTOR_ETFS.get(sector)
    if not etf or etf not in datos or ticker not in datos:
        return None

    try:
        periodo = p["RS_PERIODO"]
        ret_activo = float(
            datos[ticker]['Close'].pct_change(periodo).iloc[-1]
        )
        ret_sector = float(
            datos[etf]['Close'].pct_change(periodo).iloc[-1]
        )
        return round(ret_activo - ret_sector, 4)
    except Exception:
        return None

def pasa_filtro_rs(ticker: str, sector: str,
                   datos: dict, p: dict = PARAMETROS) -> bool:
    """True si el activo supera a su ETF de referencia."""
    rs = calcular_fuerza_relativa(ticker, sector, datos, p)
    if rs is None:
        return True   # Si no hay ETF de referencia, no bloqueamos
    return rs > p["RS_MIN"]


# ======================================================================
# 7. MEJORA 3: UNIVERSO ADAPTATIVO (top N por ADN histórico)
#    En vez de escanear 100 activos siempre, priorizamos los 30
#    con mejor historial comprobado. Reduce ruido y tiempo.
# ======================================================================

def obtener_universo_elite(db_historica: pd.DataFrame,
                            p: dict = PARAMETROS) -> list[str]:
    """
    Filtra la base de datos histórica y devuelve los tickers con
    mejor ADN: win rate ≥ WIN_RATE_MINIMO y al menos MIN_SEÑALES_ADN
    señales históricas. Limitado a los TOP_ACTIVOS mejores.

    Si no hay base de datos, devuelve el universo completo.
    """
    if db_historica is None or db_historica.empty:
        print("⚠️  Sin DB histórica — usando universo completo.")
        return list(SECTORES.keys())

    condicion = (
        (db_historica['RSI'] < p["RSI_ENTRADA"]) &
        (db_historica['RV']  > p["RV_MIN"])
    )
    señales = db_historica[condicion]

    adn = señales.groupby('Ticker').agg(
        win_rate=('Ret_10d', lambda x: (x > 0).mean()),
        n=('Ret_10d', 'count'),
        ret_prom=('Ret_10d', 'mean'),
        sharpe=('Ret_10d', lambda x: (
            x.mean() / x.std() if x.std() > 0 else 0
        )),
    ).reset_index()

    elite = (
        adn[
            (adn['win_rate'] >= p["WIN_RATE_MINIMO"]) &
            (adn['n']        >= p["MIN_SEÑALES_ADN"])
        ]
        .sort_values('sharpe', ascending=False)
        .head(p["TOP_ACTIVOS"])
    )

    tickers_elite = elite['Ticker'].tolist()
    print(f"🎯 Universo élite: {len(tickers_elite)} activos "
          f"(de {len(SECTORES)} totales).")
    return tickers_elite


# ======================================================================
# 8. MEJORA 4: TAMAÑO DE POSICIÓN VARIABLE
#    El riesgo en USD se ajusta según la calidad de la señal:
#    alta convicción → 2%, normal → 1%, débil → 0.5%
# ======================================================================

def clasificar_calidad_senal(rsi: float, rv: float,
                              rr_ratio: float,
                              win_rate_hist: float | None,
                              p: dict = PARAMETROS) -> str:
    """
    Devuelve 'ALTA', 'NORMAL' o 'DEBIL' según los indicadores.

    ALTA (2% de riesgo):
        - Win rate histórico ≥ 70% Y RV ≥ 2.0 Y R:R ≥ 3.0
    NORMAL (1% de riesgo):
        - Win rate ≥ 60% (o sin histórico) con RV ≥ 1.5
    DEBIL (0.5% de riesgo):
        - Cumple entrada mínima pero sin confirmaciones extra
    """
    wr = win_rate_hist or 0

    if (wr >= p["WR_ALTA_CONV"] and
            rv >= p["RV_ALTA_CONV"] and
            rr_ratio >= p["RR_ALTA_CONV"]):
        return "ALTA"
    # NORMAL requiere WR suficiente O volumen claramente por encima del mínimo
    # Se usa rv > RV_MIN (estricto) para evitar clasificar señales límite como NORMAL
    elif wr >= p["WR_NORMAL"] or rv > p["RV_MIN"]:
        return "NORMAL"
    else:
        return "DEBIL"

def calcular_riesgo_usd(calidad: str, capital: float,
               