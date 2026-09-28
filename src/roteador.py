"""Roteador: UMA decisão por pedido - ação local, conversa ou Claude.

Substitui três camadas que decidiam separadas e se atropelavam (lista de
ações, juiz, e as regras de texto "composto", "rotina", "continuação",
"aprender"). Agora o modelo rápido (LM Studio ou DeepSeek) lê o pedido, a
última troca da conversa e a lista de ações, e responde um JSON só:

    {"acao": "<ação da lista>", "args": {...}}   executa aqui, ~1 s
    {"acao": "conversa"}                          ele mesmo responde
    {"acao": "claude"}                            vai ao Claude (Hermes)

Ordem no servidor:
    comandos prontos + atalhos de skill (comandos.py, sem modelo)
    -> roteador (aqui)  -> ação | conversa | Claude

Para ESCALAR (a direção do projeto):
- Pedido que o Claude resolve sempre do mesmo jeito vira ATALHO
  (atalhos.json), não regra aqui.
- Ajuste de decisão vira EXEMPLO na lista abaixo, não regex nova.
- Única trava fixa: pedido destrutivo nunca é ação local (vai ao Claude).
"""
from __future__ import annotations

import json
import re
import time

import acoes

PROMPT = (
    "Você decide o que um assistente de voz chamado Luna faz com o pedido do usuário "
    "(português do Brasil, transcrito da fala; pode ter erros de transcrição).\n\n"
    "Ações que ela executa sozinha, na hora:\n{lista}\n\n"
    "Além delas, duas saídas:\n"
    "- conversa: papo, opinião, piada, sugestão, curiosidade, definição, tradução, "
    "pergunta de conhecimento geral com resposta curta. É a maioria.\n"
    "- claude: tudo que precisa AGIR no computador ou na internet além das ações acima "
    "(arquivos, pastas, programas, sistema, e-mail, mensagens, sites com login, "
    "pesquisar e ler a resposta); pedido com MAIS DE UMA ETAPA (abrir X e depois "
    "fazer Y); fechar, desligar, apagar; tarefas, agenda, diário e rotina do usuário "
    "; pedir para ela aprender, salvar skill, memória ou comando; mudar "
    "o próprio assistente; trabalho longo (plano, roteiro, texto formal, análise, "
    "conta com várias etapas); e a continuação curta (\"sim\", \"pode fazer\", "
    "\"verifica e gera tudo\") de algo que o Claude estava fazendo.\n\n"
    "Responda SÓ um JSON numa linha: {{\"acao\": \"<nome>\", \"args\": {{...}}}}. "
    "Na dúvida entre uma ação e claude, claude: executar a coisa errada é pior que "
    "ser lento."
)

# Exemplos pesam mais que a descrição (medido nas versões anteriores).
EXEMPLOS = [
    ("bota um som do legião urbana aí no meu spotify",
     {"acao": "spotify_buscar", "args": {"o_que": "legião urbana"}}),
    ("deixa o som na metade", {"acao": "volume_definir", "args": {"nivel": 50}}),
    ("me acorda daqui vinte minutos",
     {"acao": "timer", "args": {"quantidade": 20, "unidade": "minutos"}}),
    ("segura a música aí rapidinho", {"acao": "midia", "args": {"comando": "pausar_ou_tocar"}}),
    ("abre a calculadora", {"acao": "abrir", "args": {"nome": "calculadora"}}),
    ("quero ver a pasta de imagens", {"acao": "abrir", "args": {"nome": "imagens"}}),
    ("dá uma pesquisada no google sobre a previsão do tempo",
     {"acao": "google_pesquisar", "args": {"o_que": "previsão do tempo"}}),
    ("me conta uma piada", {"acao": "conversa"}),
    ("por que o mar é salgado", {"acao": "conversa"}),
    ("me conta uma curiosidade sobre o spotify", {"acao": "conversa"}),
    ("abre a netflix e pesquisa um filme de terror", {"acao": "claude"}),
    ("fecha o spotify", {"acao": "claude"}),
    ("quanto espaço livre tem no disco c", {"acao": "claude"}),
    ("o que eu tenho pra hoje", {"acao": "claude"}),
    ("salva isso como skill pra próxima vez", {"acao": "claude"}),
    ("monta um plano de treino de dois meses", {"acao": "claude"}),
    # Conversa com fatos da pessoa: fica na rápida (a revisão do dia guarda).
    ("dá um oi pro meu amigo joão", {"acao": "conversa"}),
    ("prefiro série de suspense a novela", {"acao": "conversa"}),
    ("vou no mercado mais tarde", {"acao": "conversa"}),
    ("na verdade meu nome é ana, o outro foi erro", {"acao": "conversa"}),
    ("não é carlos, meu nome é pedro, você entendeu errado", {"acao": "conversa"}),
    ("manda um boa noite pra minha mãe", {"acao": "conversa"}),
    ("amanhã a gente vai assistir aquela série", {"acao": "conversa"}),
    # Conta que FEZ algo da rotina: a especialista marca no app de tarefas.
    ("já fiz a caminhada de hoje", {"acao": "claude"}),
    # Pedido EXPLÍCITO para lembrar: vai à especialista, que grava na hora.
    ("guarda que eu sou alérgico a camarão", {"acao": "claude"}),
]

_DESTRUTIVO = acoes.destrutivo


def _mensagens(texto: str, contexto: str) -> list[dict]:
    msgs = [{"role": "system", "content": PROMPT.format(lista=acoes._lista())}]
    for f, r in EXEMPLOS:
        msgs += [{"role": "user", "content": f},
                 {"role": "assistant", "content": json.dumps(r, ensure_ascii=False)}]
    pedido = texto if not contexto else f"[{contexto}]\n{texto}"
    msgs.append({"role": "user", "content": pedido})
    return msgs


def contexto_de(historico: list[dict], ultima_rota: str) -> str:
    """Resumo da última troca, para continuação curta ("sim", "pode fazer")."""
    resp = [m["content"] for m in historico[:-1] if m.get("role") == "assistant"]
    if not resp or not isinstance(resp[-1], str):
        return ""
    quem = "Claude" if ultima_rota == "hermes" else "a Luna"
    return "última resposta (de %s): %s" % (quem, resp[-1][:220].replace("\n", " "))


def decidir(llm, modelo: str, texto: str, contexto: str = "",
            extra: dict | None = None) -> dict:
    """-> {"destino": "acao"|"conversa"|"claude", "acao", "args", "t"}.

    Falha do modelo = "conversa" (o barato; o usuário pode dizer "pensa melhor").
    """
    t0 = time.time()
    if _DESTRUTIVO(texto):
        return {"destino": "claude", "acao": "", "args": {}, "t": time.time() - t0,
                "motivo": "destrutivo"}
    try:
        r = llm.chat.completions.create(
            model=modelo, temperature=0, max_tokens=80,
            messages=_mensagens(texto, contexto), extra_body=extra or {})
        bruto = (r.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", bruto, re.S)
        d = json.loads(m.group(0)) if m else {}
    except Exception as e:  # noqa: BLE001
        print("  [roteador] falhou, fica em conversa: %s" % str(e)[:90], flush=True)
        return {"destino": "conversa", "acao": "", "args": {}, "t": time.time() - t0}
    nome = str(d.get("acao") or "conversa").strip().lower()
    args = d.get("args") if isinstance(d.get("args"), dict) else {}
    if nome in ("claude", "hermes"):
        destino = "claude"
    elif nome in acoes.ACOES:
        destino = "acao"
    else:
        destino, nome = "conversa", ""
    return {"destino": destino, "acao": nome if destino == "acao" else "",
            "args": args, "t": time.time() - t0}
