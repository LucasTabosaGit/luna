"""Palavra de ativação "Luna" (modo sempre ouvindo, estilo Alexa).

Como funciona: o microfone fica aberto, o Silero (VAD) corta cada fala e o
Whisper transcreve - como já acontece hoje. Com a ativação ligada, só segue
adiante a fala que contém "Luna"; o resto é descartado em silêncio.

Por que Whisper e não um detector dedicado (openWakeWord): o modelo que vem
com o Hermes foi treinado para "hey hermes" em inglês, e treinar um "Hermes"
em pt-BR exige gerar milhares de amostras. O Whisper já está carregado,
transcreve cada fala em ~0,2s na GPU e escreveu "Hermes" certo em 20/20
frases de teste, sem confundir com "hermética".

Depois de cada resposta fica uma janela de conversa (JANELA_S) em que não
precisa repetir "Luna": cada pedido dentro dela renova a janela, então
dá para emendar vários comandos seguidos. JANELA_S segundos sem falar
nada (contados do fim da resposta) e volta a esperar "Luna".
"Obrigado", "valeu", "só isso" fecham a conversa na hora.

Chamar só o nome ("Luna") NÃO gera resposta falada: responder "Oi?" por
cima de quem já está emendando o pedido atropela a fala. O nome sozinho
só abre a escuta por JANELA_NOME_S (a UI mostra "pode falar"), tempo
maior que JANELA_S porque aqui o usuário ainda vai formular o pedido.
"""
import re
import unicodedata

JANELA_S = 5.0
# Janela depois do nome falado sozinho (sem resposta falada avisando).
JANELA_NOME_S = 8.0

# Fala que só agradece/encerra, dentro da janela: fecha a conversa.
_ENCERRA = re.compile(
    r"^(ta |tá |ok |beleza |entao |então )?(obrigad[oa]( luna)?|valeu|brigad[oa]|"
    r"so isso|só isso|era so isso|era só isso|pode parar|pode dormir|tchau|"
    r"nada nao|nada não|deixa|esquece)[\s.!,]*$")


def encerra(texto: str) -> bool:
    return bool(_ENCERRA.match(texto.lower().strip(" .!,")))

# Nome da assistente. Formas como o Whisper escreve "Luna" falado em pt-BR,
# medidas em src/teste_luna_stt.py: "Luna", "Lona" (nome sozinho) e "Oluna"
# ("Ô Luna" colado). NÃO aceita "Lua" nem "Luana": aparecem em frases
# comuns ("a lua está cheia", "a Luana chegou") e disparariam sem querer.
NOME = "Luna"
_NOME = r"(?:o)?luna"
# Dica ao Whisper quando ninguém chamou ainda: com ela o nome sozinho
# sai "Luna." em vez de "Lona"/"Lê", sem inventar Luna em "lua"/"Luana".
DICA = NOME + "."
_RE = re.compile(r"(?:\b(?:ei|oi|ok|hey|e ai|ola|fala|o)[\s,]+)?\b" + _NOME + r"\b[\s,.!?:;]*")


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t)
                   if unicodedata.category(c) != "Mn")


def _norm(t: str) -> str:
    t = _sem_acento(t.lower())
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", t).split())


def eco_da_luna(ditas, texto: str, janela: float = 180.0) -> bool:
    """True se o texto ouvido é (quase) algo que a Luna falou há pouco.
    ditas = [(instante, texto falado), ...]. O eco da própria voz ("Vou
    ficar atenta.") era resgatado pelo detector como se fosse chamada."""
    import time
    n = _norm(texto)
    if not n:
        return False
    agora = time.time()
    for t, dita in ditas:
        if agora - t > janela:
            continue
        frases = [_norm(f) for f in re.split(r"[.!?]+", dita)] + [_norm(dita)]
        if n in frases or any(len(n) >= 8 and n in d for d in frases):
            return True
    return False


# Resgate de frase LONGA sem o nome (o Whisper sumiu com ele) só quando a
# frase soa como pedido ("Play no Spotify", "Abre o YouTube"). Conversa na
# sala ("Você não está passando, não tem um minuto") fica de fora.
_PEDIDO = re.compile(
    r"^(?:(?:e|ei|oi|o|ta|entao|agora|por favor)\s+)?(?:abr|toc|play|da play|"
    r"de play|paus|para a|parar|continua|volta|aument|diminu|baix|sob|coloc|"
    r"bot|pesquis|procur|busc|mostr|liga|desliga|fech|minimiz|maximiz|pul|"
    r"avanc|mud|troc|cri|marc|anot|lembr|me lembr|qual|quais|quant|quando|"
    r"onde|como|que horas|que dia|o que|quem|por que|pode|consegue|faz|"
    r"fala|diz|me diz|me fala|tir|salv|mand|envi|escrev|digit|abaix|"
    r"silenci|mut|next|proxim)")


def parece_pedido(texto: str) -> bool:
    return bool(_PEDIDO.match(_norm(texto)))


# Muleta/hesitação: nunca é pedido, na janela ou fora dela.
_MULETA = re.compile(
    r"^(e ai|e|ai|um+|hum+|ham+|ah+|eh+|oh+|ok|ta|tá|sim|nao|isso|beleza|blz|"
    r"cara|mano|nossa|vamos la|deixa eu ver|sei la|tipo|entao|pois e|"
    r"sim deixa eu ver|ta bom|tudo bem|certo|opa|oi|ei)$")


def vale_na_janela(texto: str, ditas=()) -> bool:
    """Na conversa contínua (sem dizer "Luna" de novo): só vale o que soa
    como pedido ou pergunta a ela. Som da caixa ("E aí", "Um...", "Sim,
    deixa eu ver.") e o eco da própria voz ficam de fora."""
    n = _norm(texto)
    if n and encerra(texto):
        return True          # "valeu", "obrigado": fecha a conversa
    if not n or _MULETA.match(n) or eco_da_luna(ditas, texto):
        return False
    if parece_pedido(texto):
        return True
    # Frase curta sem cara de pedido ("Mandei?", "Tá, tá?") é ruído; com 4+
    # palavras ("Vamos ver o filme amanhã à noite.") é a conversa seguindo.
    return len(n.split()) >= 4


def detectar(texto: str) -> tuple[bool, str]:
    """-> (chamou?, pedido sem o nome). "Luna, que horas são?" ->
    (True, "que horas são?"). "Luna." -> (True, "")."""
    base = _sem_acento(texto).lower()
    # "Lona" só vale como o nome falado SOZINHO (o Whisper escreve assim);
    # numa frase é a palavra lona ("cobre com a lona").
    if re.fullmatch(r"\W*(?:o\s+)?lona\W*", base):
        return True, ""
    m = _RE.search(base)
    if not m:
        return False, ""
    # Corta no texto ORIGINAL (mesmo tamanho: só tiramos acentos
    # combinantes, que o NFD separa - por isso comparamos em NFC abaixo).
    orig = unicodedata.normalize("NFC", texto)
    if len(orig) != len(base):
        orig = base
    # Nome no meio da frase ("a Luna do Harry Potter") faz parte do
    # pedido: só corta quando está no começo ou no fim.
    if m.start() > 0 and m.end() < len(base.rstrip(" .!?")):
        return True, texto.strip()
    resto = (orig[:m.start()] + " " + orig[m.end():]).strip(" ,.!;:")
    resto = re.sub(r"\s{2,}", " ", resto).strip()
    if resto:
        resto = resto[0].upper() + resto[1:]
    return True, resto


# Palavras que o Whisper escreve no lugar de "Luna" (medido no uso real:
# "Coluna, você consertou a skill?", "Lona", "Uma", "Lua", "Luana").
# Aparecer uma delas NO COMEÇO ou NO FIM da frase não basta para acordar -
# são palavras comuns -, mas faz o servidor perguntar ao detector de áudio
# (src/detector_luna.py) se foi o nome. No meio da frase é a palavra comum.
_PARECIDO = re.compile(
    r"^\W*(?:(?:ei|oi|o|e ai|fala)[\s,]+)?(coluna|lona|luana|lua|luma|lunna|una|uma|"
    r"louna|lu na|luna)\b[\s,.!?]*|[\s,]+(coluna|lona|luana|lua|luma|una)[\s.!?]*$")


def parece_nome(texto: str) -> bool:
    return bool(_PARECIDO.search(_sem_acento(texto).lower()))


def tirar_parecido(texto: str) -> str:
    """Tira a palavra parecida (o nome mal transcrito) do começo/fim."""
    base = _sem_acento(texto).lower()
    orig = unicodedata.normalize("NFC", texto)
    if len(orig) != len(base):
        orig = base
    m = _PARECIDO.search(base)
    if not m:
        return texto.strip()
    resto = (orig[:m.start()] + " " + orig[m.end():]).strip(" ,.!;:")
    resto = re.sub(r"\s{2,}", " ", resto).strip()
    return (resto[0].upper() + resto[1:]) if resto else ""


_NO_COMECO = re.compile(r"^\W*(?:(?:o|oh|ô|ei|oi|e ai|e aí|fala)[\s,]+)?o?luna\b")


def nome_no_comeco(texto: str) -> bool:
    """ "Luna, ..." / "Ô Luna ..." no começo: é chamada, não palavra inventada.

    O veto do detector só serve para "Luna" no MEIO/fim (onde a dica ao
    Whisper às vezes troca "lona" por "Luna").
    """
    return bool(_NO_COMECO.search(_sem_acento(texto).lower()))
