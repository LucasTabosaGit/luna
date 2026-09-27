"""Resgate do detector: só pedido de verdade vira chamada sem o nome.

    .venv/Scripts/python.exe src/teste_resgate.py

Caso real (logs): "Você não está passando, não tem um minuto." (conversa de
jogo) foi resgatado 1,00 e respondido; "Vou ficar atenta." (eco da própria
Luna) foi resgatado 0,92. Positivos: pedidos que o Whisper cortou o nome
continuam passando.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ativacao  # noqa: E402

PEDIDOS = ["Play no Spotify.", "Abre o YouTube.", "Toca uma música.",
           "Que horas são?", "Pausa o vídeo.", "Aumenta o volume.",
           "Pesquisa o preço do dólar.", "Qual a previsão do tempo?",
           "Abre o Prime Video.", "Fecha o Chrome."]
CONVERSA = ["Você não está passando, não tem um minuto.",
            "Estão indo para a gente.", "A gente vai ver.",
            "Pô, Ronaldo. Põe pra caralho isso aqui.",
            "Já tomamos os primeiros danos e já estamos na mana.",
            "Matheus, eu vou te mandar pelo WhatsApp o vídeo.",
            "Ah, você tá louco?", "Não, não.", "Perfeito."]

agora = time.time()
DITAS = [(agora - 20, "Certo. Vou ficar atenta."),
         (agora - 5, "Entendi. Desculpe pela demora. Estou aqui agora.")]
ECOS = ["Vou ficar atenta.", "Estou aqui agora.", "Certo."]
NAO_ECO = ["Abre a calculadora.", "Que horas são?"]
VELHO = [(agora - 600, "Vou ficar atenta.")]

erros = []
for f in PEDIDOS:
    if not ativacao.parece_pedido(f):
        erros.append("pedido barrado: " + f)
for f in CONVERSA:
    if ativacao.parece_pedido(f):
        erros.append("conversa virou pedido: " + f)
for f in ECOS:
    if not ativacao.eco_da_luna(DITAS, f):
        erros.append("eco nao reconhecido: " + f)
for f in NAO_ECO:
    if ativacao.eco_da_luna(DITAS, f):
        erros.append("pedido tomado por eco: " + f)
if ativacao.eco_da_luna(VELHO, "Vou ficar atenta."):
    erros.append("eco de 10 min atras ainda barra")

total = len(PEDIDOS) + len(CONVERSA) + len(ECOS) + len(NAO_ECO) + 1
print("%d/%d" % (total - len(erros), total))
for e in erros:
    print("  ERRO", e)
sys.exit(1 if erros else 0)
