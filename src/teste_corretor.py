"""Testa o corretor: conserta erros de reconhecimento sem estragar frase certa.

Uso: .venv/Scripts/python.exe src/teste_corretor.py
"""
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from openai import OpenAI  # noqa: E402

import config  # noqa: E402
import corretor  # noqa: E402

# (errado, palavras que a correção precisa ter)
ERRADOS = [
    ("abre o espotifai", ["spotify"]),
    ("toca legião urbana no spot fai", ["spotify"]),
    ("abre o prime vídeo no crome", ["chrome"]),
    ("abre o discordi", ["discord"]),
    ("abre o iu tubi", ["youtube"]),
    ("qual a temperatura em mace ió", ["maceió"]),
    ("abre o vis code", ["code"]),
    ("liga a netiflix", ["netflix"]),
    ("quanto espaço tem no disco éle", ["disco l"]),
    ("abri o uatsap", ["abre o whatsapp"]),
    ("abre o bloco de nota", ["bloco de notas"]),
    ("toca uma música do legi ão urbana", ["legião urbana"]),
    ("fecha o espoti fai", ["fecha o spotify"]),
    ("abre o zap", ["abre o"]),
    ("vê quanto tem livre na unidade cê", ["unidade c"]),
]

# Frases certas: o corretor não pode mudar o sentido (nem responder).
CERTAS = [
    "Que horas são?",
    "Por que o céu é azul?",
    "Me conta uma piada",
    "Quanto espaço livre tem no disco L?",
    "Tô com sono hoje",
    "Pensa melhor",
    "Monta um plano de treino de dois meses",
    "Abre o Prime Video",
    "Fecha a calculadora",
    "Qual é a capital da Austrália?",
    "Me lembra de tirar o bolo do forno em dez minutos",
    "Qual o melhor álbum do Spotify este ano?",
    "Abre o disco L",
    "Me dê uma ideia de jantar",
    "O que é um ele no português?",
    "Hermes, você tá me ouvindo?",
    "Desliga o computador daqui a meia hora",
    "Qual o preço do dólar hoje?",
    "Toca Legião Urbana",
    "E amanhã?",
]

llm = OpenAI(base_url=config.LLM_URL, api_key=config.LLM_API_KEY, timeout=30)
modelo = config.LLM_MODELO
ok, tempos = 0, []

corretor.corrigir(llm, modelo, "aquecendo o modelo", config.LLM_EXTRA)
for errado, precisa in ERRADOS:
    novo, dt = corretor.corrigir(llm, modelo, errado, config.LLM_EXTRA)
    tempos.append(dt)
    bom = all(p in novo.lower() for p in precisa)
    ok += bom
    print("%s %.2fs  %-40s -> %s" % ("OK " if bom else "ERR", dt, errado, novo))

for certa in CERTAS:
    novo, dt = corretor.corrigir(llm, modelo, certa, config.LLM_EXTRA)
    tempos.append(dt)
    bom = not corretor.mudou(certa, novo)
    ok += bom
    print("%s %.2fs  %-40s -> %s" % ("OK " if bom else "ERR", dt, certa, novo))

total = len(ERRADOS) + len(CERTAS)
print("\n%d/%d corretos | tempo mediano %.2fs, pior %.2fs"
      % (ok, total, statistics.median(tempos), max(tempos)))
