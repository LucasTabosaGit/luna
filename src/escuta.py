"""Captura de microfone com detecção de fala (VAD) neural.

Por que não usar limiar de energia (RMS), como o teste anterior fazia:
um ventilador, o clique do teclado ou música de fundo passam do limiar e
disparam gravação; e fala baixa no fim da frase fica abaixo dele e é
cortada. O Silero VAD decide por *conteúdo de voz*, não por volume, e
custa ~1 ms por bloco de 32 ms na CPU.

A classe entrega utterances completas: ela segura o áudio enquanto você
fala e devolve o trecho assim que detecta o fim da frase.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Iterator, Optional

import numpy as np
import sounddevice as sd
import torch

import config


class EscutaVAD:
    def __init__(self, dispositivo: Optional[int] = None) -> None:
        self.dispositivo = dispositivo
        self.fila: queue.Queue = queue.Queue()
        self._modelo = None
        self._stream: Optional[sd.InputStream] = None
        self._surdo = threading.Event()

    # ------------------------------------------------------------------
    def carregar(self) -> None:
        if self._modelo is not None:
            return

        # ONNX em vez de JIT: `torch.jit.load` falha com
        # "errno 2 ... No such file or directory" quando o caminho tem
        # acento ("D:\Aplicações\..."), mesmo com o arquivo
        # presente — o loader em C++ não lida com o encoding do Windows.
        # É o mesmo defeito do espeak-ng, com mensagem diferente.
        # O runtime ONNX abre o arquivo pelo Python e não sofre disso.
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                modelo, _ = torch.hub.load(
                    repo_or_dir="snakers4/silero-vad",
                    model="silero_vad",
                    trust_repo=True,
                    onnx=True,
                )
            except Exception:
                # Último recurso: JIT (funciona se o caminho for ASCII).
                modelo, _ = torch.hub.load(
                    repo_or_dir="snakers4/silero-vad",
                    model="silero_vad",
                    trust_repo=True,
                    onnx=False,
                )
                modelo.eval()
        self._modelo = modelo

    # ------------------------------------------------------------------
    def _prob_fala(self, bloco: np.ndarray) -> float:
        """Probabilidade de haver voz no bloco (0..1).

        O Silero exige exatamente 512 amostras por chamada em 16 kHz.
        Blocos maiores são quebrados; sobra é descartada.
        """
        assert self._modelo is not None
        JANELA = 512
        melhor = 0.0
        with torch.no_grad():
            for i in range(0, len(bloco) - JANELA + 1, JANELA):
                t = torch.from_numpy(bloco[i:i + JANELA]).float()
                saida = self._modelo(t, config.SAMPLE_RATE)
                # O wrapper ONNX devolve ndarray; o JIT devolve tensor.
                try:
                    p = float(saida.item())
                except AttributeError:
                    p = float(np.asarray(saida).reshape(-1)[0])
                melhor = max(melhor, p)
        return melhor

    # ------------------------------------------------------------------
    def _callback(self, dados, frames, tempo, status) -> None:  # noqa: ANN001
        # Roda na thread do PortAudio: só enfileira, nunca processa.
        # Enquanto o bot fala, descarta: o que entra no microfone nesse
        # momento é a própria voz dele saindo das caixas. Sem isto, o
        # ciclo seguinte transcreve o bot e ele responde a si mesmo —
        # medido: 4,5s de áudio capturado durante uma resposta.
        if self._surdo.is_set():
            return
        self.fila.put(dados[:, 0].copy())

    # ------------------------------------------------------------------
    def silenciar(self) -> None:
        """Ignora o microfone (chamar antes de o bot falar)."""
        self._surdo.set()

    def escutar(self) -> None:
        """Volta a ouvir e joga fora o que sobrou da fala do bot."""
        try:
            while True:
                self.fila.get_nowait()
        except queue.Empty:
            pass
        self._surdo.clear()

    # ------------------------------------------------------------------
    def abrir(self) -> None:
        self.carregar()
        self._stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=config.BLOCO,
            device=self.dispositivo,
            callback=self._callback,
        )
        self._stream.start()

    def fechar(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    # ------------------------------------------------------------------
    def utterances(self, limiar: float = 0.5) -> Iterator[np.ndarray]:
        """Gera um array por frase falada, já cortada no fim da fala."""
        buf: list[np.ndarray] = []
        falando = False
        ultimo_voz = 0.0
        inicio_espera = time.time()

        while True:
            try:
                bloco = self.fila.get(timeout=1.0)
            except queue.Empty:
                if not falando and time.time() - inicio_espera > config.ESPERA_MAX_S:
                    return
                continue

            p = self._prob_fala(bloco)
            agora = time.time()

            if not falando:
                if p >= limiar:
                    falando = True
                    ultimo_voz = agora
                    buf = [bloco]
                continue

            buf.append(bloco)
            if p >= limiar:
                ultimo_voz = agora
                continue

            if agora - ultimo_voz >= config.SILENCIO_FIM_S:
                audio = np.concatenate(buf)
                dur = len(audio) / config.SAMPLE_RATE
                falando = False
                buf = []
                inicio_espera = agora
                # Descarta ruído curto que passou pelo VAD (tosse, porta).
                if dur >= config.FALA_MIN_S:
                    yield audio
