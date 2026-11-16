"""Orquestación: rastrear, calcular, comprobar y (si procede) enviar a Smoobu."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from . import calendario, config, mercado, motor, smoobu

RUTA_ESTADO = config.DATOS / "estado.json"
RUTA_EVENTOS = config.DATOS / "eventos.json"
RUTA_ULTIMO = config.DATOS / "ultimo.json"
DIR_EJEC = config.DATOS / "ejecuciones"
VOLCADO = config.DATOS / "calendario_smoobu_inicial.txt"


def _leer(ruta: Path, defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def _escribir(ruta: Path, datos) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, ruta)


def cargar_eventos() -> list[dict]:
    """Eventos verificados que mantiene la revisión periódica (datos/eventos.json)."""
    datos = _leer(RUTA_EVENTOS, {})
    return [e for e in datos.get("eventos", []) if e.get("desde") and e.get("hasta")]


def cliente_smoobu():
    s = config.secretos()
    return smoobu.Smoobu(s["api_key"], s["api_secret"]) if s else None


def calendarios(cfg: dict, hoy: dt.date, cliente=None):
    ids = [int(a["id"]) for a in cfg["apartamentos"]]
    if cliente:
        return calendario.desde_smoobu(cliente, ids, hoy, int(cfg["horizonte_dias"])), "smoobu"
    if VOLCADO.exists():
        return calendario.desde_volcado(VOLCADO, hoy, int(cfg["horizonte_dias"])), "volcado"
    raise RuntimeError("No hay credenciales de Smoobu (secretos.json) ni volcado inicial del calendario.")


def modelo_mercado(cfg: dict, hoy: dt.date):
    fotos = mercado.cargar_fotos(int(cfg["mercado"].get("rastreos_memoria", 10)))
    propios = {str(a["airbnb_id"]) for a in cfg["apartamentos"] if a.get("airbnb_id")}
    return mercado.ModeloMercado(fotos, excluir=propios, hoy=hoy)


def calcular_todo(cfg: dict, hoy: dt.date | None = None, cliente=None, cals=None, origen=None) -> dict:
    hoy = hoy or dt.date.today()
    estado = _leer(RUTA_ESTADO, {})
    if cals is None:
        cals, origen = calendarios(cfg, hoy, cliente)
    modelo = modelo_mercado(cfg, hoy)
    eventos = cargar_eventos()
    avisos = []
    if not modelo.disponible:
        avisos.append("No hay rastreo de mercado: se calcula solo con las reglas propias.")
    elif modelo.antiguedad() > int(cfg["mercado"].get("dias_validez", 6)):
        avisos.append(f"El último rastreo de mercado tiene {modelo.antiguedad()} días.")
    if origen == "volcado":
        avisos.append("Calendario leído del volcado inicial (sin API): solo sirve para simular.")
    resultados = []
    for apto in cfg["apartamentos"]:
        cal = cals.get(int(apto["id"]))
        if cal is None:
            avisos.append(f"Smoobu no devolvió datos del apartamento {apto['nombre']}.")
            continue
        resultados.append(motor.calcular(cfg, apto, cal, modelo, hoy, estado, eventos))
    return {
        "generado": dt.datetime.now().isoformat(timespec="seconds"), "hoy": hoy.isoformat(),
        "modo": cfg["modo"], "estrategia": cfg["estrategia"], "origen_calendario": origen,
        "avisos": avisos, "eventos_verificados": eventos, "apartamentos": resultados,
        "mercado_dia": modelo.actual["dia"] if modelo.disponible else None,
    }


# --------------------------------------------------------------------------
# Cortafuegos y envío
# --------------------------------------------------------------------------

def comprobar(cfg: dict, salida: dict) -> list[str]:
    """Motivos para NO enviar. Lista vacía = todo en orden."""
    pegas = []
    if salida["origen_calendario"] != "smoobu":
        pegas.append("el calendario no viene de la API de Smoobu")
    for r in salida["apartamentos"]:
        a = r["apartamento"]
        libres = [x for x in r["dias"] if x["libre"] and x["origen"] == "motor"]
        if not libres:
            continue
        fuera = [x for x in libres if not (a["precio_minimo"] - 0.01 <= x["precio"] <= a["precio_maximo"] + 0.01)]
        if fuera:
            pegas.append(f"{a['nombre']}: {len(fuera)} noches fuera de los límites mínimo/máximo")
        bruscos = [x for x in libres if x["actual"] and abs(x["precio"] / x["actual"] - 1) > 0.35 and not x["hueco"]]
        if len(bruscos) > 0.25 * len(libres):
            pegas.append(f"{a['nombre']}: {len(bruscos)} noches cambian más de un 35 % de golpe")
        if any((x["precio"] or 0) <= 0 for x in libres):
            pegas.append(f"{a['nombre']}: hay precios nulos o negativos")
    return pegas


def aplicar(cfg: dict, salida: dict, cliente, hoy: dt.date) -> dict:
    """Envía a Smoobu lo que cambia, lo verifica y anota lo enviado."""
    estado = _leer(RUTA_ESTADO, {})
    estado.setdefault("enviados", {})
    estado.setdefault("base", {})
    informe = {"enviado": {}, "verificacion": {}}
    for r in salida["apartamentos"]:
        a = r["apartamento"]
        ops = motor.operaciones(r)
        clave = str(a["id"])
        if ops:
            cliente.enviar_tarifas(int(a["id"]), smoobu.agrupar_operaciones(ops))
        informe["enviado"][a["nombre"]] = len(ops)
        # Lo que queda vigente (enviado ahora o ya coincidente) se anota para
        # detectar después los cambios hechos a mano en Smoobu.
        registro = {x["fecha"]: [x["precio"], x["minimo"]] for x in r["dias"]
                    if x["origen"] in ("motor", "fijo") and x["precio"] is not None}
        estado["enviados"][clave] = registro
        b = r["base"]
        if b["automatica"]:
            previo = estado["base"].get(clave) or {}
            estado["base"][clave] = {
                "valor": b["valor"], "config_base": b["configurada"],
                "fecha": hoy.isoformat() if b["aplicada"] else previo.get("fecha", hoy.isoformat()),
            }
        # Verificación: se relee Smoobu y se compara.
        if ops:
            fechas = sorted(ops)
            leido = cliente.tarifas([int(a["id"])], fechas[0], fechas[-1]).get(int(a["id"]), {})
            mal = [f.isoformat() for f in fechas
                   if abs(float((leido.get(f.isoformat()) or {}).get("price") or -1) - ops[f][0]) > 0.01]
            informe["verificacion"][a["nombre"]] = {"comprobadas": len(fechas), "no_coinciden": mal[:20]}
    estado["ultima_aplicacion"] = dt.datetime.now().isoformat(timespec="seconds")
    _escribir(RUTA_ESTADO, estado)
    return informe


def ejecutar(cfg: dict, escanear: bool = True, forzar_aplicar: bool | None = None,
             rapido: bool = False, avisar=print, hoy: dt.date | None = None) -> dict:
    """Ciclo completo. Envía a Smoobu solo en modo automático (o si se fuerza)."""
    hoy = hoy or dt.date.today()
    if escanear:
        avisar("Rastreando el mercado…")
        try:
            foto = mercado.escanear(cfg, hoy, rapido=rapido, avisar=avisar)
            if foto.get("incompleta"):
                avisar("AVISO: rastreo incompleto; se usa el último rastreo completo.")
        except Exception as e:  # el rastreo nunca debe impedir el resto
            avisar(f"AVISO: el rastreo ha fallado ({e}); se sigue con el último disponible.")
    cliente = cliente_smoobu()
    salida = calcular_todo(cfg, hoy, cliente)
    quiere = (cfg["modo"] == "automatico") if forzar_aplicar is None else forzar_aplicar
    salida["aplicado"] = False
    if quiere:
        pegas = comprobar(cfg, salida)
        if pegas:
            salida["bloqueado_por"] = pegas
            avisar("NO se envía a Smoobu: " + "; ".join(pegas))
        else:
            salida["envio"] = aplicar(cfg, salida, cliente, hoy)
            salida["aplicado"] = True
    guardar(salida)
    return salida


def guardar(salida: dict) -> None:
    _escribir(RUTA_ULTIMO, salida)
    DIR_EJEC.mkdir(parents=True, exist_ok=True)
    marca = salida["generado"].replace(":", "").replace("-", "")[:13]
    ligero = {k: v for k, v in salida.items() if k != "apartamentos"}
    ligero["apartamentos"] = [{"apartamento": r["apartamento"], "base": r["base"],
                               "indicadores": r["indicadores"],
                               "precios": {x["fecha"]: [x["precio"], x["minimo"], x["estado"]] for x in r["dias"]}}
                              for r in salida["apartamentos"]]
    _escribir(DIR_EJEC / f"{marca}.json", ligero)


# --------------------------------------------------------------------------
# Resumen en texto
# --------------------------------------------------------------------------

def _pct(v):
    return "—" if v is None else f"{v:.0%}"


def resumen(salida: dict) -> str:
    L = [f"PRECIOS DINÁMICOS · {salida['generado'][:16].replace('T', ' ')} · modo {salida['modo']} · "
         f"estrategia {salida['estrategia']}"]
    if salida.get("mercado_dia"):
        L.append(f"Mercado: rastreo del {salida['mercado_dia']}")
    for a in salida["avisos"]:
        L.append(f"  ! {a}")
    for r in salida["apartamentos"]:
        a, b, k = r["apartamento"], r["base"], r["indicadores"]
        m = r["mercado"]
        L.append("")
        L.append(f"== {a['nombre']} ({a['dormitorios']} dorm.) · base {b['valor']:.0f} € "
                 f"(configurada {b['configurada']:.0f}; límites {a['precio_minimo']:.0f}–{a['precio_maximo']:.0f})")
        L.append(f"   Base: {b['motivo']}"
                 + (f" Propuesta: {b['propuesta']:.0f} €{' (aplicada)' if b['aplicada'] else ''}." if b['propuesta'] != b['anterior'] else ""))
        if m.get("disponible"):
            n = {int(k): v for k, v in m["nivel"].items()}  # tras leer de JSON las claves son texto
            L.append(f"   Mercado ({m['anuncios']} comparables, oferta {m['oferta']}): precio por noche en Airbnb "
                     f"P25 {n[25]:.0f} · mediana {n[50]:.0f} · P75 {n[75]:.0f} €  → nuestro precio en Airbnb con la base: "
                     f"{b['valor'] * k['recargo_airbnb']:.0f} €")
        L.append(f"   Ocupación propia: últimos 90 días {_pct(k['ocupacion_pasada_90'])} · próximos 30 {_pct(k['30']['ocupacion'])} "
                 f"(mercado {_pct(k['30']['ocupacion_mercado'])}) · próximos 90 {_pct(k['90']['ocupacion'])} "
                 f"(mercado {_pct(k['90']['ocupacion_mercado'])})")
        for n_ in ("30", "90", "365"):
            t = k[n_]
            L.append(f"   Noches libres a {n_} días: {t['noches_libres']} · precio medio actual {t['precio_medio_actual']} → "
                     f"nuevo {t['precio_medio_nuevo']} € (mediana mercado {t['mediana_mercado']})")
        L.append(f"   Cambian {k['noches_que_cambian']} noches libres: {k['suben']} suben, {k['bajan']} bajan")
        prox = [x for x in r["dias"] if x["libre"]][:14]
        if prox:
            L.append("   Próximas noches libres: " + " · ".join(
                f"{x['fecha'][8:]}/{x['fecha'][5:7]} {x['actual'] or '—'}→{x['precio']:.0f}€/{x['minimo']}n" for x in prox))
    if salida.get("bloqueado_por"):
        L += ["", "ENVÍO BLOQUEADO: " + "; ".join(salida["bloqueado_por"])]
    elif salida.get("aplicado"):
        L += ["", f"ENVIADO A SMOOBU: {salida['envio']['enviado']} · verificación: {salida['envio']['verificacion']}"]
    else:
        L += ["", "Simulación: no se ha enviado nada a Smoobu."]
    return "\n".join(L)
