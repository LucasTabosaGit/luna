"""STT: áudio → texto, com Whisper large-v3-turbo na GPU.

Escolha do modelo: o `small` do teste anterior erra nomes próprios e
termos técnicos em português. O large-v3-turbo tem o encoder completo do
large-v3 com decoder enxuto — qualidade de large com velocidade próxima
do small, e cabe com folga nos 16 GB da 5060 Ti.

Dois cuidados que vieram de erro medido:

1. `vad_filter=True` do faster-whisper é redundante aqui: o Silero já
   cortou o silêncio antes. Ligar os dois faz o Whisper recortar de novo
   e às vezes devolver string vazia para fala curta ("sim", "para").

2. Whisper alucina em áudio quase mudo — devolve "Legendas pela
   comunidade Amara.org" ou "Obrigado por assistir". É o defeito clássico
   e some filtrando por no_speech_prob e por lista de frases-fantasma.
"""
from __future__ import annotations

import re
import time


import numpy as np

import config

# Frases que o Whisper inventa em silêncio/ruído. Medidas em português.
_FANTASMAS = {
    "legendas pela comunidade amara.org",
    "legendas pela comunidade amara org",
    "obrigado por assistir",
    "obrigado",
    "tchau",
    "ate mais",
    "subtitles by the amara.org community",
    "amara.org",
    "muito obrigado",
    "inscreva-se no canal",
}


def _normaliza(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s.]", "", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def _filtra(texto: str) -> str:
    texto = (texto or "").strip()
    if not texto or _normaliza(texto) in _FANTASMAS:
        return ""
    return texto


class TranscritorNuvem:
    """Mesma interface do Transcritor, mas manda o áudio para a nuvem.

    Não usa placa de vídeo. Se a nuvem falhar (sem internet, cota), cai
    num Whisper pequeno no processador, carregado só nessa hora."""

    def __init__(self, motor: str) -> None:
        self.motor = motor
        self.dispositivo = "nuvem"
        self.compute = motor
        self._reserva = None
        self.carga_s = 0.0

    def carregar(self) -> None:
        pass

    @staticmethod
    def _wav(audio: np.ndarray) -> bytes:
        import io
        import wave
        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(config.SAMPLE_RATE)
            w.writeframes(pcm)
        return buf.getvalue()

    def _nuvem(self, audio: np.ndarray, dica: str = "") -> str:
        s = config.STTS[self.motor]
        chave = config.chave_stt(self.motor)
        if not chave:
            raise RuntimeError("falta a chave de %s" % s["nome"])
        wav = self._wav(audio)
        if self.motor == "gemini":
            import base64
            import json
            import urllib.request
            corpo = {
                "contents": [{"parts": [
                    {"text": "Transcreva exatamente o que é falado neste áudio, em português do Brasil. "
                             "Responda só com o texto falado, sem comentários nem aspas. "
                             "Se não houver fala, responda com nada."},
                    {"inline_data": {"mime_type": "audio/wav",
                                     "data": base64.b64encode(wav).decode("ascii")}},
                ]}],
                "generationConfig": {"temperature": 0},
            }
            req = urllib.request.Request(
                "%s/models/%s:generateContent" % (s["url"], s["modelo"]),
                data=json.dumps(corpo).encode("utf-8"),
                headers={"Content-Type": "application/json", "x-goog-api-key": chave})
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode("utf-8"))
            partes = d.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            return " ".join(p.get("text", "") for p in partes).strip()
        from openai import OpenAI
        cli = OpenAI(base_url=s["url"], api_key=chave, timeout=10, max_retries=0)
        extra = {"prompt": dica[-150:]} if dica else {}
        r = cli.audio.transcriptions.create(model=s["modelo"], file=("fala.wav", wav, "audio/wav"),
                                            language=config.STT_IDIOMA, temperature=0.0, **extra)
        return (getattr(r, "text", "") or "").strip()

    def _local(self, audio: np.ndarray) -> str:
        if self._reserva is None:
            from faster_whisper import WhisperModel
            self._reserva = WhisperModel(config.STT_RESERVA, device="cpu", compute_type="int8",
                                         download_root=str(config.MODELOS / "whisper"))
        segs, _ = self._reserva.transcribe(audio, language=config.STT_IDIOMA, beam_size=1,
                                           vad_filter=False, temperature=0.0,
                                           condition_on_previous_text=False)
        return " ".join(sg.text.strip() for sg in segs if getattr(sg, "no_speech_prob", 0) < 0.6)

    def testar(self) -> None:
        """Levanta exceção se a nuvem não aceitar (chave, cota, rede)."""
        t = np.arange(config.SAMPLE_RATE, dtype="float32") / config.SAMPLE_RATE
        self._nuvem((0.05 * np.sin(2 * np.pi * 220 * t)).astype("float32"))

    def transcrever(self, audio: np.ndarray, dica: str = "") -> tuple[str, float]:
        t0 = time.time()
        try:
            texto = self._nuvem(audio, dica)
        except Exception as e:  # noqa: BLE001
            print("  [stt] nuvem (%s) falhou: %s; usando o Whisper do processador"
                  % (self.motor, str(e)[:120]), flush=True)
            try:
                texto = self._local(audio)
            except Exception as e2:  # noqa: BLE001
                print("  [stt] reserva também falhou: %s" % str(e2)[:120], flush=True)
                texto = ""
        return _filtra(texto), time.time() - t0


def criar():
    """O transcritor da escolha atual (Ajustes -> Inteligência)."""
    motor = config.stt_id()
    if motor == "vulkan":
        from ouvido_vulkan import TranscritorVulkan
        return TranscritorVulkan()
    if motor != "local":
        return TranscritorNuvem(motor)
    import plataforma
    return TranscritorMac() if plataforma.MAC else Transcritor()


class Transcritor:
    def __init__(self) -> None:
        self._modelo = None
        self.dispositivo = "cpu"
        self.compute = "int8"

    # ------------------------------------------------------------------
    def carregar(self) -> None:
        if self._modelo is not None:
            return
        from faster_whisper import WhisperModel

        try:
            import torch

            if torch.cuda.is_available():
                self.dispositivo, self.compute = "cuda", config.STT_COMPUTE
        except Exception:
            pass

        t0 = time.time()
        try:
            self._modelo = WhisperModel(
                config.STT_MODELO,
                device=self.dispositivo,
                compute_type=self.compute,
                download_root=str(config.MODELOS / "whisper"),
            )
        except Exception:
            # cuDNN/driver incompatível é comum em GPU nova: cai para CPU
            # em vez de derrubar o assistente inteiro.
            self.dispositivo, self.compute = "cpu", "int8"
            self._modelo = WhisperModel(
                config.STT_MODELO,
                device="cpu",
                compute_type="int8",
                download_root=str(config.MODELOS / "whisper"),
            )
        self.carga_s = time.time() - t0

        # Aquecimento: a PRIMEIRA transcrição paga a inicialização dos
        # kernels CUDA e leva ~15s, contra 0,23s das seguintes (medido
        # nesta máquina: 0,3x tempo real na 1a chamada, 20x a partir da
        # 2a). Sem isto, a primeira pergunta da conversa parece travada.
        try:
            import numpy as _np

            mudo = _np.zeros(config.SAMPLE_RATE, dtype="float32")
            segs, _ = self._modelo.transcribe(
                mudo, language=config.STT_IDIOMA, beam_size=1,
                vad_filter=False, temperature=0.0,
            )
            for _ in segs:
                pass
        except Exception:
            pass

    def descarregar(self) -> None:
        """Devolve a VRAM do Whisper. Só `del` não basta: o CTranslate2
        segura a memória até unload_model() (medido: libera ~2,1 GB)."""
        if self._modelo is not None:
            try:
                self._modelo.model.unload_model()
            except Exception:  # noqa: BLE001
                pass
            self._modelo = None

    # ------------------------------------------------------------------
    def transcrever(self, audio: np.ndarray, dica: str = "") -> tuple[str, float]:
        """Devolve (texto, segundos gastos). Texto vazio = nada útil.

        `dica`: última fala do assistente, vira initial_prompt (até ~150
        caracteres; mais que isso o Whisper começa a "ouvir" a dica).
        """
        self.carregar()
        assert self._modelo is not None

        t0 = time.time()
        segmentos, _info = self._modelo.transcribe(
            audio,
            language=config.STT_IDIOMA,
            beam_size=1,              # greedy: metade da latência
            vad_filter=False,         # o Silero já cortou
            condition_on_previous_text=False,  # evita arrastar alucinação
            temperature=0.0,
            initial_prompt=(dica[-150:] or None),
        )

        partes, suspeito = [], True
        for s in segmentos:
            # no_speech_prob alto = o modelo "ouviu" silêncio e inventou
            if getattr(s, "no_speech_prob", 0.0) < 0.6:
                suspeito = False
            partes.append(s.text)

        texto = " ".join(p.strip() for p in partes).strip()
        dt = time.time() - t0

        if not texto or suspeito:
            return "", dt
        if _normaliza(texto) in _FANTASMAS:
            return "", dt
        return texto, dt


class TranscritorMac(Transcritor):
    """Mac (Apple Silicon): Whisper na GPU pelo MLX (mlx-whisper).

    O CTranslate2 (faster-whisper) não tem backend Metal: no Mac ele só
    roda no processador, e fica de reserva se o MLX falhar. Adaptado do
    fork de felipyenzo7543-blip (luna-mac).
    """

    def __init__(self) -> None:
        super().__init__()
        self.dispositivo, self.compute = "gpu", "mlx"
        self._pronto = False

    def carregar(self) -> None:
        if self._pronto or self._modelo is not None:
            return
        t0 = time.time()
        try:
            import mlx_whisper
            # Aquecimento: a 1ª transcrição compila os kernels Metal e
            # demora; também confirma que o MLX carrega de verdade.
            mlx_whisper.transcribe(
                np.zeros(config.SAMPLE_RATE, dtype="float32"),
                path_or_hf_repo=config.STT_MODELO_MLX, language=config.STT_IDIOMA,
                condition_on_previous_text=False, temperature=0.0)
            self._pronto = True
        except Exception as e:  # noqa: BLE001
            print("  [stt] mlx-whisper falhou (%s); usando o processador" % str(e)[:100], flush=True)
            self.dispositivo, self.compute = "cpu", "int8"
            super().carregar()
        self.carga_s = time.time() - t0

    def descarregar(self) -> None:
        if self._pronto:
            try:
                import mlx.core as mx
                import mlx_whisper
                mlx_whisper.transcribe.ModelHolder.model = None
                mlx_whisper.transcribe.ModelHolder.model_path = None
                mx.clear_cache()
            except Exception:  # noqa: BLE001
                pass
            self._pronto = False
        super().descarregar()

    def transcrever(self, audio: np.ndarray, dica: str = "") -> tuple[str, float]:
        self.carregar()
        if not self._pronto:                    # caiu para o processador
            return super().transcrever(audio, dica)
        import mlx_whisper
        t0 = time.time()
        r = mlx_whisper.transcribe(
            audio, path_or_hf_repo=config.STT_MODELO_MLX, language=config.STT_IDIOMA,
            condition_on_previous_text=False, temperature=0.0,
            initial_prompt=(dica[-150:] or None))
        partes, suspeito = [], True
        for seg in r.get("segments", []):
            if seg.get("no_speech_prob", 0.0) < 0.6:
                suspeito = False
            partes.append(seg.get("text", ""))
        texto = " ".join(p.strip() for p in partes).strip()
        dt = time.time() - t0
        if not texto or suspeito or _normaliza(texto) in _FANTASMAS:
            return "", dt
        return texto, dt
