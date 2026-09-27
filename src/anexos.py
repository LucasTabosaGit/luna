"""Anexos de arquivo no chat (além de imagem): PDF e texto viram contexto.

O navegador manda {nome, tipo, dados(base64)}. Aqui vira TEXTO que entra
na pergunta (limitado), e o pedido vai ao Claude - resumir/analisar
documento é trabalho de raciocínio. O arquivo também é salvo em
dados/anexos/ para o Claude poder abrir de novo se precisar.
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
from pathlib import Path

PASTA = Path(__file__).resolve().parent.parent / "dados" / "anexos"
MAX_BYTES = 15 * 1024 * 1024
MAX_TEXTO = 60_000            # caracteres por arquivo no prompt
TEXTO_EXT = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".css",
             ".xml", ".yaml", ".yml", ".log", ".ini", ".toml", ".sql", ".ps1", ".bat"}


def _nome_seguro(nome: str) -> str:
    nome = Path(nome or "arquivo").name
    return re.sub(r"[^\w.\- ]+", "_", nome)[:80] or "arquivo"


def ler(item: dict) -> dict | None:
    """-> {"nome", "caminho", "texto", "paginas"?} ou None se não suportado."""
    nome = _nome_seguro(item.get("nome", ""))
    try:
        dados = base64.b64decode((item.get("dados") or "").split(",", 1)[-1])
    except ValueError:
        return None
    if not dados or len(dados) > MAX_BYTES:
        return None
    ext = Path(nome).suffix.lower()
    PASTA.mkdir(parents=True, exist_ok=True)
    caminho = PASTA / (hashlib.sha1(dados).hexdigest()[:10] + "_" + nome)
    if not caminho.exists():
        caminho.write_bytes(dados)
    if ext == ".pdf":
        from pypdf import PdfReader
        try:
            pdf = PdfReader(io.BytesIO(dados))
            paginas = [p.extract_text() or "" for p in pdf.pages[:200]]
        except Exception:  # noqa: BLE001 - PDF quebrado/protegido
            return {"nome": nome, "caminho": str(caminho), "texto": "", "paginas": 0}
        texto = "\n\n".join("[página %d]\n%s" % (i + 1, t.strip()) for i, t in enumerate(paginas) if t.strip())
        return {"nome": nome, "caminho": str(caminho), "texto": texto[:MAX_TEXTO],
                "paginas": len(pdf.pages), "cortado": len(texto) > MAX_TEXTO}
    if ext in TEXTO_EXT or (item.get("tipo") or "").startswith("text/"):
        for cod in ("utf-8", "cp1252", "latin-1"):
            try:
                texto = dados.decode(cod)
                break
            except UnicodeDecodeError:
                continue
        return {"nome": nome, "caminho": str(caminho), "texto": texto[:MAX_TEXTO],
                "cortado": len(texto) > MAX_TEXTO}
    return {"nome": nome, "caminho": str(caminho), "texto": "", "binario": True}


def montar_pergunta(pergunta: str, lidos: list[dict]) -> str:
    partes = []
    for a in lidos:
        cab = "Arquivo anexado: %s (salvo em %s)" % (a["nome"], a["caminho"])
        if a.get("paginas") is not None:
            cab += ", %d páginas" % a["paginas"]
        if a.get("binario"):
            partes.append(cab + ". Formato sem texto extraível: abra pelo caminho se precisar.")
        elif not a["texto"].strip():
            partes.append(cab + ". Não tem texto extraível (talvez seja escaneado).")
        else:
            partes.append(cab + (" (cortado)" if a.get("cortado") else "") + ":\n<<<\n" + a["texto"] + "\n>>>")
    return "\n\n".join(partes) + "\n\nPedido: " + (pergunta or "Resuma o arquivo.")
