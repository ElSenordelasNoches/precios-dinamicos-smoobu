"""Estado propio de cada apartamento: noches libres, precios vigentes y reservas."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

UN_DIA = dt.timedelta(days=1)

# Parte de la ocupación final que suele estar ya reservada a X días vista.
# Se usa mientras no haya historial propio suficiente para calcularla.
CURVA_POR_DEFECTO = [(0, 1.0), (3, .93), (7, .85), (14, .72), (21, .62), (30, .50), (45, .38),
                     (60, .28), (90, .16), (120, .10), (180, .04), (270, .015), (365, 0.0)]


def _interpolar(puntos, x):
    if x <= puntos[0][0]:
        return puntos[0][1]
    for (x0, y0), (x1, y1) in zip(puntos, puntos[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return puntos[-1][1]


@dataclass
class Calendario:
    apt_id: int
    origen: str
    # fecha -> {"libre": bool, "precio": float|None, "minimo": int|None}
    dias: dict = field(default_factory=dict)
    # reservas: {"llegada": date, "salida": date, "creada": date|None, "canal": str, "precio": float|None, "bloqueo": bool}
    reservas: list = field(default_factory=list)

    def __post_init__(self):
        self._noche: dict[dt.date, str] = {}
        for r in self.reservas:
            d = r["llegada"]
            while d < r["salida"]:
                self._noche[d] = "B" if r.get("bloqueo") else "R"
                d += UN_DIA
        self._curva = None

    # -- estado de una noche --------------------------------------------------
    def estado(self, d: dt.date) -> str:
        """'R' reservada, 'B' bloqueada, 'L' libre."""
        if d in self._noche:
            return self._noche[d]
        info = self.dias.get(d)
        if info is not None and not info.get("libre", True):
            return "R"  # Smoobu la da por no disponible y no consta como bloqueo
        return "L"

    def libre(self, d: dt.date) -> bool:
        return self.estado(d) == "L"

    def ocupacion(self, desde: dt.date, hasta: dt.date) -> float | None:
        """Noches vendidas / noches vendibles en [desde, hasta)."""
        vendidas = vendibles = 0
        d = desde
        while d < hasta:
            e = self.estado(d)
            if e != "B":
                vendibles += 1
                vendidas += e == "R"
            d += UN_DIA
        return vendidas / vendibles if vendibles else None

    def hueco(self, d: dt.date, hoy: dt.date, fin: dt.date) -> int | None:
        """Noches del hueco libre que contiene a ``d`` si está cerrado por ambos
        lados (por una reserva, un bloqueo o por el día de hoy). None si es abierto."""
        if not self.libre(d):
            return None
        a = d
        while a - UN_DIA >= hoy and self.libre(a - UN_DIA):
            a -= UN_DIA
        b = d
        while b + UN_DIA <= fin and self.libre(b + UN_DIA):
            b += UN_DIA
        if b >= fin:  # no hay nada reservado después: no es un hueco
            return None
        return (b - a).days + 1

    # -- antelación con la que se reserva --------------------------------------
    def curva_antelacion(self):
        """f(antelación) = parte de las noches finalmente vendidas ya reservada."""
        if self._curva is None:
            leads = []
            for r in self.reservas:
                if r.get("bloqueo") or not r.get("creada"):
                    continue
                noches = (r["salida"] - r["llegada"]).days
                if noches > 28:  # estancias de temporada: distorsionan la curva
                    continue
                d = r["llegada"]
                while d < r["salida"]:
                    leads.append(max(0, (d - r["creada"]).days))
                    d += UN_DIA
            if len(leads) >= 120:
                leads.sort()
                n = len(leads)
                cortes = [0, 3, 7, 14, 21, 30, 45, 60, 90, 120, 180, 270, 365]
                puntos = [(c, sum(1 for x in leads if x >= c) / n) for c in cortes]
                self._curva = ("propia", puntos)
            else:
                self._curva = ("por defecto", CURVA_POR_DEFECTO)
        return self._curva

    def reservado_a(self, antelacion: int) -> float:
        return _interpolar(self.curva_antelacion()[1], antelacion)

    def antelacion_mediana(self) -> int | None:
        leads = sorted((r["llegada"] - r["creada"]).days for r in self.reservas
                       if r.get("creada") and not r.get("bloqueo"))
        return leads[len(leads) // 2] if leads else None


# --------------------------------------------------------------------------
# Orígenes
# --------------------------------------------------------------------------

def _fecha(v):
    return dt.date.fromisoformat(str(v)[:10]) if v else None


def desde_smoobu(cliente, ids: list[int], hoy: dt.date, horizonte: int,
                 historial_dias: int = 540) -> dict[int, Calendario]:
    fin = hoy + dt.timedelta(days=horizonte)
    tarifas = cliente.tarifas(ids, hoy, fin)
    reservas = cliente.reservas(hoy - dt.timedelta(days=historial_dias), fin)
    salida = {}
    for apt in ids:
        dias = {}
        for f, v in (tarifas.get(apt) or {}).items():
            dias[_fecha(f)] = {"libre": bool(v.get("available")),
                               "precio": v.get("price"),
                               "minimo": v.get("min_length_of_stay")}
        propias = []
        for r in reservas:
            if r["apartamento"] != apt or r.get("tipo") == "cancellation":
                continue
            ll, sa = _fecha(r["llegada"]), _fecha(r["salida"])
            if ll and sa and sa > ll:
                propias.append({"llegada": ll, "salida": sa, "creada": _fecha(r.get("creada")),
                                "canal": r.get("canal") or "", "precio": r.get("precio"),
                                "bloqueo": r.get("bloqueo", False)})
        salida[apt] = Calendario(apt, "smoobu", dias, propias)
    return salida


CANALES_VOLCADO = {"A": "Airbnb", "B": "Bloqueo", "C": "Booking.com", "D": "Directa",
                   "E": "Otro", "F": "Otro", "G": "HomeToGo"}


def desde_volcado(ruta: Path | str, hoy: dt.date, horizonte: int) -> dict[int, Calendario]:
    """Lee el volcado compacto del calendario de precios (sirve para simular
    sin clave de API). Formato: ``@id S AAMMDDxNc … R AAMMDDxN_min/precio …``."""
    texto = Path(ruta).read_text(encoding="utf-8")
    fin = hoy + dt.timedelta(days=horizonte)
    salida = {}
    for m in re.finditer(r"@(\d+) S (.*?) R ([^\n@]*)", texto):
        apt = int(m.group(1))
        reservas = []
        for s in m.group(2).split():
            mm = re.fullmatch(r"(\d{2})(\d{2})(\d{2})x(\d+)([A-Z])", s)
            if not mm:
                continue
            ll = dt.date(2000 + int(mm.group(1)), int(mm.group(2)), int(mm.group(3)))
            reservas.append({"llegada": ll, "salida": ll + dt.timedelta(days=int(mm.group(4))),
                             "creada": None, "canal": CANALES_VOLCADO.get(mm.group(5), "Otro"),
                             "precio": None, "bloqueo": mm.group(5) == "B"})
        dias = {}
        for s in m.group(3).split():
            mm = re.fullmatch(r"(\d{2})(\d{2})(\d{2})x(\d+)_(\d*)/(\d*(?:\.\d+)?)", s)
            if not mm:
                continue
            d = dt.date(2000 + int(mm.group(1)), int(mm.group(2)), int(mm.group(3)))
            for _ in range(int(mm.group(4))):
                if hoy <= d <= fin:
                    dias[d] = {"libre": True,
                               "precio": float(mm.group(6)) if mm.group(6) else None,
                               "minimo": int(mm.group(5)) if mm.group(5) else None}
                d += UN_DIA
        cal = Calendario(apt, "volcado", dias, reservas)
        for d in dias:
            dias[d]["libre"] = cal.estado(d) == "L"
        salida[apt] = cal
    return salida
