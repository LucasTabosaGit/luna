"""Detector de "Luna" no caminho real (Whisper + texto parecido + áudio).

    .venv/Scripts/python.exe src/teste_detector_luna.py

Frases faladas por vozes que NÃO estão no treino do detector (Edge
en-GB/Sonia, pt-BR Antonio com fala rápida, Kokoro pm_santa com tom
diferente), mais ruído de fundo. Para cada uma: o que o Whisper escreveu,
se o texto sozinho acordaria, e o que o detector decidiu.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
import numpy as np  # noqa: E402

import config  # noqa: E402,F401
import ativacao  # noqa: E402
import detector_luna as D  # noqa: E402
from gera_dados_luna import _edge  # noqa: E402

# (frase, voz, rate, tem o nome?)
CASOS = [
    ("Ô Luna, você consertou a skill?", "pt-BR-AntonioNeural", "+25%", True),
    ("Luna, pausa a música.", "pt-BR-AntonioNeural", "+30%", True),
    ("Luna.", "pt-BR-FranciscaNeural", "+30%", True),
    ("Luna, abre a calculadora.", "en-GB-SoniaNeural", "+0%", True),
    ("Oi Luna, tudo bem?", "es-CO-GonzaloNeural", "+0%", True),
    ("Luna, que horas são?", "fr-FR-VivienneMultilingualNeural", "+10%", True),
    ("Lunaa, aumenta o som.", "pt-BR-FranciscaNeural", "+20%", True),
    ("Minha coluna está doendo muito.", "pt-BR-AntonioNeural", "+0%", False),
    ("Lua cheia hoje, olha que linda.", "pt-BR-FranciscaNeural", "+0%", False),
    ("Uma pizza grande, por favor.", "pt-BR-AntonioNeural", "+20%", False),
    ("Luana, vem jantar.", "pt-BR-FranciscaNeural", "+0%", False),
    ("Lona preta resolve.", "pt-BR-AntonioNeural", "+0%", False),
    ("Uma hora dessas eu te ligo.", "en-GB-SoniaNeural", "+0%", False),
    ("Coluna do meio.", "pt-BR-FranciscaNeural", "+20%", False),
    ("Lona.", "pt-BR-AntonioNeural", "+0%", False),
    ("Cobre com a lona.", "pt-BR-FranciscaNeural", "+0%", False),
    ("Luna, pausa.", "en-GB-SoniaNeural", "+10%", True),
    ("Luna?", "pt-BR-AntonioNeural", "+0%", True),
    ("Luna, toca Legião Urbana.", "pt-PT-RaquelNeural", "+15%", True),
    ("Luna, próxima.", "es-MX-DaliaNeural", "+0%", True),
]


async def main():
    from ouvido import Transcritor
    ouv = Transcritor()
    ouv.carregar()
    rng = np.random.default_rng(1)
    certos, total = 0, 0
    for frase, voz, rate, tem in CASOS:
        a = await _edge(frase, voz, rate, "+0Hz")
        # Ruído de fundo leve (sala), ~30 dB abaixo da fala.
        a = (a + rng.normal(0, 0.03 * np.abs(a).max(), len(a))).astype(np.float32)
        txt = ouv.transcrever(a, dica=ativacao.DICA)[0]
        por_texto, _ = ativacao.detectar(txt)
        det, p = (False, 0.0)
        if por_texto:
            p = D.pontuar(a)
            if p < D.VETO:          # mesmo veto do servidor
                por_texto = False
        elif ativacao.parece_nome(txt):
            det, p = D.chamou(a)
        final = por_texto or det
        ok = final == tem
        certos += ok
        total += 1
        via = ("texto %.2f" % p) if por_texto else ("DETECTOR %.2f" % p if ativacao.parece_nome(txt)
                                          else "-")
        print(f"{'ok ' if ok else 'ERR'} {'NOME' if tem else 'sem '} acordou={final!s:5} "
              f"via={via:14} whisper={txt!r}")
    print(f"\n{certos}/{total} corretos")


if __name__ == "__main__":
    asyncio.run(main())
