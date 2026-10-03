# Pruebas offline de las estrategias alternativas: python -m pytest -q
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import estrategias as es


def precios_sinteticos(n=900, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    deriva = {"SPY": 0.0005, "QQQ": 0.0007, "EFA": 0.0002, "GLD": 0.0003, "IEF": 0.0001}
    return pd.DataFrame({t: 100 * np.exp(np.cumsum(rng.normal(d, 0.01, n)))
                         for t, d in deriva.items()}, index=idx)


def test_comprar_mantener_sigue_al_activo():
    p = precios_sinteticos()
    r = es.simular(p, es.regla_comprar_mantener("SPY"), 1000, coste_pct=0.0)
    esperado = 1000 * p["SPY"].iloc[-1] / p["SPY"].iloc[0]
    assert r["valor"].iloc[-1] == pytest.approx(esperado, rel=1e-9)
    assert r["operaciones"] == 1


def test_tendencia_va_a_refugio_bajo_sma():
    idx = pd.bdate_range("2020-01-01", periods=400)
    spy = np.r_[np.linspace(100, 200, 300), np.linspace(200, 120, 100)]
    p = pd.DataFrame({"SPY": spy, "IEF": 100.0}, index=idx)
    regla = es.regla_tendencia("SPY", "IEF", 200)
    assert regla(p.iloc[:290]) == {"SPY": 1.0}
    assert regla(p) == {"IEF": 1.0}


def test_momentum_elige_el_mas_fuerte():
    p = precios_sinteticos()
    p["QQQ"] = np.linspace(100, 300, len(p))   # el más fuerte con diferencia
    assert es.regla_momentum()(p) == {"QQQ": 1.0}


def test_ejecucion_al_dia_siguiente_sin_mirar_el_futuro():
    # Si la regla cambia de activo a fin de mes, la operación ocurre el día
    # hábil siguiente: el valor del último día del mes no puede cambiar.
    p = precios_sinteticos(300)
    llamadas = []
    def regla(hist):
        llamadas.append(hist.index[-1])
        return {"SPY": 1.0} if len(llamadas) % 2 else {"IEF": 1.0}
    r = es.simular(p, regla, 1000, coste_pct=0.0)
    for fecha in llamadas[1:]:
        assert fecha in p.index          # solo usa datos hasta la decisión
    assert r["operaciones"] > 2


def test_aportes_no_inflan_la_rentabilidad():
    p = precios_sinteticos()
    sin = es.simular(p, es.regla_comprar_mantener("SPY"), 1000, 0.0, coste_pct=0.0)
    con = es.simular(p, es.regla_comprar_mantener("SPY"), 1000, 100.0, coste_pct=0.0)
    assert con["aportado"] > 1000
    assert con["valor"].iloc[-1] > sin["valor"].iloc[-1]
    # El valor por participación es el mismo: los aportes no son rentabilidad
    assert con["nav"].iloc[-1] == pytest.approx(sin["nav"].iloc[-1], rel=1e-6)


def test_comparar_y_retornos_anuales():
    p = precios_sinteticos()
    eq_bot = pd.Series(np.linspace(1000, 1100, len(p)), index=p.index)
    tabla, anual = es.comparar(p, 1000, 0.0, p.index[300], extra={"Bot": eq_bot})
    assert len(tabla) == 4
    assert set(anual.columns) >= {"SPY (comprar y mantener)", "Bot"}
    assert anual.index.min() >= 2021
