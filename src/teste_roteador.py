"""Roteador (src/roteador.py): cada pedido vai ao lugar certo, numa decisão só.

    .venv/Scripts/python.exe src/teste_roteador.py              # completo (203)
    .venv/Scripts/python.exe src/teste_roteador.py --rapido     # amostra (~40 frases)

Junta as baterias antigas (ações + juiz) e os casos que antes precisavam de
regex própria (composto, rotina, continuação, aprender). Não executa nada.

Métrica GRAVE: pedido que não é ação e virou ação (executaria algo errado).
"""
import os
import re
import statistics
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
from openai import OpenAI  # noqa: E402

import config  # noqa: E402
import roteador  # noqa: E402
from teste_acoes import NEGATIVOS, POSITIVOS  # noqa: E402
from teste_juiz import RACIOCINIO, SIMPLES_EXTRA  # noqa: E402
from teste_laya import FRASES  # noqa: E402
from teste_laya_ajuste import NOVAS  # noqa: E402

# (frase, contexto, destinos aceitos)
ESPECIAIS = [
    ("Abre a Netflix e pesquisa um filme de terror", "", {"claude"}),
    ("Abre o Chrome e entra no meu Gmail", "", {"claude"}),
    ("Pesquisa um filme de comédia na Netflix pra mim", "", {"claude"}),
    ("O que eu tenho pra hoje?", "", {"claude"}),
    ("Cria uma tarefa de pagar a conta de luz", "", {"claude"}),
    ("Anota no meu diário que hoje foi pesado", "", {"claude"}),
    ("Salva isso como skill pra ficar mais rápido", "", {"claude"}),
    ("Você consegue aprender a fazer isso sozinha?", "", {"claude"}),
    ("verifique o que são e gere tudo",
     "última resposta (de Claude): Preciso saber o endpoint e a chave da API do app de tarefas.",
     {"claude"}),
    ("sim, pode fazer",
     "última resposta (de Claude): Quer que eu organize os arquivos da área de trabalho em pastas?",
     {"claude"}),
    ("Me conta outra",
     "última resposta (de a Luna): Por que o livro de matemática ficou triste? Tinha muitos problemas.",
     {"conversa"}),
    ("E em Curitiba?",
     "última resposta (de a Luna): Em São Paulo está fazendo 22 graus e nublado.",
     {"conversa", "acao"}),
    # Registro real: iam ao Hermes sem usar ferramenta nenhuma.
    ("Vamos ver o filme amanhã à noite.", "", {"conversa"}),
    ("E meu nome é Marcos, o Rafael foi um erro.", "", {"conversa"}),
    ("Dá bom dia pra minha avó", "", {"conversa"}),
    ("Dá boa tarde para meu amigo Bruno", "", {"conversa"}),
    ("gosto mais de filme de terror do que de comédia", "", {"conversa"}),
    ("lembra que eu prefiro respostas curtas", "", {"claude"}),
    ("Eu já tomei as vitaminas", "", {"claude"}),
]


def _n(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s).lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def casos():
    for f, acao, args in POSITIVOS:
        yield f, "", {"acao"}, ("acao", acao, args)
    for f in NEGATIVOS:
        yield f, "", {"conversa", "claude"}, None
    for f, c in list(FRASES) + list(NOVAS):
        # "tarefa" antiga = agir no PC: ação da lista também serve.
        yield f, "", ({"claude", "acao"} if c == "tarefa" else {"conversa"}), None
    for f, c in RACIOCINIO:
        yield f, "", {"claude"}, None
    for f, c in SIMPLES_EXTRA:
        yield f, "", {"conversa"}, None
    for f, ctx, ok in ESPECIAIS:
        yield f, ctx, ok, None


def main() -> int:
    llm = OpenAI(base_url=config.LLM_URL, api_key=config.LLM_API_KEY, timeout=30)
    modelo, extra = config.LLM_MODELO, config.LLM_EXTRA
    todos = list(casos())
    if "--rapido" in sys.argv:
        todos = todos[::5] + [c for c in todos if c[0] in {e[0] for e in ESPECIAIS}]
    ok = graves = 0
    tempos = []
    for frase, ctx, aceitos, exato in todos:
        d = roteador.decidir(llm, modelo, frase, ctx, extra)
        tempos.append(d["t"] * 1000)
        certo = d["destino"] in aceitos
        if certo and exato:
            _, acao, args = exato
            certo = d["acao"] == acao and all(
                _n(v) in _n(d["args"].get(k, "")) for k, v in args.items())
        grave = d["destino"] == "acao" and "acao" not in aceitos
        graves += grave
        ok += certo
        if not certo:
            print(f"{'GRAVE' if grave else 'ERR  '} {frase[:64]:64s} esperado={sorted(aceitos)} "
                  f"deu={d['destino']} {d['acao']} {d['args']}")
    print(f"negativos executados (grave): {graves}/{len(todos)}")
    print(f"\nmodelo {modelo}: {ok}/{len(todos)} corretos, {graves} graves, "
          f"mediana {statistics.median(tempos):.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
