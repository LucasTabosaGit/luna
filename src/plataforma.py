"""O que muda entre Windows e Mac, num lugar só.

O resto da Luna chama daqui e não precisa saber em que sistema está:

    import plataforma as P
    P.MAC / P.WINDOWS
    P.python_venv()          # .venv/Scripts/python.exe  ou  .venv/bin/python3
    P.abrir("https://...")   # os.startfile  ou  open
    P.sem_janela()           # kwargs do subprocess: sem console / sessão própria
    P.hermes_home()          # %LOCALAPPDATA%\\hermes  ou  ~/.hermes

A adaptação para o Mac (Apple Silicon) partiu do fork de
felipyenzo7543-blip (github.com/felipyenzo7543-blip/luna-mac): volume por
osascript, teclas por Quartz, apps de /Applications, trava por fcntl,
Whisper na GPU pelo MLX, atalho .app, janela mini em AppKit.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"
NOME = "Windows" if WINDOWS else "Mac" if MAC else "Linux"
RAIZ = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------ Python / processos
def python_venv(pasta: str = ".venv", sem_console: bool = False) -> Path:
    if WINDOWS:
        return RAIZ / pasta / "Scripts" / ("pythonw.exe" if sem_console else "python.exe")
    return RAIZ / pasta / "bin" / "python3"


def sem_janela(desligado: bool = False) -> dict:
    """kwargs do subprocess.Popen: sem console no Windows; no Mac, sessão
    própria (sobrevive a quem abriu). desligado=True: nem morre junto com o
    processo pai no Windows (DETACHED + grupo novo)."""
    if WINDOWS:
        f = 0x08000000                                   # CREATE_NO_WINDOW
        if desligado:
            f = 0x00000008 | 0x00000200                  # DETACHED | NEW_PROCESS_GROUP
        return {"creationflags": f}
    return {"start_new_session": True}


def abrir(alvo: str) -> None:
    """URL, pasta, arquivo ou esquema (spotify:...) no programa padrão."""
    if WINDOWS:
        os.startfile(alvo)  # noqa: S606
    else:
        subprocess.Popen(["open" if MAC else "xdg-open", alvo], **sem_janela())  # noqa: S603


def aviso(texto: str, titulo: str = "Luna") -> None:
    """Caixa de aviso nativa (para quando a janela ainda não abriu)."""
    try:
        if WINDOWS:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, texto, titulo, 0x30)
        elif MAC:
            subprocess.run(["osascript", "-e", 'display alert "%s" message "%s"' % (
                titulo, texto.replace("\\", "\\\\").replace('"', '\\"'))], timeout=60)
        else:
            print(texto)
    except Exception:  # noqa: BLE001
        print(texto)


# ------------------------------------------------------------ Hermes
def hermes_home() -> Path:
    """Raiz dos dados do Hermes. No Windows é sempre %LOCALAPPDATA%\\hermes
    (lá o HERMES_HOME pode apontar para um PERFIL, e não para a raiz). No
    Mac/Linux, ~/.hermes, a menos que HERMES_HOME diga outra raiz."""
    if WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", "")) / "hermes"
    env = os.environ.get("HERMES_HOME", "")
    if env and "profiles" not in Path(env).parts:
        return Path(env)
    return Path.home() / ".hermes"


def hermes_exe() -> str | None:
    achado = shutil.which("hermes")
    if achado:
        return achado
    if WINDOWS:
        p = hermes_home() / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"
        return str(p) if p.is_file() else None
    p = hermes_home() / "hermes-agent" / "venv" / "bin" / "hermes"
    return str(p) if p.is_file() else None


# ------------------------------------------------------------ placa de vídeo
def gpu() -> dict:
    """{"tipo": "cuda"|"mlx"|"", "nome": str, "total_gb": float, "usada_gb": float}.

    Mac: memória UNIFICADA (a RAM é a mesma da GPU): total = RAM e usada =
    o que o MLX tem alocado agora."""
    try:
        if MAC:
            import mlx.core as mx
            info = mx.device_info()
            return {"tipo": "mlx", "nome": info.get("device_name", "GPU da Apple"),
                    "total_gb": round(info.get("memory_size", 0) / 2**30, 1),
                    "usada_gb": round(mx.get_active_memory() / 2**30, 2)}
        import torch
        if torch.cuda.is_available():
            livre, total = torch.cuda.mem_get_info()
            return {"tipo": "cuda", "nome": torch.cuda.get_device_name(0),
                    "total_gb": round(total / 2**30, 1), "usada_gb": round((total - livre) / 2**30, 1)}
    except Exception:  # noqa: BLE001
        pass
    return {"tipo": "", "nome": "", "total_gb": 0.0, "usada_gb": 0.0}


def liberar_gpu() -> None:
    try:
        if MAC:
            import mlx.core as mx
            mx.clear_cache()
        else:
            import torch
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------ teclas e som
def _osascript(script: str, timeout: int = 5) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


# Teclas com nome -> código de cada sistema.
_VK_WIN = {"ctrl": 0x11, "shift": 0x10, "win": 0x5B, "d": 0x44, "t": 0x54, "w": 0x57,
           "print": 0x2C, "play": 0xB3, "proxima": 0xB0, "anterior": 0xB1}
_VK_MAC = {"t": 0x11, "w": 0x0D, "f11": 0x67}               # kVK_ANSI_*
_MIDIA_MAC = {"play": 16, "proxima": 17, "anterior": 18}    # NX_KEYTYPE_*


def teclas(*nomes: str) -> None:
    """Combinação de teclas na janela em foco. Use "ctrl" como modificador
    principal: no Mac ele vira Command (Ctrl+T -> Cmd+T)."""
    if WINDOWS:
        import ctypes
        u32 = ctypes.windll.user32
        vks = [_VK_WIN[n] for n in nomes]
        for vk in vks:
            u32.keybd_event(vk, 0, 0, 0)
        for vk in reversed(vks):
            u32.keybd_event(vk, 0, 0x0002, 0)
        return
    import Quartz
    flags = 0
    if "ctrl" in nomes:
        flags |= Quartz.kCGEventFlagMaskCommand
    if "shift" in nomes:
        flags |= Quartz.kCGEventFlagMaskShift
    tecla = next(_VK_MAC[n] for n in nomes if n not in ("ctrl", "shift"))
    origem = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for desce in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(origem, tecla, desce)
        if flags:
            Quartz.CGEventSetFlags(ev, flags)
        Quartz.CGEventPost(Quartz.kCGSessionEventTap, ev)


def midia(nome: str) -> None:
    """"play", "proxima" ou "anterior": vai para o player que está tocando."""
    if WINDOWS:
        teclas(nome)
        return
    from AppKit import NSEvent
    import Quartz
    codigo = _MIDIA_MAC[nome]
    for desce in (True, False):
        estado = 0xA if desce else 0xB
        ev = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            Quartz.NSEventTypeSystemDefined, (0, 0), estado << 8, 0, 0, None, 8,
            (codigo << 16) | (estado << 8), -1)
        Quartz.CGEventPost(Quartz.kCGSessionEventTap, ev.CGEvent())


def mostrar_area_de_trabalho() -> None:
    if WINDOWS:
        teclas("win", "d")
    else:
        teclas("f11")                # atalho padrão do Mission Control


def print_da_tela(pasta: Path) -> None:
    if WINDOWS:
        teclas("win", "print")       # o Windows salva em Imagens\\Capturas de Tela
        return
    import datetime as _dt
    pasta.mkdir(parents=True, exist_ok=True)
    arq = pasta / _dt.datetime.now().strftime("Captura de Tela %Y-%m-%d as %H.%M.%S.png")
    subprocess.Popen(["screencapture", "-x", str(arq)])  # noqa: S603,S607


def bloquear_tela() -> None:
    if WINDOWS:
        import ctypes
        ctypes.windll.user32.LockWorkStation()
    else:
        _osascript('tell application "System Events" to keystroke "q" using {control down, command down}')


def volume_ler() -> tuple[float, bool]:
    """(0 a 100, mudo?)"""
    if WINDOWS:
        v = _volume_win()
        return v.GetMasterVolumeLevelScalar() * 100, bool(v.GetMute())
    d = dict(p.strip().split(":", 1) for p in _osascript("get volume settings").split(",") if ":" in p)
    return float(d.get("output volume", "0") or 0), d.get("output muted", "false").strip() == "true"


def volume_definir(n: float) -> None:
    n = max(0.0, min(100.0, n))
    if WINDOWS:
        _volume_win().SetMasterVolumeLevelScalar(n / 100, None)
    else:
        _osascript("set volume output volume %d" % round(n))


def volume_mudo(ligar: bool) -> None:
    if WINDOWS:
        _volume_win().SetMute(1 if ligar else 0, None)
    else:
        _osascript("set volume output muted %s" % ("true" if ligar else "false"))


def _volume_win():
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers().EndpointVolume


def cursor() -> tuple[int, int]:
    """Posição do mouse (para saber em qual monitor a pessoa está)."""
    if WINDOWS:
        import ctypes

        class _P(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        p = _P()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y
    import Quartz
    loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return int(loc.x), int(loc.y)


# ------------------------------------------------------------ apps instalados
def listar_apps() -> list[tuple[str, str]]:
    """[(nome, como abrir)]: AppID do menu Iniciar ou caminho do .app."""
    if WINDOWS:
        import json
        cmd = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                             capture_output=True, timeout=30, **sem_janela()).stdout
        itens = json.loads(out.decode("utf-8", "replace") or "[]")
        itens = itens if isinstance(itens, list) else [itens]
        return [((a.get("Name") or "").strip(), a.get("AppID") or "") for a in itens
                if a.get("Name") and a.get("AppID")]
    achados = []

    def andar(base: Path, prof: int):
        try:
            for p in base.iterdir():
                if p.suffix == ".app":
                    achados.append((p.stem, str(p)))
                elif prof > 0 and p.is_dir() and not p.name.startswith("."):
                    andar(p, prof - 1)
        except OSError:
            pass
    for pasta in (Path("/Applications"), Path("/System/Applications"),
                  Path("/System/Applications/Utilities"), Path("/System/Library/CoreServices"),
                  Path.home() / "Applications"):
        if pasta.is_dir():
            andar(pasta, 2)
    return achados


def abrir_app(como: str) -> None:
    if WINDOWS:
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + como])  # noqa: S603,S607
    else:
        subprocess.Popen(["open", como])  # noqa: S603,S607


# Nomes de apps que mudam entre os sistemas: (Windows, Mac).
APPS_EQUIVALENTES = {
    "bloco de notas": ("bloco de notas", "textedit"),
    "calculadora": ("calculadora", "calculator"),
    "calendario": ("calendario", "calendar"),
    "explorador de arquivos": ("explorador de arquivos", "finder"),
    "gerenciador de tarefas": ("gerenciador de tarefas", "activity monitor"),
    "configuracoes": ("configuracoes", "system settings"),
    "painel de controle": ("painel de controle", "system settings"),
    "prompt de comando": ("prompt de comando", "terminal"),
    "windows powershell": ("windows powershell", "terminal"),
    "libreoffice writer": ("libreoffice writer", "microsoft word"),
    "libreoffice calc": ("libreoffice calc", "microsoft excel"),
    "obs studio (64bit)": ("obs studio (64bit)", "obs"),
    "camera": ("camera", "photo booth"),
    "fotos": ("fotos", "photos"),
    "microsoft store": ("microsoft store", "app store"),
    "paint": ("paint", "preview"),
}


def nome_app(nome_windows: str) -> str:
    par = APPS_EQUIVALENTES.get(nome_windows)
    return par[1] if (par and not WINDOWS) else nome_windows


# ------------------------------------------------------------ trava de arquivo
def travar(fd: int) -> None:
    """Trava 1 byte, sem esperar (erro se outro processo já travou)."""
    if WINDOWS:
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1)


def destravar(fd: int) -> None:
    if WINDOWS:
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.lockf(fd, fcntl.LOCK_UN, 1)
