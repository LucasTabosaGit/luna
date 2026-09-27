"""Modo mini da Luna: só o globo, flutuando sempre por cima.

Aberto pelo botão "Mini" da tela (POST /mini). A janela principal é
MINIMIZADA e continua com o microfone e o áudio; o globo só espelha o
estado dela (/ws_espelho) e manda os cliques.

    1 clique ............. acorda para ouvir (ou, se ela está falando /
                           pensando, para tudo)
    legenda .............. abaixo do globo: o que ela está fazendo e a
                           última frase (some sozinha depois de alguns s)
    2 cliques ............ volta para a janela normal
    segurar e arrastar ... move o globo; perto de uma borda, gruda nela

Por que desenhado aqui e não com a página: o navegador embutido não deixa
o fundo transparente (testado: recorte, cor-chave e transparent=True -
sobrava sempre um quadrado). Uma janela "layered" com alfa por pixel mostra
SÓ o globo, e o clique fora dele passa para o que está atrás.

A posição fica salva em cache/mini.json.
"""
from __future__ import annotations

import colorsys
import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

RAIZ = Path(__file__).resolve().parent.parent
ESTADO = RAIZ / "cache" / "mini.json"
WS_URL = "ws://127.0.0.1:8777/ws_espelho"
TAM = 150                 # lado do globo, em pixels lógicos
LEG_L, LEG_A = 300, 92    # legenda (largura x altura máx.), pixels lógicos
LEG_SEG = 9               # legenda some depois disso sem novidade
IMA = 40                  # distância (px físicos) que puxa para a borda
MARGEM = 10
DUPLO_MS = 320
ARRASTO_PX = 5

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
k32 = ctypes.windll.kernel32
u32.SetProcessDPIAware()

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
u32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.DefWindowProcW.restype = LRESULT


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                ("r", ctypes.c_long), ("b", ctypes.c_long)]


class MONINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                ("dwFlags", wt.DWORD)]


class BLEND(ctypes.Structure):
    _fields_ = [("op", ctypes.c_byte), ("flags", ctypes.c_byte),
                ("alpha", ctypes.c_ubyte), ("fmt", ctypes.c_byte)]


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


g32.CreateDIBSection.restype = wt.HBITMAP
g32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BIH), wt.UINT,
                                 ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
g32.CreateCompatibleDC.argtypes = [wt.HDC]
g32.CreateCompatibleDC.restype = wt.HDC
g32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
g32.SelectObject.restype = wt.HGDIOBJ
g32.DeleteObject.argtypes = [wt.HGDIOBJ]
g32.DeleteDC.argtypes = [wt.HDC]
u32.GetDC.restype = wt.HDC
u32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
u32.CreateWindowExW.restype = wt.HWND
u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU,
                                wt.HINSTANCE, wt.LPVOID]
u32.LoadCursorW.restype = wt.HANDLE
u32.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
k32.GetModuleHandleW.restype = wt.HMODULE
u32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
u32.SetTimer.restype = ctypes.c_size_t
u32.KillTimer.argtypes = [wt.HWND, ctypes.c_size_t]
u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, wt.UINT]
u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
u32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
u32.MonitorFromWindow.restype = wt.HMONITOR
u32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONINFO)]
u32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
u32.SetCapture.argtypes = [wt.HWND]
u32.DestroyWindow.argtypes = [wt.HWND]
u32.SetForegroundWindow.argtypes = [wt.HWND]
u32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
u32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
u32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
u32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT),
                                    ctypes.POINTER(wt.SIZE), wt.HDC, ctypes.POINTER(wt.POINT),
                                    wt.COLORREF, ctypes.POINTER(BLEND), wt.DWORD]


# ------------------------------------------------------------------ estado
class Estado:
    estado = "desligado"
    ws = None
    trava = threading.Lock()
    legenda = ""          # última frase dela (ou "Você: ...")
    rotulo = ""           # "pensando", "executando · terminal"...
    quando = 0.0


def _espelho():
    """Recebe o estado da Luna; reconecta sozinho se o servidor reiniciar."""
    from websockets.sync.client import connect
    while True:
        try:
            with connect(WS_URL, open_timeout=3) as ws:
                Estado.ws = ws
                for msg in ws:
                    d = json.loads(msg)
                    tipo = d.get("tipo")
                    if tipo == "estado":
                        Estado.estado = d.get("estado", "ouvindo")
                        Estado.rotulo = {"transcrevendo": "ouvindo…", "pensando": "pensando…",
                                         "falando": "", "executando": "trabalhando…"}.get(Estado.estado, "")
                        if Estado.estado in ("pensando", "transcrevendo", "executando", "falando"):
                            Estado.quando = time.time()
                    elif tipo == "interrompido":
                        Estado.estado = "ouvindo"
                        Estado.rotulo = ""
                    elif tipo == "voce" and not d.get("parcial"):
                        Estado.legenda, Estado.quando = "Você: " + d.get("texto", ""), time.time()
                    elif tipo == "bot":
                        atual = Estado.legenda if not Estado.legenda.startswith("Você:") else ""
                        Estado.legenda = (atual + " " + d.get("texto", "")).strip()[-400:]
                        Estado.quando = time.time()
                    elif tipo == "aviso":
                        Estado.legenda, Estado.quando = d.get("texto", ""), time.time()
                    elif tipo == "ferramenta" and d.get("status") == "running":
                        Estado.rotulo = "usando " + (d.get("ferramenta") or "ferramenta")
                        Estado.quando = time.time()
        except Exception:  # noqa: BLE001
            pass
        Estado.ws = None
        Estado.estado = "desligado"
        time.sleep(1.5)


def _clique():
    ws = Estado.ws
    if ws is not None:
        try:
            with Estado.trava:
                ws.send(json.dumps({"tipo": "clique"}))
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ desenho
# Mesmos tons da tela principal: azul ouvindo, âmbar pensando, dourado falando.
CORES = {
    "desligado": ((0.60, 0.05, 0.55), 0.05, 0.25),
    "ouvindo": ((0.56, 0.75, 1.0), 0.25, 0.45),
    "gravando": ((0.56, 0.75, 1.0), 0.35, 0.55),
    "transcrevendo": ((0.09, 0.85, 1.0), 0.80, 1.4),
    "pensando": ((0.09, 0.85, 1.0), 0.95, 1.6),
    "executando": ((0.09, 0.85, 1.0), 0.95, 1.6),
    "falando": ((0.13, 0.75, 1.0), 0.70, 0.9),
}


def _esfera(n=96):
    ouro = math.pi * (3 - math.sqrt(5))
    pts = []
    for i in range(n):
        y = 1 - 2 * (i + 0.5) / n
        r = math.sqrt(1 - y * y)
        pts.append((math.cos(ouro * i) * r, y, math.sin(ouro * i) * r))
    pts = np.array(pts)
    d = np.linalg.norm(pts[:, None] - pts[None], axis=2)
    pares = [(i, j) for i in range(n) for j in range(i + 1, n) if d[i, j] < 0.36]
    return pts, pares


PTS, PARES = _esfera()


def _fonte(tam):
    from PIL import ImageFont
    for nome in ("segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(nome, tam)
        except OSError:
            continue
    return ImageFont.load_default()


def _quebrar(d, texto, fonte, largura, linhas_max):
    palavras, linhas, atual = texto.split(), [], ""
    for p in palavras:
        teste = (atual + " " + p).strip()
        if d.textlength(teste, font=fonte) <= largura:
            atual = teste
        else:
            if atual:
                linhas.append(atual)
            atual = p
    if atual:
        linhas.append(atual)
    if len(linhas) > linhas_max:          # mostra o FIM (o mais recente)
        linhas = linhas[-linhas_max:]
        linhas[0] = "…" + linhas[0]
    return linhas


class Legenda:
    """Balão escuro com a frase atual, desenhado com alfa (some suave)."""

    def __init__(self, esc):
        self.esc = esc
        self.w, self.h = round(LEG_L * esc), round(LEG_A * esc)
        self.f_txt = _fonte(round(13 * esc))
        self.f_rot = _fonte(round(10.5 * esc))
        self.alfa = 0.0
        self.cache = (None, None)

    def quadro(self, rotulo, texto, visivel):
        from PIL import Image, ImageDraw
        self.alfa += ((1.0 if visivel else 0.0) - self.alfa) * 0.18
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        if self.alfa < 0.02 or not (rotulo or texto):
            return img
        chave = (rotulo, texto)
        if self.cache[0] != chave:
            tmp = ImageDraw.Draw(img)
            pad = round(10 * self.esc)
            linhas = _quebrar(tmp, texto, self.f_txt, self.w - 2 * pad, 3) if texto else []
            alt_l = round(17 * self.esc)
            alt = pad * 2 + (round(15 * self.esc) if rotulo else 0) + alt_l * len(linhas)
            larg = max([tmp.textlength(ln, font=self.f_txt) for ln in linhas]
                       + [tmp.textlength(rotulo, font=self.f_rot) if rotulo else 0]) + 2 * pad
            larg = min(self.w, int(larg))
            base = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
            d = ImageDraw.Draw(base)
            x0 = (self.w - larg) // 2
            d.rounded_rectangle((x0, 0, x0 + larg, min(self.h - 1, alt)), radius=round(11 * self.esc),
                                fill=(22, 19, 16, 228), outline=(70, 62, 52, 255), width=1)
            y = pad
            if rotulo:
                d.text((x0 + pad, y - round(2 * self.esc)), rotulo.upper(), font=self.f_rot, fill=(236, 178, 72, 255))
                y += round(15 * self.esc)
            for ln in linhas:
                d.text((x0 + pad, y - round(2 * self.esc)), ln, font=self.f_txt, fill=(242, 238, 230, 255))
                y += alt_l
            self.cache = (chave, base)
        base = self.cache[1]
        if self.alfa < 0.98:
            a = base.getchannel("A").point(lambda v: int(v * self.alfa))
            base = base.copy()
            base.putalpha(a)
        return base


class Desenho:
    def __init__(self, lado):
        self.lado = lado
        self.giro = 0.0
        self.energia = 0.1
        self.cor = np.array(CORES["desligado"][0], dtype=float)
        self.t0 = time.time()
        s = lado * 2
        fundo = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(fundo)
        d.ellipse((s * 0.06, s * 0.06, s * 0.94, s * 0.94), fill=(14, 13, 12, 150))
        self.fundo = fundo.filter(ImageFilter.GaussianBlur(s * 0.03))

    def quadro(self, estado):
        alvo_cor, alvo_en, vel = CORES.get(estado, CORES["ouvindo"])
        self.cor += (np.array(alvo_cor) - self.cor) * 0.12
        t = time.time() - self.t0
        pulso = 0.0
        if estado == "falando":
            pulso = 0.5 + 0.5 * abs(math.sin(t * 7.1)) * abs(math.sin(t * 2.3 + 1))
        self.energia += (alvo_en + pulso * 0.4 - self.energia) * 0.15
        self.giro += 0.012 + vel * 0.02
        s = self.lado * 2
        img = self.fundo.copy()
        d = ImageDraw.Draw(img, "RGBA")
        raio = s * (0.30 + 0.035 * self.energia + 0.02 * pulso)
        cy_, sy = math.cos(self.giro), math.sin(self.giro)
        ci, si = math.cos(0.35), math.sin(0.35)
        x = PTS[:, 0] * cy_ + PTS[:, 2] * sy
        z = -PTS[:, 0] * sy + PTS[:, 2] * cy_
        y = PTS[:, 1] * ci - z * si
        z2 = PTS[:, 1] * si + z * ci
        k = 3.2 / (3.2 - z2)
        px = s / 2 + x * raio * k
        py = s / 2 + y * raio * k
        h, sat, v = self.cor
        r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(h, sat, v))
        luz = 0.35 + 0.65 * min(1.0, self.energia)
        for i, j in PARES:
            prof = (z2[i] + z2[j] + 2) / 4          # 0 atrás, 1 na frente
            a = int((40 + 110 * prof) * luz)
            d.line((px[i], py[i], px[j], py[j]), fill=(r, g, b, a), width=1)
        for i in np.argsort(z2):
            prof = (z2[i] + 1) / 2
            rr = s * (0.0035 + 0.0055 * prof) * (1 + 0.6 * self.energia)
            brilho = int((90 + 165 * prof) * (0.5 + 0.5 * luz))
            d.ellipse((px[i] - rr * 3, py[i] - rr * 3, px[i] + rr * 3, py[i] + rr * 3),
                      fill=(r, g, b, int(brilho * 0.16)))
            d.ellipse((px[i] - rr, py[i] - rr, px[i] + rr, py[i] + rr),
                      fill=(min(255, r + 60), min(255, g + 60), min(255, b + 60), brilho))
        return img.resize((self.lado, self.lado), Image.LANCZOS)


# ------------------------------------------------------------------ janela
def _cursor():
    p = wt.POINT()
    u32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _rect(h):
    r = RECT()
    u32.GetWindowRect(h, ctypes.byref(r))
    return r


def _area_util(h):
    mi = MONINFO()
    mi.cbSize = ctypes.sizeof(MONINFO)
    u32.GetMonitorInfoW(u32.MonitorFromWindow(h, 2), ctypes.byref(mi))
    return mi.rcWork


def _janela_principal():
    achada = []
    PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cada(h, _):
        t = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(h, t, 256)
        c = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(h, c, 256)
        if t.value == "Luna" and c.value.startswith("Chrome_WidgetWin"):
            achada.append(h)
        return True
    u32.EnumWindows(PROC(cada), 0)
    return achada[0] if achada else None


def _salvar(x, y):
    try:
        ESTADO.parent.mkdir(exist_ok=True)
        ESTADO.write_text(json.dumps({"x": x, "y": y}), encoding="utf-8")
    except OSError:
        pass


def _posicao_inicial(lado):
    try:
        d = json.loads(ESTADO.read_text(encoding="utf-8"))
        return int(d["x"]), int(d["y"])
    except (OSError, ValueError, KeyError):
        w = RECT()
        u32.SystemParametersInfoW(0x0030, 0, ctypes.byref(w), 0)
        return w.r - lado - 24, w.b - lado - 24


class Globo:
    TIMER_QUADRO, TIMER_CLIQUE = 1, 2

    def __init__(self):
        esc = u32.GetDpiForSystem() / 96
        self.lado = round(TAM * esc)
        self.desenho = Desenho(self.lado)
        self.legenda = Legenda(esc)
        self.larg = max(self.lado, self.legenda.w)
        self.alt = self.lado + self.legenda.h
        self.ox = (self.larg - self.lado) // 2          # globo centralizado
        self.baixo = None          # (cursor, posição da janela) ao apertar
        self.arrastou = False
        self.ultimo_clique = 0.0
        self._proc = WNDPROC(self._wndproc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = k32.GetModuleHandleW(None)
        wc.hCursor = u32.LoadCursorW(None, 32649)          # mãozinha
        wc.lpszClassName = "LunaMini"
        u32.RegisterClassExW(ctypes.byref(wc))
        gx, y = _posicao_inicial(self.lado)
        x = gx - self.ox
        estilo_ex = 0x80000 | 0x8 | 0x80 | 0x08000000       # LAYERED|TOPMOST|TOOLWINDOW|NOACTIVATE
        self.h = u32.CreateWindowExW(estilo_ex, "LunaMini", "Luna mini", 0x80000000,  # WS_POPUP
                                     x, y, self.larg, self.alt, None, None, wc.hInstance, None)
        self._posicionar(gx, y)
        self._pintar()
        u32.ShowWindow(self.h, 4)                           # SW_SHOWNOACTIVATE
        u32.SetTimer(self.h, self.TIMER_QUADRO, 33, None)

    # -- desenho com alfa por pixel
    def _pintar(self):
        globo = self.desenho.quadro(Estado.estado)
        visivel = time.time() - Estado.quando < LEG_SEG or Estado.estado in (
            "pensando", "executando", "transcrevendo", "falando")
        rot = Estado.rotulo if Estado.estado in ("pensando", "executando", "transcrevendo") else ""
        leg = self.legenda.quadro(rot, Estado.legenda if Estado.estado != "transcrevendo" else "", visivel)
        img = Image.new("RGBA", (self.larg, self.alt), (0, 0, 0, 0))
        img.paste(globo, (self.ox, 0))
        lx = min(max(0, self.ox + self.lado // 2 - leg.width // 2), self.larg - leg.width)
        img.alpha_composite(leg, (lx, self.lado))
        a = np.asarray(img, dtype=np.uint16)
        alfa = a[..., 3:4]
        bgra = np.concatenate([(a[..., [2, 1, 0]] * alfa // 255), alfa], axis=2).astype(np.uint8)
        dc_tela = u32.GetDC(None)
        dc = g32.CreateCompatibleDC(dc_tela)
        bih = BIH(ctypes.sizeof(BIH), self.larg, -self.alt, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        bmp = g32.CreateDIBSection(dc, ctypes.byref(bih), 0, ctypes.byref(bits), None, 0)
        dados = np.ascontiguousarray(bgra).tobytes()
        ctypes.memmove(bits, dados, len(dados))
        velho = g32.SelectObject(dc, bmp)
        tam = wt.SIZE(self.larg, self.alt)
        origem = wt.POINT(0, 0)
        mistura = BLEND(0, 0, 255, 1)                       # AC_SRC_ALPHA
        u32.UpdateLayeredWindow(self.h, dc_tela, None, ctypes.byref(tam), dc,
                                ctypes.byref(origem), 0, ctypes.byref(mistura), 2)
        g32.SelectObject(dc, velho)
        g32.DeleteObject(bmp)
        g32.DeleteDC(dc)
        u32.ReleaseDC(None, dc_tela)

    # -- mouse
    def _wndproc(self, h, msg, wp, lp):
        if msg == 0x0113:                                   # WM_TIMER
            if wp == self.TIMER_QUADRO:
                self._pintar()
            elif wp == self.TIMER_CLIQUE:
                u32.KillTimer(h, self.TIMER_CLIQUE)
                threading.Thread(target=_clique, daemon=True).start()
            return 0
        if msg == 0x0201:                                   # WM_LBUTTONDOWN
            r = _rect(h)
            cx, cy = _cursor()
            if cy - r.t > self.lado:                        # clicou na legenda: esconde
                Estado.quando = 0.0
                return 0
            self.baixo = (_cursor(), (r.l, r.t))
            self.arrastou = False
            u32.SetCapture(h)
            return 0
        if msg == 0x0200 and self.baixo:                    # WM_MOUSEMOVE
            (cx0, cy0), (x0, y0) = self.baixo
            cx, cy = _cursor()
            if not self.arrastou and math.hypot(cx - cx0, cy - cy0) > ARRASTO_PX:
                self.arrastou = True
            if self.arrastou:
                u32.SetWindowPos(h, 0, x0 + cx - cx0, y0 + cy - cy0, 0, 0, 0x1 | 0x4 | 0x10)
            return 0
        if msg == 0x0202 and self.baixo:                    # WM_LBUTTONUP
            u32.ReleaseCapture()
            self.baixo = None
            if self.arrastou:
                self._grudar()
                return 0
            agora = time.time()
            if agora - self.ultimo_clique < DUPLO_MS / 1000:
                u32.KillTimer(h, self.TIMER_CLIQUE)
                self.ultimo_clique = 0.0
                self._restaurar()
            else:
                self.ultimo_clique = agora
                u32.SetTimer(h, self.TIMER_CLIQUE, DUPLO_MS, None)
            return 0
        if msg == 0x0002:                                   # WM_DESTROY
            u32.PostQuitMessage(0)
            return 0
        return u32.DefWindowProcW(h, msg, wp, lp)

    def _grudar(self):
        r, w = _rect(self.h), _area_util(self.h)
        x, y = r.l + self.ox, r.t
        if x - w.l < IMA:
            x = w.l + MARGEM
        elif w.r - (x + self.lado) < IMA:
            x = w.r - self.lado - MARGEM
        if y - w.t < IMA:
            y = w.t + MARGEM
        elif w.b - (y + self.lado) < IMA:
            y = w.b - self.lado - MARGEM
        self._posicionar(x, y)
        _salvar(x, y)

    def _posicionar(self, gx, gy):
        """Globo em (gx, gy); a janela (mais larga, por causa da legenda) fica
        DENTRO do monitor e o globo se desloca dentro dela - grudado na
        borda esquerda, a legenda abre para a direita, e vice-versa."""
        w = _area_util(self.h)
        wx = min(max(gx - (self.larg - self.lado) // 2, w.l), w.r - self.larg)
        self.ox = gx - wx
        u32.SetWindowPos(self.h, 0, wx, gy, 0, 0, 0x1 | 0x4 | 0x10)

    def _restaurar(self):
        p = _janela_principal()
        if p:
            u32.ShowWindow(p, 9)                            # SW_RESTORE
            u32.SetForegroundWindow(p)
        else:
            os.startfile("http://127.0.0.1:8777")
        u32.DestroyWindow(self.h)


def main():
    if u32.FindWindowW("LunaMini", None):   # já aberto
        return
    threading.Thread(target=_espelho, daemon=True).start()
    globo = Globo()  # noqa: F841 - guardar: o callback da janela mora nele
    p = _janela_principal()
    if p:
        u32.ShowWindow(p, 6)                # SW_MINIMIZE
    m = wt.MSG()
    while u32.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
        u32.TranslateMessage(ctypes.byref(m))
        u32.DispatchMessageW(ctypes.byref(m))


if __name__ == "__main__":
    main()
