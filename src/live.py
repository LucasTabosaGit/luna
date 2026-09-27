"""Modo Live: conversa por áudio nativo com o Gemini.

Diferença para o pipeline convencional:

    pipeline   microfone → Silero → Whisper → LLM → TTS → alto-falante
    live       microfone ──────────→ Gemini ──────────→ alto-falante

O modelo ouve e fala pelo mesmo WebSocket. Não há STT nem TTS: some o
acúmulo de latência de três etapas encadeadas. Medido nesta máquina:
**1,25 s** do fim da fala ao primeiro áudio, contra 3,2 s do pipeline
com a mesma voz.

Ganhos: latência, interrupção no meio da fala (barge-in nativo do
servidor), transcrição automática dos dois lados.
Perdas: exige internet e é pago por minuto de áudio.

Duas armadilhas que custaram tempo e não aparecem na documentação:

1. O campo do áudio é `realtimeInput.audio` (objeto), NÃO
   `realtimeInput.mediaChunks` (lista). O formato antigo é aceito sem
   erro e silenciosamente ignorado: a sessão abre, nada responde, e não
   há mensagem para diagnosticar.

2. O servidor tem VAD próprio e só responde quando OUVE a pausa. Enviar
   `audioStreamEnd` não basta — é preciso mandar silêncio real. Por isso
   `SILENCIO_FIM` é anexado ao fim de cada turno.
"""
from __future__ import annotations

import asyncio
import base64
import json
from typing import Optional

import config

URL_BASE = ("wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService."
            "BidiGenerateContent")

# Modelos aceitos pela chave (verificado com src/testa_live.py).
MODELO = "models/gemini-3.8-live"

TAXA_ENTRADA = 16000      # o que a Live API espera receber
TAXA_SAIDA = 24000        # o que ela devolve

# Silêncio anexado ao fim do turno para o VAD do servidor fechar a fala.
SILENCIO_FIM_S = 0.8


class SessaoLive:
    """Uma conversa com a Live API, do ponto de vista do servidor web."""

    def __init__(self, voz: str = "Leda") -> None:
        self.voz = voz
        self.ws = None
        self._ctx = None

    # ------------------------------------------------------------------
    async def abrir(self) -> None:
        import websockets

        chave = config.chave_gemini()
        if not chave:
            raise RuntimeError("Live API exige GEMINI_API_KEY no .env")

        self._ctx = websockets.connect(
            "%s?key=%s" % (URL_BASE, chave), max_size=None, open_timeout=30)
        self.ws = await self._ctx.__aenter__()

        await self.ws.send(json.dumps({
            "setup": {
                "model": MODELO,
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {"voiceName": self.voz}
                        },
                        "languageCode": "pt-BR",
                    },
                },
                # Transcrição dos dois lados: é o que alimenta as bolhas
                # da interface. Sem isto a conversa seria só áudio.
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
                "systemInstruction": {
                    "parts": [{"text": config.SISTEMA}]
                },
            }
        }))

        resp = await asyncio.wait_for(self.ws.recv(), timeout=30)
        if isinstance(resp, (bytes, bytearray)):
            resp = resp.decode("utf-8", "ignore")
        if "setupComplete" not in json.loads(resp):
            raise RuntimeError("Live API recusou o setup: %s" % resp[:200])

    # ------------------------------------------------------------------
    async def enviar_audio(self, pcm: bytes) -> None:
        """Manda um pedaço de PCM int16 16 kHz (como o microfone produz)."""
        if self.ws is None:
            return
        await self.ws.send(json.dumps({
            "realtimeInput": {
                "audio": {
                    "mimeType": "audio/pcm;rate=%d" % TAXA_ENTRADA,
                    "data": base64.b64encode(pcm).decode(),
                }
            }
        }))

    async def fechar_turno(self) -> None:
        """Silêncio para o VAD do servidor entender que a fala acabou."""
        mudo = b"\x00" * int(TAXA_ENTRADA * 2 * SILENCIO_FIM_S)
        passo = 2048
        for i in range(0, len(mudo), passo):
            await self.enviar_audio(mudo[i:i + passo])

    # ------------------------------------------------------------------
    async def receber(self):
        """Gera eventos do servidor: ('voce'|'bot'|'audio'|'fim', dado)."""
        if self.ws is None:
            return
        async for msg in self.ws:
            if isinstance(msg, (bytes, bytearray)):
                msg = msg.decode("utf-8", "ignore")
            try:
                j = json.loads(msg)
            except ValueError:
                continue

            sc = j.get("serverContent") or {}

            trans = sc.get("inputTranscription") or {}
            if trans.get("text"):
                yield ("voce", trans["text"])

            saida = sc.get("outputTranscription") or {}
            if saida.get("text"):
                yield ("bot", saida["text"])

            for parte in (sc.get("modelTurn") or {}).get("parts", []):
                dados = (parte.get("inlineData") or {}).get("data")
                if dados:
                    yield ("audio", base64.b64decode(dados))
                if parte.get("text"):
                    yield ("bot", parte["text"])

            # O usuário falou por cima: o cliente deve calar a boca.
            if sc.get("interrupted"):
                yield ("interrompido", None)

            if sc.get("turnComplete"):
                yield ("fim", None)

    # ------------------------------------------------------------------
    async def fechar(self) -> None:
        if self._ctx is not None:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        self.ws = None
        self._ctx = None
