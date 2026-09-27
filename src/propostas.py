"""Trava de aprovação: a Luna (Claude/Hermes) PROPÕE mudanças, não aplica.

Fluxo (tudo com git, nada mexe no código que está rodando até você aprovar):

    python src/propostas.py nova "Spotify aceita 'no meu Spotify'"
        -> cria uma cópia isolada em propostas/<id>/ (git worktree, ramo
           proposta/<id>) e imprime o caminho. As edições vão LÁ.
    python src/propostas.py enviar <id>
        -> commita a cópia, roda a bateria de testes nela e compara com o
           código atual. Se nada piorar, fica "aguardando aprovação" e a
           tela da Luna mostra o aviso com [Aprovar] [Recusar].
           Se algum teste piorar: recusada na hora, nada muda.
    python src/propostas.py lista | aprovar <id> | recusar <id>

Regras da bateria (src/teste_*.py, rodados na cópia):
- nenhum teste pode ter MENOS acertos que o código atual;
- a lista de ações (teste_acoes) não pode executar nenhuma frase proibida;
- mexeu em comandos.py ou acoes.py sem acrescentar teste: recusada.

O servidor, por sua vez, NÃO recarrega um módulo editado direto na pasta
principal sem commit (ver `_atualizado` em servidor.py): editar por fora da
trava não tem efeito.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTA = RAIZ / "propostas"
ESTADO = PASTA / "estado.json"
import plataforma  # noqa: E402
PY = str(plataforma.python_venv())
if not Path(PY).exists():
    PY = sys.executable          # rodando fora do .venv do projeto
SERVIDOR = "http://127.0.0.1:8777"

# (arquivo de teste, regex do placar "acertos/total", precisa de LLM)
TESTES = [
    ("teste_comandos.py", r"(\d+)/(\d+) corretos", False),
    ("teste_atalhos.py", r"(\d+)/(\d+) corretos", False),
    ("teste_roteador.py --rapido", r"(\d+)/(\d+) corretos", True),
    ("teste_corretor.py", r"(\d+)/(\d+) corretos", True),
]
_GRAVE = re.compile(r"negativos executados \(grave\): (\d+)/")
# Mexer nestes exige mexer num teste também.
PEDEM_TESTE = ("src/comandos.py", "src/acoes.py", "src/roteador.py", "src/ativacao.py",
               "src/corretor.py", "atalhos.json")


def _git(*args, cwd=RAIZ, check=True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def _ler() -> dict:
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _gravar(d: dict) -> None:
    PASTA.mkdir(exist_ok=True)
    ESTADO.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def _avisar(evento: dict) -> None:
    """Conta ao servidor (que mostra na tela). Sem servidor: só fica no estado."""
    try:
        req = urllib.request.Request(
            SERVIDOR + "/propostas/aviso", data=json.dumps(evento).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5).read()  # noqa: S310
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ testes

def rodar_testes(pasta: Path) -> dict:
    """Roda a bateria em `pasta` -> {teste: {"ok", "total", "grave", "erro"}}."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", HF_HUB_OFFLINE="1")
    res = {}
    for arq, placar, _llm in TESTES:
        arq, *args = arq.split()
        p = pasta / "src" / arq
        if not p.exists():
            continue
        try:
            r = subprocess.run([PY, str(p), *args], cwd=pasta, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", env=env, timeout=600)
            saida = r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            res[arq] = {"ok": 0, "total": 0, "erro": "demorou mais de 10 min"}
            continue
        m = re.findall(placar, saida)
        if not m:
            res[arq] = {"ok": 0, "total": 0, "erro": saida.strip()[-300:] or "sem placar"}
            continue
        ok, total = map(int, m[-1])
        g = _GRAVE.search(saida)
        res[arq] = {"ok": ok, "total": total, "grave": int(g.group(1)) if g else 0}
    return res


def _base() -> dict:
    """Placar do código atual (main), guardado por commit para não refazer."""
    cab = _git("rev-parse", "HEAD")
    d = _ler()
    if d.get("_base", {}).get("commit") == cab:
        return d["_base"]["res"]
    res = rodar_testes(RAIZ)
    d = _ler()
    d["_base"] = {"commit": cab, "res": res}
    _gravar(d)
    return res


def comparar(base: dict, novo: dict) -> list[str]:
    """Motivos para recusar (vazio = passou)."""
    motivos = []
    for arq, b in base.items():
        n = novo.get(arq)
        if n is None:
            motivos.append(f"{arq} sumiu")
        elif n.get("erro"):
            motivos.append(f"{arq} quebrou: {n['erro'][:120]}")
        elif n["ok"] < b["ok"]:
            motivos.append(f"{arq} piorou: {n['ok']}/{n['total']} (antes {b['ok']}/{b['total']})")
        elif n["total"] - n["ok"] > b["total"] - b["ok"]:
            motivos.append(f"{arq} errou mais: {n['total'] - n['ok']} erros "
                           f"(antes {b['total'] - b['ok']})")
        if n and n.get("grave"):
            motivos.append(f"{arq}: executou {n['grave']} frase(s) proibida(s)")
    return motivos


# ------------------------------------------------------------------ comandos

def nova(titulo: str) -> str:
    pid = _dt.datetime.now().strftime("%m%d-%H%M%S")
    pasta = PASTA / pid
    _git("worktree", "add", "-q", "-b", f"proposta/{pid}", str(pasta), "HEAD")
    d = _ler()
    d[pid] = {"titulo": titulo, "estado": "editando", "criada": time.time(),
              "pasta": str(pasta)}
    _gravar(d)
    print(f"id: {pid}\nEdite os arquivos em: {pasta}\n"
          f"Depois: python src/propostas.py enviar {pid}")
    return pid


def enviar(pid: str) -> bool:
    d = _ler()
    p = d.get(pid)
    if not p:
        raise SystemExit(f"proposta {pid} não existe")
    pasta = Path(p["pasta"])
    _git("add", "-A", cwd=pasta)
    if _git("status", "--porcelain", cwd=pasta):
        _git("commit", "-qm", f"Proposta {pid}: {p['titulo']}", cwd=pasta)
    mudou = _git("diff", "--name-only", "HEAD", f"proposta/{pid}").split()
    mudou = [m for m in mudou if m]
    if not mudou:
        raise SystemExit("a proposta não muda nada")
    motivos = []
    if any(m in PEDEM_TESTE for m in mudou) and not any(
            m.startswith("src/teste_") for m in mudou):
        motivos.append("mexeu em " + ", ".join(m for m in mudou if m in PEDEM_TESTE)
                       + " sem acrescentar teste")
    print("rodando testes no código atual (base)...", flush=True)
    base = _base()
    print("rodando testes na proposta...", flush=True)
    novo = rodar_testes(pasta)
    motivos += comparar(base, novo)
    placar = {k: f"{v.get('ok')}/{v.get('total')}" for k, v in novo.items()}
    p.update(arquivos=mudou, testes=placar,
             diff=_git("diff", "--stat", "HEAD", f"proposta/{pid}"))
    d = _ler()
    if motivos:
        p.update(estado="recusada_testes", motivos=motivos)
        d[pid] = p
        _gravar(d)
        _remover(pid)
        print("RECUSADA pelos testes (nada mudou):\n  " + "\n  ".join(motivos))
        _avisar({"id": pid, "titulo": p["titulo"], "estado": "recusada_testes",
                 "motivos": motivos})
        return False
    p["estado"] = "aguardando"
    d[pid] = p
    _gravar(d)
    print(f"PASSOU nos testes {placar}. Aguardando o usuário aprovar na tela da Luna.")
    _avisar({"id": pid, "titulo": p["titulo"], "estado": "aguardando",
             "arquivos": mudou, "testes": placar})
    return True


def _remover(pid: str) -> None:
    pasta = PASTA / pid
    _git("worktree", "remove", "--force", str(pasta), check=False)
    _git("branch", "-D", f"proposta/{pid}", check=False)


def aprovar(pid: str) -> str:
    d = _ler()
    p = d.get(pid)
    if not p or p.get("estado") != "aguardando":
        return "Essa proposta não está esperando aprovação."
    if _git("status", "--porcelain", "--", "src", "web"):
        return ("A pasta principal tem mudanças sem commit; não dá para aplicar "
                "com segurança agora.")
    try:
        _git("merge", "--no-ff", "-q", "-m",
             f"Aprovada pelo usuário: {p['titulo']}", f"proposta/{pid}")
    except RuntimeError as e:
        _git("merge", "--abort", check=False)
        return f"Não consegui aplicar (conflito com outra mudança): {str(e)[:120]}"
    _remover(pid)
    p.update(estado="aprovada", decidida=time.time())
    d[pid] = p
    _gravar(d)
    reinicio = any(a in ("src/servidor.py", "src/config.py") for a in p.get("arquivos", []))
    return "Aplicada." + (" Precisa reiniciar para valer." if reinicio else " Já está valendo.")


def recusar(pid: str) -> str:
    d = _ler()
    p = d.get(pid)
    if not p:
        return "Essa proposta não existe."
    _remover(pid)
    p.update(estado="recusada", decidida=time.time())
    d[pid] = p
    _gravar(d)
    return "Descartada. Nada mudou."


def pendentes() -> list[dict]:
    return [dict(v, id=k) for k, v in _ler().items()
            if not k.startswith("_") and v.get("estado") == "aguardando"]


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help", "ajuda"):
        print(__doc__)
        return
    cmd, resto = argv[0], argv[1:]
    if cmd == "nova" and resto:
        nova(" ".join(resto))
    elif cmd == "enviar" and resto:
        sys.exit(0 if enviar(resto[0]) else 1)
    elif cmd == "aprovar" and resto:
        print(aprovar(resto[0]))
    elif cmd == "recusar" and resto:
        print(recusar(resto[0]))
    elif cmd == "lista":
        for k, v in _ler().items():
            if not k.startswith("_"):
                print(f"{k}  {v.get('estado'):16}  {v.get('titulo')}")
    elif cmd == "base":
        print(json.dumps(_base(), ensure_ascii=False, indent=1))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
