"""Ouvido na placa AMD / Intel (Windows), pelo whisper.cpp com Vulkan.

O Whisper de sempre (faster-whisper) só acelera em placa NVIDIA (CUDA). O
whisper.cpp compilado com Vulkan roda em qualquer placa com driver de vídeo
atualizado: AMD Radeon, Intel Arc e também NVIDIA, sem instalar ROCm nem
CUDA. É EXPERIMENTAL: foi feito sem uma placa AMD para testar.

Como funciona:
  * Na primeira vez baixa duas coisas para modelos/whisper-vulkan/:
      - o whisper-server com Vulkan (~20 MB), compilado pelo workflow
        `whisper-vulkan` do repositório público da Luna;
      - o modelo large-v3-turbo quantizado em 5 bits (~550 MB, Hugging Face).
    Os dois são conferidos pelo SHA-256 antes de usar.
  * Sobe o whisper-server em 127.0.0.1 numa porta livre (só a própria
    máquina acessa) e manda cada frase para ele, como faz com a nuvem.
  * Se algo falhar (sem Vulkan, driver velho, download), a Luna não trava:
    cai no Whisper pequeno do processador, como quando a internet cai.
"""
from __future__ import annotations

import atexit
import hashlib
import io
import os
import socket
import subprocess
import time
import zipfile
from pathlib import Path

import numpy as np

import config

PASTA = config.MODELOS / "whisper-vulkan"
_PID = PASTA / "servidor.pid"
ULTIMO_ERRO = [""]          # a tela de conexões mostra isto no "Salvar e testar"


def _log(msg: str) -> None:
    print("  [stt-vulkan] " + msg, flush=True)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _baixar(url: str, destino: Path, sha: str, nome: str) -> None:
    """Baixa para .parte e só renomeia depois de conferir o SHA-256."""
    import httpx
    parte = destino.with_suffix(destino.suffix + ".parte")
    _log("baixando %s..." % nome)
    t0, ult = time.time(), 0.0
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        feito = 0
        with open(parte, "wb") as f:
            for bloco in r.iter_bytes(1 << 20):
                f.write(bloco)
                feito += len(bloco)
                if total and time.time() - ult > 5:
                    ult = time.time()
                    _log("  %s: %d%%" % (nome, feito * 100 // total))
    if sha and _sha256(parte) != sha.lower():
        parte.unlink(missing_ok=True)
        raise RuntimeError("%s veio corrompido (SHA-256 não confere)" % nome)
    os.replace(parte, destino)
    _log("%s pronto em %.0f s" % (nome, time.time() - t0))


def _exe() -> Path:
    return PASTA / "bin" / "whisper-server.exe"


def _modelo() -> Path:
    return PASTA / config.VULKAN_MODELO_ARQ


def garantir_arquivos() -> None:
    PASTA.mkdir(parents=True, exist_ok=True)
    if not _exe().exists():
        zip_ = PASTA / "whisper-vulkan-win-x64.zip"
        _baixar(config.VULKAN_BIN_URL, zip_, config.VULKAN_BIN_SHA256, "whisper.cpp (Vulkan)")
        with zipfile.ZipFile(zip_) as z:
            for n in z.namelist():                  # sem caminhos fora da pasta
                if n.startswith(("/", "\\")) or ".." in Path(n).parts:
                    raise RuntimeError("zip com caminho estranho: %s" % n)
            z.extractall(PASTA / "bin")
        zip_.unlink(missing_ok=True)
        if not _exe().exists():
            raise RuntimeError("o pacote não tem o whisper-server.exe")
    if not _modelo().exists():
        _baixar(config.VULKAN_MODELO_URL, _modelo(), config.VULKAN_MODELO_SHA256,
                "modelo Whisper (~550 MB)")


def vulkan_no_windows() -> bool:
    """O driver de vídeo instala o vulkan-1.dll. Sem ele não há Vulkan."""
    raiz = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return (raiz / "System32" / "vulkan-1.dll").exists()


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _encerrar_sobra() -> None:
    """Se a Luna fechou sem desligar o servidor, ele ficou segurando a
    memória da placa. Encerra só se o PID ainda for o whisper-server."""
    try:
        pid = int(_PID.read_text().strip())
    except (OSError, ValueError):
        return
    try:
        import psutil
        p = psutil.Process(pid)
        if p.name().lower() == "whisper-server.exe":
            p.terminate()
            p.wait(5)
    except Exception:  # noqa: BLE001
        pass
    _PID.unlink(missing_ok=True)


class TranscritorVulkan:
    """Mesma interface do Transcritor: carregar / transcrever / descarregar."""

    motor = "vulkan"

    def __init__(self) -> None:
        self.dispositivo = "vulkan"
        self.compute = "vulkan"
        self.carga_s = 0.0
        self._proc: subprocess.Popen | None = None
        self._url = ""
        self._reserva = None
        self._log = None

    # ------------------------------------------------------------------
    def carregar(self) -> None:
        """Não levanta exceção: se falhar, guarda o motivo e usa a reserva."""
        t0 = time.time()
        ULTIMO_ERRO[0] = ""
        try:
            if not vulkan_no_windows():
                raise RuntimeError("sem Vulkan: atualize o driver da placa de vídeo")
            garantir_arquivos()
            self._subir()
            # A 1ª frase na placa compila os shaders (medido: 16 s, depois
            # 0,5 s). Faz isso agora, com 1 s de silêncio, e não na 1ª fala.
            try:
                self._placa(np.zeros(16000, dtype="float32"))
            except Exception:  # noqa: BLE001
                pass
            self.carga_s = time.time() - t0
            _log("pronto em %.1f s (%s)" % (self.carga_s, self._url))
        except Exception as e:  # noqa: BLE001
            ULTIMO_ERRO[0] = str(e)[:160]
            _log("não subiu: %s. Usando o Whisper do processador." % ULTIMO_ERRO[0])
            self.descarregar()

    def _subir(self) -> None:
        import plataforma
        _encerrar_sobra()
        porta = _porta_livre()
        # Caminho RELATIVO do modelo: o whisper.cpp quebra (0xC0000409) com
        # acento no caminho da pasta ("Aplicações", nome de usuário "João").
        args = [str(_exe()), "-m", _modelo().name, "--host", "127.0.0.1", "--port", str(porta),
                "-l", config.STT_IDIOMA, "-nt", "-t", str(max(2, min(8, (os.cpu_count() or 4) // 2)))]
        disp = os.environ.get("LUNA_VULKAN_PLACA", "").strip()     # PC com 2 placas: 0, 1...
        if disp.isdigit():
            args += ["-dev", disp]
        config.LOGS.mkdir(exist_ok=True)
        self._log = open(config.LOGS / "whisper-vulkan.log", "ab")
        self._proc = subprocess.Popen(args, cwd=str(PASTA), stdout=self._log,
                                      stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                      **plataforma.sem_janela())
        _PID.write_text(str(self._proc.pid))
        atexit.register(self.descarregar)
        self._url = "http://127.0.0.1:%d" % porta
        import httpx
        limite = time.time() + 180                  # carregar o modelo na placa
        while time.time() < limite:
            if self._proc.poll() is not None:
                raise RuntimeError("o whisper-server fechou ao iniciar (código %s; veja "
                                   "logs/whisper-vulkan.log)" % self._proc.returncode)
            try:
                if httpx.get(self._url + "/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError("o whisper-server não ficou pronto em 3 min")

    def pronto(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def descarregar(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(5)
            except Exception:  # noqa: BLE001
                pass
            self._proc = None
            _PID.unlink(missing_ok=True)
        if self._log is not None:
            self._log.close()
            self._log = None

    # ------------------------------------------------------------------
    def _placa(self, audio: np.ndarray, dica: str = "") -> str:
        import httpx
        from ouvido import TranscritorNuvem
        wav = TranscritorNuvem._wav(audio)
        dados = {"temperature": "0.0", "response_format": "json", "language": config.STT_IDIOMA}
        if dica:
            dados["prompt"] = dica[-150:]
        r = httpx.post(self._url + "/inference", files={"file": ("fala.wav", io.BytesIO(wav), "audio/wav")},
                       data=dados, timeout=30)
        r.raise_for_status()
        return str(r.json().get("text", "")).strip()

    def _local(self, audio: np.ndarray) -> str:
        if self._reserva is None:
            from faster_whisper import WhisperModel
            self._reserva = WhisperModel(config.STT_RESERVA, device="cpu", compute_type="int8",
                                         download_root=str(config.MODELOS / "whisper"))
        segs, _ = self._reserva.transcribe(audio, language=config.STT_IDIOMA, beam_size=1,
                                           vad_filter=False, temperature=0.0,
                                           condition_on_previous_text=False)
        return " ".join(sg.text.strip() for sg in segs if getattr(sg, "no_speech_prob", 0) < 0.6)

    def transcrever(self, audio: np.ndarray, dica: str = "") -> tuple[str, float]:
        from ouvido import _filtra
        t0 = time.time()
        texto = ""
        if self.pronto():
            try:
                texto = self._placa(audio, dica)
                return _filtra(texto), time.time() - t0
            except Exception as e:  # noqa: BLE001
                _log("falhou nesta frase: %s; usando o processador" % str(e)[:120])
        try:
            texto = self._local(audio)
        except Exception as e:  # noqa: BLE001
            _log("reserva também falhou: %s" % str(e)[:120])
        return _filtra(texto), time.time() - t0
