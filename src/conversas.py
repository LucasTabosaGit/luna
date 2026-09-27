"""Conversas salvas da Luna: sobrevivem a recarregar a tela e a reiniciar.

Uma conversa = um arquivo `dados/conversas/<id>.json`:
    {"id", "titulo", "criada", "atualizada",
     "mensagens": [{"quem": "voce"|"bot", "texto", "t", "rota"?, "fotos"?: [url]}]}

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
MAX_LISTA = 60
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
              substituir: bool = False, **extra) -> None:
    """Acrescenta uma mensagem. `juntar`: emenda na última do mesmo autor
    (a resposta chega frase a frase). `substituir`: troca o texto da última
    (resposta final formatada, ou "você" corrigido pelo corretor)."""
    if not valido(cid) or not (texto or "").strip():
        return
    with _trava:
        conv = carregar(cid) or {"id": cid, "titulo": "", "criada": time.time(),
                                 "mensagens": []}
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
                      "atualizada": c.get("atualizada", 0), "n": len(c["mensagens"])})
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
