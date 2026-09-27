"""Gera áudios para treinar o detector de "Luna" (src/detector_luna.py).

    .venv/Scripts/python.exe src/gera_dados_luna.py

Saída: dados_luna/{pos,neg}/<voz>_<n>.wav (16 kHz mono).
- pos: "Luna" sozinho e em frases ("Luna, que horas são?", "Ô Luna...").
- neg: palavras parecidas (lua, Luana, lona, uma, coluna, luva, Lula,
  Hermes...) e frases comuns. É o que evita disparo falso.
Vozes: Edge (pt-BR, pt-PT, multilíngues, espanhol) com velocidade e tom
variados, e Kokoro offline. Síntese já feita é pulada (dá para retomar).
"""
from __future__ import annotations

import asyncio
import io
import random
import sys
from pathlib import Path

import av
import numpy as np
import soundfile as sf

RAIZ = Path(__file__).resolve().parent.parent
SAIDA = RAIZ / "dados_luna"

POS = [
    "Luna.", "Luna!", "Luna?", "Ô Luna.", "Ei, Luna.", "Oi Luna.",
    "Luna, que horas são?", "Luna, abre o Spotify.", "Luna, aumenta o volume.",
    "Luna, me conta uma piada.", "Luna, pausa a música.", "Luna, que dia é hoje?",
    "Ô Luna, tá me ouvindo?", "Luna, liga um timer de cinco minutos.",
    "Luna, como está o tempo?", "Luna, abre o Chrome.", "Luna, cancela.",
    "Que horas são, Luna?", "Obrigado, Luna.", "Luna, próxima música.",
    "Fala, Luna.", "E aí Luna, tudo bem?", "Luna, você consertou a skill?",
    "Luna, pesquisa no Google.", "Luna, bloqueia a tela.",
]
NEG = [
    "Lua.", "A lua está cheia hoje.", "Luana.", "A Luana chegou tarde.", "Lona.",
    "Cobre com a lona.", "Uma.", "Uma coisa que eu queria falar.", "Coluna.",
    "Minha coluna está doendo.", "Luva.", "Coloca a luva.", "Lula.", "Duna.",
    "Fortuna.", "Tribuna.", "Laguna.", "Lunar.", "Lúcia.", "Lunático.",
    "Hermes.", "Alguma.", "Nenhuma.", "Vacina.", "Menina.", "Luz.", "Lindo.",
    "Que horas são?", "Abre o Spotify.", "Vamos ver o filme amanhã à noite.",
    "Me passa o sal.", "Tô com fome.", "Lua de mel foi em Maceió.",
    "Nenhuma novidade por aqui.", "A escola fica na rua de cima.",
    "Liga pra minha mãe.", "O jogo começa às nove.", "Hoje é sexta-feira.",
    "Fecha a porta, por favor.", "Música boa essa.", "Uma luneta nova.",
    "Tem alguma ideia?", "Que legal.", "Tá bom, tchau.", "Lua nova.",
    "Oi, tudo bem?", "Obrigado.", "Valeu.", "Vou dormir.", "Lanche.",
]

EDGE = [
    "pt-BR-AntonioNeural", "pt-BR-FranciscaNeural", "pt-BR-ThalitaMultilingualNeural",
    "pt-PT-DuarteNeural", "pt-PT-RaquelNeural",
    "en-US-AndrewMultilingualNeural", "en-US-AvaMultilingualNeural",
    "en-US-BrianMultilingualNeural", "en-US-EmmaMultilingualNeural",
    "fr-FR-RemyMultilingualNeural", "de-DE-SeraphinaMultilingualNeural",
    "it-IT-GiuseppeMultilingualNeural", "es-MX-JorgeNeural", "es-AR-ElenaNeural",
]
# Variações de fala por voz: (rate, pitch)
VARIA = [("+0%", "+0Hz"), ("-15%", "-5Hz"), ("+15%", "+5Hz")]
KOKORO = ["pf_dora", "pm_alex", "pm_santa"]


def _mp3_para_16k(dados: bytes) -> np.ndarray:
    c = av.open(io.BytesIO(dados))
    rs = av.AudioResampler(format="s16", layout="mono", rate=16000)
    out = []
    for fr in c.decode(audio=0):
        for r in rs.resample(fr):
            out.append(r.to_ndarray().reshape(-1))
    for r in rs.resample(None):
        out.append(r.to_ndarray().reshape(-1))
    return np.concatenate(out).astype(np.float32) / 32768.0


async def _edge(texto, voz, rate, pitch) -> np.ndarray:
    import edge_tts
    com = edge_tts.Communicate(texto, voz, rate=rate, pitch=pitch)
    buf = bytearray()
    async for p in com.stream():
        if p["type"] == "audio":
            buf += p["data"]
    return _mp3_para_16k(bytes(buf))


def _grava(p: Path, a: np.ndarray):
    p.parent.mkdir(parents=True, exist_ok=True)
    sf.write(p, a, 16000, subtype="PCM_16")


async def gerar_edge():
    sem = asyncio.Semaphore(6)
    tarefas = []
    for classe, frases in (("pos", POS), ("neg", NEG)):
        for voz in EDGE:
            curta = voz.split("-")[2].replace("Neural", "").replace("Multilingual", "M")
            for vi, (rate, pitch) in enumerate(VARIA):
                # Nem toda frase em toda variação: o suficiente e mais rápido.
                for fi, f in enumerate(frases):
                    if vi and (fi + vi) % 2:
                        continue
                    p = SAIDA / classe / f"{curta}_{vi}_{fi:02d}.wav"
                    if p.exists():
                        continue

                    async def um(f=f, voz=voz, rate=rate, pitch=pitch, p=p):
                        async with sem:
                            for tentativa in range(3):
                                try:
                                    _grava(p, await _edge(f, voz, rate, pitch))
                                    return
                                except Exception as e:  # noqa: BLE001
                                    if tentativa == 2:
                                        print("  falhou", p.name, str(e)[:60])
                                    await asyncio.sleep(1)
                    tarefas.append(um())
    print(f"Edge: {len(tarefas)} áudios a gerar", flush=True)
    for i in range(0, len(tarefas), 60):
        await asyncio.gather(*tarefas[i:i + 60])
        print(f"  {min(i + 60, len(tarefas))}/{len(tarefas)}", flush=True)


def gerar_kokoro():
    sys.path.insert(0, str(RAIZ / "src"))
    import config  # noqa: F401  aponta HF_HOME para modelos/hf (Kokoro offline)
    import ambiente  # noqa: F401
    from kokoro import KPipeline
    ambiente.reaplicar_espeak()
    pipe = KPipeline(lang_code="p")
    n = 0
    for classe, frases in (("pos", POS), ("neg", NEG)):
        for voz in KOKORO:
            for vel in (0.9, 1.1):
                for fi, f in enumerate(frases):
                    p = SAIDA / classe / f"k{voz}_{int(vel * 10)}_{fi:02d}.wav"
                    if p.exists():
                        continue
                    partes = [a if isinstance(a, np.ndarray) else a.detach().cpu().numpy()
                              for _g, _p, a in pipe(f, voice=voz, speed=vel)]
                    if not partes:
                        continue
                    a = np.concatenate(partes).astype(np.float32)
                    # Kokoro sai em 24 kHz: reamostra para 16 kHz.
                    idx = np.arange(0, len(a), 24000 / 16000)
                    a = np.interp(idx, np.arange(len(a)), a).astype(np.float32)
                    _grava(p, a)
                    n += 1
    print(f"Kokoro: {n} áudios", flush=True)


if __name__ == "__main__":
    random.seed(0)
    asyncio.run(gerar_edge())
    gerar_kokoro()
    for c in ("pos", "neg"):
        print(c, len(list((SAIDA / c).glob("*.wav"))))
