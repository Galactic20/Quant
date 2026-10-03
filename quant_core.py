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
    quant_core.py                    ← Este archivo (fuente única de verdad)
    bot_telegram.py                  ← Bot de GitHub Actions
    backtest.py                      ← Backtest con las mismas reglas del bot
    notebooks/ultimate_quant_v3.ipynb← Colab (descarga este repo, sin copia propia)
    posiciones.json                  ← Posiciones abiertas (compartido)
    datos/operaciones_reales.json    ← Operaciones cerradas en eToro
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
    # v2.3 — ajuste calibrado con datos históricos (jun 2026):
    #   RSI_ENTRADA: 40 → 45  (captura pullbacks en mercados alcistas fuertes)
    #   RV_MIN:     1.5 → 1.2  (interés institucional moderado, no solo días extremos)
    "RSI_ENTRADA":          45,    # RSI máximo para compra (era 40)
    "RV_MIN":              1.2,    # Volumen relativo mínimo (era 1.5)
    "SMA_PERIODO":         200,    # Período SMA de tendencia

    # ── Condiciones de Entrada (régimen RECUPERACION — más estricto) ──
    "RSI_ENTRADA_REC":      38,    # RSI más bajo exigido en recuperación (era 35)
    "RV_MIN_REC":          1.8,    # Más volumen exigido en recuperación (era 2.0)

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

    # ── Excepciones RSI por ticker (basado en análisis histórico jun 2026) ──
    # Activos con evidencia estadística sólida de win rate > 65% a RSI < 50:
    #   GOOG:  9 señales históricas, WR 77.8%, Sharpe 0.41 → umbral 50
    #   GOOGL: misma empresa, mismo comportamiento esperado → umbral 50
    "EXCEPCIONES_RSI": {
        "GOOG":  50,
        "GOOGL": 50,
    },

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

    # ── Capital y ejecución (eToro) ──
    "CAPITAL_INICIAL":    2380,
    # eToro permite fracciones de acción: las unidades se redondean a
    # DECIMALES_UNIDADES en vez de a acciones enteras. Con False se vuelve
    # al comportamiento anterior (acciones enteras), que descartaba toda
    # acción con ATR > ~$9.5 porque salían 0 unidades.
    "FRACCIONES":          True,
    "DECIMALES_UNIDADES":     4,
    "INVERSION_MINIMA":      10,   # USD; mínimo por posición en eToro
    "MAX_INVERSION_PCT":   0.25,   # Tope por posición (% del capital)
}
PARAMETROS["RIESGO_USD_BASE"] = (
    PARAMETROS["CAPITAL_INICIAL"] * PARAMETROS["RIESGO_NORMAL"]
)


# ======================================================================
# 2. UNIVERSO COMPLETO DE ACTIVOS
# ======================================================================
SECTORES = {
    'QQQ': 'Índices (ETF)', 'VTI': 'Índices (ETF)', 'IEF': 'Bonos',
    'BTC-USD': 'Crypto', 'ETH-USD': 'Crypto',
    'AAPL': 'Big Tech', 'MSFT': 'Big Tech', 'META': 'Big Tech',
    'GOOG': 'Big Tech', 'GOOGL': 'Big Tech', 'AMZN': 'Big Tech',
    'NVDA': 'Semiconductores', 'AMD': 'Semiconductores', 'TSM': 'Semiconductores',
    'AVGO': 'Semiconductores', 'ASML': 'Semiconductores', 'ON': 'Semiconductores',
    'INTC': 'Semiconductores', 'LRCX': 'Semiconductores', 'AMAT': 'Semiconductores',
    'KLAC': 'Semiconductores', 'MU': 'Semiconductores', 'SWKS': 'Semiconductores',
    'MCHP': 'Semiconductores', 'QRVO': 'Semiconductores', 'TER': 'Semiconductores',
    'CRM': 'Software/SaaS', 'SNOW': 'Software/SaaS', 'ADBE': 'Software/SaaS',
    'ORCL': 'Software/SaaS', 'INTU': 'Software', 'ACN': 'Servicios IT',
    'SNDK': 'Hardware', 'DELL': 'Hardware', 'HPQ': 'Hardware', 'KD': 'Tech',
    'MELI': 'E-commerce', 'SHOP': 'E-commerce', 'ETSY': 'E-commerce',
    'BABA': 'China Tech', 'UBER': 'Movilidad',
    'NET': 'Ciberseguridad', 'PANW': 'Ciberseguridad',
    'CRWD': 'Ciberseguridad',  # 1 señal WR100% — principalmente alertas BREAKOUT
    'NFLX': 'Streaming', 'DIS': 'Medios', 'CMCSA': 'Telecom/Media',
    'TSLA': 'Automotriz/Tech', 'BA': 'Aeroespacial', 'LUV': 'Aerolíneas',
    'LMT': 'Defensa',   # 2 señales WR50% — principalmente BREAKOUT (conflicto activo)
    'RTX': 'Defensa',   # 2 señales WR100% Sharpe1.13 — mejor candidato del grupo
    'UPS': 'Logística',
    'JPM': 'Finanzas', 'MA': 'Finanzas', 'V': 'Finanzas',
    'BAC': 'Finanzas', 'WFC': 'Finanzas', 'C': 'Finanzas',
    'GS': 'Finanzas', 'MS': 'Finanzas', 'AIG': 'Finanzas', 'CME': 'Finanzas',
    'AXP': 'Finanzas',  # 0 señales COMPRA — muy estable, pocas caídas a RSI<40
    'JNJ': 'Salud', 'UNH': 'Salud', 'PFE': 'Salud', 'MRK': 'Salud',
    'LLY': 'Salud', 'ABT': 'Salud', 'BMY': 'Salud',
    'AMGN': 'Biotech', 'GILD': 'Biotech',  # 2 señales WR0% — monitoreo preventivo
    'CVX': 'Energía', 'OXY': 'Energía', 'XOM': 'Energía', 'COP': 'Energía',
    'XLE': 'Energía',
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
    'CL': 'Consumo defensivo',   # 0 señales históricas — muy estable, beta 0.3
    'WMT': 'Retail defensivo', 'PG': 'Consumo defensivo',
    'COST': 'Retail defensivo',
    'TGT': 'Retail defensivo',   # 1 señal WR100% — monitoreo BREAKOUT y COMPRA rara
    'MO': 'Tabaco',
    'PM': 'Tabaco',              # 1 señal WR100% — Philip Morris Intl, sin litigios US
    'ABEV': 'Consumo defensivo',
    'GLD': 'Oro/Refugio',
    'SLV': 'Oro/Refugio',        # 0 señales COMPRA — cobertura inflación energética
    'PLD': 'REIT Industrial', 'AMT': 'REIT Telecom',
    'EQIX': 'REIT Data Centers', 'SPG': 'REIT Retail',
    'GRBK': 'Inmobiliario',
    'DHI': 'Inmobiliario',       # 2 señales WR0% — mayor constructor EEUU, monitoreo
    'CSCO': 'Networking',
    'ANET': 'Networking',        # 1 señal WR100% — líder data centers, BREAKOUT
    'NTAP': 'Almacenamiento',
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
    'Defensa':            'ITA',   # iShares U.S. Aerospace & Defense ETF
    'Crypto':             'QQQ',   # Proxy macro (no hay ETF cripto universal fiable)
    'Índices (ETF)':      'SPY',
    'Bonos':              'AGG',
    'Oro/Refugio':        'GLD',
}


# ======================================================================
# 3. GESTIÓN DE POSICIONES (JSON compartido)
# ======================================================================
POSICIONES_FILE = "posiciones.json"

# posiciones.json acepta dos formatos por ticker (se pueden mezclar):
#   Simple    : "PLD": 2.0
#   Detallado : "PLD": {"unidades": 2, "precio_entrada": 125.4,
#                       "stop_loss": 118.2, "take_profit": 141.0}
# Con el formato detallado el bot reporta PnL y vigila el SL/TP fijos.

def _leer_posiciones_raw() -> dict:
    try:
        with open(POSICIONES_FILE, 'r') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _num(valor) -> float | None:
    try:
        v = float(valor)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None

def cargar_posiciones_detalle() -> dict:
    """
    Devuelve {ticker: {"unidades", "precio_entrada", "stop_loss", "take_profit"}}
    normalizando ambos formatos. Ignora entradas con unidades <= 0.
    """
    detalle = {}
    for ticker, valor in _leer_posiciones_raw().items():
        if isinstance(valor, dict):
            info = {
                "unidades":       _num(valor.get("unidades")) or 0,
                "precio_entrada": _num(valor.get("precio_entrada")),
                "stop_loss":      _num(valor.get("stop_loss")),
                "take_profit":    _num(valor.get("take_profit")),
            }
        else:
            info = {"unidades": _num(valor) or 0, "precio_entrada": None,
                    "stop_loss": None, "take_profit": None}
        if info["unidades"] > 0:
            detalle[ticker.strip().upper()] = info
    return detalle

def cargar_posiciones() -> dict:
    """Devuelve {ticker: unidades} (compatible con el formato original)."""
    return {t: d["unidades"] for t, d in cargar_posiciones_detalle().items()}

def guardar_posiciones(posiciones: dict):
    with open(POSICIONES_FILE, 'w') as f:
        json.dump(posiciones, f, indent=4)
    print(f"✅ posiciones.json actualizado ({len(posiciones)} posiciones).")

def abrir_posicion(ticker: str, cantidad: float,
                   precio_entrada: float = None,
                   stop_loss: float = None,
                   take_profit: float = None):
    pos = _leer_posiciones_raw()
    if precio_entrada is None and stop_loss is None and take_profit is None:
        pos[ticker] = cantidad
    else:
        pos[ticker] = {
            "unidades":       cantidad,
            "precio_entrada": precio_entrada,
            "stop_loss":      stop_loss,
            "take_profit":    take_profit,
        }
    guardar_posiciones(pos)

def cerrar_posicion(ticker: str):
    pos = _leer_posiciones_raw()
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

def clasificar_regimen(spy: float, sma200: float, sma50: float,
                       vix: float, p: dict = PARAMETROS) -> str:
    """Regla de régimen (compartida por el bot y el backtest)."""
    if vix > p["VIX_PANICO"]:
        return "PANICO"
    if not spy > sma200:
        return "BAJISTA"
    if sma50 > sma200 and vix < p["VIX_ALERTA"]:
        return "TENDENCIA_ALCISTA"
    return "RECUPERACION"

def serie_regimen(spy_close: pd.Series, vix_close: pd.Series,
                  p: dict = PARAMETROS) -> pd.Series:
    """Régimen de cada día histórico (NaN-safe: sin datos → RECUPERACION)."""
    vix    = vix_close.reindex(spy_close.index).ffill()
    sma200 = spy_close.rolling(200).mean()
    sma50  = spy_close.rolling(50).mean()
    reg = pd.Series("RECUPERACION", index=spy_close.index)
    reg[(sma50 > sma200) & (vix < p["VIX_ALERTA"])] = "TENDENCIA_ALCISTA"
    reg[~(spy_close > sma200)] = "BAJISTA"
    reg[vix > p["VIX_PANICO"]] = "PANICO"
    reg[sma200.isna() | vix.isna()] = "RECUPERACION"
    return reg

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

        regimen = clasificar_regimen(spy, sma200, sma50, vix, p)

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
        # Sin datos de mercado no se puede confirmar la tendencia: se usa
        # RECUPERACION (umbrales estrictos) en vez de asumir mercado alcista.
        print(f"⚠️ Error detectando régimen: {e}")
        return "RECUPERACION", {
            "regimen":     "RECUPERACION",
            "descripcion": "Desconocido (error de datos)",
            "emoji":       "⚠️",
            "accion":      "Datos de mercado no disponibles: solo señales estrictas.",
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
        rs = float(serie_fuerza_relativa(datos[ticker], datos[etf], p).iloc[-1])
        return None if math.isnan(rs) else round(rs, 4)
    except Exception:
        return None

def serie_fuerza_relativa(df_activo: pd.DataFrame, df_etf: pd.DataFrame,
                          p: dict = PARAMETROS) -> pd.Series:
    """Retorno del activo menos el de su ETF en RS_PERIODO días, por fecha."""
    periodo = p["RS_PERIODO"]
    ret_etf = df_etf['Close'].pct_change(periodo).reindex(df_activo.index)
    return df_activo['Close'].pct_change(periodo) - ret_etf

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
                         p: dict = PARAMETROS) -> float:
    """Devuelve el monto en USD a arriesgar según calidad."""
    mapa = {
        "ALTA":   p["RIESGO_ALTA_CONV"],
        "NORMAL": p["RIESGO_NORMAL"],
        "DEBIL":  p["RIESGO_DEBIL"],
    }
    return capital * mapa.get(calidad, p["RIESGO_NORMAL"])

def calcular_gestion_riesgo(precio: float, atr: float,
                             calidad: str = "NORMAL",
                             capital: float = None,
                             p: dict = PARAMETROS) -> dict:
    """
    Calcula Stop Loss, Take Profit, unidades y R:R.
    El riesgo en USD varía según la calidad de la señal.
    """
    cap      = capital or p["CAPITAL_INICIAL"]
    riesgo   = calcular_riesgo_usd(calidad, cap, p)
    dist_sl  = atr * p["ATR_SL"]
    sl       = precio - dist_sl
    tp       = precio + (atr * p["ATR_TP"])
    rr       = (tp - precio) / dist_sl if dist_sl > 0 else 0
    unidades = 0
    if dist_sl > 0 and precio > 0:
        unidades = riesgo / dist_sl
        # Tope por posición: evita concentrar el capital en activos de baja
        # volatilidad (stop muy cercano → muchas unidades)
        unidades = min(unidades, cap * p.get("MAX_INVERSION_PCT", 1.0) / precio)
        if p.get("FRACCIONES", False):
            factor   = 10 ** p.get("DECIMALES_UNIDADES", 4)
            unidades = math.floor(unidades * factor) / factor
        else:
            unidades = math.floor(unidades)
        if unidades * precio < p.get("INVERSION_MINIMA", 0):
            unidades = 0

    return {
        "stop_loss":    round(sl,               2),
        "take_profit":  round(tp,               2),
        "unidades":     unidades,
        "riesgo_usd":   round(unidades*dist_sl, 2),
        "inversion":    round(unidades*precio,  2),
        "rr_ratio":     round(rr,               2),
        "calidad":      calidad,
        "pct_riesgo":   mapa_pct(calidad, p),
    }

def mapa_pct(calidad: str, p: dict) -> str:
    m = {
        "ALTA":   f"{p['RIESGO_ALTA_CONV']*100:.1f}%",
        "NORMAL": f"{p['RIESGO_NORMAL']*100:.1f}%",
        "DEBIL":  f"{p['RIESGO_DEBIL']*100:.1f}%",
    }
    return m.get(calidad, "1.0%")


# ======================================================================
# 9. MEJORA 5: FILTRO DE CORRELACIÓN ENTRE SECTORES
#    Máximo MAX_POR_SECTOR operaciones nuevas por sector en un mismo día.
#    Evita sobreexposición cuando hay 5 semiconductores con señal.
# ======================================================================

def filtrar_por_correlacion(señales: list[dict],
                             posiciones_abiertas: dict,
                             p: dict = PARAMETROS) -> list[dict]:
    """
    Recibe la lista de señales de COMPRA y elimina duplicados de sector,
    dejando solo la mejor señal (mayor win rate → RV → R:R) por sector.

    También cuenta las posiciones ya abiertas por sector para no superar
    MAX_POR_SECTOR en total (abiertas + nuevas).
    """
    # Contar posiciones abiertas por sector
    sector_count: dict[str, int] = {}
    for ticker in posiciones_abiertas:
        sector = SECTORES.get(ticker, "Otros")
        sector_count[sector] = sector_count.get(sector, 0) + 1

    # Solo trabajar sobre señales de compra
    compras  = [s for s in señales if s.get("tipo") == "COMPRA"]
    no_compra = [s for s in señales if s.get("tipo") != "COMPRA"]

    # Ordenar compras por calidad descendente
    orden_calidad = {"ALTA": 0, "NORMAL": 1, "DEBIL": 2}
    compras_ordenadas = sorted(
        compras,
        key=lambda x: (
            orden_calidad.get(x.get("calidad_senal", "NORMAL"), 1),
            -(x.get("win_rate_raw", 0) or 0),
            -(x.get("rv", 0)),
            -(x.get("rr_ratio", 0)),
        )
    )

    compras_filtradas = []
    sector_nuevas: dict[str, int] = {}

    for s in compras_ordenadas:
        sector  = s.get("sector", "Otros")
        ya_abiertas = sector_count.get(sector, 0)
        ya_nuevas   = sector_nuevas.get(sector, 0)

        if ya_abiertas + ya_nuevas < p["MAX_POR_SECTOR"]:
            compras_filtradas.append(s)
            sector_nuevas[sector] = ya_nuevas + 1
        else:
            print(f"  ⛔ {s['ticker']} descartado "
                  f"(ya hay exposición a {sector})")

    return no_compra + compras_filtradas


# ======================================================================
# 10. LÓGICA DE SEÑALES — FUNCIÓN ÚNICA Y COMPARTIDA
# ======================================================================

def umbrales_entrada(regimen: str, ticker: str = "",
                     p: dict = PARAMETROS) -> tuple[float, float] | None:
    """(rsi_max, rv_min) para el régimen y ticker, o None si no se compra."""
    if regimen in ("PANICO", "BAJISTA"):
        return None
    rsi_max = (p["RSI_ENTRADA_REC"] if regimen == "RECUPERACION"
               else p["RSI_ENTRADA"])
    rv_min  = (p["RV_MIN_REC"]      if regimen == "RECUPERACION"
               else p["RV_MIN"])
    excepciones = p.get("EXCEPCIONES_RSI", {})
    if ticker and ticker in excepciones:
        rsi_max = excepciones[ticker]
    return rsi_max, rv_min

def mascara_compra(df: pd.DataFrame, regimen: pd.Series,
                   p: dict = PARAMETROS, ticker: str = "") -> pd.Series:
    """
    Versión vectorizada de es_senal_compra para todo el histórico.
    `regimen` es una Serie (de serie_regimen) alineada por fecha.
    Las pruebas verifican que coincide fila a fila con es_senal_compra.
    """
    reg     = regimen.reindex(df.index)
    rsi_max = pd.Series(np.nan, index=df.index)
    rv_min  = pd.Series(np.nan, index=df.index)
    for nombre in ("TENDENCIA_ALCISTA", "RECUPERACION"):
        u = umbrales_entrada(nombre, ticker, p)
        rsi_max[reg == nombre] = u[0]
        rv_min[reg == nombre]  = u[1]
    return (
        (df['RSI']   < rsi_max)               &
        (df['RV']    > rv_min)                &
        (df['Close'] > df['Open'])            &
        (df['Close'] > df['SMA_200'])         &
        (df['Close'] > df['Chandelier_Exit'])
    ).fillna(False)

def mascara_venta_urgente(df: pd.DataFrame) -> pd.Series:
    return (df['Close'] < df['SMA_200']).fillna(False)

def mascara_stop_dinamico(df: pd.DataFrame, p: dict = PARAMETROS) -> pd.Series:
    margen = df['ATR'] * p.get("MARGEN_CHANDELIER", 0.10)
    return (df['Close'] < df['Chandelier_Exit'] - margen).fillna(False)

def es_senal_compra(row: pd.Series, regimen: str,
                    p: dict = PARAMETROS,
                    ticker: str = "") -> bool:
    """
    Condición de ENTRADA unificada, ajustada por régimen y ticker.

    TENDENCIA_ALCISTA : RSI < 45, RV > 1.2  (v2.3)
    RECUPERACION      : RSI < 38, RV > 1.8  (más estricto)
    PANICO / BAJISTA  : nunca comprar

    Excepciones por ticker (respaldadas por análisis histórico):
      GOOG, GOOGL → RSI < 50 (9 señales históricas, WR 77.8%)
    """
    umbrales = umbrales_entrada(regimen, ticker, p)
    if umbrales is None:
        return False
    rsi_max, rv_min = umbrales

    try:
        return (
            float(row['RSI'])   < rsi_max                       and
            float(row['RV'])    > rv_min                        and
            float(row['Close']) > float(row['Open'])            and  # Vela verde
            float(row['Close']) > float(row['SMA_200'])         and  # Tendencia
            float(row['Close']) > float(row['Chandelier_Exit'])      # Soporte din.
        )
    except (KeyError, TypeError, ValueError):
        return False

def es_senal_venta_urgente(row: pd.Series) -> bool:
    return float(row['Close']) < float(row['SMA_200'])

def es_senal_stop_dinamico(row: pd.Series, p: dict = PARAMETROS) -> bool:
    """
    Activa el stop solo si el precio cerró al menos MARGEN_CHANDELIER × ATR
    por debajo del Chandelier Exit. Evita falsas alertas en activos de alta
    volatilidad (TSLA, AMD) donde el precio roza el soporte sin romperlo.

    Ejemplo con AMD: ATR=6.50, MARGEN=0.10
      → el precio debe estar al menos $0.65 bajo el Chandelier
      → una ruptura de $0.59 no activa la alerta (era ruido)
    """
    try:
        margen = float(row['ATR']) * p.get("MARGEN_CHANDELIER", 0.10)
        return float(row['Close']) < float(row['Chandelier_Exit']) - margen
    except (KeyError, TypeError, ValueError):
        # Fallback sin margen si falta el ATR
        return float(row['Close']) < float(row['Chandelier_Exit'])

def es_senal_sobrecompra(row: pd.Series, p: dict = PARAMETROS) -> bool:
    return float(row['RSI']) > p["RSI_SOBRECOMPRA"]

def es_senal_breakout(df: pd.DataFrame, p: dict = PARAMETROS) -> bool:
    """
    Detecta ruptura de máximo histórico de 52 semanas con volumen institucional.

    Condiciones:
      1. El precio de hoy supera el máximo de los últimos 252 días (excluyendo hoy)
      2. El volumen relativo supera RV_BREAKOUT (por defecto 2.0x)
      3. El precio está sobre la SMA 200 (tendencia alcista de fondo)

    Esta señal es INFORMATIVA — avisa de movimientos explosivos como el de
    energía en el conflicto de Ormuz, donde el RSI nunca baja a <40.
    No reemplaza la señal de COMPRA estándar.
    """
    try:
        if len(df) < p["PERIODO_MAX_HIST"]:
            return False
        last         = df.iloc[-1]
        precio_hoy   = float(last['Close'])
        max_52w      = float(df['Close'].iloc[-(p["PERIODO_MAX_HIST"]+1):-1].max())
        rv_hoy       = float(last['RV'])
        sma200       = float(last['SMA_200'])
        return (
            precio_hoy > max_52w                  and  # Rompe máximo histórico
            rv_hoy     > p["RV_BREAKOUT"]         and  # Volumen institucional
            precio_hoy > sma200                        # Tendencia alcista de fondo
        )
    except (KeyError, TypeError, ValueError, IndexError):
        return False


# ======================================================================
# 11. MOTOR QUANT UNIFICADO v2
#     Integra todas las mejoras: régimen, RS, calidad, correlación.
# ======================================================================

def _pnl(precio: float, info: dict) -> dict:
    """PnL de una posición si se conoce el precio de entrada."""
    entrada = info.get("precio_entrada") if info else None
    if not entrada:
        return {}
    unidades = info.get("unidades", 0)
    return {
        "precio_entrada": round(entrada, 2),
        "pnl_pct":        round((precio / entrada - 1) * 100, 2),
        "pnl_usd":        round((precio - entrada) * unidades, 2),
    }

def motor_quant(ticker: str,
                df: pd.DataFrame,
                regimen: str,
                datos_globales: dict = None,
                win_rate_hist: float = None,
                p: dict = PARAMETROS,
                posiciones: dict = None) -> dict | None:
    """
    Analiza un ticker y devuelve un dict con señal completa o None.

    Parámetros:
        ticker        : Símbolo del activo
        df            : DataFrame con indicadores (de calcular_indicadores)
        regimen       : Resultado de detectar_regimen()
        datos_globales: dict completo {ticker: df} para filtro RS
        win_rate_hist : Win rate histórico del backtest (opcional)
        p             : Parámetros de la estrategia
        posiciones    : Resultado de cargar_posiciones_detalle() (si es None
                        se lee posiciones.json)
    """
    try:
        if df.empty or len(df) < p["SMA_PERIODO"]:
            return None

        last       = df.iloc[-1]
        precio     = float(last['Close'])
        rsi        = float(last['RSI'])
        rv         = float(last['RV'])
        sma200     = float(last['SMA_200'])
        atr        = float(last['ATR'])
        chandelier = float(last['Chandelier_Exit'])
        sector     = SECTORES.get(ticker, "Otros")

        if posiciones is None:
            posiciones = cargar_posiciones_detalle()
        info       = posiciones.get(ticker)
        tengo      = info["unidades"] if info else 0
        en_cartera = tengo > 0

        # ── Señales de SALIDA (si ya tienes el activo) ──
        if en_cartera:
            base = {
                "ticker":  ticker, "sector": sector,
                "precio":  round(precio, 2),
                "rsi":     round(rsi,    1),
                "rv":      round(rv,     2),
                "chandelier": round(chandelier, 2),
                "unidades_en_cartera": tengo,
                **_pnl(precio, info),
            }
            sl = info.get("stop_loss")
            tp = info.get("take_profit")
            if sl and precio <= sl:
                return {**base, "tipo": "STOP_LOSS",
                        "motivo": f"Tocó el Stop Loss fijo (${round(sl,2)})",
                        "accion": "🛑 VENDER (stop loss)"}
            if es_senal_venta_urgente(last):
                return {**base, "tipo": "VENTA_URGENTE",
                        "motivo": f"Rompió SMA 200 (${round(sma200,2)})",
                        "accion": "🚨 VENDER TODO"}
            if es_senal_stop_dinamico(last, p):
                margen = round(atr * p.get("MARGEN_CHANDELIER", 0.10), 2)
                return {**base, "tipo": "STOP_DINAMICO",
                        "motivo": f"Bajo Chandelier Exit (${round(chandelier,2)}) con margen ${margen}",
                        "accion": "💰 VENTA POR STOP"}
            if tp and precio >= tp:
                return {**base, "tipo": "TAKE_PROFIT",
                        "motivo": f"Alcanzó el Take Profit (${round(tp,2)})",
                        "accion": f"🎯 TOMAR GANANCIAS o subir stop a ${round(chandelier,2)}"}
            if es_senal_sobrecompra(last, p):
                return {**base, "tipo": "SOBRECOMPRA",
                        "motivo": f"RSI ({round(rsi,1)}) sobrecomprado",
                        "accion": f"⚠️ AJUSTAR STOP a ${round(chandelier,2)}"}
            return {**base, "tipo": "MANTENER", "accion": "💎 MANTENER"}

        # ── Señal de BREAKOUT (informativa; aquí el activo NO está en cartera) ──
        # Se detecta antes de la señal de compra estándar porque son mutuamente
        # excluyentes: un activo en breakout tiene RSI alto, nunca generaría COMPRA.
        if es_senal_breakout(df, p):
            return {
                "tipo":    "BREAKOUT",
                "ticker":  ticker, "sector": sector,
                "precio":  round(precio, 2),
                "rsi":     round(rsi,    1),
                "rv":      round(rv,     2),
                "sma200":  round(sma200, 2),
                "chandelier": round(chandelier, 2),
                "motivo":  f"Rompe máximo 52 semanas con volumen {round(rv,1)}x",
                "accion":  "👀 OBSERVAR — esperar pullback para entrada",
            }

        # ── Señal de ENTRADA ──
        if not es_senal_compra(last, regimen, p, ticker=ticker):
            return None

        # MEJORA 2: Filtro de fuerza relativa
        if datos_globales:
            rs = calcular_fuerza_relativa(ticker, sector, datos_globales, p)
            rs_ok = (rs is None) or (rs > p["RS_MIN"])
            if not rs_ok:
                print(f"  📉 {ticker} descartado: rezagado vs su sector "
                      f"({sector}, RS={rs:.2%})")
                return None
        else:
            rs = None

        # MEJORA 4: Calidad de señal y tamaño de posición
        riesgo_base  = calcular_gestion_riesgo(precio, atr, "NORMAL", None, p)
        rr_ratio     = riesgo_base["rr_ratio"]
        calidad      = clasificar_calidad_senal(rsi, rv, rr_ratio, win_rate_hist, p)
        riesgo       = calcular_gestion_riesgo(precio, atr, calidad, None, p)

        # Filtro R:R mínimo
        if riesgo["rr_ratio"] < p["RR_MINIMO"]:
            return None
        if riesgo["unidades"] == 0:
            return None

        return {
            "tipo":          "COMPRA",
            "ticker":        ticker,
            "sector":        sector,
            "precio":        round(precio, 2),
            "rsi":           round(rsi,    1),
            "rv":            round(rv,     2),
            "sma200":        round(sma200, 2),
            "chandelier":    round(chandelier, 2),
            "stop_loss":     riesgo["stop_loss"],
            "take_profit":   riesgo["take_profit"],
            "unidades":      riesgo["unidades"],
            "inversion":     riesgo["inversion"],
            "riesgo_usd":    riesgo["riesgo_usd"],
            "rr_ratio":      riesgo["rr_ratio"],
            "calidad_senal": calidad,
            "pct_riesgo":    riesgo["pct_riesgo"],
            "win_rate_raw":  win_rate_hist,
            "win_rate_hist": (
                f"{round(win_rate_hist*100,1)}%"
                if win_rate_hist else "N/A"
            ),
            "rs_vs_sector":  (
                f"{rs:.2%}" if rs is not None else "N/A"
            ),
            "regimen":       regimen,
            "accion":        f"🛒 COMPRAR {riesgo['unidades']:g} u. ({calidad})",
        }

    except Exception as e:
        print(f"⚠️ Error en motor_quant({ticker}): {e}")
        return None


# ======================================================================
# 12. DESCARGA MASIVA
# ======================================================================

def descargar_datos_globales(tickers: list = None,
                              periodo: str = "3y") -> dict:
    """
    Descarga y calcula indicadores para todos los activos en un
    solo request (bulk). Incluye ETFs de referencia para filtro RS.
    """
    if tickers is None:
        tickers = list(SECTORES.keys())
    # Las posiciones abiertas siempre se descargan (aunque no estén en SECTORES)
    tickers = list(tickers) + [t for t in cargar_posiciones() if t not in tickers]

    # Incluir ETFs sectoriales para filtro de fuerza relativa
    etfs_extra = [e for e in SECTOR_ETFS.values()
                  if e not in tickers]
    todos = list(dict.fromkeys(tickers + etfs_extra))  # sin duplicados

    print(f"📡 Descargando {len(todos)} símbolos (período: {periodo})...")
    raw = yf.download(
        todos, period=periodo,
        progress=False, group_by="ticker", auto_adjust=True
    )

    datos = {}
    for ticker in todos:
        try:
            df = raw[ticker].dropna() if len(todos) > 1 else raw.dropna()
            df = _normalizar_columnas(df)
            if len(df) < PARAMETROS["SMA_PERIODO"]:
                continue
            datos[ticker] = calcular_indicadores(df)
        except Exception:
            continue

    print(f"✅ {len(datos)} activos procesados.")
    return datos


# ======================================================================
# 13. ESCÁNER COMPLETO v2
#     Integra régimen + universo élite + RS + correlación
# ======================================================================

def escanear_universo(datos_globales: dict = None,
                       db_historica: pd.DataFrame = None,
                       p: dict = PARAMETROS) -> pd.DataFrame:
    """
    Pipeline completo del escáner:

    1. Detectar régimen de mercado
    2. Reducir universo a élite por ADN histórico
    3. Calcular señales con motor_quant (incluye filtro RS y calidad)
    4. Filtrar correlación entre sectores
    5. Devolver DataFrame ordenado por calidad de señal
    """
    if datos_globales is None:
        datos_globales = descargar_datos_globales()

    # Paso 1: Régimen
    regimen, contexto = detectar_regimen(p)
    print(f"\n{contexto['emoji']} Régimen: {contexto['descripcion']}")
    print(f"   Acción: {contexto['accion']}")
    print(f"   SPY: ${contexto.get('SPY','N/A')} | "
          f"SMA50: ${contexto.get('SPY_SMA50','N/A')} | "
          f"VIX: {contexto.get('VIX','N/A')}\n")

    # Paso 2: Universo élite + posiciones abiertas (siempre se vigilan,
    # aunque hayan salido del élite, para no perder alertas de salida)
    posiciones = cargar_posiciones_detalle()
    universo = obtener_universo_elite(db_historica, p)
    universo = list(universo) + [t for t in posiciones if t not in universo]
    faltantes = [t for t in posiciones if t not in datos_globales]
    if faltantes:
        print(f"⚠️  Sin datos para posiciones abiertas: {', '.join(faltantes)}")

    # Paso 3: Win rates históricos
    win_rates = {}
    if db_historica is not None:
        cond  = ((db_historica['RSI'] < p["RSI_ENTRADA"]) &
                 (db_historica['RV']  > p["RV_MIN"]))
        señales = db_historica[cond]
        adn = señales.groupby('Ticker').agg(
            wr=('Ret_10d', lambda x: (x > 0).mean()),
            n=('Ret_10d', 'count')
        )
        win_rates = (
            adn[adn['n'] >= p["MIN_SEÑALES_ADN"]]['wr'].to_dict()
        )

    # Paso 4: Señales
    señales_raw = []
    for ticker in universo:
        if ticker not in datos_globales:
            continue
        wr     = win_rates.get(ticker)
        señal  = motor_quant(ticker, datos_globales[ticker],
                              regimen, datos_globales, wr, p, posiciones)
        if señal:
            señales_raw.append(señal)

    if not señales_raw:
        print("⏳ Sin señales en este momento.")
        return pd.DataFrame()

    # Paso 5: Filtro de correlación (solo para compras)
    señales_filtradas = filtrar_por_correlacion(señales_raw, posiciones, p)

    # Ordenar: COMPRA ALTA > COMPRA NORMAL > ventas > MANTENER
    orden = {
        "STOP_LOSS":      0, "VENTA_URGENTE":   1, "STOP_DINAMICO":  2,
        "COMPRA_ALTA":    3, "COMPRA_NORMAL":   4, "COMPRA_DEBIL":   5,
        "TAKE_PROFIT":    6, "SOBRECOMPRA":     7, "BREAKOUT":       8,
        "MANTENER":       9,
    }
    def sort_key(s):
        tipo = s.get("tipo", "")
        cal  = s.get("calidad_senal", "")
        key  = f"{tipo}_{cal}" if tipo == "COMPRA" else tipo
        return (orden.get(key, 10),
                -(s.get("win_rate_raw") or 0))

    señales_filtradas.sort(key=sort_key)
    return pd.DataFrame(señales_filtradas)


# ======================================================================
# 14. FORMATEADOR DE MENSAJES TELEGRAM
# ======================================================================

EMOJIS_CALIDAD = {"ALTA": "💎", "NORMAL": "🛒", "DEBIL": "👀"}

def formatear_pnl(señal: dict) -> str:
    """'PnL: +3.2% ($12.5)' si se conoce el precio de entrada, si no ''."""
    pct = señal.get("pnl_pct")
    if pct is None or (isinstance(pct, float) and math.isnan(pct)):
        return ""
    usd = señal.get("pnl_usd", 0)
    return f"PnL: `{pct:+.2f}%` (${usd:+.2f}) desde ${señal.get('precio_entrada')}"

def formatear_alerta(señal: dict) -> str | None:
    """Convierte un dict de señal en mensaje Markdown para Telegram."""
    if señal is None or señal.get("tipo") == "MANTENER":
        return None

    t   = señal["ticker"]
    p   = señal["precio"]
    rsi = señal["rsi"]
    rv  = señal["rv"]
    ch  = señal["chandelier"]

    if señal["tipo"] == "COMPRA":
        cal  = señal.get("calidad_senal", "NORMAL")
        emo  = EMOJIS_CALIDAD.get(cal, "🛒")
        wr   = señal.get("win_rate_hist", "N/A")
        rs   = señal.get("rs_vs_sector",  "N/A")
        reg  = señal.get("regimen",       "N/A")
        return (
            f"{emo} *COMPRA {cal}*: `{t}` ({señal['sector']})\n"
            f"Régimen: `{reg}` | RSI: `{rsi}` | Mano Fuerte: `{rv}x`\n"
            f"Fuerza vs sector: `{rs}`\n"
            f"{'─'*35}\n"
            f"📌 Comprar: `{float(señal['unidades']):g} acciones` — ${señal['inversion']}\n"
            f"🛑 Stop Loss: `${señal['stop_loss']}`\n"
            f"✅ Take Profit: `${señal['take_profit']}`\n"
            f"📊 R:R: `{señal['rr_ratio']}:1` | "
            f"Riesgo: `${señal['riesgo_usd']}` ({señal['pct_riesgo']})\n"
            f"🔬 Win Rate Hist: `{wr}` | Chandelier: `${ch}`"
        )

    pnl = formatear_pnl(señal)
    pnl = f"\n{pnl}" if pnl else ""

    if señal["tipo"] == "STOP_LOSS":
        return (
            f"🛑 *STOP LOSS*: `{t}`\n"
            f"Precio: `${p}` | RSI: `{rsi}`{pnl}\n"
            f"{'─'*35}\n"
            f"⚠️ {señal['motivo']}\n"
            f"Tienes `{float(señal['unidades_en_cartera']):g}` acciones. Ejecuta la venta."
        )

    if señal["tipo"] == "TAKE_PROFIT":
        return (
            f"🎯 *TAKE PROFIT*: `{t}`\n"
            f"Precio: `${p}` | RSI: `{rsi}`{pnl}\n"
            f"{'─'*35}\n"
            f"✅ {señal['motivo']}\n"
            f"📌 Toma ganancias o sube el stop a `${ch}` (Chandelier Exit)."
        )

    if señal["tipo"] == "VENTA_URGENTE":
        return (
            f"🚨 *VENTA URGENTE*: `{t}`\n"
            f"Precio: `${p}` | RSI: `{rsi}`{pnl}\n"
            f"{'─'*35}\n"
            f"⚠️ {señal['motivo']}\n"
            f"Tienes `{float(señal['unidades_en_cartera']):g}` acciones. Vende ahora."
        )

    if señal["tipo"] == "STOP_DINAMICO":
        return (
            f"💰 *STOP DINÁMICO*: `{t}`\n"
            f"Precio: `${p}` | Chandelier: `${ch}`{pnl}\n"
            f"{'─'*35}\n"
            f"⚠️ {señal['motivo']}\n"
            f"Tienes `{float(señal['unidades_en_cartera']):g}` acciones. Ejecuta la venta."
        )

    if señal["tipo"] == "SOBRECOMPRA":
        return (
            f"⚠️ *SOBRECOMPRA*: `{t}`\n"
            f"Precio: `${p}` | RSI: `{rsi}`{pnl}\n"
            f"{'─'*35}\n"
            f"{señal['motivo']}\n"
            f"📌 Ajusta Stop a `${ch}` (Chandelier Exit)."
        )

    if señal["tipo"] == "BREAKOUT":
        return (
            f"🚀 *BREAKOUT — ALERTA MACRO*: `{t}` ({señal['sector']})\n"
            f"Precio: `${p}` | RSI: `{rsi}` | Volumen: `{rv}x`\n"
            f"{'─'*35}\n"
            f"📈 {señal['motivo']}\n"
            f"SMA200: `${señal['sma200']}` — tendencia alcista confirmada\n"
            f"⏳ *No comprar ahora* — RSI elevado, riesgo de entrada en pico.\n"
            f"📌 Esperar pullback con RSI < {PARAMETROS['RSI_ENTRADA']} "
            f"y RV > {PARAMETROS['RV_MIN']} para señal de COMPRA."
        )
    return None


# ======================================================================
# 15. CLIENTE TELEGRAM
# ======================================================================

def enviar_telegram(mensaje: str, token: str = None, chat_id: str = None):
    token   = token   or os.getenv('TELEGRAM_TOKEN')
    chat_id = chat_id or os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        print("⚠️  TELEGRAM_TOKEN o TELEGRAM_CHAT_ID no configurados.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # Intento 1: con Markdown
    try:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"},
            timeout=10
        )
        data = r.json()
        if data.get("ok"):
            return True
        # Telegram devuelve HTTP 200 incluso con errores de Markdown.
        # Si ok=False, reintentamos sin parse_mode.
        print(f"⚠️  Telegram rechazo Markdown: {data.get('description','?')} — reintentando sin formato.")
    except Exception as e:
        print(f"❌ Error Telegram (intento 1): {e}")

    # Intento 2: texto plano sin Markdown
    try:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": mensaje},
            timeout=10
        )
        data = r.json()
        if data.get("ok"):
            return True
        print(f"❌ Telegram rechazo texto plano: {data.get('description','?')}")
        return False
    except Exception as e:
        print(f"❌ Error Telegram (intento 2): {e}")
        return False


# ======================================================================
# 16. BITÁCORA DE TRADES v2
#     Ahora incluye: calidad de señal, actualización de stop,
#     estadísticas avanzadas y sincronización con posiciones.json
# ======================================================================

class BitacoraTrades:

    def __init__(self, archivo: str = "mi_bitacora.json"):
        self.archivo     = archivo
        self.operaciones = self._cargar()

    def _cargar(self) -> list:
        try:
            with open(self.archivo, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _guardar(self):
        with open(self.archivo, 'w') as f:
            json.dump(self.operaciones, f, indent=4, ensure_ascii=False)

    def registrar_compra(self, ticker: str, precio: float,
                         unidades: float, stop_loss: float,
                         calidad: str = "NORMAL",
                         motivo: str = "Mano Fuerte",
                         take_profit: float = None):
        op = {
            "id":             len(self.operaciones) + 1,
            "fecha_entrada":  datetime.now().strftime("%Y-%m-%d"),
            "ticker":         ticker,
            "precio_entrada": precio,
            "unidades":       unidades,
            "stop_loss":      stop_loss,
            "take_profit":    take_profit,
            "calidad":        calidad,
            "motivo":         motivo,
            "estado":         "ABIERTA",
            "fecha_revision": (
                datetime.now() + timedelta(days=PARAMETROS["DIAS_REVISION"])
            ).strftime("%Y-%m-%d"),
            "precio_salida":   None,
            "resultado_final": None,
            "pnl_pct":         None,
        }
        self.operaciones.append(op)
        self._guardar()
        abrir_posicion(ticker, unidades, precio, stop_loss, take_profit)
        print(f"✅ Compra [{calidad}]: {unidades}×{ticker} @ ${precio}. "
              f"Revisión: {op['fecha_revision']}.")

    def actualizar_stop(self, id_op: int, nuevo_stop: float):
        for op in self.operaciones:
            if op['id'] == id_op and op['estado'] == "ABIERTA":
                viejo = op['stop_loss']
                op['stop_loss'] = nuevo_stop
                self._guardar()
                pos = _leer_posiciones_raw()
                if isinstance(pos.get(op['ticker']), dict):
                    pos[op['ticker']]['stop_loss'] = nuevo_stop
                    guardar_posiciones(pos)
                print(f"📌 Stop {op['ticker']} (ID {id_op}): "
                      f"${viejo} → ${nuevo_stop}")
                return
        print(f"❌ ID {id_op} no encontrado o ya cerrado.")

    def cerrar_operacion(self, id_op: int, precio_salida: float):
        for op in self.operaciones:
            if op['id'] == id_op:
                if op['estado'] == "CERRADA":
                    print(f"⚠️  ID {id_op} ya estaba cerrado.")
                    return
                op['estado']          = "CERRADA"
                op['precio_salida']   = precio_salida
                ganancia              = (precio_salida - op['precio_entrada']) * op['unidades']
                op['resultado_final'] = round(ganancia, 2)
                op['pnl_pct']         = round(
                    ((precio_salida / op['precio_entrada']) - 1) * 100, 2
                )
                self._guardar()
                cerrar_posicion(op['ticker'])
                emoji = "🚀" if ganancia > 0 else "📉"
                print(f"{emoji} {op['ticker']} cerrado | "
                      f"PnL: ${op['resultado_final']} ({op['pnl_pct']}%)")
                return
        print(f"❌ ID {id_op} no encontrado.")

    def revisar_pendientes(self):
        hoy = datetime.now().strftime("%Y-%m-%d")
        pendientes = [
            op for op in self.operaciones
            if op['estado'] == "ABIERTA" and op['fecha_revision'] <= hoy
        ]
        if not pendientes:
            print("☕ Sin operaciones para revisar hoy.")
            return
        print(f"🔔 {len(pendientes)} operaciones para revisar:")
        for op in pendientes:
            print(f"  ID {op['id']:3d} | {op['ticker']:6s} | "
                  f"${op['precio_entrada']} | Stop: ${op['stop_loss']} | "
                  f"Cal: {op.get('calidad','N/A')} | {op['fecha_entrada']}")

    def resumen(self):
        if not self.operaciones:
            print("📭 Bitácora vacía.")
            return
        df   = pd.DataFrame(self.operaciones)
        cols = ['id','ticker','calidad','fecha_entrada','precio_entrada',
                'unidades','stop_loss','estado','pnl_pct','resultado_final']
        cols = [c for c in cols if c in df.columns]
        print("\n📒 RESUMEN DE OPERACIONES:")
        print(tabulate(df[cols], headers="keys",
                       tablefmt="fancy_grid", showindex=False))

    def estadisticas(self) -> dict:
        """Métricas globales + desglose por calidad de señal."""
        cerradas = [op for op in self.operaciones if op['estado'] == "CERRADA"]
        if not cerradas:
            print("⏳ Sin operaciones cerradas.")
            return {}

        pnl      = [op['resultado_final'] for op in cerradas]
        pct      = [op['pnl_pct']         for op in cerradas]
        ganadoras = [x for x in pnl if x > 0]

        stats = {
            "total_trades":  len(cerradas),
            "ganadoras":     len(ganadoras),
            "perdedoras":    len(cerradas) - len(ganadoras),
            "win_rate":      f"{round(len(ganadoras)/len(cerradas)*100, 1)}%",
            "pnl_total":     round(sum(pnl), 2),
            "pnl_promedio":  round(np.mean(pnl), 2),
            "mejor_trade":   round(max(pnl), 2),
            "peor_trade":    round(min(pnl), 2),
            "sharpe_approx": round(
                np.mean(pct) / np.std(pct) if np.std(pct) > 0 else 0, 2
            ),
        }

        # Desglose por calidad
        print("\n📊 ESTADÍSTICAS GLOBALES:")
        for k, v in stats.items():
            print(f"  {k:20s}: {v}")

        print("\n📊 DESGLOSE POR CALIDAD DE SEÑAL:")
        for cal in ["ALTA", "NORMAL", "DEBIL"]:
            ops_cal = [op for op in cerradas if op.get('calidad') == cal]
            if ops_cal:
                pnl_cal = [op['resultado_final'] for op in ops_cal]
                wr_cal  = sum(1 for x in pnl_cal if x > 0) / len(pnl_cal)
                print(f"  {cal:6s} → {len(ops_cal):3d} trades | "
                      f"WR: {wr_cal:.0%} | "
                      f"PnL: ${round(sum(pnl_cal),2)}")
        return stats


# ======================================================================
# 17. CLASE QuantApp (para Colab: build_database + analyze_ticker)
# ======================================================================

class QuantApp:
    """Construye la base de datos histórica y analiza activos individuales."""

    def __init__(self, sectores: dict = SECTORES):
        self.sectores = sectores
        self.db_file  = "mi_universo_quant.csv"
        self.db       = None

    def build_database(self, periodo: str = "5y"):
        print(f"🏗️  Construyendo base de datos ({len(self.sectores)} activos)...")
        tickers = list(self.sectores.keys())
        raw     = yf.download(
            tickers, period=periodo,
            progress=False, group_by="ticker", auto_adjust=True
        )
        spy = yf.download("SPY", period=periodo, progress=False, auto_adjust=True)
        spy = _normalizar_columnas(spy)
        spy['Mercado_Sano'] = spy['Close'] > spy['Close'].rolling(200).mean()

        all_data = []
        for ticker in tickers:
            try:
                df = raw[ticker].dropna() if len(tickers) > 1 else raw.dropna()
                df = _normalizar_columnas(df)
                if len(df) < 200:
                    continue
                df = calcular_indicadores(df)
                df['Ticker'] = ticker
                df['Sector'] = self.sectores.get(ticker, "Otros")
                df = df.join(spy[['Mercado_Sano']], how='inner')
                all_data.append(df.dropna(subset=['SMA_200','RSI','ATR','RV']))
            except Exception:
                continue

        self.db = pd.concat(all_data)
        self.db.to_csv(self.db_file)
        print(f"✅ Base de datos: {len(self.db):,} registros guardados.")

    def analyze_ticker(self, ticker: str,
                        p: dict = PARAMETROS):
        if self.db is None:
            try:
                self.db = pd.read_csv(
                    self.db_file, index_col='Date', parse_dates=True
                )
            except FileNotFoundError:
                print("⚠️  Ejecuta build_database() primero.")
                return

        df_t = self.db[self.db['Ticker'] == ticker].copy()

        cond = (
            (df_t['RSI']   < p["RSI_ENTRADA"]) &
            (df_t['RV']    > p["RV_MIN"])       &
            (df_t['Close'] > df_t['Open'])       &
            (df_t['Close'] > df_t['SMA_200'])
        )
        signals = df_t[cond]

        if signals.empty:
            print(f"\n📊 {ticker}: Sin señales bajo criterios estrictos.")
            return

        wr      = (signals['Ret_10d'] > 0).mean() * 100
        ret     = signals['Ret_10d'].mean() * 100
        sharpe  = (signals['Ret_10d'].mean() /
                   signals['Ret_10d'].std()
                   if signals['Ret_10d'].std() > 0 else 0)

        print(f"\n📊 ADN DE {ticker}:")
        print(f"  Señales: {len(signals)} | "
              f"Win Rate: {wr:.1f}% | "
              f"Ret Prom: {ret:.2f}% | "
              f"Sharpe aprox: {sharpe:.2f}")

        import matplotlib.pyplot as plt
        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(15, 9), sharex=True,
            gridspec_kw={'height_ratios': [3, 1]}
        )
        ax1.plot(df_t.index, df_t['Close'],   color='black', alpha=0.6, label='Precio')
        ax1.plot(df_t.index, df_t['SMA_200'], color='blue',  linestyle='--', label='SMA 200')
        ax1.scatter(signals.index, signals['Close'],
                    color='green', s=100, label='Señal validada', zorder=5)
        ax1.set_title(f"Análisis Histórico: {ticker} | "
                      f"WR: {wr:.1f}% | Sharpe: {sharpe:.2f}", fontsize=14)
        ax1.legend()
        ax2.bar(df_t.index, df_t['RV'], color='gray', alpha=0.3)
        ax2.axhline(p["RV_MIN"], color='orange', label=f'RV >{p["RV_MIN"]}')
        ax2.set_ylabel("Volumen Relativo")
        ax2.legend()
        plt.tight_layout()
        plt.show()


# ======================================================================
# 18. REPORTE EN CONSOLA (para Colab)
# ======================================================================

def imprimir_reporte(df_señales: pd.DataFrame):
    if df_señales.empty:
        print("⏳ Sin señales que reportar.")
        return

    cols = [c for c in [
        "tipo","ticker","sector","precio","rsi","rv",
        "calidad_senal","win_rate_hist","rs_vs_sector",
        "accion","stop_loss","take_profit","rr_ratio",
        "pct_riesgo","unidades"
    ] if c in df_señales.columns]

    print("\n🔥 REPORTE QUANT ELITE v2.0:\n")
    print(tabulate(
        df_señales[cols],
        headers="keys", tablefmt="fancy_grid", showindex=False
    ))
