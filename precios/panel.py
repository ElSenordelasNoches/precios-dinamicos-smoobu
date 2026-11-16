"""Panel de configuración (http://127.0.0.1:8777).

El servidor solo escucha en la propia máquina. Desde ella se entra sin más.
Para verlo desde fuera (el móvil) se publica con un túnel; entonces:

* solo se atiende el nombre de dominio configurado;
* hay que iniciar sesión con contraseña (se guarda solo su huella, con sal);
* los intentos fallidos se limitan por dirección;
* las peticiones que cambian algo exigen además la cabecera ``X-Panel``, que
  ninguna otra web puede enviar en nombre del usuario.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config, ejecucion, mercado

HTML = Path(__file__).with_name("panel.html")
LOCALES = ("127.0.0.1", "localhost")
SESION_DIAS = 30
MAX_FALLOS, BLOQUEO_S = 5, 15 * 60

_rastreo = {"activo": False, "lineas": [], "error": None}
_cerrojo = threading.Lock()
_fallos: dict[str, list[float]] = {}


# --------------------------------------------------------------------------
# Contraseña y sesiones
# --------------------------------------------------------------------------

def _huella(clave: str, sal: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), sal, 300_000).hex()


def fijar_clave(clave: str, host: str | None = None) -> None:
    """Guarda en secretos.json la huella de la contraseña del panel."""
    datos = json.loads(config.RUTA_SECRETOS.read_text(encoding="utf-8")) if config.RUTA_SECRETOS.exists() else {}
    previo = datos.get("panel") or {}
    sal = secrets.token_bytes(16)
    datos["panel"] = {"sal": sal.hex(), "huella": _huella(clave, sal),
                      "firma": secrets.token_hex(32),  # al cambiarla caducan las sesiones abiertas
                      "host": host or previo.get("host")}
    config.RUTA_SECRETOS.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


def _acceso() -> dict:
    try:
        return json.loads(config.RUTA_SECRETOS.read_text(encoding="utf-8")).get("panel") or {}
    except (OSError, ValueError):
        return {}


def _clave_correcta(clave: str) -> bool:
    a = _acceso()
    if not a.get("huella"):
        return False
    return hmac.compare_digest(_huella(clave, bytes.fromhex(a["sal"])), a["huella"])


def _galleta(firma: str) -> str:
    caduca = str(int(time.time()) + SESION_DIAS * 86400)
    sello = hmac.new(firma.encode(), caduca.encode(), hashlib.sha256).hexdigest()
    return f"{caduca}.{sello}"


def _galleta_valida(valor: str, firma: str) -> bool:
    caduca, _, sello = (valor or "").partition(".")
    if not (caduca.isdigit() and sello):
        return False
    bueno = hmac.new(firma.encode(), caduca.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sello, bueno) and int(caduca) > time.time()


PAGINA_ENTRADA = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Precios dinámicos</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f5f3ee;color:#1d2430;font:16px/1.4 "Segoe UI",system-ui,sans-serif}
@media (prefers-color-scheme: dark){body{background:#12161c;color:#e8ebef}form{background:#1b212a!important;border-color:#2c3440!important}input{background:#12161c!important;color:#e8ebef!important;border-color:#2c3440!important}}
form{background:#fff;border:1px solid #e4e0d6;border-radius:14px;padding:26px;width:min(340px,88vw)}
h1{font-size:18px;margin:0 0 16px}label{font-size:13px;opacity:.75}
input{width:100%;box-sizing:border-box;font:inherit;padding:10px;margin:6px 0 14px;border:1px solid #d5d0c5;border-radius:8px}
button{width:100%;font:inherit;padding:10px;border:0;border-radius:8px;background:#0f6e6a;color:#fff;cursor:pointer}
p{color:#b3401f;font-size:14px;margin:0 0 12px}</style></head><body>
<form method="post" action="/entrar"><h1>Precios dinámicos</h1>__AVISO__
<label for="c">Contraseña</label><input id="c" name="clave" type="password" autocomplete="current-password" autofocus required>
<button>Entrar</button></form></body></html>"""


# --------------------------------------------------------------------------
# Datos
# --------------------------------------------------------------------------

def _datos() -> dict:
    return {
        "config": config.cargar(),
        "ultimo": ejecucion._leer(ejecucion.RUTA_ULTIMO, None),
        "credenciales": bool(config.secretos()),
        "estado": {k: v for k, v in ejecucion._leer(ejecucion.RUTA_ESTADO, {}).items()
                   if k not in ("enviados", "referencia")},
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


# --------------------------------------------------------------------------
# Servidor
# --------------------------------------------------------------------------

class Manejador(BaseHTTPRequestHandler):
    server_version = "PreciosDinamicos/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # -- quién llama ----------------------------------------------------------
    def _anfitrion(self) -> str:
        return (self.headers.get("Host") or "").split(":")[0].lower()

    def _remota(self) -> bool:
        """Llega por el túnel (o por cualquier intermediario), no desde esta máquina."""
        return (self._anfitrion() not in LOCALES
                or any(self.headers.get(c) for c in ("Cf-Connecting-Ip", "Cf-Ray", "X-Forwarded-For")))

    def _origen(self) -> str:
        return self.headers.get("Cf-Connecting-Ip") or self.client_address[0]

    def _permitida(self) -> bool:
        if not self._remota():
            return True
        a = _acceso()
        return bool(a.get("huella") and a.get("host") and self._anfitrion() == a["host"].lower())

    def _con_sesion(self) -> bool:
        if not self._remota():
            return True
        firma = _acceso().get("firma") or ""
        for trozo in (self.headers.get("Cookie") or "").split(";"):
            nombre, _, valor = trozo.strip().partition("=")
            if nombre == "sesion" and firma and _galleta_valida(valor, firma):
                return True
        return False

    # -- respuestas -----------------------------------------------------------
    def _enviar(self, codigo: int, cuerpo: bytes, tipo: str, extra: dict | None = None) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, codigo: int, datos) -> None:
        self._enviar(codigo, json.dumps(datos, ensure_ascii=False, default=str).encode("utf-8"),
                     "application/json; charset=utf-8")

    def _ir(self, destino: str, extra: dict | None = None) -> None:
        self._enviar(303, b"", "text/plain", {"Location": destino, **(extra or {})})

    def _entrada(self, aviso: str = "", codigo: int = 200) -> None:
        pagina = PAGINA_ENTRADA.replace("__AVISO__", f"<p>{aviso}</p>" if aviso else "")
        self._enviar(codigo, pagina.encode("utf-8"), "text/html; charset=utf-8")

    def _cuerpo(self) -> bytes:
        largo = min(int(self.headers.get("Content-Length") or 0), 2_000_000)
        return self.rfile.read(largo) if largo else b""

    # -- GET --------------------------------------------------------------------
    def do_GET(self):
        ruta = self.path.split("?")[0]
        if not self._permitida():
            return self._enviar(404, b"No existe", "text/plain; charset=utf-8")
        if ruta == "/entrar":
            return self._ir("/") if self._con_sesion() else self._entrada()
        if ruta == "/salir":
            return self._ir("/entrar", {"Set-Cookie": "sesion=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax"})
        if not self._con_sesion():
            return self._json(401, {"error": "sesión caducada"}) if ruta.startswith("/api/") else self._ir("/entrar")
        if ruta in ("/", "/index.html"):
            self._enviar(200, HTML.read_bytes(), "text/html; charset=utf-8")
        elif ruta == "/api/datos":
            d = _datos()
            d["remoto"] = self._remota()
            self._json(200, d)
        else:
            self._json(404, {"error": "no existe"})

    def do_HEAD(self):
        self._enviar(200 if self._permitida() else 404, b"", "text/html; charset=utf-8")

    # -- POST -------------------------------------------------------------------
    def do_POST(self):
        ruta = self.path.split("?")[0]
        # El cuerpo se lee siempre, aunque se vaya a rechazar la petición: el
        # túnel reutiliza las conexiones y lo que quedara sin leer se tomaría
        # por el principio de la petición siguiente.
        crudo = self._cuerpo()
        if not self._permitida():
            return self._enviar(404, b"No existe", "text/plain; charset=utf-8")
        if ruta == "/entrar":
            return self._entrar(crudo)
        if not self._con_sesion():
            return self._json(401, {"error": "sesión caducada"})
        if self.headers.get("X-Panel") != "1":
            return self._json(403, {"error": "petición no permitida"})
        try:
            cuerpo = json.loads(crudo.decode("utf-8") or "{}")
        except ValueError:
            return self._json(400, {"error": "JSON no válido"})
        try:
            if ruta == "/api/simular":
                return self._json(200, _simular(cuerpo))
            if ruta == "/api/config":
                with _cerrojo:
                    config.guardar(cuerpo)
                    salida = _simular(cuerpo)
                    ejecucion.guardar(salida)
                return self._json(200, salida)
            if ruta == "/api/aplicar":
                if cuerpo.get("confirmar") is not True:
                    return self._json(400, {"error": "falta la confirmación"})
                with _cerrojo:
                    salida = ejecucion.ejecutar(config.cargar(), escanear=False, forzar_aplicar=True,
                                                avisar=lambda *_: None)
                return self._json(200, salida)
            if ruta == "/api/escanear":
                if not _rastreo["activo"]:
                    _rastreo.update(activo=True, lineas=[], error=None)
                    threading.Thread(target=_rastrear, daemon=True).start()
                return self._json(200, _rastreo)
        except ValueError as e:
            return self._json(422, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        self._json(404, {"error": "no existe"})

    def _entrar(self, crudo: bytes):
        quien, ahora = self._origen(), time.time()
        recientes = [t for t in _fallos.get(quien, []) if ahora - t < BLOQUEO_S]
        if len(recientes) >= MAX_FALLOS:
            return self._entrada("Demasiados intentos. Espere un cuarto de hora.", 429)
        campos = urllib.parse.parse_qs(crudo.decode("utf-8", "replace"))
        clave = (campos.get("clave") or [""])[0]
        time.sleep(0.4)  # frena los intentos en cadena
        if not _clave_correcta(clave):
            _fallos[quien] = recientes + [ahora]
            return self._entrada("Contraseña incorrecta.", 401)
        _fallos.pop(quien, None)
        galleta = _galleta(_acceso()["firma"])
        seguro = "; Secure" if self._remota() else ""
        self._ir("/", {"Set-Cookie": f"sesion={galleta}; Max-Age={SESION_DIAS * 86400}; Path=/; HttpOnly{seguro}; SameSite=Lax"})


def servir(puerto: int = 8777, abrir: bool = True) -> None:
    servidor = ThreadingHTTPServer(("127.0.0.1", puerto), Manejador)
    servidor.daemon_threads = True
    url = f"http://127.0.0.1:{puerto}/"
    print(f"Panel de precios dinámicos en {url}  (Ctrl+C para cerrarlo)", flush=True)
    if abrir:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nPanel cerrado.")
