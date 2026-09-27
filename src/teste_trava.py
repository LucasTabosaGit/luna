"""Testa a TRAVA de aprovação de ponta a ponta, com o servidor no ar.

    .venv/Scripts/python.exe src/teste_trava.py

1. Edita comandos.py DIRETO (sem proposta) -> o servidor tem que ignorar.
2. Proposta boa (com teste) -> passa nos testes, espera aprovação; aprovada
   pela rota da tela -> vale na frase seguinte, sem reiniciar.
3. Proposta ruim (quebra um comando) -> recusada pelos testes, nada muda.
Deixa tudo como estava no fim (desfaz o commit da proposta boa).
"""
import asyncio
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import websockets

RAIZ = Path(__file__).resolve().parent.parent
PY = str(RAIZ / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():
    PY = sys.executable          # rodando fora do .venv do projeto
COM = RAIZ / "src" / "comandos.py"
TESTE = RAIZ / "src" / "teste_comandos.py"
HORA = 'return Resultado("hora", f"São {a.hour} e {a.minute:02d}.")'
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


async def pergunta(texto: str) -> str:
    async with websockets.connect("ws://127.0.0.1:8777/ws", max_size=None) as ws:
        await ws.send(json.dumps({"tipo": "texto", "texto": texto}))
        for _ in range(60):
            try:
                d = json.loads(await asyncio.wait_for(ws.recv(), 10))
            except (asyncio.TimeoutError, TypeError, ValueError):
                continue
            if d.get("tipo") == "bot":
                return d["texto"]
    return ""


def git(*a):
    return subprocess.run(["git", *a], cwd=RAIZ, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def prop(*a):
    r = subprocess.run([PY, "src/propostas.py", *a], cwd=RAIZ, capture_output=True,
                       text=True, encoding="utf-8", env=ENV)
    return r.returncode, r.stdout + r.stderr


def edita(pasta: Path, trocas: list[tuple[str, str, str]]):
    for arq, a, b in trocas:
        p = pasta / "src" / arq
        s = p.read_text(encoding="utf-8")
        assert a in s, (arq, a[:40])
        p.write_text(s.replace(a, b, 1), encoding="utf-8", newline="\n")


async def main():
    ok = 0
    inicio = git("rev-parse", "HEAD")
    assert not git("status", "--porcelain", "--", "src", "web"), "pasta principal suja"

    # ---- 1: edição direta é ignorada
    await pergunta("Que horas são?")      # servidor sincroniza com o commit atual
    original = COM.read_text(encoding="utf-8")
    COM.write_text(original.replace(HORA, 'return Resultado("hora", "EDITADO SEM APROVACAO.")'),
                   encoding="utf-8", newline="\n")
    try:
        r = await pergunta("Que horas são?")
    finally:
        COM.write_text(original, encoding="utf-8", newline="\n")
    certo = "são" in r.lower() and "EDITADO" not in r
    ok += certo
    print(f"{'ok ' if certo else 'ERR'} edição direta ignorada: {r!r}")

    # ---- 2: proposta boa -> aprovada -> vale sem reiniciar
    _c, out = prop("nova", "teste: hora responde 'Agora são ...'")
    pid = out.split()[1]
    edita(RAIZ / "propostas" / pid, [
        ("comandos.py", HORA, 'return Resultado("hora", f"Agora são {a.hour} e {a.minute:02d}.")'),
        ("teste_comandos.py", '("Me diz as horas", "hora"),',
         '("Me diz as horas", "hora"), ("Hora certa", "hora"),'),
    ])
    antes = await pergunta("Que horas são?")
    c, out = prop("enviar", pid)
    passou = c == 0 and "PASSOU" in out
    with urllib.request.urlopen(urllib.request.Request(  # noqa: S310
            f"http://127.0.0.1:8777/propostas/{pid}/aprovar", method="POST"), timeout=30) as f:
        aprov = json.load(f)["texto"]
    depois = await pergunta("Que horas são?")
    certo = passou and aprov.startswith("Aplicada") and depois.startswith("Agora são ") \
        and antes.startswith("São ")
    ok += certo
    print(f"{'ok ' if certo else 'ERR'} proposta boa: antes={antes[:40]!r} "
          f"testes={'passou' if passou else out[-200:]!r} aprovação={aprov!r} depois={depois!r}")

    # ---- 3: proposta ruim -> recusada, nada muda
    cab = git("rev-parse", "HEAD")
    _c, out = prop("nova", "teste: remendo que quebra a hora")
    pid2 = out.split()[1]
    edita(RAIZ / "propostas" / pid2, [
        ("comandos.py", '(r"^(que horas (sao|e)|', '(r"^(que horas (xxx)|'),
        ("teste_comandos.py", '("Me diz as horas", "hora"),',
         '("Me diz as horas", "hora"), ("Hora", "hora"),'),
    ])
    c, out = prop("enviar", pid2)
    certo = c != 0 and "RECUSADA" in out and git("rev-parse", "HEAD") == cab \
        and "são" in (await pergunta("Que horas são?")).lower()
    ok += certo
    print(f"{'ok ' if certo else 'ERR'} proposta ruim recusada: "
          f"{[x for x in out.splitlines() if 'piorou' in x or 'quebrou' in x][:2]}")

    # ---- desfaz o commit da proposta boa (volta ao estado do início)
    git("reset", "--hard", "-q", inicio)
    print(f"\n{ok}/3 corretos")


asyncio.run(main())
