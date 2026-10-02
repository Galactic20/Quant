# Pruebas offline (sin red): python -m pytest -q
import json
import os
import sys
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import quant_core as qc
import bot_telegram as bot


def serie_precios(n=300, inicio=100.0, paso=0.2, ultimo=None):
    idx = pd.bdate_range(end="2026-09-30", periods=n)
    close = inicio + paso * np.arange(n, dtype=float)
    if ultimo is not None:
        close[-1] = ultimo
    df = pd.DataFrame({
        "Open":   close - 0.1,
        "High":   close + 0.5,
        "Low":    close - 0.5,
        "Close":  close,
        "Volume": np.full(n, 1_000_000.0),
    }, index=idx)
    return qc.calcular_indicadores(df)


@pytest.fixture
def posiciones_tmp(tmp_path, monkeypatch):
    archivo = tmp_path / "posiciones.json"
    monkeypatch.setattr(qc, "POSICIONES_FILE", str(archivo))
    def escribir(data):
        archivo.write_text(json.dumps(data))
    return escribir


# ── Posiciones ─────────────────────────────────────────────────────────

def test_posiciones_formatos_mixtos(posiciones_tmp):
    posiciones_tmp({"pld": 2.0, "MSFT": {"unidades": 3, "precio_entrada": 400,
                                         "stop_loss": 380}, "X": 0})
    det = qc.cargar_posiciones_detalle()
    assert set(det) == {"PLD", "MSFT"}
    assert det["MSFT"]["stop_loss"] == 380
    assert qc.cargar_posiciones() == {"PLD": 2.0, "MSFT": 3.0}


def test_posiciones_vacias_o_invalidas(posiciones_tmp):
    posiciones_tmp([])
    assert qc.cargar_posiciones() == {}


def test_abrir_posicion_detallada(posiciones_tmp):
    posiciones_tmp({"PLD": 2})
    qc.abrir_posicion("MSFT", 3, 400.0, 380.0, 450.0)
    det = qc.cargar_posiciones_detalle()
    assert det["PLD"]["unidades"] == 2
    assert det["MSFT"]["take_profit"] == 450.0


# ── Señales de salida ──────────────────────────────────────────────────

def test_stop_loss_fijo_tiene_prioridad():
    df = serie_precios()
    precio = float(df["Close"].iloc[-1])
    pos = {"AAPL": {"unidades": 5, "precio_entrada": precio + 20,
                    "stop_loss": precio + 1, "take_profit": None}}
    s = qc.motor_quant("AAPL", df, "TENDENCIA_ALCISTA", posiciones=pos)
    assert s["tipo"] == "STOP_LOSS"
    assert s["pnl_pct"] < 0
    msg = qc.formatear_alerta(s)
    assert "STOP LOSS" in msg and "PnL" in msg


def test_take_profit():
    df = serie_precios()
    precio = float(df["Close"].iloc[-1])
    pos = {"AAPL": {"unidades": 5, "precio_entrada": precio - 20,
                    "stop_loss": None, "take_profit": precio - 1}}
    s = qc.motor_quant("AAPL", df, "TENDENCIA_ALCISTA", posiciones=pos)
    assert s["tipo"] == "TAKE_PROFIT"


def test_mantener_formato_simple():
    df = serie_precios(paso=0.0)
    pos = {"AAPL": {"unidades": 5, "precio_entrada": None,
                    "stop_loss": None, "take_profit": None}}
    s = qc.motor_quant("AAPL", df, "TENDENCIA_ALCISTA", posiciones=pos)
    assert s["tipo"] == "MANTENER"
    assert "pnl_pct" not in s


def test_venta_urgente_bajo_sma200():
    df = serie_precios(ultimo=50.0)
    pos = {"AAPL": {"unidades": 1, "precio_entrada": 120.0,
                    "stop_loss": None, "take_profit": None}}
    s = qc.motor_quant("AAPL", df, "TENDENCIA_ALCISTA", posiciones=pos)
    assert s["tipo"] == "VENTA_URGENTE"


# ── Escáner: las posiciones abiertas siempre se vigilan ─────────────────

def test_escaner_incluye_posiciones_fuera_del_elite(posiciones_tmp, monkeypatch):
    posiciones_tmp({"ZZZZ": 3})
    monkeypatch.setattr(qc, "detectar_regimen",
                        lambda p=qc.PARAMETROS: ("TENDENCIA_ALCISTA",
                                                 {"emoji": "", "descripcion": "",
                                                  "accion": ""}))
    monkeypatch.setattr(qc, "obtener_universo_elite", lambda db, p=None: ["AAPL"])
    datos = {"AAPL": serie_precios(paso=0.0), "ZZZZ": serie_precios(paso=0.0)}
    df = qc.escanear_universo(datos_globales=datos)
    assert "ZZZZ" in set(df["ticker"])
    assert df.set_index("ticker").loc["ZZZZ", "tipo"] == "MANTENER"


def test_regimen_sin_datos_es_conservador(monkeypatch):
    def falla(*a, **k):
        raise ConnectionError("sin red")
    monkeypatch.setattr(qc.yf, "download", falla)
    regimen, ctx = qc.detectar_regimen()
    assert regimen == "RECUPERACION"
    assert not qc.es_senal_compra(pd.Series({"RSI": 42, "RV": 1.5}), regimen)


# ── Calendario y modo ──────────────────────────────────────────────────

@pytest.mark.parametrize("d", [
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 4, 3), date(2026, 5, 25),
    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26),
    date(2026, 12, 25), date(2027, 3, 26), date(2027, 6, 18), date(2027, 12, 24),
])
def test_feriados_nyse(d):
    assert not bot.es_dia_habil_nyse(d)


def test_dias_habiles():
    assert bot.es_dia_habil_nyse(date(2026, 10, 2))       # viernes normal
    assert not bot.es_dia_habil_nyse(date(2026, 10, 3))   # sabado
    assert bot.es_dia_habil_nyse(date(2027, 12, 31))      # 1-ene-2028 es sabado


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_modo_por_cron_en_verano():
    t = utc(2026, 10, 2, 12, 45)   # EDT
    assert bot.resolver_modo(t, "0 12 * * 1-5", "") == "APERTURA"
    assert bot.resolver_modo(t, "0 13 * * 1-5", "") is None
    t = utc(2026, 10, 2, 19, 55)
    assert bot.resolver_modo(t, "10 19 * * 1-5", "") == "CIERRE"
    assert bot.resolver_modo(t, "10 20 * * 1-5", "") is None


def test_modo_por_cron_en_invierno():
    t = utc(2026, 12, 2, 13, 45)   # EST
    assert bot.resolver_modo(t, "0 12 * * 1-5", "") is None
    assert bot.resolver_modo(t, "0 13 * * 1-5", "") == "APERTURA"
    assert bot.resolver_modo(utc(2026, 12, 2, 20, 55), "10 20 * * 1-5", "") == "CIERRE"


def test_cron_en_feriado_se_omite():
    assert bot.resolver_modo(utc(2026, 11, 26, 19, 55), "10 20 * * 1-5", "") is None


def test_modo_manual_y_local():
    t = utc(2026, 11, 26, 13, 0)   # feriado, pero manual siempre corre
    assert bot.resolver_modo(t, "", "apertura") == "APERTURA"
    assert bot.resolver_modo(utc(2026, 10, 2, 13, 0), "", "") == "APERTURA"
    assert bot.resolver_modo(utc(2026, 10, 2, 19, 0), "", "") == "CIERRE"


def test_resumen_cartera_cierre():
    df = pd.DataFrame([
        {"tipo": "MANTENER", "ticker": "PLD", "precio": 130.0, "rsi": 55.0,
         "chandelier": 120.0, "precio_entrada": 125.0, "pnl_pct": 4.0, "pnl_usd": 10.0},
        {"tipo": "COMPRA", "ticker": "AAPL", "precio": 200.0, "rsi": 40.0,
         "chandelier": 190.0, "precio_entrada": np.nan, "pnl_pct": np.nan, "pnl_usd": np.nan},
    ])
    txt = bot.resumen_cartera_cierre(df, {"PLD": {}, "MSFT": {}})
    assert "PLD" in txt and "+4.00%" in txt
    assert "MSFT" in txt and "sin datos" in txt
    assert "AAPL" not in txt
