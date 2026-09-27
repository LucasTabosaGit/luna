"""Conversa contínua DESLIGADA (padrão): só atende com "Luna" na frase.

    .venv/Scripts/python.exe src/teste_so_luna.py

Com áudio real (Edge TTS) pelo mesmo caminho do microfone:
  1. "Luna, que horas são?"            -> responde
  2. (sem nome) "Que dia é hoje?"        -> responde (janela renovada)
  3. (sem nome) outro pedido             -> responde (renovou de novo)
  4. 7 s em silêncio, (sem nome)         -> IGNORADO (janela de 5 s fechou)
  5. "Luna, que dia é hoje?"           -> responde
  6. (sem nome) "Valeu"                  -> encerra ("Tá bom.")
  7. (sem nome, logo em seguida)         -> IGNORADO
"""
import asyncio
import json
import os
import sys
import time

import av
import numpy as np
import websockets

URL = "ws://127.0.0.1:8777/ws"
PASTA = "logs/janela_luna"
VOZ = "pt-BR-AntonioNeural"


async def gerar(nome, frase):
    import edge_tts

    caminho = os.path.join(PASTA, nome + ".mp3")
    if not os.path.exists(caminho):
        os.makedirs(PASTA, exist_ok=True)
        await edge_tts.Communicate(frase, VOZ).save(caminho)
    return caminho


def carrega(p):
    c = av.open(p)
    r = av.AudioResampler(format="s16", layout="mono", rate=16000)
    a = [f.to_ndarray() for fr in c.decode(audio=0) for f in r.resample(fr)]
    return np.concatenate(a, axis=1).flatten().astype(np.int16)


async def falar(ws, pcm):
    silencio = np.zeros(16000, dtype=np.int16)
    dados = np.concatenate([silencio[:4000], pcm, silencio])
    for i in range(0, len(dados), 1024):
        await ws.send(dados[i:i + 1024].tobytes())
        await asyncio.sleep(0.064)


async def ouvir(ws, espera=30):
    ev = {"voce": None, "bot": [], "ignorado": None, "estados": []}
    fim = time.time() + espera
    while time.time() < fim:
        try:
            m = await asyncio.wait_for(ws.recv(), fim - time.time())
        except asyncio.TimeoutError:
            break
        if isinstance(m, bytes):
            continue
        d = json.loads(m)
        t = d.get("tipo")
        if t == "estado":
            ev["estados"].append(d.get("estado"))
        if t == "voce":
            ev["voce"] = d["texto"]
        elif t == "bot":
            ev["bot"].append(d["texto"])
        elif t == "ignorado":
            ev["ignorado"] = d["texto"]
            return ev
        elif t == "metrica":
            return ev
        elif t == "estado" and d.get("estado") == "ouvindo" and ev["bot"]:
            return ev
    return ev


async def main():
    # Conversa contínua DESLIGADA (padrão): sem "Luna" na frase, nada.
    passos = [
        ("j1", "Luna, que horas são?", "responde", 0),
        ("j2", "Que dia é hoje?", "ignora", 0),
        ("j3", "Me conta uma curiosidade curta.", "ignora", 0),
        ("s1", "E aí", "ignora", 0),
        ("s2", "Sim, deixa eu ver.", "ignora", 0),
        ("j5", "Luna, que dia é hoje?", "responde", 0),
        ("s3", "Luna.", "acorda", 0),
        ("j2", "Que dia é hoje?", "responde", 0),
        ("j4", "Que horas são?", "ignora", 0),
    ]
    arquivos = {n: await gerar(n, f) for n, f, _, _ in passos}
    ok = 0
    async with websockets.connect(URL, max_size=None) as ws:
        await ws.send(json.dumps({"tipo": "voz", "voz": "pf_dora", "velocidade": 1.0}))
        await ws.send(json.dumps({"tipo": "cerebro", "cerebro": "rapido"}))
        await ws.send(json.dumps({"tipo": "ativacao", "ligada": True}))
        await ws.send(json.dumps({"tipo": "continua", "ligada": False}))
        await asyncio.sleep(1)
        for nome, frase, espera, pausa in passos:
            if pausa:
                print(f"   ... {pausa}s em silêncio")
                await asyncio.sleep(pausa)
            t0 = time.time()
            await falar(ws, carrega(arquivos[nome]))
            ev = await ouvir(ws, 3 if espera == "acorda" else 30)
            # O navegador avisa quando o áudio da resposta acaba de tocar.
            await ws.send(json.dumps({"tipo": "fim_audio"}))
            # Descarta mensagens atrasadas (o portão responde em ms; um
            # "ignorado" de um pedaço da frase anterior contava na próxima).
            fim_dreno = time.time() + (0.3 if espera == "acorda" else 1.5)
            while time.time() < fim_dreno:
                try:
                    await asyncio.wait_for(ws.recv(), fim_dreno - time.time())
                except asyncio.TimeoutError:
                    break
            if espera == "acorda":
                certo = not ev["bot"] and ev["ignorado"] is None
            elif espera == "ignora":
                certo = ev["ignorado"] is not None and not ev["bot"] and not (
                    {"transcrevendo", "pensando", "executando", "falando"} & set(ev["estados"]))
            elif espera == "encerra":
                certo = ev["bot"] == ["Tá bom."]
            else:
                certo = bool(ev["bot"])
            ok += certo
            print(f"{'ok ' if certo else 'ERR'} {frase:32s} {espera:9s} {time.time() - t0:5.1f}s"
                  f"  ouviu={ev['voce'] or ev['ignorado']!r}"
                  f"  resposta={' '.join(ev['bot'])[:60]!r}")
        await ws.send(json.dumps({"tipo": "ativacao", "ligada": False}))
        await ws.send(json.dumps({"tipo": "cerebro", "cerebro": "auto"}))
    print(f"\n{ok}/{len(passos)} corretos")
    return 0 if ok == len(passos) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
