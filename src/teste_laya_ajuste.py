"""Laya ajustado com exemplos: cabeça de decisão treinada por cima do encoder.

    .venv-laya/Scripts/python.exe src/teste_laya_ajuste.py

Mesma técnica da Anthus (Jev-Flywheel): o modelo fica congelado e uma
regressão logística aprende a decisão a partir da representação dele.
Acerto medido de forma honesta:

  1. validação cruzada estratificada (8 dobras) nas 64 frases do
     teste_laya.py: cada frase é julgada por uma cabeça que NÃO a viu;
  2. 24 frases NOVAS, escritas de outro jeito, julgadas pela cabeça
     treinada nas 64.

Também mede o `noul` corrigido (o valor vem no campo "noul").
"""
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parent.parent / "modelos" / "hf"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

from teste_laya import FORMAS, FRASES  # noqa: E402

NOVAS = [
    ("E aí, beleza?", "conversa"),
    ("Qual a altura do Everest?", "conversa"),
    ("Me conta a história do Tiradentes em duas frases", "conversa"),
    ("Qual o sinônimo de rápido?", "conversa"),
    ("O que você faria num dia de chuva?", "conversa"),
    ("Quantas patas tem uma aranha?", "conversa"),
    ("Sugere um nome para um canal de receitas", "conversa"),
    ("Por que o céu é azul?", "conversa"),
    ("Tá, entendi, valeu", "conversa"),
    ("Qual a diferença entre RAM e SSD?", "conversa"),
    ("Me anima, tô meio pra baixo", "conversa"),
    ("Em que ano o homem foi à Lua?", "conversa"),
    ("Dá uma olhada em quanto de RAM o Chrome está usando", "tarefa"),
    ("Joga os PDFs da pasta downloads numa pasta chamada PDFs", "tarefa"),
    ("Vê no Google quanto está o bitcoin", "tarefa"),
    ("Tem algum arquivo de vídeo na área de trabalho?", "tarefa"),
    ("Mata o processo que está travando o sistema", "tarefa"),
    ("Checa se a internet está funcionando", "tarefa"),
    ("Anota num arquivo que preciso pagar a luz amanhã", "tarefa"),
    ("Quanto tempo o computador está ligado?", "tarefa"),
    ("Roda os testes do projeto e me fala se passaram", "tarefa"),
    ("Qual placa de vídeo está instalada aqui?", "tarefa"),
    ("Esvazia a lixeira", "tarefa"),
    ("Abre o último arquivo que eu baixei", "tarefa"),
]


def main() -> int:
    import torch
    from laya import load
    from laya.shortlist import embed_fn_from_agent
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold

    agent = load("convaiinnovations/laya", subfolder="multilingual",
                 device="cuda" if torch.cuda.is_available() else "cpu")
    embed = embed_fn_from_agent(agent)
    embed(["aquecimento"])

    # --- noul corrigido (sem ajuste)
    acertos = 0
    for frase, certo in FRASES:
        r = agent.predict({"pedido": frase}, FORMAS["noul_en"])
        p = float(r["answers"]["destino"]["noul"])
        acertos += ("tarefa" if p >= 0.5 else "conversa") == certo
    print(f"noul (sem ajuste, corrigido): {acertos}/{len(FRASES)} = {acertos / len(FRASES):.0%}")

    # --- cabeça treinada
    textos = [f for f, _ in FRASES]
    y = np.array([1 if c == "tarefa" else 0 for _, c in FRASES])
    X = np.asarray(embed(textos))
    print(f"\nrepresentação: {X.shape[1]} dimensões por frase")

    skf = StratifiedKFold(n_splits=8, shuffle=True, random_state=0)
    pred = np.zeros(len(y), dtype=int)
    prob = np.zeros(len(y))
    for tr, te in skf.split(X, y):
        clf = LogisticRegression(C=1.0, max_iter=2000, class_weight="balanced")
        clf.fit(X[tr], y[tr])
        pred[te] = clf.predict(X[te])
        prob[te] = clf.predict_proba(X[te])[:, 1]
    ok = (pred == y)
    print(f"validação cruzada (64 frases, cada uma julgada sem ter sido vista): "
          f"{ok.sum()}/{len(y)} = {ok.mean():.0%}")
    for i in np.where(~ok)[0]:
        print(f"    ERR p(tarefa)={prob[i]:.2f}  {textos[i]:60s} certo={FRASES[i][1]}")

    clf = LogisticRegression(C=1.0, max_iter=2000, class_weight="balanced").fit(X, y)
    tempos = []
    certos = 0
    erros = []
    for frase, certo in NOVAS:
        t = time.perf_counter()
        v = np.asarray(embed([frase]))
        p = clf.predict_proba(v)[0, 1]
        tempos.append((time.perf_counter() - t) * 1000)
        dado = "tarefa" if p >= 0.5 else "conversa"
        certos += dado == certo
        if dado != certo:
            erros.append((frase, certo, p))
    print(f"\nfrases NOVAS (outro jeito de falar): {certos}/{len(NOVAS)} = {certos / len(NOVAS):.0%}"
          f"   mediana {statistics.median(tempos):.1f} ms por frase")
    for frase, certo, p in erros:
        print(f"    ERR p(tarefa)={p:.2f}  {frase:60s} certo={certo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
