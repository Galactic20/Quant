# Pruebas offline del backtest fundamental (datos sintéticos con formato SimFin)
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest_fundamental as bf
from backtest_fundamental import COL

TICKERS = {"AAA": "Technology", "BBB": "Healthcare", "CCC": "Industrials",
           "DDD": "Consumer Defensive", "EEE": "Energy", "FFF": "Technology",
           "GGG": "Financial Services", "HHH": "Basic Materials"}


def datos_sinteticos(seed=0, n_dias=1600, trampa=False):
    rng = np.random.default_rng(seed)
    fechas = pd.bdate_range("2018-01-01", periods=n_dias)
    adj = pd.DataFrame({t: 50 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, n_dias)))
                        for t in TICKERS}, index=fechas)
    filas_t, filas_b = [], []
    for i, t in enumerate(TICKERS):
        calidad = 1 + i * 0.3
        for q, rep in enumerate(pd.date_range("2016-03-31", fechas[-1], freq="QE")):
            pub = rep + pd.Timedelta(days=45)
            rev = 4e10 * (1 + 0.02 * q)
            ni = rev * 0.05 * calidad
            filas_t.append({COL["ticker"]: t, COL["report"]: rep, COL["publish"]: pub,
                            COL["revenue"]: rev, COL["op_income"]: ni * 1.3, COL["net_income"]: ni,
                            COL["cfo"]: ni * 1.2, COL["capex"]: -ni * 0.2})
            filas_b.append({COL["ticker"]: t, COL["report"]: rep, COL["publish"]: pub,
                            COL["equity"]: rev * 0.5, COL["st_debt"]: rev * 0.05,
                            COL["lt_debt"]: rev * 0.2})
    ttm = pd.DataFrame(filas_t)
    if trampa:
        # Reporte espectacular de AAA publicado MUY tarde: no debe usarse antes
        idx = ttm[(ttm[COL["ticker"]] == "AAA")].index[-8]
        ttm.loc[idx, COL["net_income"]] *= 50
        ttm.loc[idx, COL["publish"]] = fechas[-1] + pd.Timedelta(days=30)
    k = [COL["ticker"], COL["report"]]
    revs = bf.fechas_revision(adj.index, adj.index[0])
    cap = {f: pd.Series({t: 2e11 for t in TICKERS}) for f in revs}
    return {"ttm": ttm.sort_values(k), "bal": pd.DataFrame(filas_b).sort_values(k),
            "adj": adj, "cap": cap, "sectores": pd.Series(TICKERS)}


def test_punto_en_el_tiempo_ignora_reportes_aun_no_publicados():
    d = datos_sinteticos(trampa=True)
    fecha = bf.fechas_revision(d["adj"].index, "2021-01-01")[2]
    tabla = bf.fundamentales_en(fecha, d["ttm"], d["bal"], d["cap"][fecha],
                                d["adj"].loc[fecha], d["sectores"])
    # El ROE de AAA debe ser el normal, no el del reporte inflado futuro
    assert tabla.at["AAA", "returnOnEquity"] < 0.5
    sin_trampa = datos_sinteticos(trampa=False)
    tabla2 = bf.fundamentales_en(fecha, sin_trampa["ttm"], sin_trampa["bal"],
                                 sin_trampa["cap"][fecha], sin_trampa["adj"].loc[fecha],
                                 sin_trampa["sectores"])
    assert tabla.at["AAA", "returnOnEquity"] == pytest.approx(tabla2.at["AAA", "returnOnEquity"])


def test_excluye_financieras_y_calcula_historial_fcf():
    d = datos_sinteticos()
    fecha = bf.fechas_revision(d["adj"].index, "2021-01-01")[0]
    tabla = bf.fundamentales_en(fecha, d["ttm"], d["bal"], d["cap"][fecha],
                                d["adj"].loc[fecha], d["sectores"])
    assert "GGG" not in tabla.index
    assert all(len(h) == 4 for h in tabla["fcf_hist"])
    assert np.allclose(tabla["debtToEquity"], 50.0)


def test_simulacion_completa_y_consistente():
    d = datos_sinteticos()
    r = bf.simular(d, "2021-01-01", n=5, max_sector=1, stops=True, capital=1000)
    eq = r["equity"]
    assert len(eq) > 200 and eq.iloc[0] == pytest.approx(1000, rel=0.02)
    assert r["calificadas_prom"] >= 5
    assert len(r["cartera_final"]) <= 5
    sectores = [d["sectores"][t] for t in r["cartera_final"]]
    assert len(set(sectores)) == len(sectores)          # 1 por sector


def test_stop_loss_se_ejecuta_en_caida():
    d = datos_sinteticos()
    revs = bf.fechas_revision(d["adj"].index, "2021-01-01")
    caida = d["adj"].index.get_loc(revs[0]) + 10
    d["adj"].iloc[caida:] = d["adj"].iloc[caida:] * 0.4          # desplome general
    r = bf.simular(d, "2021-01-01", n=5, max_sector=1, stops=True)
    assert (r["operaciones"]["motivo"] == "STOP_LOSS").sum() >= 3
    sin = bf.simular(d, "2021-01-01", n=5, max_sector=1, stops=False)
    assert len(sin["operaciones"]) == 0 or (sin["operaciones"]["motivo"] != "STOP_LOSS").all()


def test_tolera_tickers_duplicados_de_simfin():
    d = datos_sinteticos()
    d["sectores"] = pd.concat([d["sectores"], pd.Series({"AAA": "Technology"})])
    fecha = bf.fechas_revision(d["adj"].index, "2021-01-01")[0]
    cap = pd.concat([d["cap"][fecha], pd.Series({"AAA": 1e11})])
    tabla = bf.fundamentales_en(fecha, d["ttm"], d["bal"], cap, d["adj"].loc[fecha], d["sectores"])
    assert "AAA" in tabla.index and not tabla.index.duplicated().any()
