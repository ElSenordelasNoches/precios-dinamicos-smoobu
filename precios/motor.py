"""Motor de precios: del precio base al precio de cada noche.

    precio = base × estacionalidad × día de la semana × eventos
                  × demanda del mercado × antelación × ritmo propio
                  × huecos × ajustes manuales
    … y después: límites, redondeo, cambio máximo por ejecución.

Cada factor se guarda como porcentaje junto al precio, de manera que siempre
se puede explicar por qué una noche cuesta lo que cuesta.
"""
from __future__ import annotations

import datetime as dt
import math

from . import config, festivos
from .calendario import Calendario

UN_DIA = dt.timedelta(days=1)


def _acotar(v, lo, hi):
    return max(lo, min(hi, v))


def _en_rango(d: dt.date, item: dict) -> bool:
    return festivos.leer_fecha(item["desde"]) <= d <= festivos.leer_fecha(item["hasta"])


def _estacional_manual(meses: dict, d: dt.date) -> float:
    """% del mes, suavizado entre el día 15 de un mes y el del siguiente."""
    def pct(anio, mes):
        return float(meses.get(str(mes), 0))
    centro = dt.date(d.year, d.month, 15)
    if d >= centro:
        sig = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 15)
        t = (d - centro).days / (sig - centro).days
        return pct(d.year, d.month) * (1 - t) + pct(sig.year, sig.month) * t
    ant = dt.date(d.year - (d.month == 1), (d.month - 2) % 12 + 1, 15)
    t = (d - ant).days / (centro - ant).days
    return pct(ant.year, ant.month) * (1 - t) + pct(d.year, d.month) * t


# --------------------------------------------------------------------------
# Precio base efectivo (autoajuste)
# --------------------------------------------------------------------------

def base_efectiva(apto: dict, reglas: dict, cal: Calendario, resumen_mercado: dict,
                  estado: dict, hoy: dt.date, ocupacion_mercado_30: float | None = None) -> dict:
    """Decide con qué precio base se calcula y qué se recomienda hacer con él."""
    cfg_base = float(apto["precio_base"])
    ba = reglas["base_automatica"]
    objetivo = reglas["ritmo_ocupacion"]["objetivo_ocupacion_pct"] / 100
    previo = (estado.get("base") or {}).get(str(apto["id"])) or {}
    if previo.get("config_base") != cfg_base:
        previo = {}  # el usuario ha cambiado la base: se parte de la nueva
    actual = float(previo.get("valor", cfg_base))

    # Sin historial (apartamento nuevo o calendario sin pasado) no se opina.
    hay_historial = any(r["llegada"] < hoy for r in cal.reservas)
    occ_pasada = cal.ocupacion(hoy - dt.timedelta(days=60), hoy) if hay_historial else None
    occ_proxima = cal.ocupacion(hoy, hoy + dt.timedelta(days=30))
    recargo = 1 + reglas["canales"].get("airbnb", 0) / 100
    nivel = (resumen_mercado or {}).get("nivel") or {}
    pct = int(ba.get("percentil_mercado", 50))
    ref = nivel.get(pct) or nivel.get(min(nivel, key=lambda k: abs(k - pct))) if nivel else None
    mercado_equiv = ref / recargo if ref else None

    motivo, direccion = "Sin cambios: la ocupación está en la zona objetivo.", 0
    if occ_pasada is not None:
        # Se llena: o bien roza el objetivo, o bien va claramente por delante
        # de la competencia en los próximos 30 días.
        gana_al_mercado = (ocupacion_mercado_30 is not None and occ_proxima is not None
                           and occ_proxima >= ocupacion_mercado_30 + 0.10)
        se_llena = occ_pasada >= objetivo - 0.10 or (gana_al_mercado and occ_pasada >= objetivo - 0.20)
        if se_llena:
            if mercado_equiv and actual < mercado_equiv * 0.97:
                direccion = 1
                motivo = (f"Ocupación del {occ_pasada:.0%} en los últimos 60 días con un precio base "
                          f"de {actual:.0f} €, por debajo del equivalente de mercado ({mercado_equiv:.0f} €): "
                          f"hay margen para subir.")
            elif occ_pasada >= 0.92:
                direccion = 0.5
                motivo = (f"Ocupación del {occ_pasada:.0%} en los últimos 60 días: se llena "
                          f"aunque ya está al nivel del mercado; se prueba un escalón más.")
        elif occ_pasada < objetivo - 0.20 and (occ_proxima or 0) < objetivo * 0.6:
            if not mercado_equiv or actual > mercado_equiv * 0.9:
                direccion = -1
                motivo = (f"Ocupación del {occ_pasada:.0%} en los últimos 60 días y del "
                          f"{(occ_proxima or 0):.0%} en los próximos 30: conviene bajar.")
            else:
                motivo = (f"Ocupación baja ({occ_pasada:.0%}) pero el precio ya está por debajo del "
                          f"mercado: bajar más la base no es la solución; se deja como está.")
    paso = ba["paso_max_pct"] / 100 * abs(direccion)
    propuesta = actual
    if direccion > 0:
        techo = mercado_equiv if (mercado_equiv and direccion == 1) else actual * (1 + paso)
        propuesta = min(actual * (1 + paso), max(techo, actual))
    elif direccion < 0:
        suelo = mercado_equiv if mercado_equiv and mercado_equiv < actual else actual * (1 - paso)
        propuesta = max(actual * (1 - paso), min(suelo, actual))
    margen = ba["margen_pct"] / 100
    propuesta = _acotar(propuesta, cfg_base * (1 - margen), cfg_base * (1 + margen))
    propuesta = _acotar(propuesta, float(apto["precio_minimo"]), float(apto["precio_maximo"]))
    propuesta = round(propuesta, 1)

    ultimo = previo.get("fecha")
    dias_desde = (hoy - dt.date.fromisoformat(ultimo)).days if ultimo else None
    toca = dias_desde is None or dias_desde >= ba["cada_dias"]
    aplicar = bool(ba["activa"] and toca and abs(propuesta - actual) >= 0.5)
    valor = propuesta if aplicar else actual
    if not ba["activa"]:
        valor = cfg_base
    return {
        "configurada": cfg_base, "anterior": actual, "valor": valor, "propuesta": propuesta,
        "aplicada": aplicar, "automatica": bool(ba["activa"]), "motivo": motivo,
        "mercado_equivalente": round(mercado_equiv, 1) if mercado_equiv else None,
        "ocupacion_60_pasados": occ_pasada, "ocupacion_30_proximos": occ_proxima,
        "dias_desde_ultimo_cambio": dias_desde,
        "tope": round(cfg_base * (1 + margen), 1), "suelo": round(cfg_base * (1 - margen), 1),
    }


# --------------------------------------------------------------------------
# Cálculo por apartamento
# --------------------------------------------------------------------------

def calcular(cfg: dict, apto: dict, cal: Calendario, modelo, hoy: dt.date,
             estado: dict | None = None, eventos_extra: list | None = None) -> dict:
    estado = estado or {}
    reglas = config.reglas_de(cfg, apto)
    estr = config.ESTRATEGIAS[cfg["estrategia"]]
    horizonte = int(cfg["horizonte_dias"])
    fin = hoy + dt.timedelta(days=horizonte)
    dorm = apto.get("dormitorios")
    hay_mercado = bool(modelo and modelo.disponible)
    resumen = modelo.resumen(dorm) if hay_mercado else {"disponible": False}

    occ_m30 = None
    if hay_mercado:
        vals = [s_["ocupacion"] for s_ in (modelo.para_fecha(hoy + i * UN_DIA, dorm) for i in range(30))
                if s_ and s_.get("ocupacion") is not None]
        occ_m30 = sum(vals) / len(vals) if vals else None
    base_info = base_efectiva(apto, reglas, cal, resumen, estado, hoy, occ_m30)

    # Curva de antelación: la propia si hay historial; si no, la del mercado.
    origen_curva, _ = cal.curva_antelacion()
    curva_m = resumen.get("curva") if hay_mercado else None
    if origen_curva == "por defecto" and curva_m:
        tope_m = curva_m["a"] + curva_m["b"]

        def reservado_a(antelacion):
            return min(1.0, (curva_m["a"] + curva_m["b"] * math.exp(-antelacion / curva_m["tau"])) / tope_m)
        origen_curva = "mercado"
    else:
        reservado_a = cal.reservado_a
    base = base_info["valor"]
    pmin, pmax = float(apto["precio_minimo"]), float(apto["precio_maximo"])
    min_defecto = int(apto.get("estancia_minima") or 1)
    redondeo = float(reglas["limites"].get("redondeo") or 1)

    eventos = list(reglas.get("eventos") or []) + list(eventos_extra or [])
    if reglas.get("festivos_automaticos", True):
        eventos += festivos.eventos_automaticos(hoy, fin)
    ajustes = [a for a in reglas.get("ajustes_manuales") or []
               if not a.get("apartamentos") or apto["id"] in a["apartamentos"]]
    enviados = (estado.get("enviados") or {}).get(str(apto["id"])) or {}
    # El cambio máximo se mide contra el precio que había al empezar el día, para
    # que repetir la ejecución el mismo día no vuelva a mover los precios.
    ref_dia = (estado.get("referencia") or {}).get(str(apto["id"])) or {}
    referencia = ref_dia.get("precios", {}) if ref_dia.get("dia") == hoy.isoformat() else {}

    est, dsem, dem = reglas["estacionalidad"], reglas["dia_semana"], reglas["demanda_mercado"]
    uh, lej, rit, hue = (reglas["ultima_hora"], reglas["antelacion_lejana"],
                         reglas["ritmo_ocupacion"], reglas["huecos"])
    emin, lim = reglas["estancia_minima"], reglas["limites"]
    ec = reglas.get("estancias_cortas") or {}
    limpieza = float(ec.get("coste_limpieza", 0))
    beneficio_min = float(ec.get("beneficio_minimo_reserva", 0))
    objetivo = rit["objetivo_ocupacion_pct"] / 100
    media = max(1, int(rit["ventana_dias"]) // 2)
    recargo = 1 + reglas["canales"].get("airbnb", 0) / 100
    nivel25 = (resumen.get("nivel") or {}).get(25)

    dias = []
    d = hoy
    while d < fin:
        antel = (d - hoy).days
        info = cal.dias.get(d) or {}
        actual, min_actual = info.get("precio"), info.get("minimo")
        libre = cal.libre(d)
        senal = modelo.para_fecha(d, dorm) if hay_mercado else None
        f: dict[str, float] = {}
        notas: list[str] = []

        # 1. Estacionalidad
        manual = _estacional_manual(est["meses"], d)
        if senal and est["modo"] != "manual":
            indice = _acotar(senal["indice"] or 1.0, 0.8, 1.4)
            if est["modo"] == "mercado":
                f["estacionalidad"] = (indice - 1) * 100
            else:
                w = _acotar(float(est["peso_mercado"]), 0, 1)
                f["estacionalidad"] = ((1 + manual / 100) ** (1 - w) * indice ** w - 1) * 100
        else:
            f["estacionalidad"] = manual

        # 2. Día de la semana
        f["dia_semana"] = float(dsem.get(config.DIAS[d.weekday()], 0))

        # 3. Eventos y festivos
        ev_factor, ev_min = 1.0, None
        for e in eventos:
            if _en_rango(d, e):
                ev_factor *= 1 + float(e.get("ajuste_pct", 0)) / 100
                notas.append(e["nombre"])
                if e.get("estancia_minima"):
                    ev_min = max(ev_min or 0, int(e["estancia_minima"]))
        f["eventos"] = (ev_factor - 1) * 100

        # 4. Demanda del mercado (ocupación frente a lo normal + ritmo de reservas)
        f["demanda_mercado"] = 0.0
        if senal and dem["activa"]:
            desv = (senal.get("desviacion") or 0.0) + (senal.get("ritmo") or 0.0)
            # Lejos en el tiempo casi nadie ha reservado aún: la señal pesa menos.
            confianza = 1.0 if antel <= 60 else max(0.35, 1 - (antel - 60) / 200)
            f["demanda_mercado"] = _acotar(dem["sensibilidad"] * estr["demanda"] * 80 * desv * confianza,
                                           -dem["tope_bajada_pct"], dem["tope_subida_pct"])

        # 5. Antelación: última hora y fechas lejanas
        f["antelacion"] = 0.0
        if uh["activa"] and antel < uh["dias"]:
            f["antelacion"] = -uh["descuento_max_pct"] * estr["ultima_hora"] * (1 - antel / uh["dias"])
        elif lej["activa"] and antel > lej["desde_dias"]:
            tramo = max(1, lej["plena_a_dias"] - lej["desde_dias"])
            f["antelacion"] = lej["prima_max_pct"] * estr["lejana"] * min(1.0, (antel - lej["desde_dias"]) / tramo)

        # 6. Ritmo de ocupación propio. En un hueco corto no se aplica: por
        # definición está rodeado de reservas y ya tiene su propia regla.
        hueco = cal.hueco(d, hoy, fin) if libre else None
        es_hueco_corto = hue["activa"] and hueco is not None and hueco < max(min_defecto, 4)
        f["ritmo_ocupacion"] = 0.0
        retrasado = False
        if not es_hueco_corto:
            a, b = max(hoy, d - media * UN_DIA), min(fin, d + (media + 1) * UN_DIA)
            occ = cal.ocupacion(a, b)
            if occ is not None:
                n, esperado, x = 0, 0.0, a
                while x < b:
                    esperado += objetivo * reservado_a((x - hoy).days)
                    n += 1
                    x += UN_DIA
                dif = occ - esperado / n
                retrasado = dif < -0.05
                if rit["activa"]:
                    tope = rit["tope_subida_pct"] * (0.5 if antel > 90 else 1.0)
                    f["ritmo_ocupacion"] = _acotar(rit["sensibilidad"] * estr["ritmo"] * 60 * dif,
                                                   -rit["tope_bajada_pct"], tope)

        # 7. Huecos entre reservas
        f["hueco"] = 0.0
        if es_hueco_corto:
            for regla in hue["reglas"]:
                if hueco <= regla["noches_max"] and antel >= regla["antelacion_min"]:
                    f["hueco"] = float(regla["ajuste_pct"])
                    break
            notas.append(f"Hueco de {hueco} noche{'s' if hueco != 1 else ''}")

        # 8. Ajustes manuales del panel
        fijo, no_tocar, manual_min = None, False, None
        f["ajuste_manual"] = 0.0
        for aj in ajustes:
            if not _en_rango(d, aj):
                continue
            if aj.get("estancia_minima"):
                manual_min = int(aj["estancia_minima"])
            if aj["tipo"] == "precio_fijo":
                fijo = float(aj["valor"])
            elif aj["tipo"] == "porcentaje":
                f["ajuste_manual"] += float(aj["valor"])
            elif aj["tipo"] == "no_tocar":
                no_tocar = True
            if aj.get("nota"):
                notas.append(aj["nota"])

        # 9. Estancia mínima de la noche
        minimo = min_defecto
        for t in emin.get("temporadas") or []:
            if _en_rango(d, t):
                minimo = int(t["noches"])
        if emin.get("lejana_desde_dias") and emin.get("lejana_noches") and antel >= emin["lejana_desde_dias"]:
            minimo = max(minimo, int(emin["lejana_noches"]))
        if ev_min:
            minimo = max(minimo, ev_min)
        normal = minimo  # la estancia mínima «de catálogo» para esa fecha
        if emin.get("ultima_hora_noches") and antel <= emin.get("ultima_hora_dias", -1):
            minimo = min(minimo, int(emin["ultima_hora_noches"]))
        # Noches libres que no son hueco: al acercarse la fecha se admiten
        # estancias más cortas (si se va retrasado de reservas, o siempre, según la regla).
        relajada = False
        if ec.get("activa") and libre and not es_hueco_corto and not ev_min:
            for regla in sorted(ec.get("relajacion") or [], key=lambda r_: r_["antelacion_max"]):
                if antel <= regla["antelacion_max"] and (retrasado or not regla.get("solo_si_retraso")):
                    if int(regla["noches"]) < minimo:
                        minimo, relajada = int(regla["noches"]), True
                    break
        if hue["activa"] and hue.get("ajustar_estancia_minima") and hueco is not None:
            minimo = min(minimo, hueco)
        if manual_min:
            minimo = manual_min
        minimo = max(1, minimo)

        # 10. Recargo por estancia corta: quien se queda menos noches reparte la
        # limpieza entre menos noches. Solo en noches abiertas a estancias cortas
        # que no son hueco (los huecos ya llevan su propia regla).
        f["estancia_corta"] = 0.0
        if relajada and minimo < normal:
            previo = base
            for v in f.values():
                previo *= 1 + v / 100
            extra = limpieza * (1 / minimo - 1 / normal)
            f["estancia_corta"] = extra / previo * 100
            notas.append("Admite %d noche%s con recargo de limpieza" % (minimo, "" if minimo == 1 else "s"))

        bruto = base
        for v in f.values():
            bruto *= 1 + v / 100

        # Freno de descuentos: si el precio ya queda por debajo del cuartil
        # barato de la competencia, los descuentos por demanda floja o por ir
        # retrasado de reservas no aportan nada y se anulan.
        fm = lim.get("freno_mercado") or {}
        p25 = (senal or {}).get("p25") or nivel25
        if fm.get("activa") and p25 and fijo is None:
            umbral = p25 / recargo * (1 - float(fm.get("holgura_pct", 0)) / 100)
            sin_descuentos = bruto
            for k in ("demanda_mercado", "ritmo_ocupacion"):
                if f[k] < 0:
                    sin_descuentos /= 1 + f[k] / 100
            objetivo_freno = min(sin_descuentos, umbral)
            if bruto < objetivo_freno - 0.01:
                f["freno_mercado"] = (objetivo_freno / bruto - 1) * 100
                bruto = objetivo_freno
                notas.append("Ya está por debajo del mercado: no se descuenta más")

        # 11. Límites
        suelo, techo = pmin, pmax
        ml = lim.get("minimo_lejano") or {}
        if ml and antel >= ml.get("desde_dias", 10**6):
            suelo = max(suelo, base * (1 + ml.get("pct_sobre_base", 0) / 100))
        for t in lim.get("temporadas") or []:
            if _en_rango(d, t):
                suelo = float(t.get("minimo", suelo))
                techo = float(t.get("maximo", techo))
        # Ninguna reserva puede dejar menos que la limpieza más el beneficio mínimo.
        suelo_reserva = (limpieza + beneficio_min) / minimo if ec.get("activa") else 0.0
        por_limpieza = suelo_reserva > suelo
        suelo = min(max(suelo, suelo_reserva), techo)
        precio = _acotar(bruto, suelo, techo)
        if precio != bruto:
            notas.append("Tope máximo" if bruto > techo else
                         ("Mínimo para cubrir limpieza y beneficio" if por_limpieza else "Suelo mínimo"))

        # 12. Cambio máximo respecto al precio vigente
        cmax = float(lim.get("cambio_maximo_pct") or 0)
        if cmax and actual and libre and not es_hueco_corto:
            partida = float(referencia.get(d.isoformat()) or actual)
            acotado = _acotar(precio, partida * (1 - cmax / 100), partida * (1 + cmax / 100))
            if abs(acotado - precio) > 0.01:
                notas.append(f"Cambio limitado al {cmax:.0f} % por ejecución")
                precio = _acotar(acotado, min(max(pmin, suelo_reserva), pmax), pmax)
        precio = round(precio / redondeo) * redondeo
        if precio < min(suelo_reserva, pmax):
            precio = math.ceil(suelo_reserva / redondeo) * redondeo

        # Precios fijados a mano (en el panel o directamente en Smoobu)
        origen = "motor"
        if fijo is not None:
            precio, origen = fijo, "fijo"
        elif no_tocar:
            precio, minimo, origen = actual, (min_actual or minimo), "no_tocar"
        elif reglas.get("respetar_cambios_manuales") and actual is not None:
            previo = enviados.get(d.isoformat())
            if previo is not None and abs(float(previo[0]) - float(actual)) > 0.01:
                precio, minimo, origen = actual, (min_actual or minimo), "manual_smoobu"
                notas.append("Precio cambiado a mano en Smoobu: se respeta")

        dias.append({
            "fecha": d.isoformat(), "libre": libre, "estado": cal.estado(d),
            "actual": actual, "precio": precio, "bruto": round(bruto, 2),
            "minimo_actual": min_actual, "minimo": minimo, "origen": origen,
            "factores": {k: round(v, 1) for k, v in f.items() if abs(v) >= 0.05},
            "notas": notas, "hueco": hueco,
            "mercado": ({"mediana": round(senal["mediana"], 1) if senal.get("mediana") else None,
                         "p25": round(senal["p25"], 1) if senal.get("p25") else None,
                         "p75": round(senal["p75"], 1) if senal.get("p75") else None,
                         "ocupacion": round(senal["ocupacion"], 3) if senal.get("ocupacion") is not None else None,
                         "ocupacion_airbnb": round(senal["ocupacion_airbnb"], 3) if senal.get("ocupacion_airbnb") is not None else None,
                         "ocupacion_booking": round(senal["ocupacion_booking"], 3) if senal.get("ocupacion_booking") is not None else None}
                        if senal else None),
        })
        d += UN_DIA

    # Nuestro anuncio tal como se ve en Airbnb: precio y posición frente a los comparables.
    propio = None
    if hay_mercado and apto.get("airbnb_id"):
        vistos = modelo.precio_propio(apto["airbnb_id"])
        habituales = sorted(modelo.segmento(dorm)["habitual"].values())
        if vistos and habituales:
            orden = sorted(vistos.values())
            mediano = orden[len(orden) // 2]
            propio = {"precio": mediano, "ventanas": len(vistos),
                      "mas_baratos_pct": round(100 * sum(1 for h in habituales if h < mediano) / len(habituales)),
                      "visible_hasta": str(max(vistos))[:10]}

    return {
        "airbnb_propio": propio,
        "apartamento": {k: apto.get(k) for k in ("id", "nombre", "dormitorios", "precio_base",
                                                 "precio_minimo", "precio_maximo", "estancia_minima")},
        "base": base_info, "dias": dias, "mercado": resumen,
        "curva_antelacion": origen_curva,
        "indicadores": indicadores(dias, cal, hoy, reglas, base),
    }


def indicadores(dias: list[dict], cal: Calendario, hoy: dt.date, reglas: dict, base: float) -> dict:
    def tramo(n):
        sel = [x for x in dias[:n]]
        libres = [x for x in sel if x["libre"] and x["precio"] is not None]
        con_actual = [x for x in libres if x["actual"]]
        occ_m = [x["mercado"]["ocupacion"] for x in sel if x["mercado"] and x["mercado"]["ocupacion"] is not None]
        med = [x["mercado"]["mediana"] for x in libres if x["mercado"] and x["mercado"]["mediana"]]
        return {
            "ocupacion": cal.ocupacion(hoy, hoy + dt.timedelta(days=n)),
            "ocupacion_mercado": sum(occ_m) / len(occ_m) if occ_m else None,
            "noches_libres": len(libres),
            "precio_medio_actual": round(sum(x["actual"] for x in con_actual) / len(con_actual), 1) if con_actual else None,
            "precio_medio_nuevo": round(sum(x["precio"] for x in libres) / len(libres), 1) if libres else None,
            "mediana_mercado": round(sum(med) / len(med), 1) if med else None,
        }
    recargo = 1 + reglas["canales"].get("airbnb", 0) / 100
    cambian = [x for x in dias if x["libre"] and x["precio"] is not None
               and (x["actual"] is None or abs(x["precio"] - x["actual"]) > 0.01 or x["minimo"] != x["minimo_actual"])]
    suben = sum(1 for x in cambian if x["actual"] and x["precio"] > x["actual"])
    bajan = sum(1 for x in cambian if x["actual"] and x["precio"] < x["actual"])
    return {
        "30": tramo(30), "60": tramo(60), "90": tramo(90), "365": tramo(len(dias)),
        "ocupacion_pasada_30": cal.ocupacion(hoy - dt.timedelta(days=30), hoy),
        "ocupacion_pasada_90": cal.ocupacion(hoy - dt.timedelta(days=90), hoy),
        "ocupacion_pasada_365": cal.ocupacion(hoy - dt.timedelta(days=365), hoy),
        "antelacion_mediana": cal.antelacion_mediana(),
        "recargo_airbnb": recargo,
        "noches_que_cambian": len(cambian), "suben": suben, "bajan": bajan,
    }


def operaciones(resultado: dict, solo_libres: bool = False) -> dict[dt.date, tuple[float, int]]:
    """Noches cuyo precio o estancia mínima hay que enviar a Smoobu."""
    out = {}
    for x in resultado["dias"]:
        if x["origen"] in ("no_tocar", "manual_smoobu") or x["precio"] is None:
            continue
        if solo_libres and not x["libre"]:
            continue
        if x["actual"] is not None and abs(x["precio"] - x["actual"]) < 0.01 and x["minimo"] == x["minimo_actual"]:
            continue
        out[dt.date.fromisoformat(x["fecha"])] = (float(x["precio"]), int(x["minimo"]))
    return out
