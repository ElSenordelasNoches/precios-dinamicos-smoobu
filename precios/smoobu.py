"""Cliente mínimo de la API de Smoobu (https://docs.smoobu.com) con firma HMAC.

Solo usa la biblioteca estándar. Las claves antiguas («Api-Key») dejaron de
funcionar el 31/10/2026; aquí se firma cada petición con clave + secreto.
Conviene crear la clave SIN la casilla «Guest Data (PII)»: la herramienta no
necesita datos personales de los huéspedes y así Smoobu ni los envía.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = "https://login.smoobu.com"


class ErrorSmoobu(RuntimeError):
    pass


def _q(v: str) -> str:
    return urllib.parse.quote(str(v), safe="-._~")  # RFC 3986


class Smoobu:
    def __init__(self, api_key: str, api_secret: str, pausa: float = 0.15):
        if not (api_key and api_secret):
            raise ErrorSmoobu("faltan la clave o el secreto de la API de Smoobu")
        self.api_key, self.api_secret, self.pausa = api_key, api_secret, pausa

    # -- transporte ---------------------------------------------------------
    def _firmar(self, metodo: str, ruta: str, pares: list[tuple[str, str]], cuerpo: bytes) -> dict:
        marca = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        nonce = str(uuid.uuid4())
        consulta = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(pares))
        canonica = "\n".join([metodo, ruta, consulta, marca, nonce,
                              hashlib.sha256(cuerpo).hexdigest(), self.api_key])
        firma = base64.b64encode(hmac.new(self.api_secret.encode(), canonica.encode(),
                                          hashlib.sha256).digest()).decode()
        return {"X-API-Key": self.api_key, "X-Timestamp": marca,
                "X-Nonce": nonce, "X-Signature": firma}

    def _pedir(self, metodo: str, ruta: str, pares=None, datos=None):
        pares = [(k, str(v)) for k, v in (pares or [])]
        cuerpo = json.dumps(datos, separators=(",", ":")).encode() if datos is not None else b""
        url = BASE + ruta + ("?" + "&".join(f"{_q(k)}={_q(v)}" for k, v in pares) if pares else "")
        for intento in range(4):
            cab = self._firmar(metodo, ruta, pares, cuerpo)
            if datos is not None:
                cab["Content-Type"] = "application/json"
            req = urllib.request.Request(url, data=cuerpo or None, method=metodo, headers=cab)
            try:
                with urllib.request.urlopen(req, timeout=45) as r:
                    time.sleep(self.pausa)
                    return json.loads(r.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as e:
                detalle = e.read().decode("utf-8", "replace")[:400]
                if e.code == 429 and intento < 3:
                    time.sleep(20 * (intento + 1))
                    continue
                raise ErrorSmoobu(f"{metodo} {ruta} -> {e.code}: {detalle}") from e
            except urllib.error.URLError as e:
                if intento < 3:
                    time.sleep(5 * (intento + 1))
                    continue
                raise ErrorSmoobu(f"{metodo} {ruta}: sin conexión ({e.reason})") from e

    # -- lectura --------------------------------------------------------------
    def yo(self) -> dict:
        return self._pedir("GET", "/api/me")

    def apartamentos(self) -> list[dict]:
        return self._pedir("GET", "/api/apartments").get("apartments", [])

    def apartamento(self, apt_id: int) -> dict:
        return self._pedir("GET", f"/api/apartments/{apt_id}")

    def tarifas(self, ids: list[int], desde: dt.date, hasta: dt.date) -> dict:
        """{id: {fecha: {price, min_length_of_stay, available}}}"""
        pares = [("apartments[]", i) for i in ids]
        pares += [("start_date", desde.isoformat()), ("end_date", hasta.isoformat())]
        datos = self._pedir("GET", "/api/rates", pares).get("data", {})
        return {int(k): v for k, v in datos.items()}

    def reservas(self, desde: dt.date, hasta: dt.date, apt_id: int | None = None,
                 canceladas: bool = False) -> list[dict]:
        """Reservas con estancia entre dos fechas. No se guardan datos del huésped."""
        salida, pagina = [], 1
        while True:
            pares = [("from", desde.isoformat()), ("to", hasta.isoformat()),
                     ("pageSize", 100), ("page", pagina)]
            if apt_id:
                pares.append(("apartmentId", apt_id))
            if canceladas:
                pares.append(("showCancellation", "true"))
            r = self._pedir("GET", "/api/reservations", pares)
            for b in r.get("bookings", []):
                salida.append({
                    "id": b.get("id"), "tipo": b.get("type"),
                    "apartamento": (b.get("apartment") or {}).get("id"),
                    "llegada": b.get("arrival"), "salida": b.get("departure"),
                    "creada": (b.get("created-at") or "")[:10],
                    "canal": (b.get("channel") or {}).get("name"),
                    "precio": b.get("price"),
                    "bloqueo": bool(b.get("is-blocked-booking")),
                })
            if pagina >= int(r.get("page_count") or 1):
                return salida
            pagina += 1

    # -- escritura ------------------------------------------------------------
    def enviar_tarifas(self, apt_id: int, operaciones: list[dict], lote: int = 60) -> int:
        """Envía operaciones {dates, daily_price, min_length_of_stay} por lotes."""
        enviadas = 0
        for i in range(0, len(operaciones), lote):
            trozo = operaciones[i:i + lote]
            r = self._pedir("POST", "/api/rates", datos={"apartments": [apt_id], "operations": trozo})
            if not r.get("success"):
                raise ErrorSmoobu(f"Smoobu no confirmó el envío de tarifas: {r}")
            enviadas += len(trozo)
        return enviadas


def agrupar_operaciones(dias: dict[dt.date, tuple[float, int | None]]) -> list[dict]:
    """Junta días consecutivos con el mismo precio y estancia mínima en rangos."""
    ops, fechas = [], sorted(dias)
    i = 0
    while i < len(fechas):
        j = i
        while (j + 1 < len(fechas) and fechas[j + 1] == fechas[j] + dt.timedelta(days=1)
               and dias[fechas[j + 1]] == dias[fechas[i]]):
            j += 1
        precio, minimo = dias[fechas[i]]
        rango = fechas[i].isoformat() if i == j else f"{fechas[i].isoformat()}:{fechas[j].isoformat()}"
        op = {"dates": [rango], "daily_price": precio}
        if minimo:
            op["min_length_of_stay"] = int(minimo)
        ops.append(op)
        i = j + 1
    return ops
