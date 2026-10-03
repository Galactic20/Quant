# -*- coding: utf-8 -*-
"""Calendario de la bolsa de Nueva York (NYSE): feriados y días hábiles."""

from datetime import date, timedelta


# ======================================================================
# CALENDARIO NYSE
# ======================================================================

def _domingo_pascua(anio: int) -> date:
    # Algoritmo gregoriano anonimo (Meeus/Jones/Butcher)
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = (h + l - 7 * m + 114) % 31 + 1
    return date(anio, mes, dia)

def _n_esimo_dia(anio: int, mes: int, weekday: int, n: int) -> date:
    # n-esimo weekday (0=lunes) del mes; n=-1 -> ultimo
    if n > 0:
        d = date(anio, mes, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(anio, mes + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)

def _observado(d: date) -> date:
    # Sabado -> viernes anterior, domingo -> lunes siguiente
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d

def feriados_nyse(anio: int) -> set:
    feriados = {
        _n_esimo_dia(anio, 1, 0, 3),                  # Martin Luther King
        _n_esimo_dia(anio, 2, 0, 3),                  # Presidents Day
        _domingo_pascua(anio) - timedelta(days=2),    # Viernes Santo
        _n_esimo_dia(anio, 5, 0, -1),                 # Memorial Day
        _observado(date(anio, 7, 4)),                 # Independencia
        _n_esimo_dia(anio, 9, 0, 1),                  # Labor Day
        _n_esimo_dia(anio, 11, 3, 4),                 # Thanksgiving
        _observado(date(anio, 12, 25)),               # Navidad
    }
    # Año Nuevo: si cae sabado, NYSE no lo traslada al viernes 31 de dic.
    anio_nuevo = date(anio, 1, 1)
    if anio_nuevo.weekday() != 5:
        feriados.add(_observado(anio_nuevo))
    if anio >= 2022:
        feriados.add(_observado(date(anio, 6, 19)))   # Juneteenth
    return feriados

def es_dia_habil_nyse(d: date) -> bool:
    return d.weekday() < 5 and d not in feriados_nyse(d.year)

def siguiente_dia_habil(d: date) -> date:
    d += timedelta(days=1)
    while not es_dia_habil_nyse(d):
        d += timedelta(days=1)
    return d

def es_ultimo_dia_habil_mes(d: date) -> bool:
    """True si `d` es hábil y el siguiente día hábil cae en otro mes."""
    return es_dia_habil_nyse(d) and siguiente_dia_habil(d).month != d.month
