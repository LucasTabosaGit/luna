"""Cria o atalho "Luna" na área de trabalho e em Aplicativos (Launchpad).

Monta um .app mínimo (Contents/MacOS + Info.plist, sem compilar nada) cujo
executável chama o Python do .venv com src/abrir.py - o mesmo que o
Luna.command chama. Sem console: roda com pythonw-like (Python puro,
sem terminal aparecendo).
"""
from __future__ import annotations

import shutil
import stat
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PYTHON = RAIZ / ".venv" / "bin" / "python3"

INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>CFBundleExecutable</key><string>luna</string>
\t<key>CFBundleIdentifier</key><string>br.com.krosten.luna</string>
\t<key>CFBundleName</key><string>Luna</string>
\t<key>CFBundlePackageType</key><string>APPL</string>
\t<key>CFBundleShortVersionString</key><string>1.0</string>
\t<key>CFBundleIconFile</key><string>icone</string>
\t<key>LSUIElement</key><false/>
\t<key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""

LAUNCHER = """#!/bin/bash
# Abre a Luna: chama o Python do .venv com src/abrir.py.
cd "{raiz}"
exec "{python}" "{raiz}/src/abrir.py"
"""


def criar(destino: Path) -> None:
    """Monta Luna.app em `destino` (uma pasta - ex.: .../Luna.app)."""
    if destino.exists():
        shutil.rmtree(destino)
    macos = destino / "Contents" / "MacOS"
    resources = destino / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    (destino / "Contents" / "Info.plist").write_text(INFO_PLIST, encoding="utf-8")
    launcher = macos / "luna"
    launcher.write_text(LAUNCHER.format(raiz=RAIZ, python=PYTHON), encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    icone = RAIZ / "web" / "icone.icns"
    if icone.exists():
        shutil.copy(icone, resources / "icone.icns")


def main() -> None:
    aplicativos = Path.home() / "Applications"
    aplicativos.mkdir(exist_ok=True)
    criar(aplicativos / "Luna.app")
    try:
        area = Path.home() / "Desktop" / "Luna.app"
        if area.exists() or area.is_symlink():
            area.unlink()
        area.symlink_to(aplicativos / "Luna.app")
    except OSError:
        pass
    print('  OK  atalho "Luna" em Aplicativos e na área de trabalho')


if __name__ == "__main__":
    main()
