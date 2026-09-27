"""Simula o Mac em qualquer sistema: finge sys.platform="darwin" e confere se
cada parte da Luna ESCOLHE o caminho do Mac (não roda o código do Mac).

    .venv/Scripts/python.exe src/teste_plataforma.py
"""
import importlib
import sys
import types
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))
import urllib.request  # noqa: E402,F401  (antes de fingir: usa _scproxy no Mac)
sys.platform = "darwin"

# módulos do Mac que não existem no Windows: stubs vazios
for nome in ("mlx", "mlx.core", "mlx_whisper", "Quartz", "AppKit", "Foundation", "pynput", "pynput.keyboard", "fcntl"):
    sys.modules.setdefault(nome, types.ModuleType(nome))

falhas = 0


def confere(nome, obtido, esperado):
    global falhas
    ok = obtido == esperado
    falhas += not ok
    print(("  ok  " if ok else "  FALHOU  ") + nome, "->", obtido)


import plataforma as P  # noqa: E402
confere("plataforma.MAC", P.MAC, True)
confere("python do .venv", P.python_venv().relative_to(RAIZ).as_posix(), ".venv/bin/python3")
confere("sem janela", P.sem_janela(), {"start_new_session": True})
confere("hermes_home", P.hermes_home(), Path.home() / ".hermes")
confere("app equivalente", P.nome_app("bloco de notas"), "textedit")

import config  # noqa: E402
importlib.reload(config)
confere("config _MAC", config._MAC, True)
confere("nome do ouvido local", config.STTS["local"]["nome"], "Na GPU do Mac (Whisper)")
confere("prompt diz Mac", "computador dele (Mac)" in config.SISTEMA_HERMES, True)

import ouvido  # noqa: E402
config.stt_id = lambda: "local"
confere("transcritor", type(ouvido.criar()).__name__, "TranscritorMac")

import atalho_global  # noqa: E402
confere("atalho global", atalho_global._m.__name__, "atalho_global_mac")

import comandos  # noqa: E402
confere("apelido 'bloco de notas'", comandos.APELIDOS.get("bloco de notas"), "textedit")

txt = (RAIZ / "src" / "criar_atalho.py").read_text(encoding="utf-8")
confere("criar_atalho escolhe o mac", '"criar_atalho_mac.py" if sys.platform == "darwin"' in txt, True)
srv = (RAIZ / "src" / "servidor.py").read_text(encoding="utf-8")
confere("mini do mac", '"mini.py" if plataforma.WINDOWS else "mini_mac.py"' in srv, True)

req = (RAIZ / "requirements.txt").read_text(encoding="utf-8")
confere("requirements tem mlx p/ mac", 'mlx-whisper==0.4.3; sys_platform == "darwin"' in req, True)
confere("requirements pycaw só win", 'pycaw; sys_platform == "win32"' in req, True)

print("\n%d falha(s)" % falhas)
sys.exit(1 if falhas else 0)
