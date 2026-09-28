"""Memória da Luna: o que ela sabe de você, para as DUAS IAs.

A memória é a do Hermes (perfil `assistente`: USER.md = sobre você,
MEMORY.md = anotações de trabalho). A IA especialista lê e escreve nela
sozinha, a cada pedido. Este módulo cobre o resto:

  - `para_rapida()`: o que a IA rápida precisa saber de você (só o USER.md,
    curto) vai junto no pedido dela. Assim as duas conhecem você, mas só
    uma escreve.
  - `revisar()`: uma vez por dia, a IA rápida lê as conversas do dia e
    PROPÕE os fatos que valem guardar (preferências, pessoas, rotinas). Uma
    chamada por dia, não uma por pedido.
  - `sugerir_limpeza()`: aponta entradas repetidas, velhas ou sem utilidade.
  NADA entra nem sai sozinho: propostas e sugestões esperam você na aba
  Aprendizado. Medido: a revisão automática gravou um nome errado (erro do
  Whisper, corrigido depois pela pessoa) e a limpeza quis apagar o nome certo.

A escrita passa pelo próprio Hermes (MemoryStore, com trava de arquivo e
filtro contra texto malicioso), rodando no Python dele.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import config

RAIZ = Path(__file__).resolve().parent.parent
ESTADO = RAIZ / "dados" / "memoria.json"      # última revisão, novidades, sugestões
CONVERSAS = RAIZ / "dados" / "conversas"
_trava = threading.Lock()

ALVOS = {"usuario": "user", "trabalho": "memory"}
ARQS = {"usuario": "USER.md", "trabalho": "MEMORY.md"}
MAX_NOVOS_POR_DIA = 5
MAX_PARA_RAPIDA = 1400       # caracteres: uns 400 tokens por pedido


# ------------------------------------------------------------------ leitura
def _pasta() -> Path:
    import plataforma
    return plataforma.hermes_home() / "profiles" / config.HERMES_PERFIL / "memories"


def _entradas(arq: str) -> list[str]:
    try:
        raw = (_pasta() / arq).read_text(encoding="utf-8").replace("\r\n", "\n")
    except OSError:
        return []
    return [e.strip() for e in raw.split("\n§\n") if e.strip()]


def ler() -> dict:
    return {nome: _entradas(arq) for nome, arq in ARQS.items()}


def para_rapida() -> str:
    """Bloco curto para o pedido da IA rápida ("" se não há nada)."""
    itens, total = [], 0
    for e in _entradas(ARQS["usuario"]):
        e = " ".join(e.split())
        if total + len(e) > MAX_PARA_RAPIDA:
            break
        itens.append("- " + e)
        total += len(e)
    if not itens:
        return ""
    return ("\n\nO que você já sabe sobre a pessoa (use quando ajudar; não repita "
            "isso sem motivo):\n" + "\n".join(itens))


# ------------------------------------------------------------------ estado
def _estado() -> dict:
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _gravar_estado(d: dict) -> None:
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
    tmp = ESTADO.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, ESTADO)


# ------------------------------------------------------------------ escrita (pelo Hermes)
_COD_HERMES = """
import json, sys
from tools.memory_tool import MemoryStore
s = MemoryStore()
s.load_from_disk()
saida = []
for op in json.loads(sys.stdin.read()):
    if op["acao"] == "add":
        r = s.add(op["alvo"], op["texto"])
    else:
        r = s.remove(op["alvo"], op["texto"])
    saida.append({"ok": bool(r.get("success")), "erro": str(r.get("error") or "")[:160]})
print(json.dumps(saida))
"""


def _aplicar(ops: list[dict]) -> list[dict]:
    """[{acao: add|remove, alvo: user|memory, texto}] no MemoryStore do Hermes."""
    import conexoes
    import plataforma
    py = conexoes._hermes_py()
    if not py:
        return [{"ok": False, "erro": "Hermes não encontrado"} for _ in ops]
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               HERMES_HOME=str(plataforma.hermes_home() / "profiles" / config.HERMES_PERFIL))
    try:
        r = subprocess.run([str(py), "-c", _COD_HERMES], input=json.dumps(ops), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60, env=env,
                           **plataforma.sem_janela())
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as e:
        return [{"ok": False, "erro": str(e)[:120]} for _ in ops]


def apagar(nome: str, texto: str) -> dict:
    """Você apagou uma entrada na aba Aprendizado."""
    if nome not in ALVOS or texto not in _entradas(ARQS[nome]):
        return {"ok": False, "erro": "não achei essa lembrança"}
    r = _aplicar([{"acao": "remove", "alvo": ALVOS[nome], "texto": texto}])[0]
    with _trava:
        d = _estado()
        d["novos"] = [n for n in d.get("novos", []) if n.get("texto") != texto]
        d["limpeza"] = [s for s in d.get("limpeza", []) if s.get("texto") != texto]
        _gravar_estado(d)
    return r


# ------------------------------------------------------------------ IA rápida (JSON)
def _perguntar(cli, modelo: str, extra: dict, instrucao: str, conteudo: str) -> dict:
    r = cli.chat.completions.create(
        model=modelo, temperature=0.2, max_tokens=900, extra_body=extra,
        messages=[{"role": "system", "content": instrucao},
                  {"role": "user", "content": conteudo}])
    txt = (r.choices[0].message.content or "").strip()
    ini, fim = txt.find("{"), txt.rfind("}")
    if ini < 0 or fim < ini:
        return {}
    try:
        return json.loads(txt[ini:fim + 1])
    except ValueError:
        return {}


def _falas_desde(desde: float) -> list[str]:
    """Falas suas com a resposta curta, de TODAS as rotas: correções como
    "o outro nome foi erro" podem ter ido à especialista e precisam ser vistas."""
    linhas = []
    for arq in sorted(CONVERSAS.glob("*.json")) if CONVERSAS.exists() else []:
        try:
            msgs = json.loads(arq.read_text(encoding="utf-8")).get("mensagens", [])
        except (OSError, ValueError):
            continue
        for i, m in enumerate(msgs):
            if m.get("quem") != "voce" or m.get("t", 0) <= desde:
                continue
            resp = msgs[i + 1] if i + 1 < len(msgs) and msgs[i + 1].get("quem") == "bot" else {}
            linhas.append((m["t"], "Pessoa: %s\nLuna: %s" % (m["texto"][:300], (resp.get("texto") or "")[:160])))
    linhas.sort()
    return [x for _t, x in linhas][-120:]


INSTR_REVISAO = (
    "Você cuida da memória de longo prazo de uma assistente de voz. Leia as falas abaixo e "
    "extraia SÓ fatos que a PESSOA AFIRMOU com clareza, com as palavras dela. Nunca deduza: "
    "não conclua nada a partir das respostas ou perguntas da Luna (se a Luna perguntou \"é seu "
    "filho?\" e a pessoa respondeu vago, NÃO é fato), nem de resultados de busca, clima ou "
    "pedidos pontuais (pesquisar um filme não quer dizer que gosta do gênero). "
    "Tipos úteis: nome e pessoas próximas, preferências "
    "(gostos, jeito de responder), rotinas e hábitos, projetos em andamento, coisas que ela pediu "
    "para lembrar. NÃO guarde: perguntas pontuais, conhecimento geral, o que a assistente fez, "
    "horários do dia, erros de transcrição, nada sensível (senhas, documentos, saúde, dinheiro). "
    "Se o fato já está na memória atual, mesmo com outras palavras, NÃO repita. "
    "Na dúvida, não guarde: é melhor lembrar pouco do que lembrar errado. "
    "Se a pessoa se corrigiu depois (\"não, é X\", \"foi um erro\"), vale só a correção. "
    "Cada fato numa frase curta em português, em terceira pessoa (\"Prefere...\"). "
    "Máximo %d fatos. Responda SÓ com JSON: {\"fatos\": [\"...\"]} (lista vazia se não houver)."
    % MAX_NOVOS_POR_DIA)

INSTR_LIMPEZA = (
    "Você cuida da memória de longo prazo de uma assistente de voz. Abaixo estão as lembranças "
    "atuais, numeradas. Aponte as que atrapalham: repetidas (diga qual fica), contraditórias "
    "(a mais nova vale), pontuais que já passaram, ou sem utilidade para ajudar a pessoa no "
    "futuro. Seja conservador: lembrança útil fica. "
    "Responda SÓ com JSON: {\"remover\": [{\"n\": <número>, \"motivo\": \"frase curta\"}]}."
)


def _normal(t: str) -> set[str]:
    import unicodedata
    t = unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode()
    return {p for p in re.findall(r"[a-z0-9]+", t) if len(p) > 2}


def _ja_sabe(fato: str) -> bool:
    """Rede de segurança contra repetição (a IA às vezes ignora a instrução):
    parecido no texto, ou todas as palavras do fato já estão numa lembrança."""
    import difflib
    a, pa = fato.lower(), _normal(fato)
    for e in _entradas(ARQS["usuario"]):
        if difflib.SequenceMatcher(None, a, e.lower()).ratio() > 0.72:
            return True
        if pa and pa <= _normal(e):
            return True
    return False


def revisar(cli, modelo: str, extra: dict, forcar: bool = False) -> dict:
    """Revisão do dia. Devolve {"novos": [...], "falas": n} ou {"pulou": motivo}."""
    with _trava:
        d = _estado()
    agora = time.time()
    if not forcar and agora - d.get("revisado", 0) < 20 * 3600:
        return {"pulou": "já revisou hoje"}
    falas = _falas_desde(d.get("revisado", agora - 3 * 86400))
    novos: list[str] = []
    if falas:
        atual = "\n".join("- " + e for e in _entradas(ARQS["usuario"])) or "(vazia)"
        j = _perguntar(cli, modelo, extra, INSTR_REVISAO,
                       "Memória atual:\n%s\n\nFalas:\n%s" % (atual, "\n\n".join(falas)))
        fatos = [" ".join(str(f).split())[:220] for f in (j.get("fatos") or []) if str(f).strip()]
        novos = [f for f in fatos if not _ja_sabe(f)][:MAX_NOVOS_POR_DIA]
    with _trava:
        d = _estado()
        d["revisado"] = agora
        antigas = [p for p in d.get("propostas", []) if p["texto"] not in novos]
        d["propostas"] = ([{"texto": f, "quando": agora} for f in novos] + antigas)[:15]
        _gravar_estado(d)
    return {"novos": novos, "falas": len(falas)}


def aprovar(texto: str) -> dict:
    """Você aprovou uma lembrança proposta: entra na memória (USER.md)."""
    with _trava:
        d = _estado()
        if texto not in {p["texto"] for p in d.get("propostas", [])}:
            return {"ok": False, "erro": "proposta não encontrada"}
    r = _aplicar([{"acao": "add", "alvo": "user", "texto": texto}])[0]
    if r.get("ok"):
        with _trava:
            d = _estado()
            d["propostas"] = [p for p in d.get("propostas", []) if p["texto"] != texto]
            d["novos"] = ([{"texto": texto, "quando": time.time()}] + d.get("novos", []))[:20]
            _gravar_estado(d)
    return r


def descartar(texto: str) -> None:
    """Você recusou a proposta (ou a sugestão de apagar): some da lista."""
    with _trava:
        d = _estado()
        d["propostas"] = [p for p in d.get("propostas", []) if p["texto"] != texto]
        d["limpeza"] = [x for x in d.get("limpeza", []) if x.get("texto") != texto]
        _gravar_estado(d)


def sugerir_limpeza(cli, modelo: str, extra: dict) -> list[dict]:
    """Sugestões de limpeza (não apaga nada). Ficam no estado até você decidir."""
    lista = [("usuario", e) for e in _entradas(ARQS["usuario"])] + \
            [("trabalho", e) for e in _entradas(ARQS["trabalho"])]
    if len(lista) < 2:
        sug: list[dict] = []
    else:
        texto = "\n".join("%d. [%s] %s" % (i + 1, n, " ".join(e.split())[:400]) for i, (n, e) in enumerate(lista))
        j = _perguntar(cli, modelo, extra, INSTR_LIMPEZA, texto)
        sug, vistos = [], set()
        for item in j.get("remover") or []:
            try:
                n = int(item.get("n")) - 1
            except (TypeError, ValueError, AttributeError):
                continue
            if 0 <= n < len(lista) and n not in vistos:
                vistos.add(n)
                sug.append({"nome": lista[n][0], "texto": lista[n][1],
                            "motivo": " ".join(str(item.get("motivo") or "").split())[:160]})
    with _trava:
        d = _estado()
        d["limpeza"] = sug
        d["limpeza_em"] = time.time()
        _gravar_estado(d)
    return sug


# ------------------------------------------------------------------ tela
def resumo() -> dict:
    d = _estado()
    mem = ler()
    novos = {n["texto"] for n in d.get("novos", []) if time.time() - n.get("quando", 0) < 7 * 86400}
    uso = {nome: sum(len(e) for e in ents) + 3 * max(0, len(ents) - 1) for nome, ents in mem.items()}
    limite = {"usuario": 1375, "trabalho": 2200}
    atuais = set(mem["usuario"]) | set(mem["trabalho"])
    rev = d.get("revisado")
    return {
        "itens": [{"nome": nome, "texto": e, "novo": e in novos} for nome, ents in mem.items() for e in ents],
        "uso": {n: round(100 * uso[n] / limite[n]) for n in uso},
        "limpeza": [s for s in d.get("limpeza", []) if s["texto"] in atuais],
        "propostas": [p["texto"] for p in d.get("propostas", [])],
        "revisado": dt.datetime.fromtimestamp(rev).strftime("%d/%m %H:%M") if rev else "",
    }
