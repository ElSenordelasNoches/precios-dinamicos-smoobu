"""Configuración: valores por defecto, carga, fusión por apartamento y validación.

Todo lo que el usuario puede tocar vive en ``config.json``. Lo que no aparezca
allí toma el valor de ``POR_DEFECTO``. Cada apartamento puede llevar su propio
bloque ``reglas`` que pisa, clave a clave, las reglas generales.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RUTA_CONFIG = RAIZ / "config.json"
RUTA_SECRETOS = RAIZ / "secretos.json"
DATOS = RAIZ / "datos"

MESES = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"]
DIAS = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]

POR_DEFECTO = {
    # "simulacion": calcula y enseña, no toca Smoobu. "automatico": envía los precios.
    "modo": "simulacion",
    "horizonte_dias": 365,
    "moneda": "EUR",
    # prudente | equilibrada | ambiciosa  (escala la sensibilidad de los factores)
    "estrategia": "equilibrada",
    "mercado": {
        "lugar": "Puerto-del-Rosario--Las-Palmas--España",
        # Rectángulo del mapa que delimita la competencia directa.
        "caja": {"sw_lat": 28.482, "sw_lng": -13.885, "ne_lat": 28.520, "ne_lng": -13.840},
        "adultos": 2,
        "noches_consulta": 4,
        "paginas_max": 5,
        "pausa_segundos": [1.5, 3.5],
        # Antigüedad máxima (días) del último rastreo para darlo por bueno.
        "dias_validez": 6,
        # Rastreos anteriores que se combinan para estimar la oferta total.
        "rastreos_memoria": 10,
    },
    "apartamentos": [],
    "reglas": {
        # Recargo (%) que Smoobu añade al precio base en cada portal. Solo se usa
        # para comparar nuestro precio con lo que ve el huésped en Airbnb.
        "canales": {"airbnb": 19, "booking": 45, "hometogo": 20},
        "estacionalidad": {
            # manual | mercado | mixta
            "modo": "mixta",
            "peso_mercado": 0.5,
            # Ajuste (%) sobre el precio base por mes. Perfil de Fuerteventura:
            # invierno alto (norte de Europa), agosto alto (nacional), primavera floja.
            "meses": {"1": 5, "2": 8, "3": 5, "4": 0, "5": -8, "6": -7,
                      "7": 2, "8": 8, "9": -2, "10": 2, "11": 3, "12": 6},
        },
        "dia_semana": {"lun": 0, "mar": 0, "mie": 0, "jue": 0, "vie": 3, "sab": 3, "dom": 0},
        "festivos_automaticos": True,
        "eventos": [],
        "demanda_mercado": {"activa": True, "sensibilidad": 1.0,
                            "tope_subida_pct": 25, "tope_bajada_pct": 15},
        "ultima_hora": {"activa": True, "dias": 14, "descuento_max_pct": 15},
        "antelacion_lejana": {"activa": True, "desde_dias": 120, "plena_a_dias": 240,
                              "prima_max_pct": 10},
        "ritmo_ocupacion": {"activa": True, "ventana_dias": 21, "objetivo_ocupacion_pct": 85,
                            "sensibilidad": 1.0, "tope_subida_pct": 20, "tope_bajada_pct": 12},
        "huecos": {
            "activa": True,
            "ajustar_estancia_minima": True,
            # Se aplica la primera regla que encaje (hueco <= noches_max y
            # antelación >= antelacion_min).
            "reglas": [
                {"noches_max": 2, "antelacion_min": 14, "ajuste_pct": 20},
                {"noches_max": 2, "antelacion_min": 6, "ajuste_pct": 5},
                {"noches_max": 2, "antelacion_min": 0, "ajuste_pct": -10},
                {"noches_max": 3, "antelacion_min": 10, "ajuste_pct": 5},
                {"noches_max": 3, "antelacion_min": 0, "ajuste_pct": -5},
            ],
        },
        "estancia_minima": {"ultima_hora_dias": 0, "ultima_hora_noches": 0,
                            "lejana_desde_dias": 0, "lejana_noches": 0,
                            "temporadas": []},
        # Estancias más cortas que la mínima habitual y coste de cada limpieza.
        "estancias_cortas": {
            "activa": True,
            "coste_limpieza": 30,
            # Lo que debe dejar cada reserva, como poco, después de pagar la limpieza.
            "beneficio_minimo_reserva": 15,
            # Noches libres que no son hueco: a menos de X días se admiten N noches.
            "relajacion": [
                {"antelacion_max": 6, "noches": 2, "solo_si_retraso": False},
                {"antelacion_max": 21, "noches": 3, "solo_si_retraso": True},
            ],
        },
        "limites": {
            # Más allá de X días no se baja de base+pct (protege fechas lejanas).
            "minimo_lejano": {"desde_dias": 30, "pct_sobre_base": -8},
            # Cuánto puede moverse un precio respecto al vigente en cada ejecución.
            "cambio_maximo_pct": 15,
            "redondeo": 1,
            # No descontar por demanda o ritmo si el precio ya está por debajo
            # del percentil 25 de la competencia (menos la holgura indicada).
            "freno_mercado": {"activa": True, "holgura_pct": 0},
        },
        "base_automatica": {"activa": True, "paso_max_pct": 5, "cada_dias": 14,
                            "margen_pct": 25, "percentil_mercado": 50},
        "respetar_cambios_manuales": True,
        "ajustes_manuales": [],
    },
}

ESTRATEGIAS = {
    #              demanda  ritmo  última hora  antelación lejana
    "prudente":   {"demanda": 0.6, "ritmo": 0.6, "ultima_hora": 1.3, "lejana": 0.7},
    "equilibrada": {"demanda": 1.0, "ritmo": 1.0, "ultima_hora": 1.0, "lejana": 1.0},
    "ambiciosa":  {"demanda": 1.4, "ritmo": 1.4, "ultima_hora": 0.6, "lejana": 1.3},
}


def fusionar(base: dict, extra: dict) -> dict:
    """Fusión profunda: ``extra`` pisa a ``base``; las listas se sustituyen enteras."""
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = fusionar(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def cargar(ruta: Path | str | None = None) -> dict:
    ruta = Path(ruta) if ruta else RUTA_CONFIG
    usuario = {}
    if ruta.exists():
        usuario = json.loads(ruta.read_text(encoding="utf-8"))
    cfg = fusionar(POR_DEFECTO, usuario)
    validar(cfg)
    return cfg


def guardar(cfg: dict, ruta: Path | str | None = None) -> None:
    ruta = Path(ruta) if ruta else RUTA_CONFIG
    validar(fusionar(POR_DEFECTO, cfg))
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, ruta)


def reglas_de(cfg: dict, apto: dict) -> dict:
    """Reglas efectivas de un apartamento (generales + las suyas)."""
    return fusionar(cfg["reglas"], apto.get("reglas") or {})


def validar(cfg: dict) -> None:
    errores = []
    if cfg["modo"] not in ("simulacion", "automatico"):
        errores.append("modo debe ser 'simulacion' o 'automatico'")
    if cfg["estrategia"] not in ESTRATEGIAS:
        errores.append("estrategia debe ser prudente, equilibrada o ambiciosa")
    if not 30 <= int(cfg["horizonte_dias"]) <= 540:
        errores.append("horizonte_dias debe estar entre 30 y 540")
    vistos = set()
    for a in cfg["apartamentos"]:
        n = a.get("nombre", a.get("id"))
        for campo in ("id", "nombre", "precio_base", "precio_minimo", "precio_maximo"):
            if a.get(campo) in (None, ""):
                errores.append(f"apartamento {n}: falta '{campo}'")
        try:
            mi, ba, ma = float(a["precio_minimo"]), float(a["precio_base"]), float(a["precio_maximo"])
            if not (0 < mi <= ba <= ma):
                errores.append(f"apartamento {n}: debe cumplirse mínimo <= base <= máximo")
        except (KeyError, TypeError, ValueError):
            pass
        if a.get("id") in vistos:
            errores.append(f"apartamento {n}: id repetido")
        vistos.add(a.get("id"))
    if errores:
        raise ValueError("Configuración no válida:\n - " + "\n - ".join(errores))


def secretos() -> dict:
    """Credenciales de Smoobu. Entorno primero; si no, ``secretos.json``."""
    clave = os.environ.get("SMOOBU_API_KEY")
    secreto = os.environ.get("SMOOBU_API_SECRET")
    if clave and secreto:
        return {"api_key": clave, "api_secret": secreto}
    if RUTA_SECRETOS.exists():
        s = json.loads(RUTA_SECRETOS.read_text(encoding="utf-8"))
        if s.get("api_key") and s.get("api_secret"):
            return s
    return {}
