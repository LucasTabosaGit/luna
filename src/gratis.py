"""Modelos grátis do momento no OpenRouter.

A lista pública (https://openrouter.ai/api/v1/models) não pede chave. Grátis =
preço zero na entrada e na saída. Fica só o que serve para conversar e usar
ferramentas (texto de saída + "tools"), porque os mesmos modelos podem ir para
a IA rápida ou para a especialista pelo Hermes. Modelos "stealth" têm data
para sumir (expiration_date); a tela mostra.

Nada se atualiza no código: a lista vem do site e fica 6 h em memória.
"""
from __future__ import annotations

import threading
import time

URL = "https://openrouter.ai/api/v1/models"
VALIDADE_S = 6 * 3600
_cache: dict = {"quando": 0.0, "lista": []}
_trava = threading.Lock()


def _gratis(m: dict) -> bool:
    p = m.get("pricing") or {}
    if str(p.get("prompt")) != "0" or str(p.get("completion")) != "0":
        return False
    if m.get("id") == "openrouter/free":          # roteador: escolhe às cegas
        return False
    saida = (m.get("architecture") or {}).get("output_modalities") or ["text"]
    return "text" in saida and "tools" in (m.get("supported_parameters") or [])


def _resumir(m: dict) -> dict:
    nome = str(m.get("name") or m["id"]).replace(" (free)", "")
    return {"id": m["id"], "nome": nome,
            "contexto": int(m.get("context_length") or 0),
            "sai_em": m.get("expiration_date") or ""}


def listar(forcar: bool = False) -> list[dict]:
    """Modelos grátis agora (cache de 6 h). Sem internet: a última lista boa."""
    with _trava:
        if not forcar and _cache["lista"] and time.time() - _cache["quando"] < VALIDADE_S:
            return _cache["lista"]
    import httpx
    try:
        dados = httpx.get(URL, timeout=15).json().get("data") or []
    except Exception:  # noqa: BLE001 - sem rede: devolve o que tinha
        return _cache["lista"]
    hoje = time.strftime("%Y-%m-%d")
    lista = [_resumir(m) for m in dados
             if _gratis(m) and not (m.get("expiration_date") and m["expiration_date"] < hoje)]
    # Os que têm data para sumir por último: quem dura mais aparece primeiro.
    lista.sort(key=lambda x: (bool(x["sai_em"]), -x["contexto"]))
    with _trava:
        _cache.update(quando=time.time(), lista=lista)
    return lista


def eh_gratis(ident: str) -> bool:
    return achar(ident) is not None


def achar(ident: str) -> dict | None:
    """Logo depois de abrir a Luna o cache está vazio: busca uma vez."""
    if not ident:
        return None
    lista = _cache["lista"] or listar()
    return next((m for m in lista if m["id"] == ident), None)
