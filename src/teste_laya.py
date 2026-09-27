"""Testa o Laya (modelo de decisão) como juiz Rápido x Hermes, em pt-BR.

    .venv-laya/Scripts/python.exe src/teste_laya.py

Cada frase tem o destino CERTO:
    conversa  -> modelo local rápido (papo, pergunta de conhecimento)
    tarefa    -> Hermes (precisa agir no PC, ler arquivo, web, várias etapas)

Mede acerto, confusão e tempo por frase na GPU. Testa três formas de
perguntar (instruções/critérios em pt e em en, e um `noul`), porque o
modelo é sensível ao texto das opções.
"""
import json
import statistics
import sys
import time
from pathlib import Path

import os
os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parent.parent / "modelos" / "hf"))

FRASES = [
    # --- conversa (modelo rápido)
    ("Oi, tudo bem?", "conversa"),
    ("Bom dia! Dormiu bem?", "conversa"),
    ("Me conta uma piada", "conversa"),
    ("Qual a capital da Austrália?", "conversa"),
    ("Quem pintou a Mona Lisa?", "conversa"),
    ("O que é fotossíntese?", "conversa"),
    ("Me explica rapidinho o que é inflação", "conversa"),
    ("Quanto tempo leva pra cozinhar um ovo?", "conversa"),
    ("Qual a diferença entre vírus e bactéria?", "conversa"),
    ("Me dá uma ideia de presente para minha mãe", "conversa"),
    ("Traduz 'obrigado' para japonês", "conversa"),
    ("Como se fala cachorro em inglês?", "conversa"),
    ("Você gosta de música?", "conversa"),
    ("Tô cansado hoje", "conversa"),
    ("Me recomenda um filme de ficção científica", "conversa"),
    ("O que você acha de café?", "conversa"),
    ("Qual o maior planeta do sistema solar?", "conversa"),
    ("Me ajuda a pensar num nome pra um cachorro", "conversa"),
    ("Qual a fórmula da área do círculo?", "conversa"),
    ("Obrigado, valeu!", "conversa"),
    ("Quantos dias tem um ano bissexto?", "conversa"),
    ("Explica o que é uma API como se eu tivesse dez anos", "conversa"),
    ("Qual é a melhor forma de aprender inglês?", "conversa"),
    ("Tchau, até amanhã", "conversa"),
    ("Escreve uma frase motivacional curta", "conversa"),
    ("Quem ganhou a copa de 2002?", "conversa"),
    ("O que significa a palavra efêmero?", "conversa"),
    ("Me fala uma curiosidade sobre polvos", "conversa"),
    ("Você é uma inteligência artificial?", "conversa"),
    ("Como faço um bolo de cenoura?", "conversa"),
    ("Quanto é a raiz quadrada de dois?", "conversa"),
    ("Me dá três dicas pra dormir melhor", "conversa"),
    # --- tarefa (Hermes)
    ("Quantos arquivos py tem na pasta src do projeto?", "tarefa"),
    ("Abre o arquivo de log e me diz o último erro", "tarefa"),
    ("Cria uma pasta chamada relatórios na área de trabalho", "tarefa"),
    ("Quanto espaço livre tem no disco C?", "tarefa"),
    ("Quais programas estão usando mais memória agora?", "tarefa"),
    ("Pesquisa na internet o preço do dólar hoje", "tarefa"),
    ("Vê se o servidor na porta 8777 está rodando", "tarefa"),
    ("Renomeia as fotos da pasta downloads com a data", "tarefa"),
    ("Lê o README do projeto e me resume", "tarefa"),
    ("Fecha o Chrome", "tarefa"),
    ("Desliga o computador daqui a uma hora", "tarefa"),
    ("Apaga os arquivos temporários do sistema", "tarefa"),
    ("Manda uma mensagem no Telegram falando que já chego", "tarefa"),
    ("Qual a versão do Python instalada aqui?", "tarefa"),
    ("Baixa o vídeo desse link do YouTube", "tarefa"),
    ("Procura no meu computador a planilha de vendas de setembro", "tarefa"),
    ("Instala o pacote requests no ambiente virtual", "tarefa"),
    ("Verifica se tem atualização do Windows", "tarefa"),
    ("Quais são os últimos e-mails que chegaram?", "tarefa"),
    ("Compacta a pasta do projeto num zip", "tarefa"),
    ("Mostra a temperatura da placa de vídeo", "tarefa"),
    ("Reinicia o servidor de voz", "tarefa"),
    ("Pesquisa as notícias de hoje sobre inteligência artificial e me resume", "tarefa"),
    ("Cria um arquivo de texto com a lista de compras: arroz, feijão e café", "tarefa"),
    ("Descobre qual processo está usando a porta 1234", "tarefa"),
    ("Faz um backup da pasta documentos no disco L", "tarefa"),
    ("Qual o IP do meu computador?", "tarefa"),
    ("Agenda um lembrete no calendário para amanhã às nove", "tarefa"),
    ("Converte esse vídeo em mp3", "tarefa"),
    ("Conta quantas linhas tem o servidor.py", "tarefa"),
    ("Abre o site do meu projeto e vê se está no ar", "tarefa"),
    ("Liste os arquivos maiores que um giga no disco C", "tarefa"),
]

FORMAS = {
    "choice_pt": {"destino": {
        "type": "choice",
        "instructions": "O usuário falou isto para um assistente de voz no computador. "
                        "Responder só com conhecimento geral, ou é preciso agir no computador "
                        "(arquivos, programas, sistema, internet)?",
        "criteria": {
            "conversa": "conversa, opinião, piada ou pergunta de conhecimento geral; "
                        "responde sem mexer no computador",
            "tarefa": "precisa executar algo no computador: arquivos, pastas, programas, "
                      "sistema, rede, internet, e-mail ou mensagens",
        }}},
    "choice_en": {"destino": {
        "type": "choice",
        "instructions": "A user said this to a desktop voice assistant. Can it be answered "
                        "from general knowledge, or must the assistant act on the computer?",
        "criteria": {
            "conversa": "chit-chat, opinion, joke or general-knowledge question; "
                        "no computer action needed",
            "tarefa": "requires acting on the computer: files, folders, apps, system, "
                      "network, web search, email or messages",
        }}},
    "noul_en": {"destino": {
        "type": "noul",
        "instructions": "Does fulfilling this request require running tools or actions on the "
                        "user's computer or the internet (files, apps, system, web)?",
    }},
}


def rotulo(forma: str, resp: dict) -> tuple[str, float]:
    a = resp["answers"]["destino"]
    if forma.startswith("noul"):
        p = float(a.get("probability", a.get("p_true", a.get("value", 0.0))))
        return ("tarefa" if p >= 0.5 else "conversa"), p
    return a["choice"], float(a.get("confidence", 0.0))


def main() -> int:
    import torch
    from laya import Router

    modelo = sys.argv[1] if len(sys.argv) > 1 else "multilingual"
    t0 = time.time()
    router = Router(default=modelo, device="cuda" if torch.cuda.is_available() else "cpu")
    router.preload([modelo])
    print(f"checkpoint {modelo} carregado em {time.time() - t0:.1f}s  "
          f"(VRAM {torch.cuda.memory_allocated() / 2**30:.2f} GB)")

    # Uma chamada de aquecimento (CUDA compila kernels na primeira).
    router.predict({"pedido": "oi"}, FORMAS["choice_pt"], model=modelo)
    amostra = router.predict({"pedido": FRASES[0][0]}, FORMAS["noul_en"], model=modelo)
    print("formato noul:", json.dumps(amostra["answers"]["destino"], ensure_ascii=False)[:200])

    resumo = {}
    for forma, perguntas in FORMAS.items():
        acertos, tempos, erros = 0, [], []
        conf_ok, conf_err = [], []
        for frase, certo in FRASES:
            t = time.perf_counter()
            r = router.predict({"pedido": frase}, perguntas, model=modelo)
            tempos.append((time.perf_counter() - t) * 1000)
            dado, conf = rotulo(forma, r)
            if dado == certo:
                acertos += 1
                conf_ok.append(conf)
            else:
                erros.append((frase, certo, dado, conf))
                conf_err.append(conf)
        n = len(FRASES)
        resumo[forma] = acertos / n
        print(f"\n=== {forma}: {acertos}/{n} = {acertos / n:.0%}   "
              f"mediana {statistics.median(tempos):.1f} ms  p95 {sorted(tempos)[int(n * .95)]:.1f} ms")
        if conf_ok and conf_err:
            print(f"    confiança média: acertos {statistics.mean(conf_ok):.2f}  "
                  f"erros {statistics.mean(conf_err):.2f}")
        vies = {}
        for _f, certo, dado, _c in erros:
            vies[f"{certo}->{dado}"] = vies.get(f"{certo}->{dado}", 0) + 1
        print("    erros por tipo:", vies)
        for frase, certo, dado, conf in erros[:12]:
            print(f"    ERR {conf:.2f}  {frase:60s} certo={certo} deu={dado}")
    base = sum(1 for _f, c in FRASES if c == "conversa") / len(FRASES)
    print(f"\nreferência: chutar sempre 'conversa' acerta {base:.0%}")
    print("resumo:", {k: f"{v:.0%}" for k, v in resumo.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
