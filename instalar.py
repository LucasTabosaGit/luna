"""Instala a Luna: ambiente Python (.venv), dependências, .env e atalho.

Serve para Windows e Mac (descobre sozinho em qual está):
    Windows:  py -3.11 instalar.py        (o Luna.bat faz isso por você)
    Mac:      python3.11 instalar.py      (o Luna.command faz isso por você)
Só usa a biblioteca padrão. Não precisa de PowerShell nem de administrador.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
MAC = sys.platform == "darwin"
VPY = RAIZ / ".venv" / ("bin/python3" if MAC else "Scripts/python.exe")


def ok(t: str) -> None:
    print("  OK  " + t, flush=True)


def passo(t: str) -> None:
    print("  ..  " + t, flush=True)


def falha(t: str) -> None:
    print("  !!  " + t)
    sys.exit(1)


def pip(*args: str) -> None:
    r = subprocess.run([str(VPY), "-m", "pip", *args])
    if r.returncode != 0:
        falha("pip falhou (veja a mensagem acima)")


def main() -> None:
    print("\n  Luna - instalação (%s)\n" % ("Mac" if MAC else "Windows"))
    if sys.version_info[:2] != (3, 11):
        falha("Precisa do Python 3.11 (este é %d.%d). Instale: %s" % (
            *sys.version_info[:2], "brew install python@3.11" if MAC else "winget install Python.Python.3.11"))
    ok("Python %d.%d" % sys.version_info[:2])
    if MAC and platform.machine() != "arm64":
        print("  !!  Mac com Intel: funciona, mas sem o Whisper na GPU. Use o ouvido na nuvem"
              " (Ajustes > Voz) para ficar rápido.", flush=True)
    if not shutil.which("ffmpeg"):
        falha("ffmpeg não encontrado. Instale: %s  (e abra o %s de novo)" % (
            ("brew install ffmpeg", "Luna.command") if MAC else ("winget install ffmpeg", "Luna.bat")))
    ok("ffmpeg")

    if not VPY.is_file():
        passo("criando o ambiente (.venv)...")
        venv.create(RAIZ / ".venv", with_pip=True)
    pip("install", "--upgrade", "pip", "--quiet")
    if MAC:
        passo("instalando o PyTorch (demora)...")
        pip("install", "torch", "--quiet")
    else:
        passo("instalando o PyTorch com CUDA (demora)...")
        pip("install", "torch", "--index-url", "https://download.pytorch.org/whl/cu128", "--quiet")
    passo("instalando o resto...")
    pip("install", "-r", str(RAIZ / "requirements.txt"), "--quiet")
    ok("dependências")

    env = RAIZ / ".env"
    if not env.exists():
        shutil.copy(RAIZ / ".env.exemplo", env)
        ok(".env criado (a chave você cola na tela)")
    else:
        ok(".env já existe")

    subprocess.run([str(VPY), str(RAIZ / "src" / "criar_atalho.py")], check=False)
    if MAC:
        print('\n  Pronto. Abra "Luna" em Aplicativos (ou o Luna.command).')
        print("  Na 1ª vez o Mac pede o Microfone e a Acessibilidade (atalho Ctrl+Alt+L).")
    else:
        print('\n  Pronto. Abra "Luna" na área de trabalho (ou no menu Iniciar).')
    print("  Na primeira vez os modelos (~3 GB) baixam sozinhos.\n")


if __name__ == "__main__":
    main()
