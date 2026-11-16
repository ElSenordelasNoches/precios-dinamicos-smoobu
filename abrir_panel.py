"""Abre el panel desde cualquier carpeta:  python abrir_panel.py [--sin-navegador]"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
os.chdir(AQUI)
sys.path.insert(0, AQUI)

from precios.__main__ import main  # noqa: E402

raise SystemExit(main(["panel", *sys.argv[1:]]))
