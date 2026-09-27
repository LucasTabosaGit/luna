"""'Luna' + respiro + pedido, falado de uma vez (sem esperar ela ouvir).

    .venv/Scripts/python.exe src/teste_nome_emendado.py

Antes: o nome virava um turno sozinho; o pedido chegava enquanto ele era
transcrito e era JOGADO FORA ("tenho que dar o comando Luna, esperar e
depois falar"). Mede com pausas de 0,3 s a 1,5 s entre o nome e o pedido.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
import numpy as np  # noqa: E402
import websockets  # noqa: E402

from gera_dados_luna import _edge  # noqa: E402

PEDIDOS = [("Que horas são?", "São "), ("Que dia é hoje?", "Hoje é ")]
VOZES = ["pt-BR-AntonioNeural", "pt-BR-FranciscaNeural"]
PAUSAS = [0.3, 0.7, 1.0, 1.5]


def pcm(a):
    return (np.clip(a, -1, 1) * 32767).astype(np.int16)


async def rodada(ws, audio, espera):
    await ws.send(json.dumps({"tipo": "ativacao", "ligada": True}))
    await asyncio.sleep(0.3)
    sil = np.zeros(16000, dtype=np.int16)
    dados = np.concatenate([sil[:4000], audio, sil, sil])
    t0 = time.time()

    async def enviar():
        for i in range(0, len(dados), 1024):
            await ws.send(dados[i:i + 1024].tobytes())
            await asyncio.sleep(0.064)

    env = asyncio.create_task(enviar())
    bot, fim = [], time.time() + 25
    while time.time() < fim:
        try:
            m = await asyncio.wait_for(ws.recv(), 1)
        except asyncio.TimeoutError:
            continue
        if isinstance(m, bytes):
            continue
        d = json.loads(m)
        if d.get("tipo") == "bot":
            bot.append(d["texto"])
        elif d.get("tipo") == "metrica":
            break
    await env
    txt = " ".join(bot)
    return espera.lower() in txt.lower(), time.time() - t0, txt


async def main():
    ok = total = 0
    async with websockets.connect("ws://127.0.0.1:8777/ws", max_size=None) as ws:
        await ws.send(json.dumps({"tipo": "cerebro", "cerebro": "auto"}))
        for voz in VOZES:
            nome = await _edge("Luna.", voz, "+0%", "+0Hz")
            for pausa in PAUSAS:
                frase, esp = PEDIDOS[int(pausa * 10) % 2]
                ped = await _edge(frase, voz, "+0%", "+0Hz")
                a = pcm(np.concatenate([nome, np.zeros(int(pausa * 16000), np.float32), ped]))
                certo, dt, txt = await rodada(ws, a, esp)
                ok += certo
                total += 1
                print(f"{'ok ' if certo else 'ERR'} {voz[6:13]} pausa {pausa:.1f}s "
                      f"{frase!r:18} {dt:4.1f}s -> {txt[:50]!r}", flush=True)
                # espera a janela de conversa fechar para a próxima rodada
                await ws.send(json.dumps({"tipo": "ativacao", "ligada": False}))
                fim = time.time() + 4            # esvazia respostas atrasadas
                while time.time() < fim:
                    try:
                        await asyncio.wait_for(ws.recv(), 0.5)
                    except asyncio.TimeoutError:
                        pass
    print(f"\n{ok}/{total} corretos")


asyncio.run(main())
