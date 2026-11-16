"""Rastreo del mercado (Airbnb) y modelo de demanda que sale de él.

Qué se mide
-----------
Para una rejilla fija de fechas de llegada se consulta la búsqueda pública de
Airbnb acotada a un rectángulo del mapa (la competencia directa) y se anota,
de cada alojamiento entero disponible, su precio por noche tal como lo ve el
huésped. Con eso se obtienen tres señales por fecha:

* **Ocupación del mercado**: 1 - disponibles / oferta conocida.
* **Índice de precio**: cuánto más caro o barato está *cada* alojamiento en esa
  fecha respecto a su propio precio habitual (comparación de igual a igual,
  que no se distorsiona cuando los baratos ya se han vendido).
* **Ritmo de reservas**: cuánto ha subido la ocupación de esa misma ventana
  desde el rastreo anterior, comparado con lo normal a esa antelación.

Las ventanas se anclan a una rejilla fija (no a «hoy»), de modo que dos
rastreos en días distintos miden exactamente las mismas estancias.

Solo se leen páginas públicas, sin iniciar sesión, con pausas entre consultas
y un volumen muy bajo (unas decenas de ventanas cada dos días).
"""
from __future__ import annotations

import base64
import datetime as dt
import gzip
import json
import math
import random
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config

DIR_MERCADO = config.DATOS / "mercado"
CABECERAS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept-Encoding": "gzip",
}
RE_ESTADO = re.compile(r'id="data-deferred-state-0"[^>]*>(.*?)</script>', re.S)
PASO = 4  # días entre ventanas de la rejilla


class ErrorRastreo(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Rejilla de ventanas
# --------------------------------------------------------------------------

def plan_ventanas(hoy: dt.date, horizonte: int) -> list[dt.date]:
    """Fechas de llegada a consultar: densas cerca, más espaciadas lejos."""
    fechas = []
    d = hoy + dt.timedelta(days=1)
    fin = hoy + dt.timedelta(days=horizonte)
    while d <= fin:
        o = d.toordinal()
        antelacion = (d - hoy).days
        if o % PASO == 0:
            if antelacion <= 62 or (antelacion <= 185 and o % (2 * PASO) == 0) \
                    or o % (4 * PASO) == 0:
                fechas.append(d)
        d += dt.timedelta(days=1)
    return fechas


# --------------------------------------------------------------------------
# Descarga y lectura
# --------------------------------------------------------------------------

def _url(cfg_m: dict, llegada: dt.date, noches: int, cursor: str | None = None) -> str:
    c = cfg_m["caja"]
    q = [
        ("checkin", llegada.isoformat()),
        ("checkout", (llegada + dt.timedelta(days=noches)).isoformat()),
        ("adults", str(cfg_m.get("adultos", 2))),
        ("room_types[]", "Entire home/apt"),
        ("currency", "EUR"),
        ("search_by_map", "true"),
        ("ne_lat", str(c["ne_lat"])), ("ne_lng", str(c["ne_lng"])),
        ("sw_lat", str(c["sw_lat"])), ("sw_lng", str(c["sw_lng"])),
        ("zoom", "14"),
    ]
    if cursor:
        q.append(("cursor", cursor))
    lugar = urllib.parse.quote(cfg_m["lugar"])
    return f"https://www.airbnb.es/s/{lugar}/homes?" + urllib.parse.urlencode(q)


def _descargar(url: str, intentos: int = 3) -> str:
    ultimo = None
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers=CABECERAS)
            with urllib.request.urlopen(req, timeout=45) as r:
                cuerpo = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    cuerpo = gzip.decompress(cuerpo)
                return cuerpo.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            ultimo = e
            if e.code in (403, 429):
                # Nos piden parar: se respeta y se abandona el rastreo.
                raise ErrorRastreo(f"Airbnb respondió {e.code}; se detiene el rastreo") from e
        except Exception as e:  # red, tiempo de espera…
            ultimo = e
        time.sleep(4 * (i + 1))
    raise ErrorRastreo(f"sin respuesta tras {intentos} intentos: {ultimo!r}")


def _numero(texto: str | None) -> float | None:
    if not texto:
        return None
    m = re.search(r"(\d[\d.\s ]*(?:,\d+)?)\s*€", texto)
    if not m:
        return None
    n = m.group(1).replace(".", "").replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(n)
    except ValueError:
        return None


def _id_anuncio(codificado: str) -> str:
    try:
        return base64.b64decode(codificado + "==").decode().split(":")[-1]
    except Exception:
        return codificado


def _dormitorios(lineas: list[str], titulo: str) -> int | None:
    for t in lineas:
        m = re.search(r"(\d+)\s+dormitorio", t)
        if m:
            return int(m.group(1))
        if "studio" in t.lower():  # «Estudio»
            return 0
    if "studio" in (titulo or "").lower():
        return 0
    return None


def leer_resultados(html: str, noches: int) -> dict:
    """Extrae anuncios, total y cursores de una página de resultados."""
    m = RE_ESTADO.search(html)
    if not m:
        raise ErrorRastreo("la página no trae el bloque de datos esperado")
    try:
        datos = json.loads(m.group(1))
        res = datos["niobeClientData"][0][1]["data"]["presentation"]["staysSearch"]["results"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise ErrorRastreo(f"estructura de datos irreconocible: {e!r}") from e

    total = None
    mt = re.search(r'"structuredTitle":"(?:Más de )?([\d.]+)[^"]*alojamiento', m.group(1))
    if mt:
        total = int(mt.group(1).replace(".", ""))

    anuncios = []
    for r in res.get("searchResults") or []:
        if r.get("__typename") != "StaySearchResult":
            continue
        dsl = r.get("demandStayListing") or {}
        if not dsl.get("id"):
            continue
        linea = ((r.get("structuredDisplayPrice") or {}).get("primaryLine")) or {}
        importe = _numero(linea.get("discountedPrice") or linea.get("price")
                          or linea.get("accessibilityLabel"))
        if importe is None:
            continue
        cual = (linea.get("qualifier") or linea.get("accessibilityLabel") or "").lower()
        noche = importe if "noche" in cual and "total" not in cual else importe / noches
        sc = r.get("structuredContent") or {}
        lineas = [x.get("body") or "" for x in (sc.get("mapPrimaryLine") or sc.get("primaryLine") or [])]
        coord = ((dsl.get("location") or {}).get("coordinate")) or {}
        nota, resenas = None, None
        mv = re.match(r"([\d,]+)\s*\((\d+)\)", r.get("avgRatingLocalized") or "")
        if mv:
            nota, resenas = float(mv.group(1).replace(",", ".")), int(mv.group(2))
        anuncios.append({
            "id": _id_anuncio(dsl["id"]),
            "titulo": r.get("title") or "",
            "dormitorios": _dormitorios(lineas, r.get("title") or ""),
            "lat": coord.get("latitude"), "lng": coord.get("longitude"),
            "nota": nota, "resenas": resenas,
            "noche": round(noche, 2),
        })
    cursores = ((res.get("paginationInfo") or {}).get("pageCursors")) or []
    return {"anuncios": anuncios, "total": total, "cursores": cursores}


def consultar_ventana(cfg_m: dict, llegada: dt.date, noches: int, pausa) -> dict:
    """Todos los alojamientos disponibles para una estancia concreta."""
    precios, fichas = {}, {}
    pagina = leer_resultados(_descargar(_url(cfg_m, llegada, noches)), noches)
    total, cursores = pagina["total"], pagina["cursores"]
    paginas = 1

    def anotar(p):
        for a in p["anuncios"]:
            precios[a["id"]] = a["noche"]
            fichas[a["id"]] = {k: a[k] for k in ("titulo", "dormitorios", "lat", "lng", "nota", "resenas")}

    anotar(pagina)
    for cursor in cursores[1:cfg_m.get("paginas_max", 5)]:
        if total is not None and len(precios) >= total:
            break
        pausa()
        anotar(leer_resultados(_descargar(_url(cfg_m, llegada, noches, cursor)), noches))
        paginas += 1
    completa = total is None or len(precios) >= min(total, 18 * paginas) * 0.9
    return {"llegada": llegada.isoformat(), "noches": noches,
            "total": total if total is not None else len(precios),
            "leidos": len(precios), "completa": bool(completa),
            "precios": precios, "_fichas": fichas}


def escanear(cfg: dict, hoy: dt.date | None = None, rapido: bool = False,
             avisar=print) -> dict:
    """Rastrea todas las ventanas y guarda la foto en ``datos/mercado``."""
    hoy = hoy or dt.date.today()
    cfg_m = dict(cfg["mercado"])
    if rapido:
        cfg_m["paginas_max"] = 1
    noches = int(cfg_m.get("noches_consulta", 4))
    lo, hi = cfg_m.get("pausa_segundos", [1.5, 3.5])

    def pausa():
        time.sleep(random.uniform(lo, hi))

    ventanas, fichas, errores = [], {}, []
    fechas = plan_ventanas(hoy, int(cfg["horizonte_dias"]))
    for i, llegada in enumerate(fechas, 1):
        try:
            v = consultar_ventana(cfg_m, llegada, noches, pausa)
            fichas.update({k: f for k, f in v.pop("_fichas").items()
                           if k not in fichas or fichas[k].get("dormitorios") is None})
            ventanas.append(v)
            avisar(f"  [{i}/{len(fechas)}] {llegada}  disponibles: {v['total']}")
        except ErrorRastreo as e:
            errores.append({"llegada": llegada.isoformat(), "error": str(e)})
            avisar(f"  [{i}/{len(fechas)}] {llegada}  ERROR: {e}")
            if "se detiene" in str(e) or len(errores) >= 6:
                break
        pausa()

    foto = {
        "fecha": dt.datetime.now().isoformat(timespec="minutes"),
        "dia": hoy.isoformat(), "noches": noches, "caja": cfg_m["caja"],
        "previstas": len(fechas), "errores": errores,
        "anuncios": fichas, "ventanas": ventanas,
    }
    if len(ventanas) < max(5, len(fechas) * 0.6):
        foto["incompleta"] = True
    DIR_MERCADO.mkdir(parents=True, exist_ok=True)
    ruta = DIR_MERCADO / f"{hoy.isoformat()}.json"
    if not foto.get("incompleta") or not ruta.exists():
        ruta.write_text(json.dumps(foto, ensure_ascii=False), encoding="utf-8")
    return foto


def cargar_fotos(maximo: int = 10) -> list[dict]:
    """Rastreos guardados, del más reciente al más antiguo (solo los completos)."""
    if not DIR_MERCADO.exists():
        return []
    fotos = []
    for ruta in sorted(DIR_MERCADO.glob("????-??-??.json"), reverse=True):
        try:
            f = json.loads(ruta.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not f.get("incompleta"):
            fotos.append(f)
        if len(fotos) >= maximo:
            break
    return fotos


# --------------------------------------------------------------------------
# Modelo
# --------------------------------------------------------------------------

def _percentil(valores: list[float], p: float) -> float:
    v = sorted(valores)
    if not v:
        return float("nan")
    k = (len(v) - 1) * p / 100
    f, c = math.floor(k), math.ceil(k)
    return v[f] if f == c else v[f] + (v[c] - v[f]) * (k - f)


def _ajuste_curva(puntos: list[tuple[float, float]]):
    """Ocupación esperada según antelación: a + b·exp(-antelación/τ)."""
    if len(puntos) < 6:
        return None
    mejor = None
    n = len(puntos)
    for tau in (10, 15, 20, 30, 45, 60, 90, 120, 180):
        xs = [math.exp(-l / tau) for l, _ in puntos]
        ys = [o for _, o in puntos]
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx < 1e-9:
            continue
        b = max(0.0, sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx)
        a = my - b * mx
        err = sum((a + b * x - y) ** 2 for x, y in zip(xs, ys))
        if mejor is None or err < mejor[0]:
            mejor = (err, a, b, tau)
    if mejor is None:
        return None
    _, a, b, tau = mejor
    return {"a": a, "b": b, "tau": tau}


class ModeloMercado:
    """Señales de mercado por fecha y por tamaño (dormitorios) de alojamiento."""

    def __init__(self, fotos: list[dict], excluir: set[str] | None = None,
                 hoy: dt.date | None = None):
        self.fotos = fotos
        self.excluir = {str(x) for x in (excluir or set())}
        self.actual = fotos[0] if fotos else None
        self.hoy = hoy or (dt.date.fromisoformat(self.actual["dia"]) if self.actual else dt.date.today())
        self.dia_foto = dt.date.fromisoformat(self.actual["dia"]) if self.actual else None
        self.fichas: dict[str, dict] = {}
        for f in reversed(fotos):
            for k, v in f.get("anuncios", {}).items():
                if k not in self.fichas or v.get("dormitorios") is not None:
                    self.fichas[k] = v
        self._cache: dict[int | None, dict] = {}

    @property
    def disponible(self) -> bool:
        return bool(self.actual and self.actual.get("ventanas"))

    def antiguedad(self) -> int | None:
        return (self.hoy - self.dia_foto).days if self.dia_foto else None

    # -- segmentos ---------------------------------------------------------
    def _ids_segmento(self, dormitorios: int | None) -> set[str]:
        todos = {k for k in self.fichas if k not in self.excluir}
        if dormitorios is None:
            return todos
        seg = {k for k in todos if self.fichas[k].get("dormitorios") == dormitorios}
        if dormitorios == 1:  # los estudios compiten con un dormitorio
            seg |= {k for k in todos if self.fichas[k].get("dormitorios") == 0}
        return seg if len(seg) >= 8 else todos

    def segmento(self, dormitorios: int | None) -> dict:
        if dormitorios in self._cache:
            return self._cache[dormitorios]
        ids = self._ids_segmento(dormitorios)
        ventanas = []
        for v in self.actual["ventanas"] if self.actual else []:
            precios = {k: p for k, p in v["precios"].items() if k in ids}
            ventanas.append({"llegada": dt.date.fromisoformat(v["llegada"]),
                             "noches": v["noches"], "precios": precios,
                             "n": len(precios), "completa": v.get("completa", True)})
        # Oferta: el máximo de disponibles visto en cualquier ventana reciente.
        oferta = max([v["n"] for v in ventanas] or [0])
        for f in self.fotos[1:]:
            for v in f.get("ventanas", []):
                oferta = max(oferta, sum(1 for k in v["precios"] if k in ids))
        # Precio habitual de cada anuncio y nivel general del segmento.
        por_anuncio: dict[str, list[float]] = {}
        for v in ventanas:
            for k, p in v["precios"].items():
                por_anuncio.setdefault(k, []).append(p)
        habitual = {k: statistics.median(ps) for k, ps in por_anuncio.items()}
        niveles = sorted(habitual.values())
        # Ocupación e índice de precio (de igual a igual) por ventana.
        for v in ventanas:
            v["antelacion"] = (v["llegada"] - self.dia_foto).days
            v["ocupacion"] = max(0.0, 1 - v["n"] / oferta) if oferta else None
            ratios = [p / habitual[k] for k, p in v["precios"].items()
                      if len(por_anuncio[k]) >= 3 and habitual[k] > 0]
            v["indice"] = statistics.median(ratios) if len(ratios) >= 4 else 1.0
            # Con menos de 4 anuncios libres la mediana no dice nada (suelen quedar
            # solo los de precio disparatado): se deja sin dato.
            ps = sorted(v["precios"].values())
            fiable = len(ps) >= 4
            v["mediana"] = statistics.median(ps) if fiable else None
            v["p25"] = _percentil(ps, 25) if fiable else None
            v["p75"] = _percentil(ps, 75) if fiable else None
        curva = _ajuste_curva([(v["antelacion"], v["ocupacion"]) for v in ventanas
                               if v["ocupacion"] is not None])
        previas = self._ocupacion_previa(ids)
        for v in ventanas:
            esperado = self._curva(curva, v["antelacion"])
            v["esperada"] = esperado
            v["desviacion"] = (v["ocupacion"] - esperado) if (
                esperado is not None and v["ocupacion"] is not None) else 0.0
            v["ritmo"] = 0.0
            pv = previas.get(v["llegada"])
            if pv and oferta and curva:
                antes_ocup = max(0.0, 1 - pv["n"] / oferta)
                normal = self._curva(curva, v["antelacion"]) - self._curva(curva, pv["antelacion"])
                v["ritmo"] = (v["ocupacion"] - antes_ocup) - normal
        seg = {"ids": ids, "oferta": oferta, "ventanas": ventanas, "curva": curva,
               "habitual": habitual,
               "nivel": {p: _percentil(niveles, p) for p in (10, 25, 40, 50, 60, 75, 90)} if niveles else {},
               "n_anuncios": len(niveles)}
        self._cache[dormitorios] = seg
        return seg

    def _ocupacion_previa(self, ids: set[str]) -> dict:
        """Disponibles por ventana en el rastreo anterior (de 1 a 8 días antes)."""
        for f in self.fotos[1:]:
            dia = dt.date.fromisoformat(f["dia"])
            if 1 <= (self.dia_foto - dia).days <= 8:
                return {dt.date.fromisoformat(v["llegada"]):
                        {"n": sum(1 for k in v["precios"] if k in ids),
                         "antelacion": (dt.date.fromisoformat(v["llegada"]) - dia).days}
                        for v in f.get("ventanas", [])}
        return {}

    @staticmethod
    def _curva(curva, antelacion):
        if not curva:
            return None
        return min(1.0, max(0.0, curva["a"] + curva["b"] * math.exp(-antelacion / curva["tau"])))

    # -- consulta por fecha --------------------------------------------------
    def para_fecha(self, d: dt.date, dormitorios: int | None) -> dict | None:
        """Señales para una noche. La demanda (ocupación, desviación, ritmo) se
        mide con toda la competencia de la zona, que da una señal más estable;
        el nivel de precios, solo con los alojamientos del mismo tamaño."""
        if not self.disponible:
            return None
        demanda = self._interpolar_fecha(d, self.segmento(None)["ventanas"])
        precio = self._interpolar_fecha(d, self.segmento(dormitorios)["ventanas"])
        if demanda is None:
            return None
        for k in ("indice", "mediana", "p25", "p75"):
            demanda[k] = (precio or {}).get(k)
        return demanda

    def _interpolar_fecha(self, d: dt.date, vs: list[dict]) -> dict | None:
        if not vs:
            return None
        dentro = [v for v in vs if v["llegada"] <= d < v["llegada"] + dt.timedelta(days=v["noches"])]
        if dentro:
            return self._salida(dentro[0], dentro[0], 0.0)
        antes = [v for v in vs if v["llegada"] <= d]
        despues = [v for v in vs if v["llegada"] > d]
        if antes and despues:
            a, b = antes[-1], despues[0]
            fin_a = a["llegada"] + dt.timedelta(days=a["noches"] - 1)
            tramo = max(1, (b["llegada"] - fin_a).days)
            return self._salida(a, b, (d - fin_a).days / tramo)
        v = antes[-1] if antes else despues[0]
        return self._salida(v, v, 0.0)

    @staticmethod
    def _salida(a: dict, b: dict, t: float) -> dict:
        def mezcla(k):
            x, y = a.get(k), b.get(k)
            if x is None or y is None:
                return x if y is None else y
            return x + (y - x) * t
        return {k: mezcla(k) for k in ("ocupacion", "indice", "mediana", "p25", "p75",
                                       "desviacion", "ritmo", "esperada", "n")}

    # -- resumen -------------------------------------------------------------
    def resumen(self, dormitorios: int | None) -> dict:
        if not self.disponible:
            return {"disponible": False}
        s, g = self.segmento(dormitorios), self.segmento(None)
        return {
            "disponible": True, "dia": self.actual["dia"], "antiguedad_dias": self.antiguedad(),
            "anuncios": s["n_anuncios"], "anuncios_zona": g["n_anuncios"],
            "oferta": g["oferta"], "nivel": s["nivel"], "curva": g["curva"],
            "ventanas": [{"llegada": v["llegada"].isoformat(), "n": v["n"], "n_tamano": w["n"],
                          "ocupacion": round(v["ocupacion"], 3) if v["ocupacion"] is not None else None,
                          "indice": round(w["indice"], 3),
                          "mediana": round(w["mediana"], 1) if w["mediana"] else None,
                          "p25": round(w["p25"], 1) if w["p25"] else None,
                          "p75": round(w["p75"], 1) if w["p75"] else None,
                          "desviacion": round(v["desviacion"], 3),
                          "ritmo": round(v["ritmo"], 3)} for v, w in zip(g["ventanas"], s["ventanas"])],
        }

    def precio_propio(self, airbnb_id: str | None) -> dict:
        """Precio por noche de nuestro anuncio en cada ventana en la que aparece."""
        if not (airbnb_id and self.actual):
            return {}
        return {v["llegada"]: v["precios"][str(airbnb_id)]
                for v in self.actual["ventanas"] if str(airbnb_id) in v["precios"]}
