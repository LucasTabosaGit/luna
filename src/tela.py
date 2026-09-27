"""A Luna olha a sua tela: captura + modelo de visão.

Dois caminhos:
- local: o Qwen3-VL já carregado no LM Studio (é modelo de visão). Grátis,
  ~2-4 s, bom para "o que está escrito", "que erro é esse", "que programa".
- Claude (via Hermes): quando a pergunta pede raciocínio de verdade ou
  você está no modo Claude / pediu "pensa melhor".

A captura é do monitor onde está o MOUSE (é para onde você está olhando),
reduzida para no máximo 1600 px de largura - o texto continua legível e a
imagem cabe no contexto de 4096 do Qwen. Nada é salvo em disco.
"""
from __future__ import annotations

import base64
import ctypes
import io
import re
import time
import unicodedata

LARGURA_MAX = 1600


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t)
                   if unicodedata.category(c) != "Mn")


# "olha minha tela", "o que tem na tela?", "vê esse erro aqui na tela",
# "analisa a tela", "lê o que está na tela", "o que você vê na tela",
# "me ajuda com isso que está na tela", "o que é isso aqui na minha tela"
_PEDE = re.compile(
    r"\b(tela|telinha|monitor|print|screen|screenshot)\b"
    r"|\b(olha|olhe|olhar|ve|veja|ver|le|leia|ler|analisa|analise|analisar|"
    r"confere|confira|da uma olhada|de uma olhada)\s+(isso|isto|aqui|esse|essa|este|esta)\b")
# "tela" em pedidos que NÃO são para olhar: esses ficam com os comandos.
_NAO = re.compile(
    r"\b(bloqueia|bloquear|trava|travar|tira|tirar|faz|fazer|bate|bater|salva|"
    r"desliga|apaga|limpa|grava|gravar|compartilha|brilho|tela cheia|"
    r"descanso de tela|protetor de tela|area de trabalho)\b")


def pede_ver(texto: str) -> bool:
    t = _sem_acento(texto).lower()
    return bool(_PEDE.search(t)) and not _NAO.search(t)


class _PONTO(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def capturar(largura_max: int = LARGURA_MAX) -> tuple[str, tuple[int, int]]:
    """Print do monitor onde está o mouse -> (data URL JPEG, (w, h))."""
    import mss
    from PIL import Image

    p = _PONTO()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    with mss.mss() as sct:
        mon = sct.monitors[1]
        for m in sct.monitors[1:]:
            if (m["left"] <= p.x < m["left"] + m["width"]
                    and m["top"] <= p.y < m["top"] + m["height"]):
                mon = m
                break
        cru = sct.grab(mon)
    img = Image.frombytes("RGB", cru.size, cru.bgra, "raw", "BGRX")
    if img.width > largura_max:
        img = img.resize((largura_max, round(img.height * largura_max / img.width)),
                         Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return ("data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(),
            img.size)


INSTRUCAO = (
    "Você é a Luna, assistente de voz. O usuário pediu para você olhar a tela "
    "dele; a imagem é um print dela AGORA. Responda em português do Brasil, "
    "falado e curto (2 a 4 frases, sem markdown, sem listas), indo direto ao "
    "que ele perguntou. Se for erro, diga o que significa e o que fazer. Se "
    "não der para ler algo, diga isso em vez de inventar.")


# Imagem mandada pelo chat (colada, arrastada ou anexada), não um print.
INSTRUCAO_IMAGEM = (
    "Você é a Luna, assistente de voz. O usuário mandou uma ou mais imagens pelo "
    "chat. Responda em português do Brasil, falado e curto (2 a 5 frases, sem "
    "markdown, sem listas), indo direto ao que ele perguntou. Se não der para "
    "ler algo, diga isso em vez de inventar.")


def mensagem(pergunta: str, data_url) -> list:
    urls = data_url if isinstance(data_url, (list, tuple)) else [data_url]
    return [{"role": "user", "content": [
        *({"type": "image_url", "image_url": {"url": u}} for u in urls),
        {"type": "text", "text": pergunta},
    ]}]


def olhar_local(llm, modelo: str, pergunta: str, data_url, extra=None,
                max_tokens: int = 260, instrucao: str = "") -> tuple[str, float]:
    t0 = time.time()
    r = llm.chat.completions.create(
        model=modelo, temperature=0.2, max_tokens=max_tokens,
        messages=[{"role": "system", "content": instrucao or INSTRUCAO}]
        + mensagem(pergunta, data_url),
        **(extra or {}))
    return (r.choices[0].message.content or "").strip(), time.time() - t0
