"""Atalho global do Windows para chamar a Luna de qualquer lugar.

    Ctrl + Alt + L  ->  traz a janela da Luna para a frente (abre se estiver
                        fechada) e já começa a ouvir, como dizer "Luna".

Usa RegisterHotKey (atalho registrado no Windows, NÃO um hook de teclado:
não lê o que você digita e não assusta o antivírus). Roda numa thread com
a própria fila de mensagens, dentro do servidor.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import threading
from pathlib import Path

u32 = ctypes.windll.user32
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
TECLA = ord("L")
DESCRICAO = "Ctrl+Alt+L"
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
RAIZ = Path(__file__).resolve().parent.parent
URL = "http://127.0.0.1:8777"

_thread_id = [0]


def janela_luna():
    achada = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cada(h, _):
        t = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(h, t, 256)
        c = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(h, c, 256)
        if t.value.startswith("Luna") and c.value.startswith("Chrome_WidgetWin") \
                and u32.IsWindowVisible(h):
            achada.append(h)
        return True
    u32.EnumWindows(proc(cada), 0)
    return achada[0] if achada else None


def trazer_para_frente() -> bool:
    """True se achou a janela; senão abre uma nova (modo app, perfil próprio)."""
    h = janela_luna()
    if h:
        u32.ShowWindow(h, 9)                  # SW_RESTORE
        u32.SetForegroundWindow(h)
        return True
    navs = [Path(os.environ.get(v, "")) / r for v, r in (
        ("ProgramFiles", r"Google\Chrome\Application\chrome.exe"),
        ("ProgramFiles(x86)", r"Google\Chrome\Application\chrome.exe"),
        ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"),
        ("ProgramFiles(x86)", r"Microsoft\Edge\Application\msedge.exe"),
        ("ProgramFiles", r"Microsoft\Edge\Application\msedge.exe"))]
    nav = next((n for n in navs if n.is_file()), None)
    if nav:
        subprocess.Popen([str(nav), "--app=" + URL, "--window-size=1280,800",
                          "--user-data-dir=" + str(RAIZ / "navegador"),
                          "--autoplay-policy=no-user-gesture-required",
                          "--no-first-run", "--no-default-browser-check"])
    else:
        os.startfile(URL)
    return False


def iniciar(ao_apertar) -> bool:
    """Registra o atalho numa thread própria. `ao_apertar()` roda nessa thread."""
    pronto = threading.Event()
    ok = [False]

    def laco():
        _thread_id[0] = ctypes.windll.kernel32.GetCurrentThreadId()
        ok[0] = bool(u32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, TECLA))
        pronto.set()
        if not ok[0]:
            return
        msg = wt.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    ao_apertar()
                except Exception as e:  # noqa: BLE001
                    print("  [atalho] falhou: %s" % str(e)[:80], flush=True)
        u32.UnregisterHotKey(None, 1)

    threading.Thread(target=laco, daemon=True, name="atalho-global").start()
    pronto.wait(3)
    return ok[0]


def parar() -> None:
    if _thread_id[0]:
        u32.PostThreadMessageW(_thread_id[0], WM_QUIT, 0, 0)
