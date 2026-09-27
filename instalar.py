"""Instala a Luna: ambiente Python (.venv), dependências, .env e atalho.

Rode com o Python 3.11 do Windows (o Luna.bat faz isso por você):
    py -3.11 instalar.py
Só usa a biblioteca padrão. Não precisa de PowerShell nem de administrador.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import venv
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
VPY = RAIZ / ".venv" / "Scripts" / "python.exe"


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
    print("\n  Luna - instalação\n")
    if sys.version_info[:2] != (3, 11):
        falha("Precisa do Python 3.11 (este é %d.%d). Instale: winget install Python.Python.3.11"
              % sys.version_info[:2])
    ok("Python %d.%d" % sys.version_info[:2])
    if not shutil.which("ffmpeg"):
        falha("ffmpeg não encontrado. Instale: winget install ffmpeg  (e abra o Luna.bat de novo)")
    ok("ffmpeg")

    if not VPY.is_file():
        passo("criando o ambiente (.venv)...")
        venv.create(RAIZ / ".venv", with_pip=True)
    passo("instalando o PyTorch com CUDA (demora)...")
    pip("install", "--upgrade", "pip", "--quiet")
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
    print('\n  Pronto. Abra "Luna" na área de trabalho (ou no menu Iniciar).')
    print("  Na primeira vez os modelos (~3 GB) baixam sozinhos.\n")


if __name__ == "__main__":
    main()
