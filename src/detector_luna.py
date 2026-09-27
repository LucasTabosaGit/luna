"""Detector próprio da palavra "Luna" (wake word), direto no áudio.

Por que: hoje o nome é procurado no TEXTO do Whisper. Se ele transcreve
"Coluna, você consertou a skill?", a Luna não acorda; e cada frase ambiente
passa pelo Whisper inteiro só para ver se tem o nome. Aqui um classificador
pequeno ouve o áudio e diz "falou Luna" em milissegundos.

Como (o jeito do openWakeWord):
1. Modelos prontos do openWakeWord (Google speech_embedding, ONNX, CPU)
   transformam cada 80 ms de áudio num vetor de 96 números que resume
   "que som é esse". Não sabem nada de "Luna"; são genéricos.
2. Um classificador pequeno (treinado aqui, src/treina_detector_luna.py)
   olha uma janela de 16 vetores (~1,3 s) e diz se contém "Luna".
   Treinado com ~1.700 áudios sintéticos de 17 vozes (src/gera_dados_luna.py),
   com negativos de propósito: lua, Luana, lona, uma, coluna...

Uso no servidor: `pontuar(audio)` na fala inteira (depois do VAD) devolve
a maior probabilidade de "Luna" em qualquer ponto da frase.
"""
from __future__ import annotations

import pickle
import threading
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
MODELOS = RAIZ / "modelos" / "oww"
# Classificador: o local (treinado com a voz do dono) ou, sem ele, o GENÉRICO
# que vem no repositório (só vozes sintéticas; serve para qualquer um).
CLASSIFICADOR = MODELOS / "luna.pkl"
if not CLASSIFICADOR.exists():
    CLASSIFICADOR = RAIZ / "recursos" / "detector" / "luna.pkl"

# Os dois modelos de base do openWakeWord NÃO vão no repositório: a licença
# deles (CC BY-NC-SA 4.0) é diferente da do código (MIT). Baixam do release
# oficial na primeira vez, conferidos pelo SHA-256.
_BASE = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
_ONNX = {
    "melspectrogram.onnx": "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
    "embedding_model.onnx": "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f",
}


def baixar_modelos_base() -> bool:
    """Baixa o que faltar dos modelos de base. True se ficaram todos no lugar."""
    import hashlib
    import urllib.request
    MODELOS.mkdir(parents=True, exist_ok=True)
    for nome, sha in _ONNX.items():
        alvo = MODELOS / nome
        if alvo.exists():
            continue
        try:
            with urllib.request.urlopen(_BASE + nome, timeout=60) as r:
                dados = r.read()
        except Exception as e:  # noqa: BLE001
            print(f"  [detector] não baixou {nome}: {e}", flush=True)
            return False
        if hashlib.sha256(dados).hexdigest() != sha:
            print(f"  [detector] {nome} veio diferente do esperado; descartado", flush=True)
            return False
        tmp = alvo.with_suffix(".parcial")
        tmp.write_bytes(dados)
        tmp.replace(alvo)
        print(f"  [detector] {nome} baixado", flush=True)
    return True
JANELA = 16            # vetores de embedding por decisão (~1,28 s)
LIMIAR = 0.5           # ajustado em treina_detector_luna.py (ver luna.pkl)
# O texto do Whisper disse "Luna", mas o áudio quase certamente não tem o
# nome: descarta. Medido (teste_detector_luna.py): palavras parecidas sem o
# nome deram 0,000; o nome de verdade, no pior caso, 0,225.
VETO = 0.02
# Trecho curtinho que o Whisper não reconheceu como o nome ("Tchau, tchau."
# no lugar de "Luna."): acorda só com confiança alta.
CURTO = 0.6
# Frase inteira sem "Luna" no texto: só conta com confiança muito alta
# (validação: 1 disparo falso em 384 frases no limiar 0,95).
RESGATE = 0.95

_lock = threading.Lock()
_feat = None
_clf = None


def _carregar():
    global _feat, _clf
    with _lock:
        if _feat is None:
            from openwakeword.utils import AudioFeatures
            _feat = AudioFeatures(
                melspec_model_path=str(MODELOS / "melspectrogram.onnx"),
                embedding_model_path=str(MODELOS / "embedding_model.onnx"),
                inference_framework="onnx")
        if _clf is None and CLASSIFICADOR.exists():
            with open(CLASSIFICADOR, "rb") as f:
                _clf = pickle.load(f)
    return _feat, _clf


def disponivel() -> bool:
    if not CLASSIFICADOR.exists():
        return False
    if not all((MODELOS / n).exists() for n in _ONNX):
        baixar_modelos_base()
    return all((MODELOS / n).exists() for n in _ONNX)


def embeddings(audio: np.ndarray) -> np.ndarray:
    """Áudio float32 16 kHz -> (n, 96). Acolchoa com silêncio nas bordas
    para o nome no começo/fim da frase caber inteiro numa janela."""
    feat, _ = _carregar()
    pad = np.zeros(8000, dtype=np.float32)
    a = np.concatenate([pad, audio.astype(np.float32), pad])
    # openWakeWord quebra com certos tamanhos (284 vs 285 quadros): múltiplo de 1280.
    a = np.concatenate([a, np.zeros((-len(a)) % 1280, dtype=np.float32)])
    pcm = np.clip(a * 32767, -32768, 32767).astype(np.int16)
    return feat.embed_clips(pcm[None, :], batch_size=1)[0]


def janelas(emb: np.ndarray, passo: int = 1) -> np.ndarray:
    """(n, 96) -> (k, JANELA*96), janelas deslizantes."""
    if len(emb) < JANELA:
        emb = np.concatenate([np.zeros((JANELA - len(emb), emb.shape[1]), emb.dtype), emb])
    return np.stack([emb[i:i + JANELA].reshape(-1)
                     for i in range(0, len(emb) - JANELA + 1, passo)])


def pontuar(audio: np.ndarray) -> float:
    """Maior probabilidade de "Luna" em qualquer ponto do áudio (0 a 1)."""
    _, clf = _carregar()
    if clf is None:
        return 0.0
    x = janelas(embeddings(audio))
    return float(clf["modelo"].predict_proba(x)[:, 1].max())


def chamou(audio: np.ndarray) -> tuple[bool, float]:
    _, clf = _carregar()
    p = pontuar(audio)
    return p >= (clf or {}).get("limiar", LIMIAR), p
