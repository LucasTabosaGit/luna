"""Conversas salvas da Luna: sobrevivem a recarregar a tela e a reiniciar.

Uma conversa = um arquivo `dados/conversas/<id>.json`:
    {"id", "titulo", "criada", "atualizada", "projeto"?: <id do projeto>,
     "hermes"?: {"sessao": <id da sessão no Hermes>, "visto": <nº de mensagens já vistas por ele>},
     "mensagens": [{"quem": "voce"|"bot", "texto", "t", "rota"?, "fotos"?: [url]}]}

Projetos (pastas de conversas) ficam em `dados/projetos.json`:
    [{"id", "nome", "instrucoes", "resumo", "pasta", "criado"}]
O que o projeto sabe (instruções + resumo) entra no começo de toda conversa
dele, nas duas IAs. O resumo só muda quando o usuário salva: a Luna sugere,
ele revisa.

Quem grava é o servidor (Sessao.enviar), a partir do que já vai para a
tela: o navegador não precisa lembrar de nada. Imagens enviadas no chat
viram arquivos em `dados/conversas/img/` (referenciados pela URL), para o
JSON continuar pequeno.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTA = RAIZ / "dados" / "conversas"
PASTA_IMG = PASTA / "img"
ARQ_PROJETOS = RAIZ / "dados" / "projetos.json"
MAX_LISTA = 300
LIMITE_CAMPO = 4000        # instruções/resumo: o que passa disso não entra no prompt
_trava = threading.Lock()
_ID = re.compile(r"^[a-f0-9]{12}$")


def novo_id() -> str:
    return uuid.uuid4().hex[:12]


def valido(cid: str | None) -> bool:
    return bool(cid) and bool(_ID.match(cid))


def _arq(cid: str) -> Path:
    return PASTA / (cid + ".json")


def carregar(cid: str) -> dict | None:
    if not valido(cid):
        return None
    try:
        return json.loads(_arq(cid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _salvar(conv: dict) -> None:
    PASTA.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=PASTA, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(conv, f, ensure_ascii=False)
    os.replace(tmp, _arq(conv["id"]))


def guardar_imagem(data_url: str) -> str:
    """data:image/...;base64 -> /conversas/img/<hash>.jpg (sem duplicar)."""
    try:
        cab, b64 = data_url.split(",", 1)
        dados = base64.b64decode(b64)
    except ValueError:
        return ""
    ext = "png" if "png" in cab else "webp" if "webp" in cab else "jpg"
    nome = hashlib.sha1(dados).hexdigest()[:16] + "." + ext
    PASTA_IMG.mkdir(parents=True, exist_ok=True)
    p = PASTA_IMG / nome
    if not p.exists():
        p.write_bytes(dados)
    return "/conversas/img/" + nome


def registrar(cid: str, quem: str, texto: str, *, juntar: bool = False,
              substituir: bool = False, projeto: str = "", **extra) -> None:
    """Acrescenta uma mensagem. `juntar`: emenda na última do mesmo autor
    (a resposta chega frase a frase). `substituir`: troca o texto da última
    (resposta final formatada, ou "você" corrigido pelo corretor)."""
    if not valido(cid) or not (texto or "").strip():
        return
    with _trava:
        conv = carregar(cid) or {"id": cid, "titulo": "", "criada": time.time(),
                                 "mensagens": []}
        if projeto and not conv.get("projeto") and not conv["mensagens"]:
            conv["projeto"] = projeto
        msgs = conv["mensagens"]
        ult = msgs[-1] if msgs else None
        if ult and ult["quem"] == quem and (juntar or substituir):
            ult["texto"] = texto if substituir else (ult["texto"] + " " + texto).strip()
            ult.update({k: v for k, v in extra.items() if v})
        else:
            m = {"quem": quem, "texto": texto, "t": time.time()}
            m.update({k: v for k, v in extra.items() if v})
            msgs.append(m)
        if not conv["titulo"] and quem == "voce":
            conv["titulo"] = re.sub(r"\s+", " ", texto).strip()[:70]
        conv["atualizada"] = time.time()
        _salvar(conv)


def marcar(cid: str, **extra) -> None:
    """Anota algo na última mensagem da Luna (ex.: rota do turno)."""
    if not valido(cid):
        return
    with _trava:
        conv = carregar(cid)
        if not conv or not conv["mensagens"] or conv["mensagens"][-1]["quem"] != "bot":
            return
        conv["mensagens"][-1].update({k: v for k, v in extra.items() if v})
        _salvar(conv)


def listar() -> list[dict]:
    itens = []
    for p in PASTA.glob("*.json") if PASTA.exists() else []:
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not c.get("mensagens"):
            continue
        itens.append({"id": c["id"], "titulo": c.get("titulo") or "Conversa",
                      "atualizada": c.get("atualizada", 0), "n": len(c["mensagens"]),
                      "projeto": c.get("projeto") or ""})
    itens.sort(key=lambda c: c["atualizada"], reverse=True)
    return itens[:MAX_LISTA]


def apagar(cid: str) -> bool:
    if not valido(cid):
        return False
    try:
        _arq(cid).unlink()
        return True
    except OSError:
        return False


def historico_llm(conv: dict | None, sistema: str, ultimas: int = 8) -> list[dict]:
    """Histórico para o modelo ao retomar uma conversa (só o texto)."""
    h = [{"role": "system", "content": sistema}]
    for m in (conv or {}).get("mensagens", [])[-ultimas:]:
        h.append({"role": "user" if m["quem"] == "voce" else "assistant",
                  "content": m["texto"]})
    return h


# ---------------------------------------------------------------- projetos
def projetos() -> list[dict]:
    try:
        lista = json.loads(ARQ_PROJETOS.read_text(encoding="utf-8"))
        return [p for p in lista if isinstance(p, dict) and valido(p.get("id"))]
    except (OSError, ValueError):
        return []


def projeto(pid: str | None) -> dict | None:
    if not valido(pid):
        return None
    return next((p for p in projetos() if p["id"] == pid), None)


def _gravar_projetos(lista: list[dict]) -> None:
    ARQ_PROJETOS.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ARQ_PROJETOS.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ARQ_PROJETOS)


def salvar_projeto(dados: dict) -> dict:
    """Cria (sem id) ou atualiza um projeto. Devolve o projeto salvo."""
    nome = re.sub(r"\s+", " ", str(dados.get("nome") or "")).strip()[:60]
    if not nome:
        raise ValueError("o projeto precisa de um nome")
    pasta = str(dados.get("pasta") or "").strip().strip('"')
    if pasta and not Path(pasta).expanduser().is_dir():
        raise ValueError("a pasta não existe neste computador: " + pasta)
    campos = {"nome": nome,
              "instrucoes": str(dados.get("instrucoes") or "").strip()[:LIMITE_CAMPO],
              "resumo": str(dados.get("resumo") or "").strip()[:LIMITE_CAMPO],
              "pasta": pasta}
    with _trava:
        lista = projetos()
        pid = dados.get("id")
        atual = next((p for p in lista if p["id"] == pid), None) if valido(pid) else None
        if atual:
            atual.update(campos)
        else:
            atual = {"id": novo_id(), "criado": time.time(), **campos}
            lista.append(atual)
        _gravar_projetos(lista)
    return atual


def apagar_projeto(pid: str) -> bool:
    """Apaga o projeto; as conversas dele voltam para Recentes (não são apagadas)."""
    with _trava:
        lista = projetos()
        nova = [p for p in lista if p["id"] != pid]
        if len(nova) == len(lista):
            return False
        _gravar_projetos(nova)
        for arq in PASTA.glob("*.json") if PASTA.exists() else []:
            try:
                c = json.loads(arq.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if c.get("projeto") == pid:
                c.pop("projeto", None)
                _salvar(c)
    return True


def mover(cid: str, pid: str) -> bool:
    """Põe a conversa num projeto ("" = tira de projeto). Só se ela já existe."""
    if not valido(cid) or (pid and not projeto(pid)):
        return False
    with _trava:
        c = carregar(cid)
        if not c:
            return False
        if pid:
            c["projeto"] = pid
        else:
            c.pop("projeto", None)
        _salvar(c)
    return True


def contexto_projeto(pid: str | None) -> str:
    """Texto que entra no prompt de sistema das conversas deste projeto."""
    p = projeto(pid)
    if not p:
        return ""
    partes = ["\n\nPROJETO ATUAL: %s. Esta conversa faz parte dele." % p["nome"]]
    if p.get("instrucoes"):
        partes.append("Instruções do usuário para este projeto: " + p["instrucoes"])
    if p.get("resumo"):
        partes.append("O que já foi feito ou decidido neste projeto (resumo revisado pelo "
                      "usuário): " + p["resumo"])
    if p.get("pasta"):
        partes.append("Pasta do projeto no computador: %s. Quando o pedido envolver "
                      "arquivos do projeto, trabalhe nela." % p["pasta"])
    return "\n".join(partes)


def material_resumo(pid: str, max_conversas: int = 12) -> str:
    """Conversas do projeto (mais recentes primeiro), enxutas, para sugerir o resumo."""
    itens = [c for c in listar() if c.get("projeto") == pid][:max_conversas]
    blocos = []
    for it in itens:
        c = carregar(it["id"]) or {}
        linhas = ["## " + (c.get("titulo") or "Conversa")]
        for m in c.get("mensagens", [])[-8:]:
            quem = "Usuário" if m["quem"] == "voce" else "Luna"
            linhas.append("%s: %s" % (quem, re.sub(r"\s+", " ", m["texto"])[:400]))
        blocos.append("\n".join(linhas))
    return "\n\n".join(blocos)


# ------------------------------------------------ continuidade no Hermes
def sessao_hermes(cid: str) -> tuple[str, list[dict]]:
    """(id da sessão no Hermes, mensagens que ele ainda não viu).

    Com o cabeçalho X-Hermes-Session-Id o Hermes carrega o histórico dele
    (com as ferramentas que usou) e IGNORA o que vai no corpo. O que a IA
    rápida respondeu no meio tempo ele não viu: essas mensagens vão como um
    preâmbulo curto no pedido. Não inclui a última mensagem (o pedido atual).
    """
    c = carregar(cid) or {}
    h = c.get("hermes") or {}
    sessao = h.get("sessao") or ("luna_" + cid)
    msgs = c.get("mensagens", [])[:-1]
    return sessao, msgs[int(h.get("visto") or 0):][-10:]


def hermes_viu(cid: str, sessao: str) -> None:
    """Chamado depois que a especialista respondeu: ela já viu tudo até aqui."""
    if not valido(cid):
        return
    with _trava:
        c = carregar(cid)
        if not c:
            return
        c["hermes"] = {"sessao": sessao, "visto": len(c["mensagens"])}
        _salvar(c)


def preambulo_hermes(pendentes: list[dict]) -> str:
    if not pendentes:
        return ""
    linhas = [("Usuário: " if m["quem"] == "voce" else "Luna: ")
              + re.sub(r"\s+", " ", m["texto"])[:600] for m in pendentes]
    return ("[Contexto: nesta conversa, depois da sua última resposta, a Luna "
            "rápida tratou isto sem você:\n" + "\n".join(linhas) + "\n]\n\n")
