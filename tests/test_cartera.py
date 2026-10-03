# Pruebas offline de la cartera modelo: python -m pytest -q
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cartera as ca
from calendario import es_ultimo_dia_habil_mes
from test_estrategias import precios_sinteticos


@pytest.fixture
def hist():
    p = precios_sinteticos(400)
    p["QQQ"] = np.linspace(100, 300, len(p))    # B elige QQQ
    return p


def correr(hoy, hist, ruta, manual=False):
    enviados = []
    texto = ca.ejecutar(hoy, manual=manual, enviar=enviados.append,
                        precios_hist=hist, ruta=str(ruta))
    return texto, enviados


def precios_de(hist):
    return {t: float(hist[t].iloc[-1]) for t in hist.columns}


def test_calendario_ultimo_dia_habil():
    assert es_ultimo_dia_habil_mes(date(2026, 10, 30))        # viernes 30-oct
    assert not es_ultimo_dia_habil_mes(date(2026, 10, 29))
    assert es_ultimo_dia_habil_mes(date(2024, 3, 28))         # 29-mar-2024 fue Viernes Santo
    assert not es_ultimo_dia_habil_mes(date(2026, 10, 31))    # sábado


def test_inicio_crea_cartera_50_50(hist, tmp_path):
    ruta = tmp_path / "modelo.json"
    texto, enviados = correr(date(2026, 10, 7), hist, ruta)
    assert "INICIO" in texto and len(enviados) == 1
    m = ca.cargar_modelo(str(ruta))
    p = precios_de(hist)
    v = ca.valor(m, p)
    assert v == pytest.approx(2380, rel=0.002)                 # solo el spread
    assert m["unidades"]["SPY"] * p["SPY"] / v == pytest.approx(0.5, abs=0.01)
    assert m["unidades"]["QQQ"] * p["QQQ"] / v == pytest.approx(0.5, abs=0.01)
    assert m["efectivo"] >= 0


def test_rebalanceo_mensual_con_aporte_una_sola_vez(hist, tmp_path):
    ruta = tmp_path / "modelo.json"
    correr(date(2026, 10, 7), hist, ruta)
    # B cambia de QQQ a GLD antes de fin de mes
    hist2 = hist.copy()
    hist2["GLD"] = np.linspace(100, 500, len(hist2))
    texto, _ = correr(date(2026, 10, 30), hist2, ruta)
    assert "REBALANCEO MENSUAL" in texto
    assert "VENDER QQQ" in texto and "COMPRAR GLD" in texto
    m = ca.cargar_modelo(str(ruta))
    assert m["aportado"] == 2480
    assert "QQQ" not in m["unidades"]
    assert set(m["asignacion"]) == {"SPY", "GLD"}
    # Una segunda ejecución el mismo día no vuelve a aportar ni rebalancear
    texto2, _ = correr(date(2026, 10, 30), hist2, ruta)
    assert texto2 is not None and "RESUMEN SEMANAL" in texto2   # 30-oct es viernes
    assert ca.cargar_modelo(str(ruta))["aportado"] == 2480


def test_dia_normal_no_envia_nada(hist, tmp_path):
    ruta = tmp_path / "modelo.json"
    correr(date(2026, 10, 7), hist, ruta)
    texto, enviados = correr(date(2026, 10, 14), hist, ruta)   # miércoles
    assert texto is None and enviados == []


def test_resumen_manual_compara_con_spy(hist, tmp_path):
    ruta = tmp_path / "modelo.json"
    correr(date(2026, 10, 7), hist, ruta)
    texto, _ = correr(date(2026, 10, 14), hist, ruta, manual=True)
    assert "RESUMEN SEMANAL" in texto and "SPY con los mismos aportes" in texto


def test_aporte_no_cambia_el_rendimiento():
    m = ca.nuevo_modelo(date(2026, 1, 1), 100.0)
    p = {"SPY": 100.0}
    ca.rebalancear(m, {"SPY": 1.0}, p, date(2026, 1, 1), {**ca.CONFIG, "COSTE_PCT": 0.0})
    p2 = {"SPY": 110.0}
    nav_antes = ca.valor(m, p2) / m["participaciones"]
    ca.aportar(m, 100, p2)
    assert ca.valor(m, p2) / m["participaciones"] == pytest.approx(nav_antes)
    assert ca.valor_referencia(m, p2) == pytest.approx(ca.valor(m, p2), rel=1e-9)


def test_inicio_en_fin_de_mes_no_duplica_aporte(hist, tmp_path):
    ruta = tmp_path / "modelo.json"
    correr(date(2026, 10, 30), hist, ruta)                     # inicio el último día hábil
    texto, _ = correr(date(2026, 10, 30), hist, ruta)
    assert "REBALANCEO" not in texto
    assert ca.cargar_modelo(str(ruta))["aportado"] == 2380
