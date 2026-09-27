"""Testa o juiz do modo Automático (src/juiz.py) com o LLM local.

    .venv/Scripts/python.exe src/teste_juiz.py

Destino certo de cada frase:
    rapido  -> Qwen local: papo, pergunta simples
    hermes  -> Hermes/Claude: agir no PC OU raciocínio elaborado

Reaproveita as 88 frases do teste do Laya ("tarefa" -> hermes) e soma
frases de RACIOCÍNIO complexo sem ação no PC, o critério novo.
"""
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from openai import OpenAI  # noqa: E402

import config  # noqa: E402
import juiz  # noqa: E402
from teste_laya import FRASES  # noqa: E402
from teste_laya_ajuste import NOVAS  # noqa: E402

RACIOCINIO = [
    ("Me ajuda a montar um plano de estudos de três meses para aprender Python", "hermes"),
    ("Compara os prós e contras de comprar ou alugar um apartamento", "hermes"),
    ("Escreve um e-mail formal para o meu chefe pedindo férias em dezembro", "hermes"),
    ("Tenho duzentos reais por semana para mercado, monta um cardápio econômico", "hermes"),
    ("Analisa por que um site pode estar lento e o que eu devo verificar primeiro", "hermes"),
    ("Me ajuda a decidir entre uma RTX 5070 e uma 4080 para rodar IA local", "hermes"),
    ("Cria um roteiro de viagem de cinco dias em Portugal com orçamento", "hermes"),
    ("Explica passo a passo como funciona o algoritmo de Dijkstra com um exemplo", "hermes"),
    ("Revisa essa ideia de negócio: vender marmitas fitness pelo WhatsApp", "hermes"),
    ("Monta uma estratégia para aumentar as vendas dos meus grupos de promoção", "hermes"),
    ("Um trem sai às três a oitenta por hora e outro às quatro a cem, quando se encontram?", "hermes"),
    ("Escreve um conto curto de terror com final surpreendente", "hermes"),
]

SIMPLES_EXTRA = [
    ("Qual a capital do Canadá?", "rapido"),
    ("Me explica em uma frase o que é machine learning", "rapido"),
    ("Qual a diferença entre café expresso e coado?", "rapido"),
    ("Fala uma frase bonita de bom dia", "rapido"),
    ("Qual o plural de cidadão?", "rapido"),
    ("Quantos minutos tem um dia?", "rapido"),
]

ESCALAR = [
    ("Pensa melhor", True), ("Manda isso pro Claude", True),
    ("Pergunta pro Hermes", True), ("Ok, usa o Claude", True),
    ("Pensa melhor nisso por favor", True),
    ("Eu penso melhor de manhã", False), ("O Claude é bom?", False),
]


def main() -> int:
    modelo = os.environ.get("LLM_MODELO", config.LLM_MODELO)
    llm = OpenAI(base_url=config.LLM_URL, api_key=config.LLM_API_KEY)
    grupos = {
        "frases do Laya (64)": [(f, "hermes" if c == "tarefa" else "rapido") for f, c in FRASES],
        "frases novas (24)": [(f, "hermes" if c == "tarefa" else "rapido") for f, c in NOVAS],
        "raciocínio sem PC (12)": RACIOCINIO,
        "simples extra (6)": SIMPLES_EXTRA,
    }
    total_ok = total = 0
    tempos = []
    for nome, frases in grupos.items():
        ok, erros = 0, []
        for frase, certo in frases:
            dado, seg = juiz.decidir(llm, modelo, frase, config.LLM_EXTRA)
            tempos.append(seg * 1000)
            ok += dado == certo
            if dado != certo:
                erros.append((frase, certo, dado))
        total_ok += ok
        total += len(frases)
        print(f"{nome:26s} {ok}/{len(frases)} = {ok / len(frases):.0%}")
        for frase, certo, dado in erros:
            print(f"    ERR {frase:70s} certo={certo} deu={dado}")
    print(f"\nTOTAL {total_ok}/{total} = {total_ok / total:.0%}   "
          f"mediana {statistics.median(tempos):.0f} ms  máx {max(tempos):.0f} ms")

    ok_e = sum(juiz.pede_escalar(f) == e for f, e in ESCALAR)
    print(f"\n'pensa melhor' e afins: {ok_e}/{len(ESCALAR)}")
    for f, e in ESCALAR:
        if juiz.pede_escalar(f) != e:
            print(f"    ERR {f!r} esperado={e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
