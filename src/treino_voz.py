"""Treinar o detector do nome "Luna" com a voz de quem usa.

A Luna guarda falas curtas em dados_luna/voz_real/ (servidor._guardar_amostra).
Daqui sai o botão "Treinar com a minha voz" (aba Aprendizado):

1. treinar(): junta as vozes sintéticas (a base) com as gravações reais,
   mede o detector novo contra o atual nas gravações MAIS NOVAS (que o novo
   nunca viu no treino) e guarda o resultado como CANDIDATO.
2. Se o novo não for melhor, é descartado sozinho: nada muda.
3. aprovar(): só com o clique da pessoa o candidato passa a valer
   (o anterior fica guardado; desfazer() volta).

Nada disso muda código: é só o arquivo do modelo (modelos/oww/luna.pkl),
fora do git. Leva de 1 a 3 minutos no processador.
"""
from __future__ import annotations

import hashlib
import io
import pickle
import re
import shutil
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

import detector_luna as D

REAL = D.RAIZ / "dados_luna" / "voz_real"
CACHE_REAL = D.RAIZ / "dados_luna" / "voz_real_emb.pkl"
PESSOAL = D.MODELOS / "luna.pkl"
CANDIDATO = D.MODELOS / "luna.candidato.pkl"
ANTERIOR = D.MODELOS / "luna.anterior.pkl"
GENERICO = D.RAIZ / "recursos" / "detector" / "luna.pkl"

# Quantas gravações precisa, no mínimo, de cada tipo.
MIN_NOME, MIN_SEM = 40, 40
# Gravações mais novas separadas para a prova (o novo nunca treina com elas).
PROVA = 0.3

# Base sintética (vozes do Edge e do Kokoro) já em embeddings, para quem não
# tem os ~160 MB de áudio de dados_luna/pos e neg. Baixa uma vez, conferida.
BASE_URL = "https://github.com/LucasTabosaGit/luna/releases/download/detector-base-v1/detector-base-v1.npz"
BASE_SHA = "eb3233d314479ffee01ea194cdd87f2394fe717a7318ac512382c7dd0bece42b"
BASE_LOCAL = D.RAIZ / "dados_luna" / "detector-base-v1.npz"

_NOME = re.compile(r"\blun[ao]\b|\boluna\b", re.I)
_lock = threading.Lock()
_estado: dict = {"rodando": False, "etapa": "", "erro": "", "resultado": None}


# ------------------------------------------------------------------ dados
def _gravacoes() -> list[Path]:
    return sorted(REAL.glob("*.wav")) if REAL.exists() else []


def _tem_nome(w: Path) -> bool:
    try:
        txt = w.with_suffix(".txt").read_text(encoding="utf-8")
    except OSError:
        txt = ""
    return bool(_NOME.search(txt)) or w.stem.endswith("_nome")


def _reais() -> list[dict]:
    """Gravações reais com embeddings (em cache: só calcula as novas)."""
    import soundfile as sf
    cache = {}
    if CACHE_REAL.exists():
        try:
            with open(CACHE_REAL, "rb") as f:
                cache = pickle.load(f)
        except Exception:  # noqa: BLE001 - cache ruim: recalcula
            cache = {}
    itens, novo = [], {}
    for w in _gravacoes():
        emb = cache.get(w.name)
        if emb is None:
            a, _ = sf.read(w, dtype="float32")
            emb = D.embeddings(a[:4 * 16000])
        novo[w.name] = emb
        itens.append({"nome": w.stem, "pos": _tem_nome(w), "voz": "real", "i": -1, "emb": emb})
    with open(CACHE_REAL, "wb") as f:
        pickle.dump(novo, f)
    return itens


def _base() -> list[dict]:
    """Vozes sintéticas: os áudios locais (quem gerou) ou o pacote pronto."""
    import treina_detector_luna as T
    if (D.RAIZ / "dados_luna" / "pos").exists():
        return T.carregar()
    if not BASE_LOCAL.exists():
        _estado["etapa"] = "baixando as vozes de base (10 MB, só na primeira vez)..."
        with urllib.request.urlopen(BASE_URL, timeout=120) as r:
            dados = r.read()
        if BASE_SHA and hashlib.sha256(dados).hexdigest() != BASE_SHA:
            raise RuntimeError("o pacote de vozes de base veio diferente do esperado")
        BASE_LOCAL.parent.mkdir(parents=True, exist_ok=True)
        BASE_LOCAL.write_bytes(dados)
    z = np.load(BASE_LOCAL)
    emb, ini = z["emb"].astype(np.float32), z["ini"]
    vozes = [str(v) for v in z["vozes"]]
    return [{"pos": bool(p), "voz": vozes[v], "i": int(i), "emb": emb[a:b]}
            for p, v, i, a, b in zip(z["pos"], z["voz"], z["i"], ini[:-1], ini[1:])]


def exportar_base(destino: Path) -> str:
    """Gera o pacote da base (para o release público). Devolve o SHA-256."""
    import treina_detector_luna as T
    itens = T.carregar()
    vozes = sorted({it["voz"] for it in itens})
    ini = np.cumsum([0] + [len(it["emb"]) for it in itens])
    buf = io.BytesIO()
    np.savez_compressed(
        buf, emb=np.concatenate([it["emb"] for it in itens]).astype(np.float16), ini=ini,
        pos=np.array([it["pos"] for it in itens]), i=np.array([it["i"] for it in itens]),
        voz=np.array([vozes.index(it["voz"]) for it in itens]), vozes=np.array(vozes))
    destino.write_bytes(buf.getvalue())
    return hashlib.sha256(buf.getvalue()).hexdigest()


# ------------------------------------------------------------------ medida
def _conjunto(itens, modelo=None):
    """Janelas rotuladas. Real com nome: a janela que o modelo aponta (na
    1ª rodada, o começo da fala, onde o nome costuma estar)."""
    import treina_detector_luna as T
    X, y = [], []
    for it in itens:
        w = D.janelas(it["emb"])
        if not it["pos"]:
            X.append(w)
            y += [0] * len(w)
        elif it["voz"] != "real" and it["i"] in T.CURTAS:
            X.append(w)
            y += [1] * len(w)
        elif modelo is not None or it["voz"] == "real":
            k = 0 if modelo is None else int(modelo.predict_proba(w)[:, 1].argmax())
            perto = [j for j in range(len(w)) if abs(j - k) <= 1]
            X.append(w[perto])
            y += [1] * len(perto)
    return np.concatenate(X), np.array(y)


def _treinar(itens):
    import treina_detector_luna as T
    m1 = T.treinar(*_conjunto(itens))
    return T.treinar(*_conjunto(itens, m1))


def _notas(modelo, itens) -> np.ndarray:
    return np.array([modelo.predict_proba(D.janelas(it["emb"]))[:, 1].max() for it in itens])


def _placar(notas, rot, limiar) -> dict:
    return {"pegou": int((notas[rot] >= limiar).sum()), "com_nome": int(rot.sum()),
            "falsos": int((notas[~rot] >= limiar).sum()), "sem_nome": int((~rot).sum())}


def _ate(info: dict) -> str:
    """Última gravação que o detector atual já ouviu. Os modelos antigos não
    guardavam isso; as gravações deles eram todas no formato "MMDD-..." de
    setembro, e as novas começam pelo ano ("2026..."): "0999" separa os dois."""
    return info.get("ate") or ("0999" if info.get("com_voz_real") else "")


def _atual() -> tuple[dict, Path]:
    arq = PESSOAL if PESSOAL.exists() else GENERICO
    with open(arq, "rb") as f:
        return pickle.load(f), arq


# ------------------------------------------------------------------ API
def status() -> dict:
    grav = _gravacoes()
    com = sum(_tem_nome(w) for w in grav)
    sem = len(grav) - com
    usado = (_atual()[0].get("info") or {}) if (PESSOAL.exists() or GENERICO.exists()) else {}
    ate = _ate(usado)
    novas = sum(1 for w in grav if w.stem > ate)
    return {
        "com_nome": com, "sem_nome": sem, "min_nome": MIN_NOME, "min_sem": MIN_SEM,
        "pronto": com >= MIN_NOME and sem >= MIN_SEM,
        "modelo": "pessoal" if PESSOAL.exists() else "generico",
        "treinado_em": usado.get("quando", ""), "novas": novas,
        "pode_desfazer": ANTERIOR.exists(),
        "rodando": _estado["rodando"], "etapa": _estado["etapa"], "erro": _estado["erro"],
        "resultado": _estado["resultado"],
    }


def treinar() -> None:
    """Roda em thread (o servidor chama com asyncio.to_thread)."""
    if not _lock.acquire(blocking=False):
        return
    _estado.update(rodando=True, erro="", resultado=None, etapa="lendo as suas gravações...")
    t0 = time.time()
    try:
        real = _reais()
        com = sum(it["pos"] for it in real)
        if com < MIN_NOME or len(real) - com < MIN_SEM:
            raise RuntimeError("ainda poucas gravações")
        info_atual = (_atual()[0].get("info") or {})
        ate = _ate(info_atual)
        # Prova justa: gravações que o modelo atual NUNCA ouviu (mais novas
        # que o treino dele). Sem o bastante, as 30% mais novas.
        nunca = [it for it in real if it["nome"] > ate]
        n_prova = int(len(real) * PROVA)
        if len(nunca) >= 20 and 5 <= sum(i["pos"] for i in nunca) < len(nunca) - 5:
            # as mais novas entre as nunca ouvidas; o resto fica para o treino
            prova, justo = nunca[-n_prova:], True
        else:
            prova, justo = real[-n_prova:], False
        na_prova = {it["nome"] for it in prova}
        treino_r = [it for it in real if it["nome"] not in na_prova]

        _estado["etapa"] = "carregando as vozes de base..."
        base = _base()
        _estado["etapa"] = "treinando (1 a 3 minutos)..."
        novo = _treinar(base + treino_r * 3)            # voz real pesa mais
        rot = np.array([it["pos"] for it in prova])

        atual, arq_atual = _atual()
        n_atual = _notas(atual["modelo"], prova)
        n_novo = _notas(novo, prova)
        # Limiar do novo: o menor sem nenhum disparo falso na prova.
        grade = [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.97, 0.99]
        limiar = next((g for g in grade if (n_novo[~rot] >= g).sum() == 0), 0.99)
        p_atual = _placar(n_atual, rot, atual.get("limiar", D.LIMIAR))
        p_novo = _placar(n_novo, rot, limiar)
        melhor = (p_novo["falsos"] <= p_atual["falsos"] and p_novo["pegou"] >= p_atual["pegou"]
                  and (p_novo["pegou"], -p_novo["falsos"]) != (p_atual["pegou"], -p_atual["falsos"]))
        igual = (p_novo["pegou"], p_novo["falsos"]) == (p_atual["pegou"], p_atual["falsos"])

        res = {"atual": p_atual, "novo": p_novo, "justo": justo, "melhor": melhor, "igual": igual,
               "modelo_atual": "pessoal" if arq_atual == PESSOAL else "generico"}
        if melhor:
            _estado["etapa"] = "treinando a versão final com todas as gravações..."
            final = _treinar(base + real * 3)
            with open(CANDIDATO, "wb") as f:
                pickle.dump({"modelo": final, "limiar": limiar, "info": {
                    "com_voz_real": len(real), "ate": max(it["nome"] for it in real),
                    "quando": time.strftime("%d/%m/%Y %H:%M"), "prova": res}}, f)
        else:
            CANDIDATO.unlink(missing_ok=True)
        res["segundos"] = round(time.time() - t0)
        _estado["resultado"] = res
        print("  [treino-voz] atual %s | novo %s | %s (%ds)" % (
            p_atual, p_novo, "melhor: aguarda aprovação" if melhor else "não melhorou: descartado",
            res["segundos"]), flush=True)
    except Exception as e:  # noqa: BLE001
        _estado["erro"] = str(e)[:200]
        print("  [treino-voz] falhou: %s" % e, flush=True)
    finally:
        _estado.update(rodando=False, etapa="")
        _lock.release()


def _recarregar() -> None:
    D.CLASSIFICADOR = PESSOAL if PESSOAL.exists() else GENERICO
    D._clf = None                         # próxima fala carrega o novo


def aprovar() -> bool:
    if not CANDIDATO.exists():
        return False
    if PESSOAL.exists():
        shutil.copy2(PESSOAL, ANTERIOR)
    else:
        ANTERIOR.unlink(missing_ok=True)
        ANTERIOR.write_bytes(b"")          # vazio = "antes era o genérico"
    CANDIDATO.replace(PESSOAL)
    _estado["resultado"] = None
    _recarregar()
    print("  [treino-voz] detector novo aprovado e em uso", flush=True)
    return True


def descartar() -> None:
    CANDIDATO.unlink(missing_ok=True)
    _estado["resultado"] = None


def desfazer() -> bool:
    if not ANTERIOR.exists():
        return False
    if ANTERIOR.stat().st_size == 0:      # antes era o genérico
        PESSOAL.unlink(missing_ok=True)
        ANTERIOR.unlink()
    else:
        ANTERIOR.replace(PESSOAL)
    _recarregar()
    print("  [treino-voz] voltou para o detector anterior", flush=True)
    return True
