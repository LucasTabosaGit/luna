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

Janela nativa do Mac (AppKit/PyObjC): sem moldura, fundo transparente,
sempre por cima (NSFloatingWindowLevel) - o clique fora do globo/legenda
não é capturado (a janela é só do tamanho do conteúdo).

A posição fica salva em cache/mini.json.
"""
from __future__ import annotations

import colorsys
import io
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
TRAVA_PID = RAIZ / "cache" / "mini.pid"
WS_URL = "ws://127.0.0.1:8777/ws_espelho"
TAM = 150                 # lado do globo, em pontos (pixels lógicos do Mac)
LEG_L, LEG_A = 300, 92    # legenda (largura x altura máx.), em pontos
LEG_SEG = 9               # legenda some depois disso sem novidade
IMA = 40                  # distância (pt) que puxa para a borda da tela
MARGEM = 10
ARRASTO_PX = 5

import atalho_global_mac as atalho_global  # acha/traz/minimiza a janela principal (mesma pasta)


def _ja_aberto() -> bool:
    """True se já existe um mini rodando (lê/grava cache/mini.pid)."""
    import psutil
    try:
        pid = int(TRAVA_PID.read_text().strip())
        if psutil.pid_exists(pid):
            return True
    except (OSError, ValueError):
        pass
    try:
        TRAVA_PID.parent.mkdir(exist_ok=True)
        TRAVA_PID.write_text(str(os.getpid()))
    except OSError:
        pass
    return False


# ------------------------------------------------------------------ estado
class Estado:
    estado = "desligado"
    ws = None
    trava = threading.Lock()
    legenda = ""          # última frase dela (ou "Você: ...")
    rotulo = ""            # "pensando", "executando · terminal"...
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
    for nome in ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc",
                "/System/Library/Fonts/Geneva.ttf"):
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
def _salvar(x, y):
    try:
        ESTADO.parent.mkdir(exist_ok=True)
        ESTADO.write_text(json.dumps({"x": x, "y": y}), encoding="utf-8")
    except OSError:
        pass


def _tela_de(x) -> "AppKit.NSScreen":
    import AppKit
    for t in AppKit.NSScreen.screens():
        f = t.frame()
        if f.origin.x <= x < f.origin.x + f.size.width:
            return t
    return AppKit.NSScreen.mainScreen()


def _limitar(x, y, larg, alt):
    scr = _tela_de(x + larg / 2).visibleFrame()
    x = min(max(x, scr.origin.x), scr.origin.x + scr.size.width - larg)
    y = min(max(y, scr.origin.y), scr.origin.y + scr.size.height - alt)
    return x, y


def _posicao_inicial(larg, alt):
    try:
        d = json.loads(ESTADO.read_text(encoding="utf-8"))
        return _limitar(float(d["x"]), float(d["y"]), larg, alt)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    import AppKit
    scr = AppKit.NSScreen.mainScreen().visibleFrame()
    return scr.origin.x + scr.size.width - larg - 24, scr.origin.y + 24


def _pil_para_nsimage(img: Image.Image, larg_pt: float, alt_pt: float):
    import AppKit
    buf = io.BytesIO()
    img.save(buf, "PNG")
    dados = buf.getvalue()
    ns_dados = AppKit.NSData.dataWithBytes_length_(dados, len(dados))
    ns_img = AppKit.NSImage.alloc().initWithData_(ns_dados)
    ns_img.setSize_((larg_pt, alt_pt))
    return ns_img


def _importar_luna_view():
    """Cria a classe da view (precisa do AppKit já importado no processo)."""
    import AppKit

    class LunaView(AppKit.NSImageView):
        controlador = None

        def mouseDown_(self, event):
            c = self.controlador
            local = self.convertPoint_fromView_(event.locationInWindow(), None)
            if local.y < LEG_A:               # clicou na faixa da legenda: esconde
                Estado.quando = 0.0
                c.inicio_arrasto = None
                return
            c.inicio_arrasto = tuple(AppKit.NSEvent.mouseLocation())
            c.arrastou = False

        def mouseDragged_(self, event):
            c = self.controlador
            if c.inicio_arrasto is None:
                return
            p = AppKit.NSEvent.mouseLocation()
            x0, y0 = c.inicio_arrasto
            if not c.arrastou and math.hypot(p.x - x0, p.y - y0) > ARRASTO_PX:
                c.arrastou = True
            if c.arrastou:
                f = self.window().frame()
                self.window().setFrameOrigin_((f.origin.x + (p.x - x0), f.origin.y + (p.y - y0)))
                c.inicio_arrasto = (p.x, p.y)

        def mouseUp_(self, event):
            c = self.controlador
            if c.inicio_arrasto is None:
                return
            c.inicio_arrasto = None
            if c.arrastou:
                c.grudar()
            elif event.clickCount() >= 2:
                c.restaurar()
            else:
                threading.Thread(target=_clique, daemon=True).start()

    return LunaView


class Controlador:
    def __init__(self):
        import AppKit

        self.lado_pt = float(TAM)
        self.larg_pt = float(max(TAM, LEG_L))
        self.alt_pt = float(TAM + LEG_A)
        self.ox_pt = (self.larg_pt - self.lado_pt) / 2      # globo sempre centralizado
        esc = AppKit.NSScreen.mainScreen().backingScaleFactor() or 2.0
        self.esc = esc
        self.desenho = Desenho(round(self.lado_pt * esc))
        self.legenda = Legenda(esc)
        self.inicio_arrasto = None
        self.arrastou = False

        AppKit.NSApplication.sharedApplication()
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

        x, y = _posicao_inicial(self.larg_pt, self.alt_pt)
        rect = ((x, y), (self.larg_pt, self.alt_pt))
        self.win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False)
        self.win.setOpaque_(False)
        self.win.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.win.setLevel_(AppKit.NSFloatingWindowLevel)
        self.win.setHasShadow_(False)
        self.win.setIgnoresMouseEvents_(False)
        self.win.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary)

        LunaView = _importar_luna_view()
        self.view = LunaView.alloc().initWithFrame_(((0, 0), (self.larg_pt, self.alt_pt)))
        self.view.controlador = self
        self.win.setContentView_(self.view)
        self.win.orderFrontRegardless()

        self._pintar()
        import Foundation
        self._timer = Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.033, True, lambda t: self._pintar())

    def _pintar(self):
        globo = self.desenho.quadro(Estado.estado)
        visivel = time.time() - Estado.quando < LEG_SEG or Estado.estado in (
            "pensando", "executando", "transcrevendo", "falando")
        rot = Estado.rotulo if Estado.estado in ("pensando", "executando", "transcrevendo") else ""
        leg = self.legenda.quadro(rot, Estado.legenda if Estado.estado != "transcrevendo" else "", visivel)
        esc = self.esc
        larg_px, alt_px = round(self.larg_pt * esc), round(self.alt_pt * esc)
        lado_px, ox_px = round(self.lado_pt * esc), round(self.ox_pt * esc)
        img = Image.new("RGBA", (larg_px, alt_px), (0, 0, 0, 0))
        img.paste(globo, (ox_px, 0))
        lx = min(max(0, ox_px + lado_px // 2 - leg.width // 2), larg_px - leg.width)
        img.alpha_composite(leg, (lx, lado_px))
        self.view.setImage_(_pil_para_nsimage(img, self.larg_pt, self.alt_pt))

    def grudar(self):
        f = self.win.frame()
        fx, fy = f.origin.x, f.origin.y
        scr = _tela_de(fx + self.larg_pt / 2).visibleFrame()
        if fx - scr.origin.x < IMA:
            fx = scr.origin.x + MARGEM
        elif (scr.origin.x + scr.size.width) - (fx + self.larg_pt) < IMA:
            fx = scr.origin.x + scr.size.width - self.larg_pt - MARGEM
        if fy - scr.origin.y < IMA:
            fy = scr.origin.y + MARGEM
        elif (scr.origin.y + scr.size.height) - (fy + self.alt_pt) < IMA:
            fy = scr.origin.y + scr.size.height - self.alt_pt - MARGEM
        fx, fy = _limitar(fx, fy, self.larg_pt, self.alt_pt)
        self.win.setFrameOrigin_((fx, fy))
        _salvar(fx, fy)

    def restaurar(self):
        import AppKit
        atalho_global.trazer_para_frente()
        AppKit.NSApp.terminate_(None)


def main():
    if _ja_aberto():
        return
    threading.Thread(target=_espelho, daemon=True).start()
    atalho_global.minimizar_principal()
    import AppKit
    controlador = Controlador()  # noqa: F841 - guardar: os callbacks moram nele
    AppKit.NSApp.run()


if __name__ == "__main__":
    main()
