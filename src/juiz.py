"""Juiz do modo Automático: resolve com o modelo local ou manda ao Hermes.

Ordem no servidor: comando pronto -> juiz -> (Qwen local | Hermes/Claude).

O juiz é o próprio LLM local, já carregado para conversar: ~0,1 s e
nenhuma VRAM extra. Medido contra o Laya (modelo de decisão de 33 ms):
o Qwen acertou 96% em frases novas, o Laya ajustado 88% (ver
src/teste_laya*.py e src/teste_juiz_llm.py).

Na dúvida, fica no local: o Hermes custa ~10 s e uso da conta Anthropic,
e o usuário pode pedir "pensa melhor" para mandar a mesma pergunta ao
Hermes.
"""
from __future__ import annotations

import re
import time
import unicodedata

PROMPT = (
    "Classifique o pedido feito por voz a um assistente no computador. "
    "Responda APENAS uma palavra:\n"
    "conversa = papo, sentimento, opinião, piada, sugestão, recomendação, curiosidade, "
    "definição, tradução, receita ou pergunta de conhecimento geral. "
    "É a grande maioria dos pedidos.\n"
    "computador = precisa verificar ou fazer algo NESTE computador ou na internet agora: "
    "arquivos, pastas, programas, processos, sistema, hardware, rede, conexão, "
    "pesquisa na web, e-mail, mensagens.\n"
    "projeto = trabalho longo que exige várias etapas: plano detalhado, roteiro, "
    "estratégia, análise de negócio, comparação técnica, texto longo ou formal, "
    "problema de matemática com várias contas."
)

# Exemplos no prompt (few-shot). Medido em src/teste_juiz_formas.py com
# 105 frases fora destes exemplos:
#     2 categorias ("simples/complexo")   88%
#     3 categorias, sem exemplos          90%
#     3 categorias + estes exemplos      100%  (0 simples->Hermes)
# Separar "mexe no PC" de "exige raciocínio" em rótulos distintos ajuda
# o modelo pequeno; juntar os dois num rótulo só confunde.
EXEMPLOS = [
    ("Me conta uma piada", "conversa"), ("Por que o mar é salgado?", "conversa"),
    ("Tô com sono", "conversa"), ("Me indica uma série", "conversa"),
    ("Quanto de disco livre eu tenho?", "computador"),
    ("Vê se o Wi-Fi está conectado", "computador"),
    ("Tem alguma planilha na área de trabalho?", "computador"),
    ("Monta um plano de treino de dois meses para correr cinco quilômetros", "projeto"),
    ("Avalia minha ideia de abrir uma loja de açaí", "projeto"),
]

# "pensa melhor", "manda pro Claude": reenviar a pergunta anterior ao Hermes.
_ESCALAR = re.compile(
    r"^(pensa|pense|pensar) (melhor|direito|com calma)|"
    r"^(manda|mande|passa|passe|joga|jogue|pergunta|pergunte|leva|leve)"
    r"( isso| essa| a pergunta)? (pro|para o|pra o|ao|pra|para a|a) (hermes|claude|luna)|"
    r"^(usa|use) o (hermes|claude)|^capricha|^responde melhor|^resposta melhor"
)


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^\w\s]", " ", t).strip()


def pede_escalar(texto: str) -> bool:
    """O usuário pediu para mandar a pergunta anterior ao Hermes?"""
    t = re.sub(r"\s+", " ", _norm(texto))
    t = re.sub(r"^(ei|oi|ok|nao|entao|hum)\s+", "", t)
    return bool(_ESCALAR.search(t)) and len(t.split()) <= 8


# Pedido sobre o PRÓPRIO assistente aprender ("salva isso numa skill",
# "registra esse comando", "lembra disso pra próxima vez"). Só o Claude
# (Hermes) cria skill, memória ou comando; o modelo local só conversaria
# e fingiria ter feito. Medido: "você pode salvar essa skill pra ser mais
# rápida na próxima" foi julgado "conversa" e nada foi salvo.
_APRENDER = re.compile(
    r"\b(skills?|skil|comando pronto)\b|"
    # Pergunta sobre como ELA funciona/aprende: o modelo local não conhece
    # o próprio código e responde qualquer coisa (medido: "essa é a melhor
    # forma de você aprender?" recebeu um "Aprender com o tempo é o segredo").
    r"\b(voce|luna) (aprende|aprender|aprendendo|funciona|melhora|evolui)\b|"
    r"\b(pra|para) (voce|te) aprender\b|\bgaps?\b|\blacunas?\b|"
    r"\b(salva|salve|salvar|registra|registre|registrar|grava|grave|gravar|"
    r"memoriza|memorize|anota|anote|guarda|guarde|cria|crie|criar|aprende|aprenda)\b"
    r".{0,40}\b(isso|esse|essa|este|esta|comando|memoria|proxima vez|mais rapid)|"
    r"\b(lembra|lembre|lembrar) (disso|dessa|desse)\b|"
    r"\bpra (proxima|outra) vez\b|\bpara a proxima vez\b|\bmais rapid[oa] na proxima\b"
)


# Continuação de um pedido que JÁ está com o Claude ("sim", "pode fazer",
# "verifique e gere tudo"): o juiz via a frase sozinha, achava "conversa"
# e o modelo local respondia sem saber do que se tratava.
_CONTINUA = re.compile(
    r"^(sim|isso|pode|ok|beleza|blz|claro|certo|vai|bora|manda|faz|faca|faça|"
    r"gera|gere|cria|crie|verifica|verifique|confere|confira|continua|continue|"
    r"segue|siga|tenta|tente|termina|termine|conclui|conclua|coloca|coloque|"
    r"arruma|arrume|resolve|resolva|isso mesmo|exato|quero|pode ser)\b"
)


def continua_pedido(texto: str) -> bool:
    t = re.sub(r"\s+", " ", _norm(texto)).strip(" .,!?")
    t = re.sub(r"^(luna|entao|ta|e|ai|agora)\s+", "", t)
    return bool(_CONTINUA.search(t)) and len(t.split()) <= 12


def pede_aprender(texto: str) -> bool:
    """Pedido para salvar skill/memória/comando: tem que ir ao Hermes."""
    return bool(_APRENDER.search(re.sub(r"\s+", " ", _norm(texto))))


def decidir(llm, modelo: str, texto: str, extra: dict | None = None) -> tuple[str, float]:
    """-> ("rapido" | "hermes", segundos gastos). Falha = "rapido"."""
    t0 = time.time()
    msgs = [{"role": "system", "content": PROMPT}]
    for f, r in EXEMPLOS:
        msgs += [{"role": "user", "content": f}, {"role": "assistant", "content": r}]
    msgs.append({"role": "user", "content": texto})
    try:
        r = llm.chat.completions.create(
            model=modelo, temperature=0, max_tokens=4,
            messages=msgs, extra_body=extra or {})
        txt = _norm(r.choices[0].message.content or "")
    except Exception as e:  # noqa: BLE001
        print("  [juiz] falhou, fica no local: %s" % str(e)[:90], flush=True)
        return "rapido", time.time() - t0
    # Só "conversa" fica no local; resposta estranha também fica (na
    # dúvida, o barato).
    rota = "hermes" if txt.startswith(("computador", "projeto")) else "rapido"
    return rota, time.time() - t0
