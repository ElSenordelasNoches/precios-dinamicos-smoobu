"""Línea de órdenes:  python -m precios <orden>

  estado      Comprueba la conexión con Smoobu y lista los apartamentos.
  escanear    Rastrea el mercado y guarda la foto del día.   [--rapido]
  calcular    Calcula los precios y enseña el resumen. No envía nada.
  ejecutar    Rastrea, calcula y, en modo automático, envía a Smoobu.
              [--sin-escanear] [--aplicar] [--no-aplicar] [--rapido]
  resumen     Enseña el resumen de la última ejecución.
  panel       Abre el panel de configuración en el navegador.  [--puerto N]
  clave-panel Fija la contraseña para entrar al panel desde fuera.
              [--host dominio] [--pedir]  (sin --pedir genera una y la deja
              en datos/clave_panel.txt)
"""
from __future__ import annotations

import argparse
import sys

from . import config, ejecucion, mercado


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    p = argparse.ArgumentParser(prog="precios", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="orden", required=True)
    sub.add_parser("estado")
    e = sub.add_parser("escanear"); e.add_argument("--rapido", action="store_true")
    sub.add_parser("calcular")
    x = sub.add_parser("ejecutar")
    x.add_argument("--sin-escanear", action="store_true")
    x.add_argument("--rapido", action="store_true")
    g = x.add_mutually_exclusive_group()
    g.add_argument("--aplicar", action="store_true")
    g.add_argument("--no-aplicar", action="store_true")
    sub.add_parser("resumen")
    pa = sub.add_parser("panel"); pa.add_argument("--puerto", type=int, default=8777)
    pa.add_argument("--sin-navegador", action="store_true")
    cp = sub.add_parser("clave-panel")
    cp.add_argument("--host")
    cp.add_argument("--pedir", action="store_true")
    a = p.parse_args(argv)

    if a.orden == "clave-panel":
        import getpass
        import secrets
        from . import panel
        if a.pedir:
            clave = getpass.getpass("Nueva contraseña del panel: ")
            if len(clave) < 10 or clave != getpass.getpass("Repítala: "):
                print("No coinciden o tiene menos de 10 caracteres. No se ha cambiado nada.")
                return 1
            panel.fijar_clave(clave, a.host)
            print("Contraseña guardada.")
        else:
            letras = "abcdefghjkmnpqrstuvwxyz23456789"
            clave = "-".join("".join(secrets.choice(letras) for _ in range(5)) for _ in range(4))
            panel.fijar_clave(clave, a.host)
            ruta = config.DATOS / "clave_panel.txt"
            ruta.parent.mkdir(exist_ok=True)
            ruta.write_text(clave + "\n", encoding="utf-8")
            print(f"Contraseña generada y guardada en {ruta}. Apúntela y borre ese archivo.")
        return 0

    if a.orden == "panel":
        from . import panel
        panel.servir(a.puerto, abrir=not a.sin_navegador)
        return 0

    cfg = config.cargar()
    if a.orden == "estado":
        c = ejecucion.cliente_smoobu()
        if not c:
            print("Sin credenciales: falta secretos.json (api_key y api_secret de Smoobu).")
            return 1
        print("Conexión con Smoobu correcta. Apartamentos de la cuenta:")
        for ap in c.apartamentos():
            marca = "configurado" if any(int(x["id"]) == ap["id"] for x in cfg["apartamentos"]) else "sin configurar"
            print(f"  {ap['id']}  {ap['name']}  ({marca})")
        return 0
    if a.orden == "escanear":
        foto = mercado.escanear(cfg, rapido=a.rapido)
        print(f"Rastreo guardado: {len(foto['ventanas'])} ventanas, {len(foto['anuncios'])} anuncios, "
              f"{len(foto['errores'])} errores{' (INCOMPLETO)' if foto.get('incompleta') else ''}.")
        return 2 if foto.get("incompleta") else 0
    if a.orden == "calcular":
        salida = ejecucion.calcular_todo(cfg, cliente=ejecucion.cliente_smoobu())
        salida["aplicado"] = False
        ejecucion.guardar(salida)
        print(ejecucion.resumen(salida))
        return 0
    if a.orden == "ejecutar":
        forzar = True if a.aplicar else (False if a.no_aplicar else None)
        salida = ejecucion.ejecutar(cfg, escanear=not a.sin_escanear, forzar_aplicar=forzar, rapido=a.rapido)
        print(ejecucion.resumen(salida))
        return 3 if salida.get("bloqueado_por") else 0
    if a.orden == "resumen":
        salida = ejecucion._leer(ejecucion.RUTA_ULTIMO, None)
        if not salida:
            print("Todavía no hay ninguna ejecución.")
            return 1
        print(ejecucion.resumen(salida))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
