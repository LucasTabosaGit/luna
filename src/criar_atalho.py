"""Cria o atalho "Luna" na área de trabalho e no menu Iniciar.

O atalho abre src/abrir.py com o pythonw do .venv (sem console e sem
PowerShell). Roda com o Python do .venv (usa o comtypes das dependências).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from pathlib import Path

from comtypes.client import CreateObject
from comtypes.persist import IPersistFile
from comtypes.shelllink import ShellLink

RAIZ = Path(__file__).resolve().parent.parent
PYTHONW = RAIZ / ".venv" / "Scripts" / "pythonw.exe"


def pasta(csidl: int) -> Path:
    buf = ctypes.create_unicode_buffer(wt.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return Path(buf.value)


def criar(destino: Path) -> None:
    link = CreateObject(ShellLink)
    link.SetPath(str(PYTHONW))
    link.SetArguments('"%s"' % (RAIZ / "src" / "abrir.py"))
    link.SetWorkingDirectory(str(RAIZ))
    link.SetIconLocation(str(RAIZ / "web" / "icone.ico"), 0)
    link.SetDescription("Abre a Luna, assistente de voz")
    link.QueryInterface(IPersistFile).Save(str(destino), True)


def main() -> None:
    area, menu = pasta(0x10), pasta(0x02)          # CSIDL_DESKTOPDIRECTORY, CSIDL_PROGRAMS
    for d in (area, menu):
        criar(d / "Luna.lnk")
    print('  OK  atalho "Luna" na área de trabalho e no menu Iniciar')


if __name__ == "__main__":
    main()
