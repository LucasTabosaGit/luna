"""Testa o reconhecimento de comandos prontos SEM executar nada.

    .venv/Scripts/python.exe src/teste_comandos.py

POSITIVOS: frases que devem virar comando (esperado = nome do comando).
NEGATIVOS: frases que DEVEM ir para o LLM/Hermes - capturá-las seria
executar a coisa errada.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comandos  # noqa: E402

POSITIVOS = [
    ("Que horas são?", "hora"), ("Hermes, que horas são agora?", "hora"),
    ("Me diz as horas", "hora"), ("Que dia é hoje?", "data"),
    ("Qual a data de hoje?", "data"),
    ("Aumenta o volume", "volume"), ("Abaixa o som", "volume"),
    ("Coloca o volume em 30", "volume"), ("Volume em trinta por cento", "volume"),
    ("Aumenta o volume para 50", "volume"), ("Diminui o volume em 20", "volume"),
    ("Volume no máximo", "volume"), ("Sobe o volume no talo", "volume"), ("Mais alto", "volume"),
    ("Silencia", "volume"), ("Tira do mudo", "volume"), ("Coloca no mudo", "volume"),
    ("Pausa a música", "mídia"), ("Continua", "mídia"), ("Próxima música", "mídia"),
    ("Pula essa", "mídia"), ("Volta a música", "mídia"),
    # dizer onde não muda a ação: teclas multimídia agem no player que toca
    ("Dá play no Spotify", "mídia"), ("Dá play", "mídia"),
    ("Pausa no Spotify", "mídia"), ("Pausa aí", "mídia"),
    ("Próxima música no Spotify", "mídia"), ("Pula essa aí", "mídia"),
    ("Continua no YouTube", "mídia"),
    # app solto ou com "do/da": "pausa o Spotify", "pausa a música do Spotify"
    ("Pausa, música do Spotify.", "mídia"), ("Pausa o Spotify", "mídia"),
    ("Pausa a música do Spotify", "mídia"), ("Continua o Spotify", "mídia"),
    ("Próxima música do Spotify", "mídia"), ("Volta a música do Spotify", "mídia"),
    ("Pausa o YouTube", "mídia"),
    ("Coloca um timer de 5 minutos", "timer"), ("Timer de dez minutos", "timer"),
    ("Me avisa em 20 minutos", "timer"), ("Me lembra daqui a meia hora", "timer"),
    ("Me lembra de tirar o bolo em 40 minutos", "timer"),
    ("Timer de 30 segundos para o ovo", "timer"), ("Cancela o timer", "timer"),
    ("Quanto é 25 vezes 4?", "conta"), ("Quanto é 1.500 dividido por 3", "conta"),
    ("Calcula 15% de 200", "conta"), ("Quanto é vinte e cinco mais dezessete", "conta"),
    ("Como está o tempo?", "clima"), ("Vai chover hoje?", "clima"),
    ("Qual a previsão do tempo em São Paulo?", "clima"),
    ("Toca Legião Urbana no YouTube", "youtube"),
    ("Pesquisa receita de bolo no Google", "pesquisa"),
    ("Abre o Chrome", "abrir"), ("Abre a calculadora", "abrir"),
    ("Abre o bloco de notas", "abrir"), ("Abre o VS Code", "abrir"),
    ("Abrir o Discord", "abrir"), ("Abre o LM Studio", "abrir"),
    ("Abre o YouTube", "site"), ("Abre o Gmail", "site"),
    ("Abre a Netflix", "site"), ("Abre o Prime Video", "site"),
    ("Abre o prime", "site"), ("Abre a Amazon Prime Video", "site"),
    ("Abre python.org.br", "site"),
    # frases reais que foram parar no Claude sem precisar
    ("Abra o crome.", "abrir"), ("Abre o espotifai", "abrir"), ("Abre o discordi", "abrir"),
    ("Abra uma nova guia no Chrome.", "navegador"), ("Abre uma aba nova", "navegador"),
    ("Fecha essa aba", "navegador"),
    ("Olha a temperatura na minha cidade", "clima"), ("Quantos graus está fazendo?", "clima"),
    ("Tá frio lá fora?", "clima"),
    ("Qual é a temperatura da minha cidade?", "clima"),
    ("Poderia abrir o Spotify.", "abrir"),
    ("Toca a playlist de academia no Spotify", "spotify"),
    ("Coloca Legião Urbana no Spotify", "spotify"),
    ("Toca a playlist de treino no Spotify aí", "spotify"),
    ("Abre a pasta downloads", "pasta"), ("Abre os documentos", "pasta"),
    ("Mostra a área de trabalho", "janelas"), ("Tira um print", "print"),
    ("Bloqueia o computador", "bloquear"),
]

NEGATIVOS = [
    "Que horas são em Tóquio?",                    # fuso: LLM
    "Abre o arquivo de log e me diz o erro",       # tarefa: Hermes
    "Abre a planilha de vendas de setembro",       # arquivo específico
    "Quanto é a raiz quadrada de dois?",           # não é conta simples
    "Pausa as promoções",                          # pedido de negócio
    "Volume de vendas da Shopee",                  # "volume" não é som
    "Me explica como funciona um timer",
    "Qual o melhor horário para postar?",
    "Desliga o computador",                        # destrutivo: fica de fora
    "Fecha o Chrome",                              # destrutivo: fica de fora
    "Toca uma música",                             # vago: LLM pergunta qual
    "Abre um chamado no suporte",
    "Oi, tudo bem?",
    "Pesquisa sobre a história do Brasil e me resume",
    "O tempo passa rápido, né?",
    "Abre o crachá do evento",                      # não é app: não pode virar "Chrome"
    "Quais filmes bons tem no Prime Video?",         # pergunta: LLM
    "Abre a guia de estilo do projeto",             # documento, não aba
    "Fecha a aba do YouTube que está tocando",      # específica: Claude
    "Fecha a calculadora",                          # fechar programa: Claude confirma
    "Qual o melhor álbum no Spotify este ano?",     # pergunta, não pedido de tocar
    "Dá play no vídeo de ontem que eu salvei",      # alvo específico: Claude
]


def main() -> int:
    comandos._APPS_PRONTO.wait(30)
    print(f"apps no Iniciar: {len(comandos._APPS)}\n")
    erros = 0
    print("POSITIVOS")
    for frase, esperado in POSITIVOS:
        t0 = time.perf_counter()
        r = comandos.tentar(frase, executar=False)
        ms = (time.perf_counter() - t0) * 1000
        nome = r.nome if r else None
        ok = nome == esperado
        erros += not ok
        extra = ""
        if r:
            extra = r.fala if r.nome != "clima" else ""
            if r.depois:
                extra += f"  [depois {r.depois[0]:.0f}s: {r.depois[1]}]"
        print(f"  {'ok ' if ok else 'ERR'} {ms:5.1f}ms  {frase:45s} -> {nome}  {extra}")
    print("\nNEGATIVOS (devem ir para o cérebro)")
    for frase in NEGATIVOS:
        r = comandos.tentar(frase, executar=False)
        ok = r is None
        erros += not ok
        print(f"  {'ok ' if ok else 'ERR'} {frase:45s} -> {r.nome + ': ' + r.fala if r else 'cérebro'}")
    total = len(POSITIVOS) + len(NEGATIVOS)
    print(f"\n{total - erros}/{total} corretos")
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
