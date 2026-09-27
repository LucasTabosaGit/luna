"""Atalhos de skill: frase de voz -> script de uma skill, sem passar pelo Claude.

Uma skill que já roda como UM script (ver skill `skills-rapidas`) vira comando
pronto: o padrão casa, o script roda direto e a Luna fala o resultado.
Medido com a Netflix: Claude + skill ~41 s; atalho ~4-8 s.

Os atalhos ficam em `atalhos.json` na raiz do projeto:

    {"nome": "netflix",
     "padroes": ["^(abre|abra)( a)? netflix e (pesquisa|procura) (?P<q>.+)$", ...],
     "limpar": ["^(um|uma) ", "^filmes? de "],          # opcional, aplicado em q
     "comando": ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", "C:/.../netflix.ps1", "-Busca", "{q}"],
     "teto_s": 40,
     "fala_ok": "Busquei {q} na Netflix.",
     "fala_erro": "Não consegui mexer na Netflix: {erro}"}

Os padrões casam com o texto JÁ normalizado (comandos.normalizar: minúsculas,
sem acento, sem pontuação, sem "por favor"/"pra mim" nas pontas).

Saída do script (uma linha cada, todas opcionais):
    FALA: <frase pronta para a Luna dizer>        (tem prioridade)
    RESULTADOS: a | b | c                          (vira "Apareceram a, b e c.")
    ERRO: <motivo>                                 (ou código de saída != 0)

TRAVA: o arquivo só vale se estiver COMMITADO (igual aos módulos do servidor).
Atalho novo entra por src/propostas.py, com teste, e o usuário aprova.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARQ = RAIZ / "atalhos.json"

_cache: dict = {"mtime": None, "itens": []}
_trava = threading.Lock()


def _commitado() -> bool:
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--", ARQ.name], cwd=RAIZ,
                           capture_output=True, text=True, timeout=5,
                           creationflags=0x08000000)
        return r.returncode == 0 and not r.stdout.strip()
    except Exception:  # noqa: BLE001 - sem git: não arrisca
        return False


def carregar() -> list[dict]:
    """Atalhos válidos (compilados). Relê quando o arquivo muda E está commitado."""
    with _trava:
        try:
            mt = os.path.getmtime(ARQ)
        except OSError:
            return []
        if mt == _cache["mtime"]:
            return _cache["itens"]
        if _cache["mtime"] is not None and not _commitado():
            print("  [trava] atalhos.json mudou sem aprovação: ignorado.", flush=True)
            _cache["mtime"] = mt
            return _cache["itens"]
        itens = []
        try:
            for a in json.loads(ARQ.read_text(encoding="utf-8")):
                a = dict(a)
                a["_re"] = [re.compile(p) for p in a["padroes"]]
                a["_limpar"] = [re.compile(p) for p in a.get("limpar", [])]
                itens.append(a)
        except Exception as e:  # noqa: BLE001 - arquivo quebrado: mantém o anterior
            print("  [atalhos] atalhos.json com erro, mantendo o anterior: %s" % e, flush=True)
            _cache["mtime"] = mt
            return _cache["itens"]
        _cache.update(mtime=mt, itens=itens)
        return itens


def _com_acento(q: str, original: str) -> str:
    """Devolve o trecho `q` (sem acento) como foi dito ("comedia" -> "comédia")."""
    import unicodedata
    if not original:
        return q
    plano = "".join(
        (unicodedata.normalize("NFKD", c)[0] if unicodedata.normalize("NFKD", c) else c)
        for c in original.lower())
    i = plano.find(q)
    return original[i:i + len(q)].lower() if i >= 0 else q


def casar(t: str, original: str = "") -> tuple[dict, dict] | None:
    """(atalho, grupos) para o texto normalizado, ou None."""
    try:
        from aprendizado import atalhos_desligados
        off = atalhos_desligados()
    except Exception:  # noqa: BLE001
        off = set()
    for a in carregar():
        if a["nome"] in off:
            continue
        for rx in a["_re"]:
            m = rx.match(t)
            if m:
                g = {k: (v or "").strip() for k, v in m.groupdict().items()}
                if "q" in g:
                    q = g["q"]
                    for _ in range(2):
                        for lp in a["_limpar"]:
                            q = lp.sub("", q).strip()
                    g["q"] = _com_acento(q, original)
                    if not q:
                        continue
                return a, g
    return None


def _juntar(nomes: list[str]) -> str:
    nomes = [n for n in nomes if n][:3]
    if len(nomes) <= 1:
        return "".join(nomes)
    return ", ".join(nomes[:-1]) + " e " + nomes[-1]


def rodar(a: dict, g: dict) -> tuple[bool, str]:
    """Roda o script do atalho. -> (deu certo?, fala)."""
    cmd = [c.format(**g) for c in a["comando"]]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=a.get("teto_s", 40),
                           creationflags=0x08000000)
        # PowerShell 5.1 escreve na página de código do console (acentos).
        saida = r.stdout.decode("utf-8", errors="strict") if r.stdout else ""
    except UnicodeDecodeError:
        saida = r.stdout.decode("cp850", errors="replace")
    except subprocess.TimeoutExpired:
        return False, a.get("fala_erro", "Não deu certo: {erro}").format(
            erro="demorou demais", **g)
    except Exception as e:  # noqa: BLE001
        return False, a.get("fala_erro", "Não deu certo: {erro}").format(erro=str(e)[:80], **g)
    linhas = {}
    for ln in saida.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            linhas.setdefault(k.strip().upper(), v.strip())
    if r.returncode != 0 or "ERRO" in linhas:
        erro = linhas.get("ERRO") or ("código %d" % r.returncode)
        print("  [atalho] %s falhou: %s" % (a["nome"], erro[:120]), flush=True)
        return False, a.get("fala_erro", "Não deu certo: {erro}").format(erro=erro, **g)
    if linhas.get("FALA"):
        return True, linhas["FALA"]
    fala = a.get("fala_ok", "Pronto.").format(**g)
    if linhas.get("RESULTADOS"):
        fala += " Apareceram " + _juntar([x.strip() for x in linhas["RESULTADOS"].split("|")]) + "."
    return True, fala
