"""Atualizações pendentes, para o botão "Atualização" da barra de cima.

Três tipos, do mais comum ao mais raro:

  tela       web/index.html mudou depois que a janela abriu: F5 resolve.
  reiniciar  código do servidor mudou (commit novo) depois que ele subiu:
             só vale depois de reiniciar. Os módulos recarregados sozinhos
             (roteador, juiz, corretor, tela, ativação, detector) não contam.
  versao     saiu versão nova da Luna no GitHub (só na versão pública:
             recursos/versao.json diz o repositório e a versão instalada).
             Com git: "Atualizar agora" (git pull + dependências + reinício).
             Sem git (baixou o ZIP): link para baixar de novo.

Nada aqui muda código sozinho: tudo espera o clique da pessoa.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.request

import config

RAIZ = config.RAIZ
VERSAO_ARQ = RAIZ / "recursos" / "versao.json"
RECARREGA_SOZINHO = {"ativacao", "corretor", "detector_luna", "juiz", "roteador", "tela",
                     "comandos"}
INTERVALO_S = 6 * 3600          # confere o GitHub no máximo a cada 6 h

_inicio_head = ""
_remoto: dict = {"quando": 0.0, "versao": None, "erro": ""}
_trava = threading.Lock()
_atualizando = {"rodando": False, "log": [], "ok": None}


def _git(*args, tempo: float = 15) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", *args], cwd=str(RAIZ), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=tempo)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def _head() -> str:
    cod, out = _git("rev-parse", "HEAD", tempo=5)
    return out.strip() if cod == 0 else ""


def marcar_inicio() -> None:
    """Chamado quando o servidor sobe: 'a versão que está rodando'."""
    global _inicio_head
    _inicio_head = _head()


def web_versao() -> str:
    try:
        return str(int((RAIZ / "web" / "index.html").stat().st_mtime))
    except OSError:
        return ""


def _instalada() -> dict:
    try:
        return json.loads(VERSAO_ARQ.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _precisa_reiniciar() -> list[str]:
    """Arquivos do servidor que mudaram (em commit) desde que ele subiu."""
    agora = _head()
    if not _inicio_head or not agora or agora == _inicio_head:
        return []
    cod, out = _git("diff", "--name-only", _inicio_head, agora)
    if cod != 0:
        return []
    mudou = []
    for f in out.split():
        if f.startswith("src/") and f.endswith(".py"):
            nome = f[4:-3]
            if nome not in RECARREGA_SOZINHO and not nome.startswith(("teste_", "testa_")):
                mudou.append(f)
        elif f in ("requirements.txt", "iniciar.ps1"):
            mudou.append(f)
    return mudou


def _checar_remoto(forcar: bool = False) -> None:
    inst = _instalada()
    repo = inst.get("repo")
    if not repo:
        return
    with _trava:
        if not forcar and time.time() - _remoto["quando"] < INTERVALO_S:
            return
        _remoto["quando"] = time.time()
    url = "https://raw.githubusercontent.com/%s/main/recursos/versao.json" % repo
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            _remoto["versao"] = json.loads(r.read().decode("utf-8"))
            _remoto["erro"] = ""
    except Exception as e:  # noqa: BLE001
        _remoto["erro"] = str(e)[:120]


def pendentes(web_da_tela: str = "", forcar: bool = False) -> dict:
    itens = []
    wv = web_versao()
    if web_da_tela and wv and web_da_tela != wv:
        itens.append({"tipo": "tela", "titulo": "Tela nova",
                      "detalhe": "A interface foi atualizada. Recarregue para ver.",
                      "acao": "recarregar", "botao": "Recarregar a tela"})
    mudou = _precisa_reiniciar()
    if mudou:
        itens.append({"tipo": "reiniciar", "titulo": "Reiniciar para aplicar",
                      "detalhe": "Mudanças no servidor só valem depois de reiniciar (%d arquivo%s: %s)."
                                 % (len(mudou), "" if len(mudou) == 1 else "s", ", ".join(mudou[:4])),
                      "acao": "reiniciar", "botao": "Reiniciar a Luna"})
    _checar_remoto(forcar)
    inst, rem = _instalada(), _remoto.get("versao") or {}
    if inst.get("versao") and rem.get("versao") and rem["versao"] > inst["versao"]:
        com_git = (RAIZ / ".git").exists()
        itens.append({"tipo": "versao", "titulo": "Nova versão da Luna",
                      "detalhe": "Versão %s disponível (você tem a %s)." % (rem["versao"], inst["versao"]),
                      "novidades": rem.get("novidades", []),
                      "acao": "atualizar" if com_git else "baixar",
                      "botao": "Atualizar agora" if com_git else "Baixar a versão nova",
                      "link": "https://github.com/%s" % inst["repo"]})
    return {"itens": itens, "web": wv, "instalada": inst.get("versao", ""),
            "atualizando": _atualizando["rodando"], "erro_remoto": _remoto["erro"]}


def atualizar() -> dict:
    """git pull + dependências. Só na versão pública com git. Síncrono (thread)."""
    if not _instalada().get("repo") or not (RAIZ / ".git").exists():
        return {"ok": False, "msg": "esta instalação não atualiza pelo git"}
    if _atualizando["rodando"]:
        return {"ok": False, "msg": "já está atualizando"}
    _atualizando.update(rodando=True, log=[], ok=None)
    try:
        cod, out = _git("status", "--porcelain", "--untracked-files=no")
        if out.strip():
            return {"ok": False, "msg": "há arquivos da Luna modificados à mão; atualize pelo git"}
        antes = _head()
        cod, out = _git("pull", "--ff-only", tempo=120)
        _atualizando["log"].append(out[-400:])
        if cod != 0:
            return {"ok": False, "msg": "o git não conseguiu atualizar: " + out.strip()[-160:]}
        cod, dif = _git("diff", "--name-only", antes, _head())
        if "requirements.txt" in dif.split():
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                                str(RAIZ / "requirements.txt")], cwd=str(RAIZ),
                               capture_output=True, text=True, timeout=1800)
            _atualizando["log"].append((r.stdout + r.stderr)[-400:])
            if r.returncode != 0:
                return {"ok": False, "msg": "atualizou o código, mas falhou ao instalar dependências"}
        _remoto["quando"] = 0.0
        return {"ok": True, "msg": "atualizado", "reiniciar": True}
    finally:
        _atualizando["rodando"] = False


if __name__ == "__main__":
    marcar_inicio()
    print(json.dumps(pendentes(forcar=True), ensure_ascii=False, indent=1))
