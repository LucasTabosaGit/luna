"""Corretor da transcrição: o Qwen local conserta o que o Whisper ouviu errado.

Ordem no servidor:
    Whisper -> comando pronto? -> (não) corretor -> comando pronto de novo? -> juiz

Por que depois do comando pronto: "abre o Spotify" já sai certo do Whisper
e resolve em 0,1 s; não vale pagar o corretor nesse caso. O corretor entra
quando o texto não casou com nada - é aí que um "abre o espoti fai" ou uma
palavra trocada faz o pedido ir ao lugar errado.

Cuidados que vêm da literatura de correção pós-reconhecimento (GER):
- O maior risco é a *supercorreção*: o modelo "melhora" a frase e muda o
  sentido, ou responde à pergunta em vez de corrigi-la. O prompt manda
  devolver igual quando estiver certo e nunca responder.
- Troca grande = suspeita. Se a correção mudar demais (proporção de
  semelhança baixa), fica o original.
- Palavra sem sentido que não tem parecida: mantém como está (quem
  responde pede para repetir).
"""
from __future__ import annotations

import difflib
import re
import time

PROMPT = (
    "Você recebe o texto que um reconhecedor de voz transcreveu de um pedido "
    "falado em português do Brasil a uma assistente de computador chamada Luna. "
    "O reconhecedor às vezes troca palavras por outras de som parecido, junta ou "
    "separa palavras e erra nomes de programas, sites e marcas.\n"
    "Tarefa: devolva APENAS o pedido corrigido, como a pessoa quis dizer.\n"
    "Regras:\n"
    "- Se o texto já estiver certo, devolva exatamente igual.\n"
    "- Corrija só erros de reconhecimento (som parecido); não reescreva o estilo, "
    "não troque o jeito de falar (\"me lembra\", \"tô\", \"pra\") nem mude o sentido.\n"
    "- Letra falada de disco ou tecla (\"éle\", \"cê\", \"dê\") vira a letra (L, C, D).\n"
    "- NÃO responda ao pedido, não explique, não use aspas.\n"
    "- Se uma palavra não fizer sentido e não houver nenhuma parecida que "
    "encaixe, deixe como está.\n"
    "Nomes comuns: Spotify, Chrome, YouTube, Netflix, Prime Video, WhatsApp, "
    "Discord, VS Code, Steam, Shopee, Mercado Livre, Luna, Claude."
)

EXEMPLOS = [
    ("abre o espoti fai", "Abre o Spotify"),
    ("que horas são", "Que horas são"),
    ("coloca uma música da legião urbana no spot fire", "Coloca uma música da Legião Urbana no Spotify"),
    ("abre o zap", "Abre o zap"),
    ("qual a previsão do tempo pra amanhã em mace ió", "Qual a previsão do tempo pra amanhã em Maceió"),
    ("me conta uma piada", "Me conta uma piada"),
    ("aumenta o volume do computa dor", "Aumenta o volume do computador"),
    ("por que o céu é azul", "Por que o céu é azul"),
    # Letra de disco falada vira palavra ("éle", "cê", "dê").
    ("quanto espaço livre tem no disco cê", "Quanto espaço livre tem no disco C"),
    ("abre a pasta do disco dê", "Abre a pasta do disco D"),
    # Pedido é ordem: "abri/abriu" é o Whisper errando "abre".
    ("abri o discord", "Abre o Discord"),
    ("me lembra de ligar pra minha mãe", "Me lembra de ligar pra minha mãe"),
    # "skill" em inglês: o Whisper escreve "esquiva", "esquil", "esquio".
    ("salva isso numa esquiva pra ser mais rápido", "Salva isso numa skill pra ser mais rápido"),
    ("você criou essa esquil", "Você criou essa skill"),
]

# Abaixo disso a "correção" mudou a frase demais: fica o original.
SEMELHANCA_MIN = 0.55


def _norm(t: str) -> str:
    return re.sub(r"[^\w\s]", "", t.lower()).strip()


# Erros previsíveis resolvidos por regra, antes do modelo (medido: o Qwen
# trocava "disco éle" por "disco E" / "disco rígido" e mantinha "abri").
_LETRAS = {"a": "A", "á": "A", "bê": "B", "be": "B", "cê": "C", "ce": "C", "sê": "C",
           "se": "C", "dê": "D", "de": "D", "é": "E", "e": "E", "efe": "F", "gê": "G",
           "ge": "G", "éle": "L", "ele": "L", "le": "L", "éli": "L", "eli": "L"}
_RE_DISCO = re.compile(r"\b(disco|unidade|drive)\s+(" + "|".join(
    sorted(map(re.escape, _LETRAS), key=len, reverse=True)) + r")\b(?=[\s?.!,]|$)",
    re.IGNORECASE)
_RE_ABRI = re.compile(r"^(?:(ô|ei|oi)\s+)?(abri|abriu)\s+(?=(o|a|os|as|meu|minha)\b)",
                      re.IGNORECASE)


def regras(texto: str) -> str:
    t = _RE_DISCO.sub(lambda m: "%s %s" % (m.group(1), _LETRAS[m.group(2).lower()]), texto)
    t = _RE_ABRI.sub(lambda m: (m.group(1) + " " if m.group(1) else "") + "abre ", t)
    return t[:1].upper() + t[1:] if t != texto else t


def corrigir(llm, modelo: str, texto: str, extra: dict | None = None,
             contexto: str = "") -> tuple[str, float]:
    """-> (texto corrigido, segundos). Em falha devolve o original."""
    t0 = time.time()
    texto = regras(texto)
    if len(texto.split()) < 2:
        return texto, 0.0
    msgs = [{"role": "system", "content": PROMPT + (
        "\nÚltima fala do assistente (ajuda a entender o contexto): " + contexto[:160]
        if contexto else "")}]
    for errado, certo in EXEMPLOS:
        msgs += [{"role": "user", "content": errado},
                 {"role": "assistant", "content": certo}]
    msgs.append({"role": "user", "content": texto})
    try:
        r = llm.chat.completions.create(
            model=modelo, temperature=0, max_tokens=max(24, len(texto) // 2),
            messages=msgs, extra_body=extra or {})
        novo = (r.choices[0].message.content or "").strip().strip('"“”\'')
    except Exception as e:  # noqa: BLE001
        print("  [corretor] falhou, fica o original: %s" % str(e)[:90], flush=True)
        return texto, time.time() - t0
    novo = novo.splitlines()[0].strip() if novo else ""
    if not novo:
        return texto, time.time() - t0
    novo = regras(novo)
    sem = difflib.SequenceMatcher(None, _norm(texto), _norm(novo)).ratio()
    if sem < SEMELHANCA_MIN:
        print("  [corretor] mudou demais (%.2f), fica o original: %r -> %r"
              % (sem, texto[:60], novo[:60]), flush=True)
        return texto, time.time() - t0
    return novo, time.time() - t0


def mudou(a: str, b: str) -> bool:
    return _norm(a) != _norm(b)
