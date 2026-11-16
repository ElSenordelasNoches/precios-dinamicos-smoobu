"""Festivos y periodos de demanda que se pueden calcular sin consultar nada.

Solo entra aquí lo que es seguro por calendario (Navidad, Semana Santa y los
puentes de los festivos nacionales y del Día de Canarias). Las fiestas locales
de fecha variable (carnaval, fiestas patronales, eventos deportivos) las añade
la revisión periódica en ``datos/eventos.json`` después de comprobar las fechas.
"""
from __future__ import annotations

import datetime as dt

# (mes, día, nombre)
FESTIVOS_FIJOS = [
    (1, 1, "Año Nuevo"), (1, 6, "Reyes"), (5, 1, "Día del Trabajo"),
    (5, 30, "Día de Canarias"), (8, 15, "Asunción"), (10, 12, "Fiesta Nacional"),
    (11, 1, "Todos los Santos"), (12, 6, "Constitución"), (12, 8, "Inmaculada"),
    (12, 25, "Navidad"),
]


def pascua(anio: int) -> dt.date:
    """Domingo de Resurrección (algoritmo gregoriano anónimo)."""
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return dt.date(anio, mes, dia + 1)


def eventos_automaticos(desde: dt.date, hasta: dt.date) -> list[dict]:
    """Eventos {nombre, desde, hasta, ajuste_pct} entre dos fechas (ambas incluidas)."""
    ev = []
    for anio in range(desde.year - 1, hasta.year + 1):
        ev.append({"nombre": "Navidad", "desde": dt.date(anio, 12, 20),
                   "hasta": dt.date(anio + 1, 1, 6), "ajuste_pct": 10})
        ev.append({"nombre": "Fin de año", "desde": dt.date(anio, 12, 28),
                   "hasta": dt.date(anio + 1, 1, 1), "ajuste_pct": 8})
    for anio in range(desde.year, hasta.year + 1):
        p = pascua(anio)
        ev.append({"nombre": "Semana Santa", "desde": p - dt.timedelta(days=9),
                   "hasta": p + dt.timedelta(days=1), "ajuste_pct": 10})
        for mes, dia, nombre in FESTIVOS_FIJOS:
            if (mes, dia) in ((1, 1), (1, 6), (12, 25)):
                continue  # ya cubiertos por Navidad
            f = dt.date(anio, mes, dia)
            puente = _puente(f)
            if puente:
                ev.append({"nombre": f"Puente de {nombre}", "desde": puente[0],
                           "hasta": puente[1], "ajuste_pct": 5})
    # El puente de diciembre junta dos festivos: se trata como uno solo.
    out = [e for e in ev if e["hasta"] >= desde and e["desde"] <= hasta]
    return out


def _puente(f: dt.date):
    """Noches del fin de semana largo que forma un festivo, o None si cae mal."""
    d = f.weekday()  # 0 = lunes
    un = dt.timedelta(days=1)
    if d == 0:   # lunes: noches de viernes a domingo
        return f - 3 * un, f - un
    if d == 1:   # martes: puente con el lunes
        return f - 4 * un, f - un
    if d == 3:   # jueves: puente con el viernes
        return f - un, f + 2 * un
    if d == 4:   # viernes: noches de jueves a sábado
        return f - un, f + un
    return None


def leer_fecha(v) -> dt.date:
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])
