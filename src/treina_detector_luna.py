"""Treina o detector de "Luna" (src/detector_luna.py) e mede.

    .venv/Scripts/python.exe src/treina_detector_luna.py

Dados: dados_luna/{pos,neg}/*.wav (src/gera_dados_luna.py).
Validação SEPARADA POR VOZ: as vozes de teste nunca aparecem no treino,
para medir como ele se sai com uma voz que nunca ouviu (a sua).

Rótulos por janela (~1,3 s) em 2 rodadas:
1. Frases curtas ("Luna.", "Oi Luna.") - toda janela tem o nome: positivas.
   Todas as janelas das frases negativas: negativas.
2. Frases longas ("Luna, que horas são?"): o modelo da rodada 1 aponta a
   janela com o nome (positiva); as janelas longe dela (o "que horas são")
   viram negativas difíceis. Treina de novo.
Limiar: o menor que não dispara em NENHUMA frase negativa da validação.
"""
from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import detector_luna as D  # noqa: E402
from gera_dados_luna import NEG, POS  # noqa: E402

DADOS = D.RAIZ / "dados_luna"
VAL_VOZES = ("ThalitaM", "Duarte", "EmmaM", "kpm_alex")   # nunca no treino
CURTAS = {i for i, f in enumerate(POS) if len(f.split()) <= 2}
# "Coluna" fica fora dos negativos: é justamente como o Whisper ouve um
# "Ô Luna" falado rápido - treinar para rejeitar brigaria com o objetivo.
IGNORA_NEG = {i for i, f in enumerate(NEG) if "coluna" in f.lower()}


def _voz(p: Path) -> str:
    return p.stem.rsplit("_", 2)[0]


def _idx(p: Path) -> int:
    return int(p.stem.rsplit("_", 1)[1])


def carregar():
    import pickle as _pk
    cache = DADOS / "emb.pkl"      # embeddings demoram ~2 min: reaproveita
    if cache.exists() and cache.stat().st_mtime > max(
            p.stat().st_mtime for p in DADOS.glob("*/*.wav")):
        with open(cache, "rb") as f:
            return _pk.load(f)
    t0 = time.time()
    itens = []
    for classe in ("pos", "neg"):
        for p in sorted((DADOS / classe).glob("*.wav")):
            if classe == "neg" and _idx(p) in IGNORA_NEG:
                continue
            a, sr = sf.read(p, dtype="float32")
            assert sr == 16000
            itens.append({"p": p, "pos": classe == "pos", "voz": _voz(p), "i": _idx(p),
                          "emb": D.embeddings(a)})
    print(f"{len(itens)} áudios em embeddings ({time.time() - t0:.0f}s)", flush=True)
    with open(cache, "wb") as f:
        _pk.dump(itens, f)
    return itens


def treinar(X, y):
    """Rede pequena (128-64) com regularização forte. Medido na validação
    por voz: regressão logística decorava as vozes (55 disparos falsos em
    384 frases); esta pega 96% com 1 falso no limiar 0,95."""
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    m = make_pipeline(StandardScaler(), MLPClassifier(
        (128, 64), alpha=0.1, max_iter=300, early_stopping=True, random_state=0))
    m.fit(X, y)
    return m


def conjunto(itens, modelo=None):
    X, y = [], []
    for it in itens:
        w = D.janelas(it["emb"])
        if not it["pos"]:
            X.append(w)
            y += [0] * len(w)
        elif it["i"] in CURTAS:
            X.append(w)
            y += [1] * len(w)
        elif modelo is not None:
            pr = modelo.predict_proba(w)[:, 1]
            k = int(pr.argmax())
            perto = [j for j in range(len(w)) if abs(j - k) <= 1]
            longe = [j for j in range(len(w)) if abs(j - k) >= D.JANELA]
            X.append(w[perto])
            y += [1] * len(perto)
            if longe:
                X.append(w[longe])
                y += [0] * len(longe)
    return np.concatenate(X), np.array(y)


def pontua(modelo, itens):
    return np.array([modelo.predict_proba(D.janelas(it["emb"]))[:, 1].max() for it in itens])


def main():
    itens = carregar()
    tr = [it for it in itens if it["voz"] not in VAL_VOZES]
    va = [it for it in itens if it["voz"] in VAL_VOZES]
    print(f"treino {len(tr)} ({sum(i['pos'] for i in tr)} pos) | "
          f"validação {len(va)} ({sum(i['pos'] for i in va)} pos), vozes {VAL_VOZES}")

    m1 = treinar(*conjunto(tr))
    X, y = conjunto(tr, m1)
    m2 = treinar(X, y)
    print(f"rodada 2: {len(y)} janelas ({int(y.sum())} positivas)")

    s = pontua(m2, va)
    rot = np.array([it["pos"] for it in va])
    # Menor limiar com no máximo 0,5% de disparo falso nas frases sem o nome.
    grade = [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.93, 0.95, 0.97, 0.98, 0.99]
    tolera = max(1, int(0.005 * (~rot).sum()))
    limiar = next((g for g in grade if (s[~rot] >= g).sum() <= tolera), 0.99)
    rec = (s[rot] >= limiar).mean()
    fp = (s[~rot] >= limiar).sum()
    print(f"\nVALIDAÇÃO (vozes nunca vistas): limiar {limiar:.2f}")
    print(f"  pegou 'Luna' em {int((s[rot] >= limiar).sum())}/{rot.sum()} frases ({100 * rec:.0f}%)")
    print(f"  disparos falsos: {fp}/{(~rot).sum()} frases sem o nome")
    for lim in (0.5, 0.7, 0.9):
        print(f"  (com limiar {lim}: pega {100 * (s[rot] >= lim).mean():.0f}%, "
              f"falsos {(s[~rot] >= lim).sum()})")
    perdidas = [(POS[it["i"]], it["voz"], round(float(x), 2))
                for it, x in zip(va, s) if it["pos"] and x < limiar]
    print("  perdidas:", perdidas[:12])
    quase = sorted([(round(float(x), 2), NEG[it["i"]], it["voz"])
                    for it, x in zip(va, s) if not it["pos"]], reverse=True)[:6]
    print("  negativas mais perto de disparar:", quase)

    # Modelo final: treina com TODAS as vozes, mantém o limiar medido.
    mf1 = treinar(*conjunto(itens))
    mf = treinar(*conjunto(itens, mf1))
    D.MODELOS.mkdir(parents=True, exist_ok=True)
    with open(D.CLASSIFICADOR, "wb") as f:
        pickle.dump({"modelo": mf, "limiar": limiar,
                     "info": {"recall_val": float(rec), "falsos_val": int(fp),
                              "val_vozes": VAL_VOZES, "n": len(itens)}}, f)
    print(f"\nsalvo em {D.CLASSIFICADOR}")


if __name__ == "__main__":
    main()
