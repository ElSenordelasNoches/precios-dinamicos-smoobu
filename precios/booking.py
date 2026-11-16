"""Señal de demanda de Booking.com a través de RapidAPI (API «Booking» de apidojo).

Booking no deja leer su web con programas, así que se usa una API de pago por
uso con plan gratuito (500 consultas al mes). Con UNA consulta por ventana de
fechas se obtiene lo que hace falta:

* ``count``: alojamientos del destino con disponibilidad para esas fechas;
* ``unfiltered_count``: alojamientos del destino en total;
* una muestra de precios de los primeros resultados.

De ahí sale la ocupación del destino en Booking: 1 - disponibles / total.

El presupuesto es corto, de modo que cada rastreo consulta solo una parte de la
rejilla (primero las fechas cercanas) y se detiene si queda poco saldo.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
import urllib.error
import urllib.parse
import urllib.request

from . import config

HOST = "apidojo-booking-v1.p.rapidapi.com"
# Tipos de alojamiento comparables con un apartamento vacacional.
TIPOS_COMPARABLES = {201, 213, 219, 220, 222, 223, 228, 229, 232}


class ErrorBooking(RuntimeError):
    pass


def credenciales() -> dict:
    try:
        r = json.loads(config.RUTA_SECRETOS.read_text(encoding="utf-8")).get("rapidapi") or {}
    except (OSError, ValueError):
        return {}
    return r if r.get("clave") else {}


def _pedir(cred: dict, ruta: str, **consulta):
    host = cred.get("host") or HOST
    req = urllib.request.Request(f"https://{host}/{ruta}?" + urllib.parse.urlencode(consulta),
                                 headers={"X-RapidAPI-Key": cred["clave"], "X-RapidAPI-Host": host})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            restantes = r.headers.get("X-RateLimit-requests-Remaining")
            return json.loads(r.read().decode("utf-8")), (int(restantes) if restantes and restantes.isdigit() else None)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise ErrorBooking("saldo mensual de consultas agotado") from e
        if e.code in (401, 403):
            raise ErrorBooking("la clave de RapidAPI no vale o no está suscrita a la API de Booking") from e
        raise ErrorBooking(f"respuesta {e.code}") from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise ErrorBooking(f"sin respuesta ({e})") from e


def buscar_destino(texto: str, idioma: str = "es") -> list[dict]:
    """Destinos que casan con un nombre (cuesta una consulta)."""
    cred = credenciales()
    if not cred:
        raise ErrorBooking("falta la clave de RapidAPI en secretos.json")
    datos, _ = _pedir(cred, "locations/auto-complete", text=texto, languagecode=idioma)
    return [{"dest_id": x.get("dest_id"), "tipo": x.get("dest_type"), "nombre": x.get("label"),
             "alojamientos": x.get("nr_hotels")} for x in datos if x.get("dest_type") in ("city", "region", "district")]


def consultar_ventana(cred: dict, destino: dict, llegada: dt.date, noches: int, adultos: int = 2):
    datos, restantes = _pedir(
        cred, "properties/v2/list", offset=0, arrival_date=llegada.isoformat(),
        departure_date=(llegada + dt.timedelta(days=noches)).isoformat(), guest_qty=adultos,
        dest_ids=destino["dest_id"], room_qty=1, search_type=destino.get("tipo", "city"),
        price_filter_currencycode="EUR", languagecode="es", units="metric")
    disponibles, total = datos.get("count"), datos.get("unfiltered_count")
    if not isinstance(disponibles, int) or not isinstance(total, int) or total <= 0:
        raise ErrorBooking("la respuesta no trae los recuentos esperados")
    enteros = None
    for f in datos.get("base_filters") or []:
        for c in f.get("categories") or []:
            if c.get("id") == "privacy_type::3":
                enteros = c.get("count")
    precios = []
    for h in datos.get("result") or []:
        if h.get("hotel_id") and h.get("accommodation_type") in TIPOS_COMPARABLES:
            bruto = ((h.get("composite_price_breakdown") or {}).get("all_inclusive_amount") or {}).get("value") \
                or h.get("min_total_price")
            if bruto:
                precios.append(float(bruto) / noches)
    return {"llegada": llegada.isoformat(), "noches": noches, "disponibles": disponibles,
            "total": total, "enteros": enteros, "muestra": len(precios),
            "mediana": round(statistics.median(precios), 1) if len(precios) >= 4 else None}, restantes


def elegir_fechas(fechas: list[dt.date], hoy: dt.date, cuantas: int) -> list[dt.date]:
    """Las fechas cercanas, todas; el resto del cupo, repartido por el año."""
    cerca = [f for f in fechas if (f - hoy).days <= 62]
    lejos = [f for f in fechas if (f - hoy).days > 62]
    if len(cerca) >= cuantas:
        return cerca[:cuantas]
    hueco = cuantas - len(cerca)
    if hueco >= len(lejos):
        return cerca + lejos
    paso = len(lejos) / hueco
    return cerca + [lejos[int(i * paso)] for i in range(hueco)]


def escanear(cfg: dict, fechas: list[dt.date], hoy: dt.date, noches: int, avisar=print) -> dict | None:
    """Consulta Booking para parte de la rejilla. None si no está configurado."""
    cfg_b = (cfg.get("mercado") or {}).get("booking") or {}
    destino = cfg_b.get("destino") or {}
    cred = credenciales()
    if not (cfg_b.get("activa", True) and cred and destino.get("dest_id")):
        return None
    reserva = int(cfg_b.get("reserva_consultas", 5))
    ventanas, restantes, error = [], None, None
    for llegada in elegir_fechas(fechas, hoy, int(cfg_b.get("consultas_por_rastreo", 28))):
        if restantes is not None and restantes <= reserva:
            error = f"quedan {restantes} consultas este mes: se para aquí"
            break
        try:
            v, restantes = consultar_ventana(cred, destino, llegada, noches, int(cfg["mercado"].get("adultos", 2)))
        except ErrorBooking as e:
            error = str(e)
            avisar(f"  Booking {llegada}: {e}")
            if "saldo" in error or "clave" in error:
                break
            continue
        ventanas.append(v)
        avisar(f"  Booking {llegada}: {v['disponibles']} de {v['total']} disponibles")
    if not ventanas:
        return {"ventanas": [], "error": error, "restantes": restantes} if error else None
    return {"dia": hoy.isoformat(), "ventanas": ventanas, "restantes": restantes, "error": error}
