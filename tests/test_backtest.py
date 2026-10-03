# Pruebas offline del backtest: python -m pytest -q
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import quant_core as qc
import backtest as bt

P = qc.PARAMETROS


def ohlcv(n=500, seed=0, deriva=0.0006, vol=0.018, fin="2026-09-30"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=fin, periods=n)
    c = 100 * np.exp(np.cumsum(rng.normal(deriva, vol, n)))
    o = c * (1 + rng.normal(0, 0.006, n))
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.01, n))
    l = np.minimum(o, c) * (1 - rng.uniform(0, 0.01, n))
    v = rng.lognormal(14, 0.5, n)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)


# ── Equivalencia con las funciones del bot ──────────────────────────────

def test_serie_regimen_igual_a_clasificar():
    spy = ohlcv(600, seed=1)["Close"]
    vix = pd.Series(np.random.default_rng(2).uniform(10, 40, len(spy)), index=spy.index)
    reg = qc.serie_regimen(spy, vix)
    sma200, sma50 = spy.rolling(200).mean(), spy.rolling(50).mean()
    for i in range(200, len(spy)):
        esperado = qc.clasificar_regimen(spy.iloc[i], sma200.iloc[i],
                                         sma50.iloc[i], vix.iloc[i])
        assert reg.iloc[i] == esperado


@pytest.mark.parametrize("ticker", ["AAPL", "GOOG"])
def test_mascara_compra_igual_a_es_senal_compra(ticker):
    df = qc.calcular_indicadores(ohlcv(600, seed=3, deriva=0.001, vol=0.015))
    reg = pd.Series(np.random.default_rng(4).choice(
        ["TENDENCIA_ALCISTA", "RECUPERACION", "BAJISTA", "PANICO"], len(df)), index=df.index)
    mask = qc.mascara_compra(df, reg, P, ticker)
    for i in range(len(df)):
        assert mask.iloc[i] == qc.es_senal_compra(df.iloc[i], reg.iloc[i], P, ticker)
    assert mask.any()   # la prueba ejercita casos verdaderos


def test_mascaras_salida_iguales_a_funciones_fila():
    df = qc.calcular_indicadores(ohlcv(400, seed=5, vol=0.03)).dropna()
    urg, sd = qc.mascara_venta_urgente(df), qc.mascara_stop_dinamico(df)
    for i in range(len(df)):
        assert urg.iloc[i] == qc.es_senal_venta_urgente(df.iloc[i])
        assert sd.iloc[i] == qc.es_senal_stop_dinamico(df.iloc[i])


# ── Tamaño de posición ─────────────────────────────────────────────────

def test_fracciones_permiten_acciones_caras():
    # Antes: ATR 20 → dist SL 50 > riesgo $23.8 → 0 acciones y señal descartada
    r = qc.calcular_gestion_riesgo(600.0, 20.0, "NORMAL", 2380)
    assert 0 < r["unidades"] < 1
    assert r["riesgo_usd"] == pytest.approx(23.8, abs=0.01)
    r_ent = qc.calcular_gestion_riesgo(600.0, 20.0, "NORMAL", 2380,
                                       {**P, "FRACCIONES": False})
    assert r_ent["unidades"] == 0


def test_tope_y_minimo_de_inversion():
    r = qc.calcular_gestion_riesgo(100.0, 0.2, "ALTA", 2380)   # stop muy cercano
    assert r["inversion"] <= 2380 * P["MAX_INVERSION_PCT"] + 0.01
    r = qc.calcular_gestion_riesgo(100.0, 20.0, "DEBIL", 300)  # $1.5 de riesgo
    assert r["unidades"] == 0                                    # < $10 eToro


# ── Motor ──────────────────────────────────────────────────────────────

def test_backtest_respeta_stop_loss_y_costes(monkeypatch):
    raw = ohlcv(400, seed=11, deriva=0.0008, vol=0.012)
    df = qc.calcular_indicadores(raw)
    fecha = df.index[300]
    # Fuerza una señal de compra el día `fecha` y un desplome al día siguiente
    monkeypatch.setattr(qc, "mascara_compra",
                        lambda d, r, p=P, t="": pd.Series(d.index == fecha, index=d.index))
    i = df.index.get_loc(fecha)
    raw.iloc[i + 1, raw.columns.get_loc("Open")] = raw["Close"].iloc[i] * 0.5
    raw.iloc[i + 1, raw.columns.get_loc("Low")] = raw["Close"].iloc[i] * 0.45
    df = qc.calcular_indicadores(raw)
    spy = df["Close"]
    vix = pd.Series(15.0, index=df.index)
    res = bt.backtest({"AAPL": df}, spy, vix, capital=2380, tickers=["AAPL"],
                      inicio=df.index[250])
    ops = res["operaciones"]
    assert len(ops) == 1
    op = ops.iloc[0]
    assert op["motivo"] == "STOP_LOSS"
    # Gap bajo el stop: sale en la apertura, no en el nivel del stop
    assert op["precio_salida"] == pytest.approx(raw["Open"].iloc[i + 1], rel=1e-6)
    assert op["pnl"] < 0
    # El capital final cuadra con el PnL de la operación
    assert res["equity"].iloc[-1] == pytest.approx(2380 + op["pnl"], abs=0.01)


def test_backtest_limite_por_sector(monkeypatch):
    base = ohlcv(400, seed=13, deriva=0.0008, vol=0.012)
    datos = {t: qc.calcular_indicadores(base * (1 + k * 0.01))
             for k, t in enumerate(["NVDA", "AMD", "MSFT"])}
    fecha = datos["NVDA"].index[300]
    monkeypatch.setattr(qc, "mascara_compra",
                        lambda d, r, p=P, t="": pd.Series(d.index == fecha, index=d.index))
    spy = datos["NVDA"]["Close"]
    vix = pd.Series(15.0, index=spy.index)
    res = bt.backtest(datos, spy, vix, capital=2380, inicio=spy.index[250])
    ops = res["operaciones"]
    # NVDA y AMD son Semiconductores: solo una de las dos; MSFT es Big Tech
    sectores = ops[ops["fecha_entrada"] == fecha]["sector"].tolist()
    assert sorted(sectores) == ["Big Tech", "Semiconductores"]


def test_metricas_y_benchmark():
    s = pd.Series([100, 110, 99, 120], index=pd.bdate_range("2026-01-01", periods=4))
    m = bt.metricas(s)
    assert m["retorno_total_pct"] == 20.0
    assert m["max_drawdown_pct"] == -10.0
    b = bt.benchmark(s, 1000, bt={"COSTE_PCT": 0.0})
    assert b["metricas"]["capital_final"] == 1200.0


def test_estadisticas_operaciones():
    ops = pd.DataFrame({"pnl": [10.0, -5.0, 20.0, -5.0]})
    e = bt.estadisticas_operaciones(ops)
    assert e["win_rate_pct"] == 50.0
    assert e["profit_factor"] == 3.0
    assert e["esperanza_trade"] == 5.0


def test_walk_forward_corre():
    datos = {t: qc.calcular_indicadores(ohlcv(1300, seed=k, vol=0.02))
             for k, t in enumerate(["AAPL", "XOM", "JPM", "KO"])}
    spy = qc.calcular_indicadores(ohlcv(1300, seed=99))["Close"]
    vix = pd.Series(15.0, index=spy.index)
    wf = bt.walk_forward(datos, spy, vix, grid={"RSI_ENTRADA": [45, 55]},
                         años_entrenamiento=2, verbose=False)
    assert len(wf) >= 1
    assert {"actual_retorno_total_pct", "spy_retorno_total_pct"} <= set(wf.columns)


# ── Costes eToro ───────────────────────────────────────────────────────

def test_costes_por_tipo_de_activo():
    c = bt.BT_DEFAULTS
    assert bt.costes_lado("AAPL", c) == (c["COSTE_PCT"], 1.0)      # acción: $1 por lado
    assert bt.costes_lado("QQQ", c) == (c["COSTE_PCT"], 0.0)       # ETF: sin comisión
    assert bt.costes_lado("BTC-USD", c) == (c["COSTE_PCT_CRIPTO"], 0.0)


def test_operacion_de_accion_paga_comision_ida_y_vuelta(monkeypatch):
    df = qc.calcular_indicadores(ohlcv(400, seed=21, deriva=0.0, vol=0.0001))
    fecha = df.index[300]
    monkeypatch.setattr(qc, "mascara_compra",
                        lambda d, r, p=P, t="": pd.Series(d.index == fecha, index=d.index))
    spy, vix = df["Close"], pd.Series(15.0, index=df.index)
    sin_spread = {"COSTE_PCT": 0.0}
    res = bt.backtest({"AAPL": df}, spy, vix, capital=2380, tickers=["AAPL"],
                      inicio=df.index[250], bt=sin_spread)
    op = res["operaciones"].iloc[0]
    bruto = op["unidades"] * (op["precio_salida"] - op["precio_entrada"])
    assert op["pnl"] == pytest.approx(bruto - 2.0, abs=0.01)   # $1 al abrir + $1 al cerrar


def test_resumen_operaciones_reales_usa_pnl_neto():
    r = bt.resumen_operaciones_reales()
    assert {"bot", "pre_sistema", "total"} <= set(r.index)
    ops = bt.cargar_operaciones_reales()
    assert r.loc["total", "pnl_total"] == pytest.approx(ops["pnl_neto"].sum(), abs=0.01)
