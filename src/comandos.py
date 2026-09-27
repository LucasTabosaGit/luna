"""Comandos prontos: pedidos comuns resolvidos sem LLM nem Hermes.

Mesma ideia do Home Assistant ("prefer handling commands locally"): a
frase transcrita passa primeiro por regras fixas; se casar, a ação roda
na hora (~0 s) e a resposta é curta. Se NÃO casar, `tentar()` devolve
None e a frase segue para o cérebro normal — por isso as regras são
ancoradas (^...$) e conservadoras: errar para "não reconheci" custa só
a latência de sempre; errar para o lado oposto executa a coisa errada.

Ações destrutivas (desligar, apagar, fechar programas) ficam de fora de
propósito: vão para o Hermes, que pode confirmar antes.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import json
import os
import re
import threading
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Resultado:
    nome: str                          # aparece no painel Atividade
    fala: str                          # o que o assistente responde
    depois: tuple[float, str] | None = None   # (segundos, fala) p/ timers
    especial: str | None = None        # tratado pelo servidor


# ----------------------------------------------------------------- texto

def normalizar(t: str) -> str:
    """Minúsculas, sem acento, sem pontuação (mantém números e + - * / %)."""
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s%,.+*/-]", " ", t)
    # Pontuação solta sai; ponto/vírgula DENTRO de palavra fica
    # ("1.500", "2,5", "exemplo.com.br").
    t = re.sub(r"[.,](?=\s|$)|(?<=\s)[.,]|^[.,]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # Vocativos e cortesias nas pontas, que o Whisper transcreve junto.
    for _ in range(3):
        t = re.sub(r"^(ei|oi|ola|ok|entao|luna|hermes|assistente|por favor|"
                   r"pode|poderia|voce pode|voce poderia|voce consegue|consegue|da pra|"
                   r"eu quero que voce|quero que voce|queria que voce)\s+",
                   "", t)
        t = re.sub(r"\s+(por favor|pra mim|para mim|ai)$", "", t)
    return t


_UNID = {
    "zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3,
    "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9,
    "dez": 10, "onze": 11, "doze": 12, "treze": 13, "catorze": 14,
    "quatorze": 14, "quinze": 15, "dezesseis": 16, "dezessete": 17,
    "dezoito": 18, "dezenove": 19, "vinte": 20, "trinta": 30,
    "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70,
    "oitenta": 80, "noventa": 90, "cem": 100, "cento": 100,
    "duzentos": 200, "trezentos": 300, "quatrocentos": 400,
    "quinhentos": 500, "mil": 1000, "meia": 0.5, "meio": 0.5,
}


def numero(s: str) -> float | None:
    """'25', '2,5', '1.500', 'vinte e cinco', 'meia' -> número."""
    s = s.strip()
    if re.fullmatch(r"-?[\d.,]+", s):
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")
        try:
            return float(s)
        except ValueError:
            return None
    total, visto = 0.0, False
    for p in s.split():
        if p == "e":
            continue
        if p == "mil":
            total = (total or 1) * 1000
        elif p in _UNID:
            total += _UNID[p]
        else:
            return None
        visto = True
    return total if visto else None


def fmt(n: float) -> str:
    """Número para fala: sem '.0', vírgula decimal."""
    if abs(n - round(n)) < 1e-9:
        return str(int(round(n)))
    return f"{n:.2f}".rstrip("0").rstrip(".").replace(".", ",")


# ------------------------------------------------- sistema (Windows ou Mac)
# Teclas, volume, apps: src/plataforma.py escolhe o jeito de cada sistema.
import plataforma as P  # noqa: E402

# Cidade do clima quando o pedido não diz qual. NÃO usar a localização do IP:
# com VPN/Cloudflare WARP ela muda de país a cada consulta (já saiu Buenos
# Aires, Monticello/EUA e Graça Torta). Troque aqui se mudar de cidade.
CIDADE_PADRAO = "Maceio"


def _atalho(nome, teclas, fala):
    """teclas: nomes de plataforma.teclas ("ctrl" vira Command no Mac)."""
    def f(_m, executar):
        if executar:
            P.teclas(*teclas)
        return Resultado(nome, fala)
    return f


def _abrir(alvo: str) -> None:
    P.abrir(alvo)                  # URL, pasta ou atalho conhecido


# ------------------------------------------------ apps (menu Iniciar / .app)

_APPS: dict[str, tuple[str, str]] = {}      # normalizado -> (nome, AppID ou .app)
_APPS_PRONTO = threading.Event()
_LIXO = re.compile(r"desinstal|uninstall|documenta|manual|help|ajuda|faq|"
                   r"release notes|modo de reparo|readme|website|leia-me")

# Como as pessoas chamam -> nome no menu Iniciar (no Mac, o equivalente:
# plataforma.nome_app troca "bloco de notas" por "textedit" etc.).
APELIDOS = {
    "chrome": "google chrome", "google chrome": "google chrome",
    "navegador": "google chrome", "o navegador": "google chrome",
    "edge": "microsoft edge", "firefox": "firefox",
    "bloco de notas": "bloco de notas", "notepad": "bloco de notas",
    "calculadora": "calculadora", "calendario": "calendario",
    "explorador": "explorador de arquivos", "explorer": "explorador de arquivos",
    "gerenciador de arquivos": "explorador de arquivos",
    "meus arquivos": "explorador de arquivos",
    "gerenciador de tarefas": "gerenciador de tarefas",
    "configuracoes": "configuracoes", "configuracao": "configuracoes",
    "painel de controle": "painel de controle",
    "vscode": "visual studio code", "vs code": "visual studio code",
    "visual studio code": "visual studio code", "code": "visual studio code",
    "terminal": "terminal", "prompt": "prompt de comando", "cmd": "prompt de comando",
    "powershell": "windows powershell", "word": "libreoffice writer",
    "excel": "libreoffice calc", "planilha": "libreoffice calc",
    "whatsapp": "whatsapp", "zap": "whatsapp", "spotify": "spotify",
    "discord": "discord", "telegram": "telegram", "obs": "obs studio (64bit)",
 # Como o Whisper costuma escrever nomes em inglês falados em pt-BR.
 "espotifai": "spotify", "spotifai": "spotify", "spotfy": "spotify",
 "crome": "google chrome", "cromi": "google chrome", "gugou chrome": "google chrome",
 "discordi": "discord", "zapi": "whatsapp", "uatsap": "whatsapp", "watsapp": "whatsapp",
 "vis code": "visual studio code", "vi es code": "visual studio code",
 "ele eme studio": "lm studio", "telegrama": "telegram", "estim": "steam",
    "lm studio": "lm studio", "paint": "paint", "camera": "camera",
    "fotos": "fotos", "loja": "microsoft store", "steam": "steam",
}
APELIDOS = {k: P.nome_app(v) for k, v in APELIDOS.items()}    # Mac: textedit, finder...


def _carregar_apps() -> None:
    try:
        for nome, como in P.listar_apps():
            chave = normalizar(nome)
            if chave and como and not _LIXO.search(chave):
                _APPS.setdefault(chave, (nome, como))
    except Exception as e:  # noqa: BLE001
        print("  [comandos] lista de apps falhou: %s" % str(e)[:100], flush=True)
    finally:
        _APPS_PRONTO.set()


threading.Thread(target=_carregar_apps, daemon=True).start()


def achar_app(pedido: str) -> tuple[str, str] | None:
    """'o chrome' -> ('Google Chrome', AppID). None se não tiver certeza."""
    _APPS_PRONTO.wait(15)
    q = re.sub(r"^(o|a|os|as|um|uma|meu|minha|aplicativo|app|programa)\s+",
               "", pedido).strip()
    q = re.sub(r"^(aplicativo|app|programa)( do| da| de)?\s+", "", q)
    if not q or len(q.split()) > 4:
        return None
    alvo = APELIDOS.get(q, q)
    if alvo in _APPS:
        return _APPS[alvo]
    # Todas as palavras do pedido no nome; o nome mais curto ganha
    # ("chrome" -> "Google Chrome", não "Chrome Remote Desktop").
    pal = alvo.split()
    cands = [k for k in _APPS if all(p in k.split() for p in pal)]
    if cands:
        return _APPS[min(cands, key=len)]
    perto = difflib.get_close_matches(alvo, list(_APPS), n=1, cutoff=0.82)
    if perto:
        return _APPS[perto[0]]
    # Erro de transcrição num nome curto ("crome", "discordi", "espotifai"):
    # compara com os APELIDOS e com cada palavra dos nomes, com corte
    # alto (0.8) para não abrir o programa errado.
    if len(pal) == 1 and len(alvo) >= 4:
        apel = difflib.get_close_matches(alvo, list(APELIDOS), n=1, cutoff=0.8)
        if apel and APELIDOS[apel[0]] in _APPS:
            return _APPS[APELIDOS[apel[0]]]
        palavras = {p: k for k in sorted(_APPS, key=len, reverse=True)
                    for p in k.split() if len(p) >= 4}
        perto = difflib.get_close_matches(alvo, list(palavras), n=1, cutoff=0.8)
        if perto:
            return _APPS[palavras[perto[0]]]
    return None


# ------------------------------------------------------------------ dados

SITES = {
    "youtube": "https://www.youtube.com", "google": "https://www.google.com",
    "gmail": "https://mail.google.com", "email": "https://mail.google.com",
    "whatsapp web": "https://web.whatsapp.com", "netflix": "https://www.netflix.com",
    # Prime Video: o Whisper varia bastante a grafia falada.
    "prime video": "https://www.primevideo.com", "prime": "https://www.primevideo.com",
    "primevideo": "https://www.primevideo.com",
    "praime video": "https://www.primevideo.com",
    "amazon prime": "https://www.primevideo.com",
    "amazon prime video": "https://www.primevideo.com",
    "instagram": "https://www.instagram.com", "facebook": "https://www.facebook.com",
    "twitter": "https://x.com", "github": "https://github.com",
    "chatgpt": "https://chatgpt.com", "mercado livre": "https://www.mercadolivre.com.br",
    "shopee": "https://shopee.com.br", "amazon": "https://www.amazon.com.br",
    "twitch": "https://www.twitch.tv", "linkedin": "https://www.linkedin.com",
    "google drive": "https://drive.google.com", "drive": "https://drive.google.com",
    "maps": "https://maps.google.com", "google maps": "https://maps.google.com",
    "tradutor": "https://translate.google.com",
}
# Sites só seus ("meu site" -> ...): pessoal/sites.json, fora da versão pública.
try:
    SITES.update(json.loads((Path(__file__).resolve().parent.parent / "pessoal" / "sites.json")
                            .read_text(encoding="utf-8")))
except (OSError, ValueError):
    pass

_HOME = Path.home()
PASTAS = {
    "downloads": _HOME / "Downloads", "download": _HOME / "Downloads",
    "documentos": _HOME / "Documents", "area de trabalho": _HOME / "Desktop",
    "imagens": _HOME / "Pictures", "fotos": _HOME / "Pictures",
    "musicas": _HOME / "Music", "videos": _HOME / "Videos",
    "projeto": Path(__file__).resolve().parent.parent,
}
# Nome falado (com acento) de cada pasta.
FALA_PASTA = {"downloads": "os downloads", "download": "os downloads",
              "documentos": "os documentos", "area de trabalho": "a área de trabalho",
              "imagens": "as imagens", "musicas": "as músicas",
              "videos": "os vídeos", "projeto": "o projeto"}

_DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
         "sexta-feira", "sábado", "domingo"]
_MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
          "agosto", "setembro", "outubro", "novembro", "dezembro"]


# --------------------------------------------------------------- comandos

_ABRE = r"(abre|abra|abrir|abri|inicia|inicie|iniciar|executa|execute|executar|entra|entre|entrar)"
_ART = r"(?:(?:o|a|os|as|no|na|em|um|uma)\s+)?"


def _hora(_m, agora=None):
    a = agora or _dt.datetime.now()
    if a.minute == 0:
        return Resultado("hora", f"São {a.hour} horas.")
    return Resultado("hora", f"São {a.hour} e {a.minute:02d}.")


def _data(_m, agora=None):
    a = agora or _dt.datetime.now()
    return Resultado("data", f"Hoje é {_DIAS[a.weekday()]}, {a.day} de "
                             f"{_MESES[a.month - 1]}.")


def _vol_definir(m, executar):
    alvo = m.group("n").strip()
    alvo = {"maximo": "100", "o maximo": "100", "minimo": "5", "o minimo": "5",
            "metade": "50", "a metade": "50"}.get(alvo, alvo)
    n = numero(alvo)
    if n is None or not 0 <= n <= 100:
        return None
    if executar:
        P.volume_mudo(False)
        P.volume_definir(n)
    return Resultado("volume", f"Volume em {fmt(n)} por cento.")


def _vol_passo(m, executar, sinal):
    passo = 10.0
    if m.groupdict().get("n"):
        n = numero(m.group("n"))
        if n is None:
            return None
        if m.group("prep") in ("para", "pra"):   # "aumenta PARA 50" = definir
            return _vol_definir(re.match(r"(?P<n>.+)", fmt(n)), executar)
        passo = n
    novo = 50.0
    if executar:
        P.volume_mudo(False)
        atual, _ = P.volume_ler()
        novo = max(0.0, min(100.0, atual + sinal * passo))
        P.volume_definir(novo)
    return Resultado("volume", f"Volume em {fmt(round(novo))} por cento.")


def _mudo(_m, executar, ligar):
    if executar:
        P.volume_mudo(ligar)
    return Resultado("volume", "Pronto, sem som." if ligar else "Som de volta.")


def _midia(nome, tecla, fala):
    def f(_m, executar):
        if executar:
            P.midia(tecla)
        return Resultado(nome, fala)
    return f


def _timer(m, _executar):
    n = numero(m.group("n"))
    if n is None or n <= 0:
        return None
    u = m.group("u")
    seg = n * (3600 if u.startswith("hora") else 60 if u.startswith("min") else 1)
    if seg > 24 * 3600:
        return None
    if u.startswith("hora"):
        dur = "meia hora" if n == 0.5 else f"{fmt(n)} hora{'s' if n > 1 else ''}"
    elif u.startswith("min"):
        dur = f"{fmt(n)} minuto{'s' if n != 1 else ''}"
    else:
        dur = f"{fmt(n)} segundo{'s' if n != 1 else ''}"
    motivo = (m.groupdict().get("o") or "").strip()
    fala_fim = (f"Ei, deu o tempo: {motivo}." if motivo
                else f"Ei, o timer de {dur} acabou.")
    return Resultado("timer", f"Timer de {dur} ligado.", depois=(seg, fala_fim))


def _conta(m, _executar):
    a, b = numero(m.group("a")), numero(m.group("b"))
    if a is None or b is None:
        return None
    op = m.group("op")
    if op in ("mais", "+"):
        r = a + b
    elif op in ("menos", "-"):
        r = a - b
    elif op in ("vezes", "x", "*", "multiplicado por"):
        r = a * b
    else:
        if b == 0:
            return Resultado("conta", "Não dá para dividir por zero.")
        r = a / b
    return Resultado("conta", f"Dá {fmt(round(r, 4))}.")


def _porcento(m, _executar):
    a, b = numero(m.group("a")), numero(m.group("b"))
    if a is None or b is None:
        return None
    return Resultado("conta", f"Dá {fmt(round(a * b / 100, 4))}.")


def _clima(m, executar):
    cidade = (m.groupdict().get("c") or "").strip()
    if re.fullmatch(r"(minha|nossa|essa|esta|aqui|sua)( cidade| regiao| area)?|cidade|casa", cidade):
        cidade = ""
    cidade = cidade or os.environ.get("CIDADE", "") or CIDADE_PADRAO
    if not executar:
        return Resultado("clima", "(previsão)")
    url = "https://wttr.in/%s?format=j1&lang=pt" % urllib.parse.quote(cidade)
    try:
        with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310
            d = json.load(r)
    except Exception:  # noqa: BLE001
        return None     # sem internet: deixa o cérebro responder
    c = d["current_condition"][0]
    hoje = d["weather"][0]
    lugar = cidade.title() if cidade else d["nearest_area"][0]["areaName"][0]["value"]
    desc = (c.get("lang_pt") or c.get("weatherDesc") or [{"value": ""}])[0]["value"]
    chuva = max(int(h.get("chanceofrain", 0)) for h in hoje.get("hourly", [{}]))
    fala = (f"Agora faz {c['temp_C']} graus em {lugar}, {desc.lower()}. "
            f"Máxima de {hoje['maxtempC']} e mínima de {hoje['mintempC']}")
    fala += f", com {chuva} por cento de chance de chuva." if chuva >= 30 else "."
    return Resultado("clima", fala)


def _spotify(m, executar):
    """Abre a busca no app do Spotify (URI spotify:search:...). Não dá para
    dar play sem a API web (conta + OAuth); a busca já cai no resultado."""
    q = m.group("q").strip()
    q = re.sub(r"^(musica|musicas|playlist|album|a playlist|o album|a musica)( de| do| da)?\s+", "", q) or q
    if executar:
        _abrir("spotify:search:" + urllib.parse.quote(q))
    return Resultado("spotify", "Abrindo no Spotify.")


def _youtube(m, executar):
    q = m.group("q").strip()
    if executar:
        _abrir("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q))
    # Não repete o termo: ele vem sem acento e a voz leria errado.
    return Resultado("youtube", "Abrindo no YouTube.")


def _google(m, executar):
    q = m.group("q").strip()
    if executar:
        _abrir("https://www.google.com/search?q=" + urllib.parse.quote(q))
    return Resultado("pesquisa", "Pesquisando no Google.")


def _area_trabalho(_m, executar):
    if executar:
        P.mostrar_area_de_trabalho()
    return Resultado("janelas", "Pronto.")


def _print(_m, executar):
    if executar:
        P.print_da_tela(PASTAS["imagens"] / "Capturas de Tela")
    return Resultado("print", "Print salvo na pasta Imagens.")


def _bloquear(_m, executar):
    if executar:
        P.bloquear_tela()
    return Resultado("bloquear", "Bloqueando.")


def _abrir_algo(m, executar):
    """'abre X': pasta conhecida, app do Iniciar, site conhecido ou domínio."""
    alvo = m.group("x").strip()
    sem_art = re.sub(r"^(o|a|os|as|no|na|pasta|pasta de|pasta do|pasta da|site|site do|site da)\s+",
                     "", alvo)
    sem_art = re.sub(r"^(pasta|site)( de| do| da)?\s+", "", sem_art)
    if alvo.startswith(("pasta", "a pasta")) or sem_art in PASTAS and sem_art != "fotos":
        p = PASTAS.get(sem_art)
        if p is None:
            return None
        if executar:
            _abrir(str(p))
        return Resultado("pasta", f"Abrindo {FALA_PASTA.get(sem_art, sem_art)}.")
    if sem_art in SITES and not (sem_art in ("whatsapp", "spotify")):
        if executar:
            _abrir(SITES[sem_art])
        return Resultado("site", "Abrindo.")
    if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.(com|br|net|org|io|dev|app|ai|tv)(\.br)?", sem_art):
        if executar:
            _abrir("https://" + sem_art)
        return Resultado("site", "Abrindo o site.")
    app = achar_app(alvo)
    if app is None:
        return None
    nome, como = app
    if executar:
        P.abrir_app(como)
    return Resultado("abrir", f"Abrindo {nome}.")


_N = r"(?P<n>[\d.,]+|[a-z]+(?: e [a-z]+)*)"
_UNIDADE = r"(?P<u>segundos?|minutos?|horas?)"
# Sufixo opcional "no Spotify / no YouTube / aí": as teclas multimídia já agem
# no player que está tocando, então dizer onde não muda a ação.
_PLAYER = (r"(?:spotify|espotifai|spotifai|spotfy|youtube|yotube|iutube|"
           r"navegador|chrome|player)")
# Aceita "no Spotify", "do Spotify", "aí no Spotify" e o app solto
# ("pausa o Spotify", "pausa a música do Spotify").
_ONDE = (r"(?: (?:no|na|do|da|em|ai no|ai na|ai do|ai da|la no|la na|la do|la da) ?"
         + _PLAYER + r"| " + _PLAYER + r"| ai| la)?")

# O que ela sabe fazer sem IA. É a lista que ela fala quando perguntam
# "o que você sabe fazer?". teste_comandos.py confere que CADA exemplo daqui
# cai mesmo no comando indicado: a lista nunca promete o que não existe.
CATALOGO: list[tuple[str, str, list[str]]] = [
    ("Hora e data", "hora", ["Que horas são?", "Que dia é hoje?"]),
    ("Volume", "volume", ["Aumenta o volume", "Volume em 30", "Coloca no mudo"]),
    ("Música e vídeo", "mídia", ["Pausa", "Pula essa", "Volta a música"]),
    ("Spotify", "spotify", ["Toca Legião Urbana no Spotify"]),
    ("YouTube e Google", "youtube", ["Toca Legião Urbana no YouTube", "Pesquisa receita de bolo no Google"]),
    ("Timers e lembretes", "timer", ["Timer de 5 minutos", "Me lembra de tirar o bolo em 40 minutos", "Cancela o timer"]),
    ("Contas", "conta", ["Quanto é 25 vezes 4?", "Quanto é 15% de 200?"]),
    ("Clima", "clima", ["Como está o tempo?", "Vai chover hoje?"]),
    ("Abrir programas, sites e pastas", "abrir", ["Abre o Chrome", "Abre o YouTube", "Abre a pasta downloads"]),
    ("Abas do navegador", "navegador", ["Abre uma aba nova", "Fecha essa aba"]),
    ("Janelas e tela", "janelas", ["Mostra a área de trabalho", "Tira um print", "Bloqueia o computador"]),
]


def _ajuda(_m, _executar):
    # A lista completa é montada pelo servidor (junta atalhos aprendidos e o
    # que está conectado); aqui só a fala curta.
    return Resultado("ajuda", "", especial="ajuda")


# Ordem importa: as mais específicas primeiro.
REGRAS: list[tuple[re.Pattern, object]] = [(re.compile(p), f) for p, f in [
    # --- ajuda: a lista real do que ela faz (CATALOGO + atalhos)
    (r"^((me )?(diz|fala|explica|conta|mostra)( pra mim| para mim)? )?"
     r"(o que|que coisas|quais coisas|oque) (voce|vc|tu)? ?(sabe|consegue|pode) fazer( por mim| aqui)?$|"
     r"^(quais (sao )?(os )?(seus )?comandos|lista de comandos|(me )?mostra (os )?(seus )?comandos|"
     r"ajuda|me ajuda|como (eu )?(te )?uso|como funciona( voce)?)$", _ajuda),
    # --- hora e data
    (r"^(que horas (sao|e)|que hora e|me (diz|fala) (as|que) horas( sao)?|"
     r"horas|hora certa|sabe que horas sao|voce sabe que horas sao)( agora)?$",
     lambda m, e: _hora(m)),
    (r"^(que dia (e|eh) hoje|hoje e que dia|qual (e )?a data( de hoje)?|"
     r"data de hoje|que data e hoje|em que dia estamos|que dia da semana e hoje)$",
     lambda m, e: _data(m)),
    # --- volume
    (r"^(silencia|silenciar|muta|mutar|tira o som|desliga o som|corta o som|"
     r"(coloca|poe|deixa|bota) (no|em) mudo|mudo|modo silencioso)$",
     lambda m, e: _mudo(m, e, True)),
    (r"^(desmuta|desmutar|tira do mudo|liga o som|volta o som|ativa o som)$",
     lambda m, e: _mudo(m, e, False)),
    (r"^(aumenta|aumente|aumentar|sobe|suba|subir)( o)? (volume|som)"
     r"(?: (?P<prep>em|para|pra) " + _N + r")?( por cento|%)?( um pouco)?$",
     lambda m, e: _vol_passo(m, e, +1)),
    (r"^(abaixa|abaixe|abaixar|baixa|baixe|baixar|diminui|diminua|diminuir|reduz|reduza)"
     r"( o)? (volume|som)(?: (?P<prep>em|para|pra) " + _N + r")?( por cento|%)?( um pouco)?$",
     lambda m, e: _vol_passo(m, e, -1)),
    (r"^(sobe|aumenta|coloca|poe|bota|deixa)( o)? (volume|som) (no talo|no maximo|no ultimo|todo)$",
     lambda m, e: _vol_definir(re.match(r"(?P<n>.+)", "100"), e)),
    (r"^(mais alto|fala mais alto|mais volume)$", lambda m, e: _vol_passo(m, e, +1)),
    (r"^(mais baixo|menos volume)$", lambda m, e: _vol_passo(m, e, -1)),
    (r"^((coloca|poe|deixa|muda|ajusta|bota)( o)? )?volume (em |no |para |pra |a )?"
     r"(?P<n>maximo|o maximo|minimo|o minimo|metade|a metade|[\d.,]+|[a-z]+(?: e [a-z]+)*)"
     r"( por cento|%)?$",
     _vol_definir),
    # --- mídia (teclas multimídia: valem para Spotify, YouTube, etc.)
    # `_ONDE` deixa dizer onde ("da play no Spotify") sem virar busca no app.
    (r"^(pausa|pause|pausar|despausa|continua|continuar|retoma|retomar|play|"
     r"da play|de play|da o play|manda play|solta)( a| o)?( musica| video| som| midia)?"
     + _ONDE + r"$",
     _midia("mídia", "play", "Pronto.")),
    (r"^(toca|tocar)( a| o)? (musica|video)" + _ONDE + r"$",
     _midia("mídia", "play", "Pronto.")),
    (r"^((proxima|proximo|pula|pular|passa|avanca)( a| o| essa| esse)?"
     r"( musica| faixa| video)?|musica seguinte)" + _ONDE + r"$",
     _midia("mídia", "proxima", "Próxima.")),
    (r"^((volta|voltar)( a| o| pra| para)? (musica|faixa|video)( anterior)?|"
     r"(musica|faixa) anterior|anterior)" + _ONDE + r"$",
     _midia("mídia", "anterior", "Voltando.")),
    # --- timers
    (r"^(cancela|cancelar|cancele|para|pare|desliga|apaga)( o| os| a| as)? "
     r"(timer|timers|temporizador|temporizadores|alarme|alarmes)$",
     lambda m, e: Resultado("timer", "", especial="cancelar_timers")),
    (r"^((coloca|cria|liga|inicia|define|faz|faca|bota|poe) )?(um |uma )?"
     r"(timer|temporizador|alarme|cronometro) (de |para |pra |em )?" + _N + r" " + _UNIDADE +
     r"(?: (de|para|pra) (?P<o>.+))?$",
     _timer),
    (r"^(me )?(avisa|avise|lembra|lembre|chama|chame)( me)? (em|daqui a|daqui|dentro de) "
     + _N + r" " + _UNIDADE + r"(?: (de|para|pra|que) (?P<o>.+))?$",
     _timer),
    (r"^(me )?(avisa|avise|lembra|lembre)( me)? (de|para|pra) (?P<o>.+?) (em|daqui a|daqui|dentro de) "
     + _N + r" " + _UNIDADE + r"$",
     _timer),
    # --- contas
    (r"^(quanto (e|eh|da|sao|que e|que da)|calcula|calcule|faz a conta( de)?) "
     r"(?P<a>.+?) (?P<op>mais|menos|vezes|x|\*|\+|-|/|multiplicado por|dividido por|divido por|sobre) "
     r"(?P<b>.+?)$",
     _conta),
    (r"^(quanto (e|eh|da|sao)|calcula|calcule) (?P<a>.+?)( por cento|%) de (?P<b>.+?)$",
     _porcento),
    # --- clima
    (r"^(como (esta|ta|vai estar|fica) o (tempo|clima)|qual (e )?a (previsao|temperatura)( do tempo)?|"
     r"(olha|ve|veja|verifica|confere|checa|me (diz|fala))( a| o)? (temperatura|previsao( do tempo)?|clima|tempo)|"
     r"quantos graus (esta|ta|faz)( fazendo)?|ta (frio|calor)( la fora)?|esta (frio|calor)( la fora)?|"
     r"previsao do tempo|vai chover|ta chovendo|esta chovendo|temperatura|clima|tempo)"
     r"( hoje| agora| la fora| de hoje)?(?: (em|no|na|da|do|de|para|pra) (?P<c>[a-z ]+?))?( hoje| agora| la fora)?$",
     _clima),
    # --- Spotify: "toca X no Spotify" busca e abre no app
    (r"^(toca|tocar|toque|coloca|colocar|poe|bota|abre|abra|procura|busca)( pra tocar| para tocar)?"
     r"( a| o| uma| um)? (?P<q>.+?) no( meu)? (spotify|espotifai|spotifai)( ai| la)?$", _spotify),
    # --- abas do navegador (teclas no navegador que estiver na frente)
    (r"^(abre|abra|abrir|cria|crie|nova)( uma)? (nova )?(guia|aba)( nova)?( no (chrome|navegador|edge))?$",
     _atalho("navegador", ("ctrl", "t"), "Pronto.")),
    (r"^(fecha|feche|fechar)( essa| esta| a)? (guia|aba)$",
     _atalho("navegador", ("ctrl", "w"), "Pronto.")),
    (r"^(reabre|reabra|reabrir|volta|recupera)( a)? (guia|aba)( que fechei| fechada)?$",
     _atalho("navegador", ("ctrl", "shift", "t"), "Pronto.")),
    # --- youtube e pesquisa (termo explícito: "no youtube", "no google")
    (r"^(toca|tocar|coloca|poe|bota|abre|abra|procura|procure|pesquisa|pesquise|busca|busque)"
     r"( a| o)? (?P<q>.+?) no youtube$", _youtube),
    (r"^(pesquisa|pesquise|procura|procure|busca|busque) no youtube( por| sobre)? (?P<q>.+)$", _youtube),
    (r"^(pesquisa|pesquise|pesquisar|procura|procure|busca|busque|joga)( por| sobre)? (?P<q>.+?) "
     r"(no google|na internet|na web)$", _google),
    (r"^(pesquisa|pesquise|procura|procure|busca|busque|joga) (no google|na internet|na web)"
     r"( por| sobre)? (?P<q>.+)$", _google),
    # --- janelas e sistema
    (r"^((mostra|mostrar|vai para|vai pra|ir para)( a)? area de trabalho|"
     r"minimiza (tudo|todas as janelas)|minimizar tudo)$", _area_trabalho),
    (r"^(tira|tirar|faz|fazer|bate|bater)( um| uma)? (print|printscreen|captura( de tela)?|screenshot)"
     r"( da tela)?$", _print),
    (r"^(bloqueia|bloquear|trava|travar|bloqueie)( a| o)? (tela|computador|pc)$", _bloquear),
    # --- abrir app / site / pasta (por último: é o mais genérico)
    (r"^" + _ABRE + r" (?P<x>.+)$", _abrir_algo),
]]


def _atalho_skill(t: str, executar: bool, original: str = "") -> Resultado | None:
    """Skill com script próprio (atalhos.json): roda direto, sem o Claude."""
    try:
        import atalhos
    except Exception:  # noqa: BLE001
        return None
    achou = atalhos.casar(t, original)
    if achou is None:
        return None
    a, g = achou
    if not executar:
        return Resultado("atalho:" + a["nome"], "(atalho %s %s)" % (a["nome"], g))
    ok, fala = atalhos.rodar(a, g)
    return Resultado("atalho:" + a["nome"], fala)


def tentar(texto: str, executar: bool = True) -> Resultado | None:
    """Resolve `texto` como comando pronto ou devolve None."""
    t = normalizar(texto)
    if not t or len(t) > 90:
        return None
    # Atalhos de skill primeiro: "abre a Netflix e pesquisa terror" não pode
    # cair no "abre X" genérico (fazia só a primeira metade).
    r = _atalho_skill(t, executar, texto)
    if r is not None:
        return r
    for padrao, f in REGRAS:
        m = padrao.match(t)
        if m:
            try:
                r = f(m, executar)
            except Exception as e:  # noqa: BLE001
                print("  [comandos] %s falhou: %s" % (padrao.pattern[:30], e), flush=True)
                return None
            if r is not None:
                return r
    return None
