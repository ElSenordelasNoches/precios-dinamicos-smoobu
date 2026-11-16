"""Servicio permanente: panel + túnel de Cloudflare.

    python servicio.py              arranca (si ya está en marcha, no hace nada)
    python servicio.py --reiniciar  para el que haya y arranca de nuevo
    python servicio.py --parar      lo para
    python servicio.py --estado     dice si está en marcha

El panel escucha solo en esta máquina (puerto 8777). Si existe
``datos/tunel.yml`` se lanza además ``cloudflared`` con esa configuración para
publicarlo en el dominio propio, y se relanza si se cae.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
os.chdir(AQUI)
sys.path.insert(0, str(AQUI))

DATOS = AQUI / "datos"
PID = DATOS / "servicio.pid"
REGISTRO = DATOS / "servicio.log"
TUNEL = DATOS / "tunel.yml"
PUERTO = 8777


def anotar(texto: str) -> None:
    DATOS.mkdir(exist_ok=True)
    with REGISTRO.open("a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {texto}\n")


def puerto_ocupado() -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", PUERTO)) == 0


def cloudflared() -> str | None:
    for candidato in (shutil.which("cloudflared"),
                      r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
                      r"C:\Program Files\cloudflared\cloudflared.exe"):
        if candidato and Path(candidato).exists():
            return candidato
    return None


def parar() -> bool:
    try:
        pid = int(PID.read_text().strip())
    except (OSError, ValueError):
        return False
    if os.name == "nt":
        r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        ok = r.returncode == 0
    else:
        try:
            os.kill(pid, 15)
            ok = True
        except OSError:
            ok = False
    PID.unlink(missing_ok=True)
    return ok


def vigilar_tunel() -> None:
    exe = cloudflared()
    if not exe:
        anotar("No se encuentra cloudflared: el panel solo se verá desde este equipo.")
        return
    espera = 5
    while True:
        anotar("Arrancando el túnel.")
        with (DATOS / "tunel.log").open("ab") as salida:
            p = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--config", str(TUNEL), "run"],
                                 stdin=subprocess.DEVNULL, stdout=salida, stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            inicio = time.time()
            p.wait()
        anotar(f"El túnel se ha parado (código {p.returncode}); se relanza en {espera} s.")
        espera = 5 if time.time() - inicio > 120 else min(espera * 2, 300)
        time.sleep(espera)


def arrancar() -> int:
    if puerto_ocupado():
        print("El servicio ya está en marcha.")
        return 0
    DATOS.mkdir(exist_ok=True)
    PID.write_text(str(os.getpid()))
    anotar(f"Servicio arrancado (PID {os.getpid()}).")
    if TUNEL.exists():
        threading.Thread(target=vigilar_tunel, daemon=True).start()
    from precios import panel
    try:
        panel.servir(PUERTO, abrir=False)
    finally:
        anotar("Servicio detenido.")
    return 0


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--estado":
        print("En marcha." if puerto_ocupado() else "Parado.")
        return 0
    if arg == "--parar":
        print("Parado." if parar() else "No estaba en marcha.")
        return 0
    if arg == "--reiniciar":
        parar()
        for _ in range(20):
            if not puerto_ocupado():
                break
            time.sleep(0.5)
    return arrancar()


if __name__ == "__main__":
    raise SystemExit(main())
