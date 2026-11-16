"""Pruebas del motor con calendarios inventados.  python -m unittest discover tests"""
import datetime as dt
import unittest

from precios import config, festivos, mercado, motor, smoobu
from precios.calendario import Calendario

HOY = dt.date(2026, 11, 16)


def cfg_prueba(**reglas):
    c = config.fusionar(config.POR_DEFECTO, {
        "apartamentos": [{"id": 1, "nombre": "Prueba", "dormitorios": 1, "precio_base": 60,
                          "precio_minimo": 45, "precio_maximo": 120, "estancia_minima": 4}],
        "reglas": reglas})
    config.validar(c)
    return c


def calendario(reservas=(), precio=60, minimo=4, dias=365):
    dd = {HOY + dt.timedelta(days=i): {"libre": True, "precio": precio, "minimo": minimo} for i in range(dias + 1)}
    rs = [{"llegada": a, "salida": b, "creada": None, "canal": "x", "precio": None, "bloqueo": False} for a, b in reservas]
    return Calendario(1, "prueba", dd, rs)


def dia(res, fecha):
    return next(x for x in res["dias"] if x["fecha"] == fecha.isoformat())


class Motor(unittest.TestCase):
    def test_limites_siempre(self):
        c = cfg_prueba()
        r = motor.calcular(c, c["apartamentos"][0], calendario(), None, HOY)
        for x in r["dias"]:
            self.assertGreaterEqual(x["precio"], 45)
            self.assertLessEqual(x["precio"], 120)

    def test_cambio_maximo(self):
        c = cfg_prueba(limites={"cambio_maximo_pct": 10})
        r = motor.calcular(c, c["apartamentos"][0], calendario(precio=60), None, HOY)
        for x in r["dias"]:
            self.assertLessEqual(abs(x["precio"] - 60), 60 * 0.10 + 0.51)

    def test_ultima_hora_baja_y_lejano_sube(self):
        c = cfg_prueba(limites={"cambio_maximo_pct": 0}, ritmo_ocupacion={"activa": False},
                       estacionalidad={"modo": "manual", "meses": {str(m): 0 for m in range(1, 13)}},
                       dia_semana={d: 0 for d in config.DIAS}, festivos_automaticos=False)
        r = motor.calcular(c, c["apartamentos"][0], calendario(), None, HOY)
        self.assertLess(dia(r, HOY + dt.timedelta(days=1))["precio"], 60)
        self.assertEqual(dia(r, HOY + dt.timedelta(days=60))["precio"], 60)
        self.assertGreater(dia(r, HOY + dt.timedelta(days=300))["precio"], 60)

    def test_hueco_ajusta_estancia_minima(self):
        a, b = HOY + dt.timedelta(days=30), HOY + dt.timedelta(days=36)
        cal = calendario([(a, a + dt.timedelta(days=4)), (b, b + dt.timedelta(days=4))])
        c = cfg_prueba()
        r = motor.calcular(c, c["apartamentos"][0], cal, None, HOY)
        x = dia(r, a + dt.timedelta(days=4))
        self.assertEqual(x["hueco"], 2)
        self.assertEqual(x["minimo"], 2)
        self.assertEqual(x["factores"].get("hueco"), 20)
        self.assertFalse(dia(r, a)["libre"])

    def test_precio_fijo_y_no_tocar(self):
        f1, f2 = HOY + dt.timedelta(days=50), HOY + dt.timedelta(days=52)
        c = cfg_prueba(ajustes_manuales=[
            {"desde": f1.isoformat(), "hasta": f1.isoformat(), "tipo": "precio_fijo", "valor": 99},
            {"desde": f2.isoformat(), "hasta": f2.isoformat(), "tipo": "no_tocar", "valor": 0}])
        r = motor.calcular(c, c["apartamentos"][0], calendario(precio=61), None, HOY)
        self.assertEqual(dia(r, f1)["precio"], 99)
        self.assertEqual(dia(r, f2)["precio"], 61)
        self.assertNotIn(f2, motor.operaciones(r))

    def test_respeta_cambio_manual_en_smoobu(self):
        f = HOY + dt.timedelta(days=40)
        estado = {"enviados": {"1": {f.isoformat(): [70, 4]}}}
        cal = calendario(precio=60)
        cal.dias[f]["precio"] = 85  # el usuario lo cambió a mano después de nuestro envío
        c = cfg_prueba()
        r = motor.calcular(c, c["apartamentos"][0], cal, None, HOY, estado)
        self.assertEqual(dia(r, f)["precio"], 85)
        self.assertEqual(dia(r, f)["origen"], "manual_smoobu")

    def test_ocupacion_alta_sube_precio(self):
        c = cfg_prueba(limites={"cambio_maximo_pct": 0})
        libre = motor.calcular(c, c["apartamentos"][0], calendario(), None, HOY)
        a = HOY + dt.timedelta(days=20)
        lleno = calendario([(a, a + dt.timedelta(days=8)), (a + dt.timedelta(days=14), a + dt.timedelta(days=24))])
        ocupado = motor.calcular(c, c["apartamentos"][0], lleno, None, HOY)
        f = a + dt.timedelta(days=10)
        self.assertGreater(dia(ocupado, f)["precio"], dia(libre, f)["precio"])


class Auxiliares(unittest.TestCase):
    def test_pascua(self):
        self.assertEqual(festivos.pascua(2026), dt.date(2026, 4, 5))
        self.assertEqual(festivos.pascua(2027), dt.date(2027, 3, 28))

    def test_agrupar_operaciones(self):
        d = dt.date(2027, 1, 1)
        ops = smoobu.agrupar_operaciones({d: (50.0, 4), d + dt.timedelta(days=1): (50.0, 4),
                                          d + dt.timedelta(days=2): (55.0, 4), d + dt.timedelta(days=4): (55.0, 4)})
        self.assertEqual(ops[0]["dates"], ["2027-01-01:2027-01-02"])
        self.assertEqual(len(ops), 3)

    def test_firma_hmac_del_ejemplo_de_la_documentacion(self):
        # Cadena canónica tal como la describe docs.smoobu.com para un GET con consulta.
        s = smoobu.Smoobu("usr_live_abc123", "your_api_secret")
        cab = s._firmar("GET", "/api/reservations", [("to", "2026-04-10"), ("from", "2026-04-01")], b"")
        self.assertEqual(set(cab), {"X-API-Key", "X-Timestamp", "X-Nonce", "X-Signature"})
        self.assertEqual(len(cab["X-Signature"]), 44)

    def test_rejilla_estable(self):
        a = set(mercado.plan_ventanas(HOY, 365))
        b = set(mercado.plan_ventanas(HOY + dt.timedelta(days=2), 365))
        comunes = {x for x in a & b}
        self.assertGreater(len(comunes), 0.8 * min(len(a), len(b)))

    def test_modelo_mercado(self):
        base = {"dia": HOY.isoformat(), "noches": 4, "anuncios": {str(i): {"dormitorios": 1} for i in range(12)}}
        vs = []
        for k, llegada in enumerate(mercado.plan_ventanas(HOY, 200)):
            disponibles = 12 if k % 5 else 4   # una de cada cinco ventanas, muy ocupada
            vs.append({"llegada": llegada.isoformat(), "noches": 4, "total": disponibles, "completa": True,
                       "precios": {str(i): 80.0 * (1.25 if not k % 5 else 1) for i in range(disponibles)}})
        m = mercado.ModeloMercado([dict(base, ventanas=vs)], hoy=HOY)
        seg = m.segmento(1)
        self.assertEqual(seg["oferta"], 12)
        punta = dt.date.fromisoformat(vs[5]["llegada"])
        s = m.para_fecha(punta, 1)
        self.assertAlmostEqual(s["ocupacion"], 1 - 4 / 12, places=3)
        self.assertGreater(s["indice"], 1.1)
        self.assertGreater(s["desviacion"], 0.2)


if __name__ == "__main__":
    unittest.main()
