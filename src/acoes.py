"""Lista de AÇÕES que a Luna sabe fazer - o modelo local escolhe qual usar.

Por que existe: os padrões de frase de comandos.py só casam com os jeitos
de pedir que alguém escreveu ("toca X no Spotify" casava, "toca X no MEU
Spotify" não). Aqui cada ação tem uma DESCRIÇÃO, e o Qwen local lê o
pedido e escolhe a ação + os argumentos (tool calling, o mesmo desenho do
Home Assistant). Qualquer jeito de pedir uma ação que existe funciona.

Ordem no servidor:
    padrões (comandos.py, ~0,1 s)  ->  ações (aqui, ~0,3-0,5 s)
    ->  juiz  ->  Qwen conversa | Claude

Regras:
- Ação DESTRUTIVA (desligar, fechar programa, apagar) NÃO entra aqui:
  continua indo ao Claude, que pede confirmação.
- Toda ação nova precisa de exemplo em src/teste_acoes.py e só entra
  depois de aprovada (ver src/propostas.py).
- "nenhuma" é a resposta padrão: na dúvida, o modelo não executa nada e o
  pedido segue para o juiz. Executar a coisa errada é pior que ser lento.

As ações reaproveitam as funções de comandos.py (mesma execução e mesma
fala dos padrões), então não há dois jeitos de fazer a mesma coisa.
"""
from __future__ import annotations

import json
import re
import time

import comandos as C


class _M:
    """Imita o `re.Match` que as funções de comandos.py esperam."""

    def __init__(self, **g):
        self.g = {k: ("" if v is None else str(v)) for k, v in g.items()}

    def group(self, k):
        return self.g.get(k)

    def groupdict(self):
        return dict(self.g)


def _txt(v) -> str:
    return C.normalizar(str(v or "")).strip()


def _num(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    return C.numero(_txt(v)) if v not in (None, "") else None


# ------------------------------------------------------------------ ações
# Cada uma: descrição (o que o modelo lê), parâmetros e a função.
# A função recebe (args, executar) e devolve comandos.Resultado ou None
# (None = argumento inválido: o pedido segue para o juiz).

def _a_hora(a, e):
    return C._hora(None)


def _a_data(a, e):
    return C._data(None)


def _a_volume_definir(a, e):
    n = _num(a.get("nivel"))
    if n is None:
        return None
    return C._vol_definir(_M(n=C.fmt(n)), e)


def _a_volume_mudar(a, e):
    d = _txt(a.get("direcao"))
    sinal = +1 if d.startswith(("aum", "sub", "mais")) else -1 if d.startswith(
        ("dim", "abai", "baix", "menos")) else 0
    if not sinal:
        return None
    q = _num(a.get("quanto"))
    return C._vol_passo(_M(n=C.fmt(q) if q else "", prep="em"), e, sinal)


def _a_mudo(a, e):
    ligar = a.get("ligar")
    if isinstance(ligar, str):
        ligar = _txt(ligar) not in ("false", "nao", "0", "desligar")
    return C._mudo(None, e, bool(ligar if ligar is not None else True))


def _a_midia(a, e):
    c = _txt(a.get("comando"))
    if c.startswith(("prox", "pul", "avan", "seg")):
        return C._midia("mídia", C.VK_PROXIMA, "Próxima.")(None, e)
    if c.startswith(("ant", "vol")):
        return C._midia("mídia", C.VK_ANTERIOR, "Voltando.")(None, e)
    if c.startswith(("paus", "toc", "play", "cont", "reto", "desp")):
        return C._midia("mídia", C.VK_PLAY_PAUSE, "Pronto.")(None, e)
    return None


def _a_timer(a, e):
    n = _num(a.get("quantidade"))
    u = _txt(a.get("unidade")) or "minutos"
    if n is None or not u.startswith(("seg", "min", "hor")):
        return None
    return C._timer(_M(n=C.fmt(n), u=u, o=a.get("motivo") or ""), e)


def _a_cancelar_timers(a, e):
    return C.Resultado("timer", "", especial="cancelar_timers")


def _a_conta(a, e):
    op = {"+": "mais", "-": "menos", "*": "vezes", "x": "vezes", "/": "dividido por",
          "somar": "mais", "soma": "mais", "subtrair": "menos", "multiplicar": "vezes",
          "dividir": "dividido por"}.get(_txt(a.get("operacao")), _txt(a.get("operacao")))
    if op in ("porcento", "porcentagem", "%"):
        return C._porcento(_M(a=a.get("a"), b=a.get("b")), e)
    if op not in ("mais", "menos", "vezes", "dividido por"):
        return None
    return C._conta(_M(a=a.get("a"), b=a.get("b"), op=op), e)


def _a_clima(a, e):
    return C._clima(_M(c=_txt(a.get("cidade"))), e)


def _a_spotify(a, e):
    q = _txt(a.get("o_que"))
    return C._spotify(_M(q=q), e) if q else None


def _a_youtube(a, e):
    q = _txt(a.get("o_que"))
    return C._youtube(_M(q=q), e) if q else None


def _a_google(a, e):
    q = _txt(a.get("o_que"))
    return C._google(_M(q=q), e) if q else None


def _a_abrir(a, e):
    x = _txt(a.get("nome"))
    return C._abrir_algo(_M(x=x), e) if x else None


def _a_aba(a, e):
    c = _txt(a.get("comando"))
    if c.startswith(("nov", "abr", "cri")):
        return C._atalho("navegador", (C.VK_CTRL, ord("T")), "Pronto.")(None, e)
    if c.startswith(("reab", "recu", "volt")):
        return C._atalho("navegador", (C.VK_CTRL, C.VK_SHIFT, ord("T")), "Pronto.")(None, e)
    if c.startswith("fech"):
        return C._atalho("navegador", (C.VK_CTRL, ord("W")), "Pronto.")(None, e)
    return None


def _a_area(a, e):
    return C._area_trabalho(None, e)


def _a_print(a, e):
    return C._print(None, e)


def _a_bloquear(a, e):
    return C._bloquear(None, e)


def _p(**props):
    """Atalho para o JSON schema dos parâmetros."""
    req = [k for k, v in props.items() if not v.pop("opcional", False)]
    return {"type": "object", "properties": props, "required": req}


S = {"type": "string"}
N = {"type": "number"}

# Pedido destrutivo: nunca vira ação local, vai ao Claude (que confirma).
# Regra fixa, verificada ANTES do modelo.
_DESTRUTIVO = re.compile(
    r"\b(fecha|feche|fechar|encerra|encerre|encerrar|mata|mate|matar|finaliza|"
    r"desliga|desligue|desligar|reinicia|reinicie|reiniciar|hiberna|suspende|"
    r"apaga|apague|apagar|deleta|delete|deletar|exclui|exclua|excluir|remove|"
    r"remova|remover|desinstala|desinstale|formata|formate|limpa|limpe|esvazia)\b")


def destrutivo(texto: str) -> bool:
    t = C.normalizar(texto)
    # "fecha a aba" é ação local segura (Ctrl+W), igual aos padrões.
    if re.search(r"\b(aba|guia)\b", t) and not re.search(r"\b(todas|tudo)\b", t):
        return False
    return bool(_DESTRUTIVO.search(t))

# Pedido que a ação ÚNICA não cobre: "abre a Netflix E pesquisa um filme" (duas
# etapas) ou "pesquisa X na Netflix" (busca dentro de um site que não tem ação
# própria). A lista só sabe UMA coisa por vez: o modelo escolhia "abrir
# Netflix" e o resto sumia calado. Esses vão ao Claude (tem a skill).
_ETAPA2 = re.compile(
    r"\b(e|e depois|depois|e ai|e ja|entao)\s+(me\s+)?(pesquis|procur|busc|coloc|"
    r"escolh|entr|clic|toc|assist|abr|digit|escrev|mand|envi|pul|selecion|pe|poe|bot|"
    r"liga|faz|cri|salv|mostr|acha|ach)\w*")
_BUSCA = re.compile(r"\b(pesquis|procur|busc|acha|ache|encontr)\w*")
_SITES_COM_ACAO = re.compile(r"\b(youtube|google|spotify)\b")


def composto(texto: str) -> bool:
    """True se o pedido tem mais de uma etapa ou busca num site sem ação."""
    t = C.normalizar(texto)
    if _ETAPA2.search(t):
        return True
    return bool(_BUSCA.search(t)) and bool(re.search(r"\b(na|no|em)\s+\w+", t)) \
        and not _SITES_COM_ACAO.search(t) and not re.search(r"\b(internet|web)\b", t)


ACOES: dict[str, tuple[str, dict, object]] = {
    "hora": ("Diz que horas são agora.", _p(), _a_hora),
    "data": ("Diz a data / o dia da semana de hoje.", _p(), _a_data),
    "volume_definir": ("Põe o volume do computador num nível exato de 0 a 100 "
                       "(máximo/no talo/tudo = 100, metade = 50, mínimo = 5).",
                       _p(nivel=dict(N)), _a_volume_definir),
    "volume_mudar": ("Aumenta ou diminui o volume do computador, sem nível exato.",
                     _p(direcao={"type": "string", "enum": ["aumentar", "diminuir"]},
                        quanto=dict(N, description="pontos a mudar; omita se não disse",
                                    opcional=True)),
                     _a_volume_mudar),
    "mudo": ("Tira ou volta o som do computador (mudo).",
             _p(ligar={"type": "boolean", "description": "true = sem som"}), _a_mudo),
    "midia": ("Controla a música ou vídeo que JÁ está tocando (Spotify, YouTube), sem "
              "dizer qual música: parar/pausar, continuar, pular para a próxima, "
              "voltar para a anterior.",
              _p(comando={"type": "string",
                          "enum": ["pausar_ou_tocar", "proxima", "anterior"]}),
              _a_midia),
    "timer": ("Liga um timer/alarme/lembrete que avisa daqui a um tempo.",
              _p(quantidade=dict(N),
                 unidade={"type": "string", "enum": ["segundos", "minutos", "horas"]},
                 motivo=dict(S, description="do que lembrar, se disse", opcional=True)),
              _a_timer),
    "cancelar_timers": ("Cancela os timers/alarmes ligados.", _p(), _a_cancelar_timers),
    "conta": ("Faz uma conta com dois números.",
              _p(a=dict(N), b=dict(N),
                 operacao={"type": "string",
                           "enum": ["mais", "menos", "vezes", "dividido por", "porcento"],
                           "description": "porcento = a por cento de b"}),
              _a_conta),
    "clima": ("Previsão do tempo: temperatura, frio/calor, se vai chover.",
              _p(cidade=dict(S, description="só se disse uma cidade", opcional=True)),
              _a_clima),
    "spotify_buscar": ("Procura e abre no Spotify uma música, artista, álbum, "
                       "gênero ou playlist. Só quando o pedido diz O QUE ouvir.",
                       _p(o_que=dict(S, description="só o que tocar, sem 'música de'")),
                       _a_spotify),
    "youtube_buscar": ("Procura um vídeo no YouTube.", _p(o_que=dict(S)), _a_youtube),
    "google_pesquisar": ("Abre uma pesquisa no Google (o usuário vai ler o resultado).",
                         _p(o_que=dict(S)), _a_google),
    "abrir": ("Abre um programa, site ou pasta pelo nome (Chrome, calculadora, "
              "Downloads, Netflix...).",
              _p(nome=dict(S, description="só o nome do programa, site ou pasta")),
              _a_abrir),
    "aba_navegador": ("Nova aba, fechar a aba atual ou reabrir a aba fechada no navegador.",
                      _p(comando={"type": "string", "enum": ["nova", "fechar", "reabrir"]}),
                      _a_aba),
    "area_de_trabalho": ("Minimiza tudo e mostra a área de trabalho.", _p(), _a_area),
    "print_tela": ("Tira um print (captura) da tela.", _p(), _a_print),
    "bloquear_tela": ("Bloqueia o computador (tela de senha).", _p(), _a_bloquear),
}

PROMPT = (
    "Você escolhe a AÇÃO que um assistente de voz no computador deve executar "
    "para o pedido do usuário (português do Brasil, transcrito da fala).\n"
    "Ações:\n{lista}\n\n"
    "Responda SÓ um JSON numa linha: {{\"acao\": \"<nome>\", \"args\": {{...}}}}.\n"
    "Use {{\"acao\": \"nenhuma\"}} quando: é conversa, pergunta de conhecimento, "
    "opinião; pede algo que nenhuma ação faz (fechar programa, desligar, apagar, "
    "arquivos, e-mail, mensagem); ou você não tem certeza. Na dúvida, nenhuma."
)

EXEMPLOS = [
    ("bota um som do legião urbana aí no meu spotify",
     {"acao": "spotify_buscar", "args": {"o_que": "legião urbana"}}),
    ("deixa o som na metade", {"acao": "volume_definir", "args": {"nivel": 50}}),
    ("me acorda daqui vinte minutos", {"acao": "timer",
                                       "args": {"quantidade": 20, "unidade": "minutos"}}),
    ("segura a música aí rapidinho", {"acao": "midia", "args": {"comando": "pausar_ou_tocar"}}),
    ("coloca de novo a anterior", {"acao": "midia", "args": {"comando": "anterior"}}),
    ("som no máximo", {"acao": "volume_definir", "args": {"nivel": 100}}),
    ("me conta uma curiosidade sobre o spotify", {"acao": "nenhuma"}),
    ("fecha o spotify", {"acao": "nenhuma"}),
    ("quanto espaço livre tem no disco c", {"acao": "nenhuma"}),
]


def _lista() -> str:
    linhas = []
    for nome, (desc, params, _f) in ACOES.items():
        ps = ", ".join(
            k + (" [opcional]" if k not in params.get("required", []) else "")
            + (" (" + "|".join(v["enum"]) + ")" if "enum" in v else "")
            for k, v in params["properties"].items())
        linhas.append(f"- {nome}({ps}): {desc}")
    return "\n".join(linhas)


def _mensagens(texto: str) -> list[dict]:
    msgs = [{"role": "system", "content": PROMPT.format(lista=_lista())}]
    for f, r in EXEMPLOS:
        msgs += [{"role": "user", "content": f},
                 {"role": "assistant", "content": json.dumps(r, ensure_ascii=False)}]
    msgs.append({"role": "user", "content": texto})
    return msgs


def escolher(llm, modelo: str, texto: str, extra: dict | None = None
             ) -> tuple[str, dict, float]:
    """-> (nome da ação | "nenhuma", args, segundos). Falha = "nenhuma"."""
    t0 = time.time()
    if destrutivo(texto):
        # Trava fixa, não depende do modelo: medido, o Qwen chegou a
        # transformar "Fecha o Chrome" em abrir(Chrome).
        return "nenhuma", {}, time.time() - t0
    try:
        r = llm.chat.completions.create(
            model=modelo, temperature=0, max_tokens=60,
            messages=_mensagens(texto), extra_body=extra or {})
        bruto = (r.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", bruto, re.S)
        d = json.loads(m.group(0)) if m else {}
    except Exception as e:  # noqa: BLE001
        print("  [acoes] falhou: %s" % str(e)[:90], flush=True)
        return "nenhuma", {}, time.time() - t0
    nome = str(d.get("acao") or "nenhuma")
    if nome not in ACOES:
        nome = "nenhuma"
    args = d.get("args") if isinstance(d.get("args"), dict) else {}
    return nome, args, time.time() - t0


def executar(nome: str, args: dict, executar: bool = True) -> C.Resultado | None:
    """Roda a ação escolhida. None = argumento inválido (segue ao juiz)."""
    if nome not in ACOES:
        return None
    try:
        return ACOES[nome][2](args, executar)
    except Exception as e:  # noqa: BLE001
        print("  [acoes] %s falhou: %s" % (nome, str(e)[:90]), flush=True)
        return None
