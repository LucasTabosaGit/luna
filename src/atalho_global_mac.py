"""Atalho global do Mac para chamar a Luna de qualquer lugar.

    Ctrl + Alt + L  ->  traz a janela da Luna para a frente (abre se estiver
                        fechada) e já começa a ouvir, como dizer "Luna".

Usa o pynput (escuta as teclas no nível do sistema, como qualquer app de
atalho global do Mac). Precisa da permissão de Acessibilidade concedida
ao Python uma vez (Ajustes do Sistema -> Privacidade e Segurança ->
Acessibilidade) - sem ela o pynput não recebe as teclas.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
URL = "http://127.0.0.1:8777"
TECLA = "<ctrl>+<alt>+l"
DESCRICAO = "Ctrl+Alt+L"

# Apps onde a janela "Luna" (modo --app) pode estar aberta.
_NAVEGADORES = ("Google Chrome", "Microsoft Edge", "Chromium")

_hotkey = [None]
_trava = threading.Lock()


def _janela_luna() -> tuple[str, str] | None:
    """(processo, nome da janela) da janela "Luna" aberta num navegador, ou None."""
    lista = ", ".join('"%s"' % a for a in _NAVEGADORES)
    script = f'''
    tell application "System Events"
        repeat with p in {{{lista}}}
            if exists process p then
                tell process p
                    repeat with w in windows
                        if name of w starts with "Luna" then
                            return (name of p) & "|" & (name of w)
                        end if
                    end repeat
                end tell
            end if
        end repeat
    end tell
    return ""
    '''
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True,
                           text=True, timeout=5)
    except Exception:  # noqa: BLE001
        return None
    saida = r.stdout.strip()
    if not saida or "|" not in saida:
        return None
    proc, nome = saida.split("|", 1)
    return proc, nome


def trazer_para_frente() -> bool:
    """True se achou a janela; senão abre uma nova (modo app, perfil próprio)."""
    achada = _janela_luna()
    if achada:
        proc, nome = achada
        nome_escapado = nome.replace('"', '\\"')
        script = (
            f'tell application "System Events"\n'
            f'    tell process "{proc}"\n'
            f'        set frontmost to true\n'
            f'        perform action "AXRaise" of (first window whose name is "{nome_escapado}")\n'
            f'    end tell\n'
            f'end tell'
        )
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
        except Exception:  # noqa: BLE001
            pass
        return True
    nav = next((Path("/Applications") / n for n in
               ("Google Chrome.app", "Microsoft Edge.app", "Chromium.app")
               if (Path("/Applications") / n).exists()), None)
    if nav:
        subprocess.Popen(["open", "-na", str(nav), "--args",
                          "--app=" + URL, "--window-size=1280,800",
                          "--user-data-dir=" + str(RAIZ / "navegador"),
                          "--autoplay-policy=no-user-gesture-required",
                          "--no-first-run", "--no-default-browser-check"])
    else:
        subprocess.Popen(["open", URL])
    return False


def minimizar_principal() -> None:
    """Minimiza a janela "Luna" (usado ao abrir o modo mini)."""
    achada = _janela_luna()
    if not achada:
        return
    proc, nome = achada
    nome_escapado = nome.replace('"', '\\"')
    script = (
        f'tell application "System Events"\n'
        f'    tell process "{proc}"\n'
        f'        set value of attribute "AXMinimized" of '
        f'(first window whose name is "{nome_escapado}") to true\n'
        f'    end tell\n'
        f'end tell'
    )
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    except Exception:  # noqa: BLE001
        pass


def iniciar(ao_apertar) -> bool:
    """Registra o atalho global. `ao_apertar()` roda numa thread do pynput."""
    try:
        from pynput import keyboard
    except Exception as e:  # noqa: BLE001
        print("  [atalho] pynput indisponível: %s" % str(e)[:80], flush=True)
        return False

    def _disparar():
        try:
            ao_apertar()
        except Exception as e:  # noqa: BLE001
            print("  [atalho] falhou: %s" % str(e)[:80], flush=True)

    with _trava:
        if _hotkey[0] is not None:
            return True
        try:
            hk = keyboard.GlobalHotKeys({TECLA: _disparar})
            hk.start()
            _hotkey[0] = hk
            return True
        except Exception as e:  # noqa: BLE001
            print("  [atalho] não consegui registrar (falta permissão de "
                  "Acessibilidade em Ajustes do Sistema?): %s" % str(e)[:100], flush=True)
            return False


def parar() -> None:
    with _trava:
        if _hotkey[0] is not None:
            _hotkey[0].stop()
            _hotkey[0] = None
