"""Abre a Luna: liga o que faltar (Hermes, servidor) e mostra a janela.

É o que o atalho "Luna" e o Luna.bat (Windows, com o pythonw, sem console)
ou o Luna.command (Mac) chamam. Python puro de propósito: nada de script
escondido, que antivírus confundem com vírus. Clicar duas vezes não duplica nada: cada parte só
sobe se a porta dela ainda não estiver no ar.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
LOGS = RAIZ / "logs"


def no_ar(porta: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", porta)) == 0


def aviso(texto: str) -> None:
    import plataforma
    plataforma.aviso(texto)


def ambiente() -> dict:
    """Modelos e caches dentro da pasta do projeto (não enchem o disco do sistema)."""
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
    import plataforma
    hermes = plataforma.hermes_exe()
    if not hermes:
        return
    log = plataforma.hermes_home() / "gateway-voz.log"
    with open(log, "ab") as out, open(str(log) + ".err", "ab") as err:
        subprocess.Popen([hermes, "-p", "default", "gateway", "run"], stdout=out, stderr=err,
                         stdin=subprocess.DEVNULL, **plataforma.sem_janela())


def reiniciar_hermes() -> bool:
    """Para e religa o gateway do Hermes (ele guarda conexões MCP em memória).
    Só age se ele estiver no ar. Espera até 90 s pela porta voltar."""
    if not no_ar(8642):
        return False
    import plataforma
    hermes = plataforma.hermes_exe()
    if not hermes:
        return False
    subprocess.run([hermes, "-p", "default", "gateway", "stop"], capture_output=True,
                   timeout=120, stdin=subprocess.DEVNULL, **plataforma.sem_janela())
    for _ in range(40):
        if not no_ar(8642):
            break
        time.sleep(0.5)
    ligar_hermes()
    for _ in range(90):
        if no_ar(8642):
            return True
        time.sleep(1)
    return False


def ligar_servidor() -> subprocess.Popen | None:
    if no_ar(8777):
        return None
    import plataforma
    py = plataforma.python_venv()
    LOGS.mkdir(exist_ok=True)
    with open(LOGS / "srv.log", "ab") as out, open(LOGS / "srv.err.log", "ab") as err:
        return subprocess.Popen([str(py), str(RAIZ / "src" / "servidor.py")], cwd=str(RAIZ), env=ambiente(),
                                stdout=out, stderr=err, stdin=subprocess.DEVNULL, **plataforma.sem_janela())


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
    sys.path.insert(0, str(RAIZ / "src"))
    import plataforma
    if not plataforma.python_venv().is_file():
        aviso("A Luna ainda não foi instalada. Clique duas vezes em %s."
              % ("Luna.command" if plataforma.MAC else "Luna.bat"))
        return 1
    ligar_hermes()
    proc = ligar_servidor()
    if not esperar(proc):
        aviso("A Luna não conseguiu ligar.\n\nVeja o arquivo logs/srv.err.log na pasta da Luna "
              "(ou abra uma issue no GitHub com ele).")
        return 1
    import atalho_global
    atalho_global.trazer_para_frente()         # reaproveita a janela se já estiver aberta
    return 0


if __name__ == "__main__":
    sys.exit(main())
