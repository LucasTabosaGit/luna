#!/usr/bin/env python3
"""Serviço de conversão de voz (RVC), isolado num processo próprio.

Por que um serviço separado e não uma importação:

    servidor web   Python 3.11  (torch cu128, kokoro, whisper)
    RVC            Python 3.10  (fairseq NÃO roda em 3.11+)

`fairseq`, dependência do RVC, usa `dataclasses` de um jeito que o
Python 3.11 rejeita ("mutable default ... is not allowed"). Não há
versão compatível. Então o RVC vive num venv próprio e conversa com o
servidor por HTTP local.

Protocolo: POST /converter com WAV cru no corpo, devolve WAV convertido.
Simples de propósito — o áudio é pequeno (uma frase) e tudo é local.

Subir:
    .venv-rvc\\Scripts\\python.exe src\\rvc_servico.py
"""
import io
import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(RAIZ / "modelos" / "hf"))
os.environ.setdefault("TORCH_HOME", str(RAIZ / "modelos" / "torch"))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

MODELOS = RAIZ / "modelos" / "rvc"
PORTA = int(os.environ.get("RVC_PORTA", "8778"))

# O faiss (usado pelo .index do RVC) abre arquivos pela API ANSI do
# Windows e falha com "could not open ... No such file or directory"
# quando o caminho tem acento — mesmo com o arquivo presente e legível
# pelo Python. É o mesmo defeito do espeak-ng e do torch.jit.load.
#
# O sintoma engana duas vezes: o RVC captura a exceção, segue sem o
# index e devolve áudio MESMO ASSIM — só que sem a conversão de timbre.
# O resultado sai idêntico à voz de entrada, como se nada tivesse
# acontecido.
ESPELHO = Path(os.environ.get("LOCALAPPDATA", str(RAIZ))) / "rvc-ascii"


def caminho_ascii(p: Path) -> str:
    """Devolve um caminho sem acentos para o arquivo, espelhando se preciso."""
    if str(p).isascii():
        return str(p)
    import shutil

    ESPELHO.mkdir(parents=True, exist_ok=True)
    destino = ESPELHO / p.name
    if not destino.exists() or destino.stat().st_size != p.stat().st_size:
        shutil.copy2(p, destino)
    return str(destino)

try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402

# PyTorch 2.6 passou a usar weights_only=True por padrão, e o
# hubert_base.pt do RVC não carrega assim. A falha acontece DENTRO do
# vc_single, que devolve o traceback como string em vez de levantar
# exceção — o sintoma vira "formato inesperado", sem pista da causa.
_load_original = torch.load


def _load_confiando(*a, **kw):
    kw.setdefault("weights_only", False)
    return _load_original(*a, **kw)


torch.load = _load_confiando

from fastapi import FastAPI, Request, Response  # noqa: E402
from rvc_python.infer import RVCInference  # noqa: E402

app = FastAPI(title="RVC")
_rvc = None
_voz_atual = None


def carregar(voz: str):
    """Carrega (ou troca) o modelo de voz. Devolve o RVC pronto."""
    global _rvc, _voz_atual

    if _rvc is None:
        # Sem CUDA (Mac não tem): processador. O rvc_python não tem MPS.
        dispositivo = "cuda:0" if torch.cuda.is_available() else "cpu"
        print("carregando RVC (%s)..." % dispositivo, flush=True)
        t0 = time.time()
        _rvc = RVCInference(models_dir=str(MODELOS), device=dispositivo)

        # Parâmetros alinhados ao pipeline de referência
        # (JarodMica/rvc-tts-pipeline) e à documentação do RVC.
        #
        # Um esclarecimento que corrigiu um erro meu: o RVC reduz a
        # entrada para 16 kHz de qualquer forma —
        #     audio = load_audio(input_audio_path, 16000)
        # porque o HuBERT, que extrai o conteúdo da fala, só opera
        # nessa taxa. Os 24 kHz do Kokoro são mais que suficientes, e a
        # banda alta que aparece na SAÍDA é sintetizada pelo decoder a
        # partir das features. Filtrá-la remove voz legítima.
        #
        # f0method: "rmvpe" é o recomendado — mais preciso no tom e mais
        #   rápido que o "harvest" padrão do wrapper.
        # protect 0.33: valor de referência do projeto. Protege
        #   consoantes surdas sem achatar a conversão.
        # index_rate 0.5: idem. Abaixo disso o timbre-alvo se perde.
        _rvc.f0method = "rmvpe"
        _rvc.protect = 0.33
        _rvc.index_rate = 0.5
        _rvc.filter_radius = 3
        _rvc.rms_mix_rate = 0.25

        print("  %.1fs | f0=%s protect=%.2f index=%.2f rms=%.2f"
              % (time.time() - t0, _rvc.f0method, _rvc.protect,
                 _rvc.index_rate, _rvc.rms_mix_rate), flush=True)

    if voz != _voz_atual:
        pth = MODELOS / ("%s.pth" % voz)
        if not pth.exists():
            raise FileNotFoundError("modelo %s não encontrado" % pth.name)
        idx = MODELOS / ("%s.index" % voz)
        _rvc.load_model(caminho_ascii(pth),
                        index_path=caminho_ascii(idx) if idx.exists() else "")
        _voz_atual = voz
        print("  modelo: %s" % voz, flush=True)

    return _rvc


def converter(wav_bytes: bytes, voz: str) -> tuple[int, np.ndarray]:
    rvc = carregar(voz)

    # O vc_single lê de ARQUIVO, e o carregador de áudio do RVC também
    # passa pelo ffmpeg/C — caminho sem acento aqui também.
    tmp = ESPELHO if not str(RAIZ).isascii() else RAIZ / "cache"
    tmp.mkdir(parents=True, exist_ok=True)
    entrada = tmp / "rvc_entrada.wav"
    entrada.write_bytes(wav_bytes)

    mi = rvc.models.get(rvc.current_model, {})
    idx = MODELOS / ("%s.index" % voz)
    file_index = mi.get("index") or (caminho_ascii(idx) if idx.exists() else "")

    saida = rvc.vc.vc_single(
        sid=0, input_audio_path=str(entrada),
        f0_up_key=rvc.f0up_key, f0_method=rvc.f0method,
        file_index=file_index,
        index_rate=rvc.index_rate, filter_radius=rvc.filter_radius,
        resample_sr=rvc.resample_sr, rms_mix_rate=rvc.rms_mix_rate,
        protect=rvc.protect, f0_file="", file_index2="",
    )

    # O retorno varia entre versões: pode ser (mensagem, (taxa, áudio))
    # ou o ndarray cru. Quando algo falha DENTRO do RVC, a mensagem traz
    # o traceback e o áudio vem None — por isso o erro é lido dali, e
    # não de uma exceção.
    #
    # O áudio sai como o RVC o produziu, sem pós-processamento. Um
    # filtro passa-baixa aqui parecia "limpar o chiado", mas removia
    # banda alta legítima: o decoder do RVC sintetiza essa faixa a
    # partir das features do HuBERT, e é assim que ele funciona.
    if isinstance(saida, np.ndarray):
        return int(rvc.vc.tgt_sr), saida

    if isinstance(saida, tuple) and len(saida) == 2:
        msg, dados = saida
        if isinstance(dados, np.ndarray):
            return int(rvc.vc.tgt_sr), dados
        if isinstance(dados, tuple) and len(dados) == 2 and dados[1] is not None:
            return int(dados[0]), np.asarray(dados[1])
        raise RuntimeError(str(msg)[-300:])

    raise RuntimeError("retorno inesperado do RVC: %s" % type(saida))


@app.get("/saude")
async def saude():
    vozes = sorted(p.stem for p in MODELOS.glob("*.pth"))
    return {"ok": True, "vozes": vozes, "carregada": _voz_atual,
            "gpu": torch.cuda.is_available()}


@app.post("/converter")
async def endpoint(req: Request):
    voz = req.query_params.get("voz", "")
    corpo = await req.body()
    t0 = time.time()
    try:
        sr, wav = converter(corpo, voz)
    except Exception as e:  # noqa: BLE001
        print("  erro: %s" % str(e)[:200], flush=True)
        return Response(content=str(e)[:300], status_code=500,
                        media_type="text/plain")

    buf = io.BytesIO()
    sf.write(buf, wav, sr, format="WAV", subtype="PCM_16")
    print("  convertido em %.3fs (%s)" % (time.time() - t0, voz), flush=True)
    return Response(content=buf.getvalue(), media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn

    vozes = sorted(p.stem for p in MODELOS.glob("*.pth"))
    print()
    print("=" * 58)
    print("  Serviço RVC  →  http://127.0.0.1:%d" % PORTA)
    print("  vozes: %s" % (", ".join(vozes) or "(nenhuma)"))
    print("=" * 58)
    print()

    # Aquece: a 1a conversão leva ~25s (kernels CUDA + hubert), contra
    # 0,17s das seguintes. Pagar isso agora evita que a primeira frase
    # da conversa trave.
    if vozes:
        try:
            carregar(vozes[0])
            amostra = RAIZ / "logs" / "vozes" / "frase_kokoro.wav"
            if amostra.exists():
                t0 = time.time()
                converter(amostra.read_bytes(), vozes[0])
                print("aquecido em %.1fs" % (time.time() - t0), flush=True)
        except Exception as e:  # noqa: BLE001
            print("aquecimento falhou: %s" % str(e)[:150], flush=True)

    uvicorn.run(app, host="127.0.0.1", port=PORTA, log_level="warning")
