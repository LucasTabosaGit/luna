"""O que a Luna já aprendeu, para a aba "Aprendizado" da tela.

Três fontes, todas só leitura:
  - skills que o Claude criou/aprendeu no perfil `assistente` do Hermes
    (`.usage.json`: created_by agent/learn, uso, última vez);
  - atalhos prontos (`atalhos.json`: skill -> script direto, ~4 s);
  - pedidos que se repetem no Claude (`logs/para_claude.jsonl`), candidatos
    a virar atalho.

Desligar um atalho NÃO mexe no atalhos.json (a trava exige commit + aprovação):
fica em `dados/atalhos_off.json`, uma preferência da tela. atalhos.py consulta.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SKILLS = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "profiles" / "assistente" / "skills"
OFF = RAIZ / "dados" / "atalhos_off.json"
REG_CLAUDE = RAIZ / "logs" / "para_claude.jsonl"


def atalhos_desligados() -> set[str]:
    try:
        return set(json.loads(OFF.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def ligar_atalho(nome: str, ligado: bool) -> None:
    off = atalhos_desligados()
    (off.discard if ligado else off.add)(nome)
    OFF.parent.mkdir(parents=True, exist_ok=True)
    OFF.write_text(json.dumps(sorted(off)), encoding="utf-8")


def _descricao(pasta: Path) -> str:
    try:
        txt = (pasta / "SKILL.md").read_text(encoding="utf-8")[:3000]
    except OSError:
        return ""
    m = re.search(r"^description:\s*[\"']?(.+?)[\"']?\s*$", txt, re.M)
    return m.group(1).strip()[:160] if m else ""


def skills() -> list[dict]:
    try:
        uso = json.loads((SKILLS / ".usage.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    pastas = {p.parent.name: p.parent for p in SKILLS.rglob("SKILL.md")}
    itens = []
    for nome, u in uso.items():
        if u.get("created_by") not in ("agent", "learn") or u.get("archived_at"):
            continue
        itens.append({"nome": nome, "descricao": _descricao(pastas[nome]) if nome in pastas else "",
                      "usos": u.get("use_count") or 0, "ultimo": u.get("last_used_at"),
                      "criada": u.get("created_at"), "origem": u.get("created_by")})
    itens.sort(key=lambda s: s.get("ultimo") or "", reverse=True)
    return itens


def atalhos() -> list[dict]:
    try:
        lista = json.loads((RAIZ / "atalhos.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    off = atalhos_desligados()
    return [{"nome": a["nome"], "skill": a.get("skill", ""),
             "exemplo": a.get("exemplo") or _exemplo(a), "ligado": a["nome"] not in off}
            for a in lista]


def _exemplo(a: dict) -> str:
    """Frase de exemplo do atalho: campo "exemplo" do atalhos.json, se houver."""
    fala = a.get("fala_ok", "")
    return fala.replace("{q}", "…") if fala else ""


def candidatos(minimo: int = 2, maximo: int = 8) -> list[dict]:
    """Pedidos que foram ao Claude mais de uma vez e ainda não são atalho."""
    if not REG_CLAUDE.exists():
        return []
    try:
        import comandos
    except Exception:  # noqa: BLE001
        return []
    grupos: Counter = Counter()
    exemplo: dict = {}
    for ln in REG_CLAUDE.read_text(encoding="utf-8").splitlines()[-500:]:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        chave = comandos.normalizar(r.get("texto", ""))
        if len(chave) < 6:
            continue
        grupos[chave] += 1
        exemplo.setdefault(chave, r["texto"])
    return [{"texto": exemplo[k], "vezes": n} for k, n in grupos.most_common(maximo) if n >= minimo]


def resumo() -> dict:
    return {"skills": skills(), "atalhos": atalhos(), "candidatos": candidatos()}
