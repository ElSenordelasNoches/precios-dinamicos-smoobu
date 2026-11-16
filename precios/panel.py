"""Panel local de configuración (http://127.0.0.1:8777).

Solo escucha en la propia máquina. Las peticiones que cambian algo exigen la
cabecera ``X-Panel`` y que el anfitrión sea local, para que ninguna otra web
abierta en el navegador pueda dar órdenes al panel.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config, ejecucion, mercado

HTML = Path(__file__).with_name("panel.html")
_rastreo = {"activo": False, "lineas": [], "error": None}
_cerrojo = threading.Lock()


def _datos() -> dict:
    return {
        "config": config.cargar(),
        "ultimo": ejecucion._leer(ejecucion.RUTA_ULTIMO, None),
        "credenciales": bool(config.secretos()),
        "estado": {k: v for k, v in ejecucion._leer(ejecucion.RUTA_ESTADO, {}).items() if k != "enviados"},
        "rastreo": _rastreo,
    }


def _simular(cfg_usuario: dict) -> dict:
    cfg = config.fusionar(config.POR_DEFECTO, cfg_usuario)
    config.validar(cfg)
    salida = ejecucion.calcular_todo(cfg, cliente=ejecucion.cliente_smoobu())
    salida["aplicado"] = False
    return salida


def _rastrear():
    def avisar(t):
        _rastreo["lineas"] = (_rastreo["lineas"] + [str(t)])[-8:]
    try:
        mercado.escanear(config.cargar(), avisar=avisar)
    except Exception as e:
        _rastreo["error"] = str(e)
    finally:
        _rastreo["activo"] = False


class Manejador(BaseHTTPRequestHandler):
    server_version = "PreciosDinamicos/1"

    def log_message(self, *a):
        pass

    def _local(self) -> bool:
        anfitrion = (self.headers.get("Host") or "").split(":")[0]
        return anfitrion in ("127.0.0.1", "localhost")

    def _json(self, codigo: int, datos) -> None:
        cuerpo = json.dumps(datos, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        if not self._local():
            return self._json(403, {"error": "solo local"})
        if self.path in ("/", "/index.html"):
            cuerpo = HTML.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cuerpo)
        elif self.path == "/api/datos":
            self._json(200, _datos())
        else:
            self._json(404, {"error": "no existe"})

    def do_POST(self):
        if not self._local() or self.headers.get("X-Panel") != "1":
            return self._json(403, {"error": "petición no permitida"})
        try:
            largo = int(self.headers.get("Content-Length") or 0)
            cuerpo = json.loads(self.rfile.read(largo).decode("utf-8") or "{}")
        except ValueError:
            return self._json(400, {"error": "JSON no válido"})
        try:
            if self.path == "/api/simular":
                return self._json(200, _simular(cuerpo))
            if self.path == "/api/config":
                with _cerrojo:
                    config.guardar(cuerpo)
                    salida = _simular(cuerpo)
                    ejecucion.guardar(salida)
                return self._json(200, salida)
            if self.path == "/api/aplicar":
                if cuerpo.get("confirmar") is not True:
                    return self._json(400, {"error": "falta la confirmación"})
                with _cerrojo:
                    salida = ejecucion.ejecutar(config.cargar(), escanear=False, forzar_aplicar=True,
                                                avisar=lambda *_: None)
                return self._json(200, salida)
            if self.path == "/api/escanear":
                if not _rastreo["activo"]:
                    _rastreo.update(activo=True, lineas=[], error=None)
                    threading.Thread(target=_rastrear, daemon=True).start()
                return self._json(200, _rastreo)
        except ValueError as e:
            return self._json(422, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        self._json(404, {"error": "no existe"})


def servir(puerto: int = 8777, abrir: bool = True) -> None:
    servidor = ThreadingHTTPServer(("127.0.0.1", puerto), Manejador)
    url = f"http://127.0.0.1:{puerto}/"
    print(f"Panel de precios dinámicos en {url}  (Ctrl+C para cerrarlo)")
    if abrir:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nPanel cerrado.")
