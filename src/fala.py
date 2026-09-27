"""TTS: texto → fala com Kokoro-82M, em streaming por frase.

O que define a latência percebida não é a velocidade total da síntese, e
sim o tempo até a PRIMEIRA palavra sair. Por isso o texto do LLM é
cortado em frases e cada uma é sintetizada e tocada assim que fica
pronta, enquanto o modelo ainda escreve o resto.

Kokoro-82M: 82 milhões de parâmetros, Apache 2.0, roda muito acima do
tempo real na GPU e tem vozes pt-BR nativas (pf_dora, pm_alex,
pm_santa). Alternativa local mais leve: piper. Mais expressiva e mais
pesada: Orpheus (3B).
"""
from __future__ import annotations

import queue
import re
import threading
import time
from typing import Iterator, Optional

import numpy as np
import sounddevice as sd

import config

# Corta em pontuação forte. Mantém o delimitador para a prosódia não
# ficar plana ("chegou" vs "chegou?").
_FIM_FRASE = re.compile(r"(?<=[.!?…:;])\s+|(?<=\n)")

# Corte de emergência: vírgula, ponto-e-vírgula ou conectivo. Só é usado
# quando a primeira frase está demorando a fechar.
_PAUSA_FRACA = re.compile(r"(?<=,)\s+|\s+(?=(?:que|porque|mas|e|ou|para|"
                          r"quando|onde|como)\s)", re.I)

# Acima disto, esperar o ponto final custa mais do que cortar numa
# vírgula. Medido: uma resposta de 104 chars numa frase só levou a
# latência de 0,65s para 3,94s, porque o TTS só começava quando o LLM
# terminava de escrever tudo.
#
# Subido de 70 para 110: cortar cedo demais produz FRAGMENTOS, e cada
# fragmento ganha entonação de fim de frase no lugar errado — medido,
# a frase inteira dura 3,9s e os dois pedaços somam 4,4s, com pausa
# artificial no meio. 110 ainda protege a latência sem picotar tanto.
PRIMEIRA_FRASE_MAX = 110


def frases(fluxo: Iterator[str], min_chars: int = None) -> Iterator[str]:
    """Junta deltas do LLM e emite frases completas.

    A primeira frase sai com um limite menor: quanto antes o áudio
    começar, menor a latência percebida. As seguintes podem ser maiores
    porque já estamos falando enquanto elas chegam.

    Se a primeira frase passar de PRIMEIRA_FRASE_MAX sem encontrar
    pontuação forte, corta numa vírgula ou conectivo — falar meia frase
    no tempo certo é melhor que a frase inteira quatro segundos depois.
    """
    min_chars = min_chars or config.FRASE_MIN_CHARS
    buf = ""
    primeira = True
    for pedaco in fluxo:
        buf += pedaco

        # corte de emergência, só enquanto a primeira frase não saiu
        if primeira and len(buf) >= PRIMEIRA_FRASE_MAX and not _FIM_FRASE.search(buf):
            m = None
            for cand in _PAUSA_FRACA.finditer(buf):
                if cand.end() >= min_chars:
                    m = cand
                    break
            if m:
                frase, buf = buf[:m.end()].strip(), buf[m.end():]
                if frase:
                    yield frase
                    primeira = False

        while True:
            m = _FIM_FRASE.search(buf)
            if not m:
                break
            frase, resto = buf[:m.end()].strip(), buf[m.end():]
            # A primeira frase sai com um mínimo bem menor: "Bom dia!"
            # deve ir para o TTS na hora, não esperar a frase seguinte.
            limite = config.PRIMEIRA_MIN if primeira else min_chars * 2
            if len(frase) < limite and resto:
                break
            if frase:
                yield frase
                primeira = False
            buf = resto
    if buf.strip():
        yield buf.strip()


# Símbolos que a voz leria pelo nome ("seta para a direita", "marcador").
_SIMBOLOS = {"→": ", ", "←": ", ", "⇒": ", ", "•": ", ", "·": ", ", "…": "...",
             "✓": "", "✔": "", "✗": "", "✘": ""}


def limpar(texto: str, pronuncia: bool = True) -> str:
    """Remove o que não se fala: markdown, emoji, blocos de raciocínio.

    As respostas do Claude vêm em markdown para a tela; na voz, "**",
    "- ", "|---|" e links viravam "asterisco", "hífen"... A tela continua
    recebendo o texto original: isto vale só para o que vai ser falado.
    """
    texto = re.sub(r"<think>.*?</think>", " ", texto, flags=re.S | re.I)
    texto = re.sub(r"```.*?(```|$)", " ", texto, flags=re.S)       # bloco de código
    texto = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", texto)        # [texto](link)
    texto = re.sub(r"https?://([^/\s]+)\S*", r"\1", texto)          # link solto: só o site
    texto = re.sub(r"(?m)^\s*\|?\s*:?-{3,}.*$", " ", texto)         # |---|---| e ---
    texto = re.sub(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+", "", texto)     # marcadores de lista
    texto = re.sub(r"(?m)\s*\|\s*$", ".", texto)                    # fim de linha de tabela
    texto = texto.replace("|", ", ")
    texto = re.sub(r"(?<=\d)\s*[*×]\s*(?=\d)", " vezes ", texto)       # 2*3
    texto = re.sub(r"~~|[*_`#>]+", " ", texto)
    for s, t in _SIMBOLOS.items():
        texto = texto.replace(s, t)
    texto = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", " ", texto)
    # Quebra de linha sem pontuação vira pausa (item de lista, título).
    texto = re.sub(r"(?<![.!?:;,])[ \t]*\n+", ". ", texto)
    if pronuncia:                  # conserto do espeak (Kokoro); o Edge não precisa
        texto = _corrigir_pronuncia(texto)
    texto = re.sub(r"\s+", " ", texto)
    texto = re.sub(r"\s+([.,!?:;])", r"\1", texto)
    texto = re.sub(r"([.,:;])(?:\s*[.,])+", r"\1", texto)           # ". ." / ", ."
    texto = re.sub(r"^[.,;:\s]+", "", texto).rstrip(" ,")
    return "" if not texto.strip(" .") else texto


# Vogal acentuada SOZINHA faz o espeak soletrar o nome do acento:
#
#     "é"  ->  ˌɛaɡˈudʊ          ("e agudo")
#     "à"  ->  ˌaɡrˈavi          ("a grave")
#     "ê"  ->  ˌɛsirkũŋflˈɛksʊ   ("e circunflexo")
#
# Medido com o próprio g2p. Dentro de uma frase o acento funciona
# normalmente ("Isso é bom" → ˈisw ɛ bˈoŋ), então o defeito é só na
# palavra isolada — que é justamente o caso de "é" como verbo.
#
# A correção troca a vogal isolada por uma grafia que o espeak
# pronuncia certo, sem mudar o texto que aparece na tela.
_SOZINHAS = {
    "é": "eh", "É": "Eh",
    "à": "a", "À": "A",
    "ê": "e", "Ê": "E",
    "ô": "o", "Ô": "O",
    "â": "a", "Â": "A",
    "ã": "an", "Ã": "An",
}
_RX_SOZINHA = re.compile(
    r"(?<![\wÀ-ÿ])([éÉàÀêÊôÔâÂãÃ])(?![\wÀ-ÿ])")


def _corrigir_pronuncia(texto: str) -> str:
    """Conserta casos em que o espeak-ng erra a pronúncia em pt-BR."""
    return _RX_SOZINHA.sub(lambda m: _SOZINHAS.get(m.group(1), m.group(1)),
                           texto)


class Voz:
    def __init__(self) -> None:
        self._pipe = None
        self._fila: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._parar = threading.Event()
        self.primeiro_audio_s: Optional[float] = None

    # ------------------------------------------------------------------
    def carregar(self) -> None:
        if self._pipe is not None:
            return
        import ambiente  # corrige espeak-ng e SSL no Windows

        from kokoro import KPipeline

        # `import kokoro` puxa misaki, que reaponta o espeak para o
        # caminho com acento no nível do módulo. Sem reaplicar aqui, o
        # KPipeline recusa "pt-br".
        ambiente.reaplicar_espeak()

        # lang_code 'p' = português brasileiro no Kokoro.
        self._pipe = KPipeline(lang_code="p")

        # Aquecimento: a PRIMEIRA síntese com uma voz baixa e carrega o
        # tensor dessa voz, e custa ~17s — o que na conversa apareceria
        # como um silêncio enorme antes da primeira resposta. Medido:
        # 1a chamada 0,2x tempo real, 3a chamada 30x. Pagar isso aqui,
        # junto dos outros modelos, deixa a conversa uniforme.
        # A voz padrão pode ser do Gemini/Edge ("gemini:Leda"): aí o
        # Kokoro é a RESERVA, e é ela que precisa estar aquecida (antes a
        # chamada falhava calada e a 1ª frase na reserva levava ~13 s).
        voz_kokoro = (config.TTS_VOZ if ":" not in config.TTS_VOZ
                      else config.TTS_VOZ_RESERVA)
        try:
            for _ in self._pipe(
                "Pronto.", voice=voz_kokoro, speed=config.TTS_VELOCIDADE
            ):
                pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _tocar_loop(self) -> None:
        """Consome a fila e toca em ordem, sem cortar o fim da frase."""
        while not self._parar.is_set():
            item = self._fila.get()
            if item is None:
                break
            sd.play(item, config.TTS_SR)
            sd.wait()

    def iniciar(self) -> None:
        self.carregar()
        self._parar.clear()
        self.primeiro_audio_s = None
        self._thread = threading.Thread(target=self._tocar_loop, daemon=True)
        self._thread.start()

    def falar(self, frase: str, t_ref: float = None) -> None:
        frase = limpar(frase)
        if not frase:
            return
        self.carregar()
        for _gs, _ps, audio in self._pipe(
            frase, voice=config.TTS_VOZ, speed=config.TTS_VELOCIDADE
        ):
            a = audio if isinstance(audio, np.ndarray) else audio.detach().cpu().numpy()
            if self.primeiro_audio_s is None and t_ref is not None:
                self.primeiro_audio_s = time.time() - t_ref
            self._fila.put(a)

    def aguardar(self) -> None:
        self._fila.put(None)
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def interromper(self) -> None:
        """Barge-in: usuário voltou a falar, cala a boca agora."""
        self._parar.set()
        try:
            while True:
                self._fila.get_nowait()
        except queue.Empty:
            pass
        sd.stop()
