"""Abre a Luna: liga o que faltar (Hermes, servidor) e mostra a janela.

É o que o atalho "Luna" e o Luna.bat chamam, com o pythonw (sem console).
Python puro de propósito: nada de PowerShell escondido, que antivírus
confundem com vírus. Clicar duas vezes não duplica nada: cada parte só
sobe se a porta dela ainda não estiver no ar.
"""
from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
LOGS = RAIZ / "logs"
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def no_ar(porta: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", porta)) == 0


def aviso(texto: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, texto, "Luna", 0x30)


def ambiente() -> dict:
    """Modelos e caches dentro da pasta do projeto (não enchem o C:)."""
    env = dict(os.environ)
    env.update({
        "HF_HOME": str(RAIZ / "modelos" / "hf"),
        "HUGGINGFACE_HUB_CACHE": str(RAIZ / "modelos" / "hf" / "hub"),
        "TORCH_HOME": str(RAIZ / "modelos" / "torch"),
        "XDG_CACHE_HOME": str(RAIZ / "cache"),
        "HF_HUB_DISABLE_XET": "1",
        "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
        "PYTHONWARNINGS": "ignore::FutureWarning,ignore::UserWarning",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        # MKL/Fortran: não morrer quando o processo que o abriu fecha.
        "FOR_DISABLE_CONSOLE_CTRL_HANDLER": "1",
    })
    return env


def ligar_hermes() -> None:
    """Hermes (opcional): modo Expert e tarefas no PC."""
    if no_ar(8642):
        return
    hermes = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"
    if not hermes.is_file():
        return
    log = hermes.parent.parent.parent.parent / "gateway-voz.log"
    with open(log, "ab") as out, open(str(log) + ".err", "ab") as err:
        subprocess.Popen([str(hermes), "-p", "default", "gateway", "run"], stdout=out, stderr=err,
                         stdin=subprocess.DEVNULL, creationflags=SEM_JANELA)


def ligar_servidor() -> subprocess.Popen | None:
    if no_ar(8777):
        return None
    py = RAIZ / ".venv" / "Scripts" / "python.exe"
    LOGS.mkdir(exist_ok=True)
    with open(LOGS / "srv.log", "ab") as out, open(LOGS / "srv.err.log", "ab") as err:
        return subprocess.Popen([str(py), str(RAIZ / "src" / "servidor.py")], cwd=str(RAIZ), env=ambiente(),
                                stdout=out, stderr=err, stdin=subprocess.DEVNULL, creationflags=SEM_JANELA)


def esperar(proc: subprocess.Popen | None) -> bool:
    # Na primeira vez os modelos (~3 GB) baixam: espera bem mais.
    primeira = not (RAIZ / "modelos" / "hf").exists()
    limite = time.time() + (900 if primeira else 150)
    while time.time() < limite:
        try:
            urllib.request.urlopen("http://127.0.0.1:8777/estado", timeout=3)
            return True
        except OSError:
            pass
        if proc is not None and proc.poll() is not None:
            return False                       # o servidor caiu: não adianta esperar
        time.sleep(1.5)
    return False


def main() -> int:
    if not (RAIZ / ".venv" / "Scripts" / "python.exe").is_file():
        aviso("A Luna ainda não foi instalada. Clique duas vezes em Luna.bat.")
        return 1
    ligar_hermes()
    proc = ligar_servidor()
    if not esperar(proc):
        aviso("A Luna não conseguiu ligar.\n\nVeja o arquivo logs\\srv.err.log na pasta da Luna "
              "(ou abra uma issue no GitHub com ele).")
        return 1
    sys.path.insert(0, str(RAIZ / "src"))
    import atalho_global
    atalho_global.trazer_para_frente()         # reaproveita a janela se já estiver aberta
    return 0


if __name__ == "__main__":
    sys.exit(main())
