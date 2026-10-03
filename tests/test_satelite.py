# Pruebas offline del filtro fundamental y la cartera satélite: python -m pytest -q
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fundamental as fu
import satelite as sa

UNIVERSO = ["AAPL", "MSFT", "GOOG", "NVDA", "AMD", "AVGO", "KO", "PEP", "JNJ",
            "LLY", "CAT", "HON", "XOM", "CVX", "HD", "MCD", "COST", "WMT"]


def fundamentales(seed=0, **cambios):
    rng = np.random.default_rng(seed)
    filas = []
    for t in UNIVERSO:
        cap = rng.uniform(50e9, 3e12)
        rev = cap * rng.uniform(0.1, 0.5)
        fila = {"ticker": t, "shortName": t, "marketCap": cap,
                "currentPrice": rng.uniform(50, 800),
                "returnOnEquity": rng.uniform(0.12, 0.6),
                "operatingMargins": rng.uniform(0.1, 0.45),
                "freeCashflow": rev * rng.uniform(0.05, 0.3), "totalRevenue": rev,
                "debtToEquity": rng.uniform(10, 180), "trailingPE": rng.uniform(10, 50),
                "forwardPE": rng.uniform(10, 40), "revenueGrowth": rng.uniform(-0.05, 0.3)}
        fila.update(cambios.get(t, {}))
        filas.append(fila)
    return pd.DataFrame(filas).set_index("ticker")


def precios(df):
    return {**{t: float(p) for t, p in df["currentPrice"].items()}, "SPY": 700.0}


# ── Filtro fundamental ─────────────────────────────────────────────────

def test_filtros_descartan_y_explican():
    df = fundamentales(**{
        "AAPL": {"freeCashflow": -1e9}, "MSFT": {"returnOnEquity": 0.05},
        "GOOG": {"marketCap": 5e9}, "NVDA": {"debtToEquity": 350},
        "AMD": {"trailingPE": -10, "forwardPE": -5},
        "COST": {"fcf_hist": [5e9, -1e9, 4e9]}, "KO": {"financialCurrency": "BRL"}})
    r = fu.puntuar(df)
    descartadas = r.attrs["descartadas"]
    assert {"AAPL", "MSFT", "GOOG", "NVDA", "AMD", "COST", "KO"} <= set(descartadas)
    assert "FCF negativo" in descartadas["AAPL"]
    assert "FCF negativo" in descartadas["COST"]          # un año negativo en el historial
    assert "otra moneda" in descartadas["KO"]
    assert "deuda alta" in descartadas["NVDA"]
    assert not set(descartadas) & set(r.index)
    assert r["puntaje"].is_monotonic_decreasing


def test_empresa_dominante_queda_primera():
    df = fundamentales(KO={"returnOnEquity": 0.9, "operatingMargins": 0.6,
                           "freeCashflow": 4e11, "totalRevenue": 5e11, "marketCap": 1e12,
                           "debtToEquity": 1, "forwardPE": 8, "revenueGrowth": 0.5})
    assert fu.puntuar(df).index[0] == "KO"


def test_seleccionar_limite_por_sector_y_buffer():
    r = fu.puntuar(fundamentales())
    elegidas = fu.seleccionar(r, 5, max_por_sector=1)
    sectores = [r.at[t, "sector"] for t in elegidas]
    assert len(elegidas) == 5 and len(set(sectores)) == 5
    # Una acción que ya tienes se mantiene si sigue en el top del buffer
    actual = r.index[8]
    assert actual in fu.seleccionar(r, 5, max_por_sector=5, actuales=[actual], buffer=10)
    assert actual not in fu.seleccionar(r, 5, max_por_sector=5, actuales=[actual], buffer=5)


# ── Cartera satélite ───────────────────────────────────────────────────

def correr(hoy, ruta, df=None, manual=False, resultados=None):
    enviados = []
    texto = sa.ejecutar(hoy, manual=manual, enviar=enviados.append,
                        fundamentales=df, precios=precios(df) if df is not None else None,
                        resultados=resultados, ruta=str(ruta))
    return texto, enviados


def test_inicio_compra_n_acciones_a_partes_iguales(tmp_path):
    ruta = tmp_path / "sat.json"
    df = fundamentales()
    texto, enviados = correr(date(2026, 10, 7), ruta, df)
    m = sa.cargar_modelo(str(ruta))
    assert len(m["unidades"]) == 5 and len(enviados) == 1
    assert "INICIO" in texto and "Por que" in texto
    p = precios(df)
    montos = [u * p[t] for t, u in m["unidades"].items()]
    assert max(montos) - min(montos) < 0.01
    # $1 de comisión por compra + spread
    assert sa.valor(m, p) == pytest.approx(1000 - 5 - 1000 * 0.0005, abs=0.05)
    assert set(m["tesis"]) == set(m["unidades"])


def test_revision_trimestral_vende_la_que_falla(tmp_path):
    ruta = tmp_path / "sat.json"
    df = fundamentales()
    correr(date(2026, 10, 7), ruta, df)
    m = sa.cargar_modelo(str(ruta))
    caida = sorted(m["unidades"])[0]
    df2 = fundamentales(**{caida: {"freeCashflow": -1e9}})
    texto, _ = correr(date(2026, 12, 31), ruta, df2)       # último día hábil de diciembre
    assert "REVISION TRIMESTRAL" in texto
    assert f"VENDER {caida}" in texto and "ya no cumple filtros" in texto
    m2 = sa.cargar_modelo(str(ruta))
    assert caida not in m2["unidades"] and len(m2["unidades"]) == 5
    # Repetir el mismo día no vuelve a operar
    texto2, _ = correr(date(2026, 12, 31), ruta, df2)
    assert texto2 is None or "REVISION" not in texto2


def test_fin_de_mes_no_trimestral_solo_reporta(tmp_path):
    ruta = tmp_path / "sat.json"
    df = fundamentales()
    correr(date(2026, 10, 7), ruta, df)
    antes = sa.cargar_modelo(str(ruta))["unidades"]
    texto, _ = correr(date(2026, 11, 30), ruta, df)        # noviembre: solo reporte
    assert "REPORTE MENSUAL" in texto
    assert sa.cargar_modelo(str(ruta))["unidades"] == antes


def test_resumen_semanal_y_dia_normal(tmp_path, monkeypatch):
    ruta = tmp_path / "sat.json"
    df = fundamentales()
    correr(date(2026, 10, 7), ruta, df)
    monkeypatch.setattr(fu, "obtener_fundamentales",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debe descargar")))
    held = list(sa.cargar_modelo(str(ruta))["unidades"])
    texto, _ = correr(date(2026, 10, 9), ruta, df, resultados={held[0]: "2026-10-28"})
    assert "RESUMEN SEMANAL" in texto and "2026-10-28" in texto and "vs SPY" in texto
    texto, enviados = correr(date(2026, 10, 14), ruta, df)  # miércoles normal
    assert texto is None and enviados == []


def test_fcf_promedio_suaviza_el_pico_ciclico():
    # Mismo FCF del último año, pero AMD tuvo años previos mucho peores:
    # su rendimiento de flujo libre promedio debe ser menor que el de CAT.
    base = {"marketCap": 1e11, "freeCashflow": 1e10, "totalRevenue": 5e10}
    df = fundamentales(AMD={**base, "fcf_hist": [1e10, 1e9, 5e8]},
                       CAT={**base, "fcf_hist": [1e10, 9e9, 9.5e9]})
    r = fu.puntuar(df)
    assert r.at["AMD", "fcf_yield"] < r.at["CAT", "fcf_yield"]


def test_universo_sin_duplicados_ni_excluidos():
    u = fu.universo_acciones()
    assert "GOOG" in u and "GOOGL" not in u
    assert not {"JPM", "PLD", "NEE", "QQQ", "BTC-USD"} & set(u)


# ── Stop loss / take profit ────────────────────────────────────────────

def test_niveles_segun_volatilidad_y_acotados():
    n = sa.niveles(100.0, 0.02)                 # 2 x 2% x sqrt(63) ≈ 31.7%
    assert n["stop_loss"] == pytest.approx(100 * (1 - 0.317), abs=0.1)
    assert n["take_profit"] == pytest.approx(100 * (1 + 2 * 0.317), abs=0.2)
    assert sa.niveles(100.0, 0.001)["distancia_pct"] == 15.0   # mínimo
    assert sa.niveles(100.0, 0.10)["distancia_pct"] == 35.0    # máximo


def test_compras_traen_stop_loss_y_take_profit(tmp_path):
    texto, _ = correr(date(2026, 10, 7), tmp_path / "sat.json", fundamentales())
    assert texto.count("SL $") == 5 and texto.count("TP $") == 5
    m = sa.cargar_modelo(str(tmp_path / "sat.json"))
    for t, te in m["tesis"].items():
        assert te["stop_loss"] < te["precio"] < te["take_profit"]


def test_stop_loss_tocado_vende_y_avisa(tmp_path):
    ruta = tmp_path / "sat.json"
    df = fundamentales()
    correr(date(2026, 10, 7), ruta, df)
    m = sa.cargar_modelo(str(ruta))
    t = sorted(m["unidades"])[0]
    p = precios(df)
    p[t] = m["tesis"][t]["stop_loss"] * 0.99
    enviados = []
    texto = sa.ejecutar(date(2026, 10, 14), enviar=enviados.append, precios=p, ruta=str(ruta))
    assert "NIVEL ALCANZADO" in texto and f"VENDER {t}" in texto and "STOP LOSS" in texto
    m2 = sa.cargar_modelo(str(ruta))
    assert t not in m2["unidades"] and m2["efectivo"] > 100


def test_posiciones_sin_niveles_reciben_niveles_una_vez(tmp_path):
    ruta = tmp_path / "sat.json"
    df = fundamentales()
    correr(date(2026, 10, 7), ruta, df)
    m = sa.cargar_modelo(str(ruta))
    for te in m["tesis"].values():          # como la cartera creada antes de esta versión
        te.pop("stop_loss"); te.pop("take_profit")
    sa.guardar_modelo(m, str(ruta))
    texto, _ = correr(date(2026, 10, 14), ruta, df)
    assert "Pon estos niveles en eToro" in texto
    texto2, _ = correr(date(2026, 10, 15), ruta, df)
    assert texto2 is None


def test_revision_solo_sube_el_stop():
    m = {"unidades": {"AAPL": 1.0}, "tesis": {"AAPL": {"stop_loss": 90.0, "take_profit": 150.0}}}
    assert sa.subir_stops(m, {"AAPL": 100.0}, {"AAPL": 0.01}) == []      # 100*(1-0.159)=84 < 90
    assert sa.subir_stops(m, {"AAPL": 130.0}, {"AAPL": 0.01}) == ["AAPL"]
    assert m["tesis"]["AAPL"]["stop_loss"] > 90


# ── Internacionales ────────────────────────────────────────────────────

def test_universo_incluye_internacionales():
    u = fu.universo_acciones()
    assert {"NVO", "SAP", "TM", "VALE", "FMX", "TSM"} <= set(u)
    assert fu.sector_de("NVO") == "Salud" and fu.pais_de("NVO") == "Dinamarca"
    assert fu.pais_de("AAPL") == "EE. UU."


def test_conversion_a_usd():
    fila = {"financialCurrency": "BRL", "freeCashflow": 10e9, "totalRevenue": 50e9,
            "netIncomeToCommon": 5e9, "marketCap": 4e9, "fcf_hist": [10e9, 8e9]}
    usd = fu.convertir_a_usd(fila, fx=lambda m: 0.2)
    assert usd["freeCashflow"] == pytest.approx(2e9)
    assert usd["fcf_hist"] == pytest.approx([2e9, 1.6e9])
    assert usd["trailingPE"] == pytest.approx(4e9 / 1e9)
    assert usd["financialCurrency"] == "USD" and usd["moneda_reporte"] == "BRL"
    # Sin tipo de cambio queda en moneda local y el filtro la descarta
    assert fu.convertir_a_usd(fila, fx=lambda m: None)["financialCurrency"] == "BRL"
