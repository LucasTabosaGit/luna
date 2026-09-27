"""Cria o atalho "Luna": no Windows, na área de trabalho e no menu Iniciar
(criar_atalho_win.py); no Mac, Luna.app em Aplicativos (criar_atalho_mac.py).

    <python do .venv> src/criar_atalho.py
"""
import runpy
import sys
from pathlib import Path

qual = "criar_atalho_mac.py" if sys.platform == "darwin" else "criar_atalho_win.py"
runpy.run_path(str(Path(__file__).with_name(qual)), run_name="__main__")
