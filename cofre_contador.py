"""
Contador de cofres de Fortnite (overlay flotante, Python + PySide6).

- Lee las líneas  __COFRE__|+1 / -1 / RESET  de chat.txt (las escribe cofre_detector.py
  y los atajos F9/F10), igual que el Dashboard.
- Con --detector también arranca aquí mismo el detector de sonido y los atajos globales.
- El conteo se guarda en cofres_estado.json (junto a este archivo) y sigue aunque lo cierres.

Al detectar un cofre: la tapa se abre, sale luz y saltan monedas.

Controles:  arrastrar = mover · rueda = +1/-1 · click derecho = menú · doble click = +1

Requisitos (Windows):   pip install PySide6-Essentials
                        (y para --detector: pip install numpy pyaudiowpatch; su salida queda en cofre_detector.log)
Uso:
    python cofre_contador.py                 # solo overlay (el detector corre aparte)
    python cofre_contador.py --detector      # overlay + detector de sonido + F9/F10
    python cofre_contador.py --chat C:\\ruta\\chat.txt

Opcional: pon un cofre_icono.png (fondo transparente) junto a este archivo para usar tu propio icono.
"""
import argparse
import base64
import json
import math
import os
import random
import re
import subprocess
import sys
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QFontDatabase, QFontMetricsF,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

AQUI = os.path.dirname(os.path.abspath(__file__))
ESTADO = os.path.join(AQUI, "cofres_estado.json")
PATRON = re.compile(r"^__COFRE__\|(\+1|-1|RESET)\s*$")

W, H, M = 350, 148, 10           # tamaño de la ventana y margen para la sombra
ARC = 34
ORO_1, ORO_2 = QColor("#FFD23F"), QColor("#F2A413")
ORO_MARCO = QColor("#5A3A06")


def c(hexa, a=255):
    col = QColor(hexa)
    col.setAlpha(a)
    return col


def mezcla(a, b, t):
    t = max(0.0, min(1.0, t))
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t),
                  round(a.alpha() + (b.alpha() - a.alpha()) * t))


# ------------------------------------------------------------------ dibujo
def gema(p, cx, cy, rx, ry, color):
    g = QLinearGradient(cx, cy - ry, cx, cy + ry)
    g.setColorAt(0, mezcla(color, QColor("white"), 0.55))
    g.setColorAt(0.55, color)
    g.setColorAt(1, mezcla(color, QColor("black"), 0.35))
    p.setBrush(QBrush(g))
    p.setPen(QPen(ORO_MARCO, 1.3))
    p.drawEllipse(QPointF(cx, cy), rx, ry)
    p.setPen(Qt.NoPen)
    p.setBrush(c("#FFFFFF", 175))
    p.drawEllipse(QPointF(cx - rx * 0.3, cy - ry * 0.35), rx * 0.32, ry * 0.24)


def _pintar_vector(p, x, y, w, h):
    """Cofre con calavera, marco dorado y gemas (diseño base 64x56), usado si no hay cofre_icono.png."""
    p.save()
    p.translate(x, y)
    p.scale(w / 64.0, h / 56.0)

    tapa = QPainterPath()
    tapa.moveTo(2, 28)
    tapa.cubicTo(2, -2, 62, -2, 62, 28)
    tapa.closeSubpath()
    cuerpo = QPainterPath()
    cuerpo.addRoundedRect(QRectF(2, 24, 60, 30), 7, 7)
    sil = tapa.united(cuerpo)

    # madera
    g = QLinearGradient(0, 0, 0, 56)
    g.setColorAt(0, c("#A36A34"))
    g.setColorAt(1, c("#5A3719"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawPath(sil)

    p.save()
    p.setClipPath(sil)
    p.setPen(QPen(c("#3F2510", 150), 1))
    for ty in range(10, 54, 8):                              # tablones
        p.drawLine(0, ty, 64, ty)
    p.setPen(Qt.NoPen)
    p.setBrush(c("#6E8F34"))                                 # musgo
    p.drawEllipse(QRectF(13, 11, 7, 4))
    p.drawEllipse(QRectF(44, 36, 8, 4))
    p.drawEllipse(QRectF(7, 38, 5, 3))
    # placa gris + calavera
    placa = QRectF(18, 13, 28, 37)
    g = QLinearGradient(0, 13, 0, 50)
    g.setColorAt(0, c("#C2C6CC"))
    g.setColorAt(1, c("#6A7078"))
    p.setBrush(QBrush(g))
    p.setPen(QPen(c("#3A3E44"), 1.4))
    p.drawRoundedRect(placa, 14, 14)
    p.setPen(Qt.NoPen)
    p.setBrush(c("#E3E5E8"))
    p.drawRect(QRectF(26, 40, 12, 8))                        # dientes
    p.setPen(QPen(c("#3A3E44"), 1))
    for tx in (29, 32, 35):
        p.drawLine(tx, 40, tx, 48)
    p.restore()

    # marco dorado
    g = QLinearGradient(0, 0, 0, 56)
    g.setColorAt(0, c("#FAD35A"))
    g.setColorAt(1, c("#B9780F"))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QBrush(g), 4.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(sil)
    p.setPen(QPen(QBrush(g), 3.0, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(3, 27, 61, 27)
    p.drawLine(6, 50, 58, 50)
    p.setPen(QPen(ORO_MARCO, 1))
    p.drawPath(sil)
    p.setPen(QPen(c("#FFFFFF", 110), 1.2, Qt.SolidLine, Qt.RoundCap))   # brillo de la tapa
    brillo = QPainterPath()
    brillo.moveTo(7, 22)
    brillo.cubicTo(7, 4, 24, 1, 32, 1)
    p.drawPath(brillo)

    gema(p, 32, 5.5, 6.5, 3.6, c("#3CCFE6"))
    gema(p, 11, 15, 5.2, 4.4, c("#C23CD8"))
    gema(p, 53, 15, 5.2, 4.4, c("#C23CD8"))
    gema(p, 11, 47, 3.6, 3.6, c("#C23CD8"))
    gema(p, 53, 47, 3.6, 3.6, c("#C23CD8"))
    gema(p, 26.5, 29, 3.9, 3.9, c("#E0261C"))
    gema(p, 37.5, 29, 3.9, 3.9, c("#E0261C"))

    p.setBrush(Qt.NoBrush)                                   # cadena
    p.setPen(QPen(c("#FAD35A"), 1.8))
    for cx in range(16, 50, 6):
        p.drawEllipse(QRectF(cx, 51.5, 5, 3))
    p.restore()




_vec_cache = None


def pixmap_vector():
    global _vec_cache
    if _vec_cache is None:
        pm = QPixmap(256, 224)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        _pintar_vector(q, 0, 0, 256, 224)
        q.end()
        _vec_cache = pm
    return _vec_cache


def abrir_curva(t):
    """0..1 de progreso -> cuánto está abierta la tapa (0 cerrado, 1 abierto; sobrepasa un poco al abrir)."""
    if t >= 1:
        return 0.0
    if t < 0.16:
        u = t / 0.16 - 1
        return 1 + 2.70158 * u ** 3 + 1.70158 * u ** 2          # OutBack
    if t < 0.60:
        return 1.0
    u = (t - 0.60) / 0.40
    ease = 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2   # InOutCubic
    return 1 - ease


def dibujar_cofre(p, x, y, w, h, flash, icono=None, apertura=0.0, limite=None):
    """Cofre partido en tapa y cuerpo: la tapa se levanta con 'apertura' (0..1) y sale luz del interior."""
    p.save()
    p.save()                                                 # los resplandores no salen del panel
    if limite is not None:
        p.setClipPath(limite)
    if flash > 0:                                            # halo al sumar
        g = QRadialGradient(x + w / 2, y + h / 2, w * 0.75)
        g.setColorAt(0, c("#FFD23F", int(150 * flash)))
        g.setColorAt(1, c("#FFD23F", 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(x + w / 2, y + h / 2), w * 0.8, h * 0.8)

    usar_png = icono is not None and not icono.isNull()
    p.restore()
    fuente = icono if usar_png else pixmap_vector()
    seam = SEAM_PNG if usar_png else SEAM_VEC
    W0, H0 = fuente.width(), fuente.height()
    k = min(w / W0, h / H0)
    dw, dh = W0 * k, H0 * k
    ox, oy = x + (w - dw) / 2, y + (h - dh) / 2
    sh = max(1, int(H0 * seam))
    seam_y = oy + dh * seam
    tapa = fuente.copy(0, 0, W0, sh)
    cuerpo = fuente.copy(0, sh, W0, H0 - sh)

    if apertura > 0:                                          # resplandor grande detrás del cofre
        o = min(apertura, 1.0)
        p.save()
        if limite is not None:
            p.setClipPath(limite)
        g = QRadialGradient(ox + dw * 0.5, seam_y, dw * 1.05)
        g.setColorAt(0, c("#FFE680", int(190 * o)))
        g.setColorAt(0.5, c("#FFC83C", int(80 * o)))
        g.setColorAt(1, c("#FFC83C", 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(ox + dw * 0.5, seam_y), dw * 1.05, dh * 0.9)
        p.restore()

    # cuerpo
    p.drawPixmap(QRectF(ox, seam_y, dw, dh - dh * seam), cuerpo, QRectF(cuerpo.rect()))

    if apertura > 0:
        o = min(apertura, 1.0)
        # interior luminoso en la boca del cofre
        cx = ox + dw * 0.50
        rx, ry = dw * 0.40, max(1.0, dh * 0.10 * o)
        g = QRadialGradient(cx, seam_y, rx)
        g.setColorAt(0, c("#FFFFFF", int(255 * o)))
        g.setColorAt(0.45, c("#FFE066", int(235 * o)))
        g.setColorAt(1, c("#FFB020", 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(cx, seam_y), rx, ry * 1.6)
        # haz de luz hacia arriba (aditivo)
        p.save()
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        alto = h * 0.70 * o
        haz = QPainterPath()
        haz.moveTo(cx - dw * 0.30, seam_y)
        haz.lineTo(cx - dw * 0.52, seam_y - alto)
        haz.lineTo(cx + dw * 0.52, seam_y - alto)
        haz.lineTo(cx + dw * 0.30, seam_y)
        haz.closeSubpath()
        g = QLinearGradient(0, seam_y, 0, seam_y - alto)
        g.setColorAt(0, c("#FFF2A8", int(190 * o)))
        g.setColorAt(1, c("#FFC83C", 0))
        p.setBrush(QBrush(g))
        p.drawPath(haz)
        p.restore()

    # tapa (gira sobre la bisagra del lado derecho)
    p.save()
    bis = QPointF(ox + dw * 0.84, seam_y)
    p.translate(bis)
    p.rotate(ANGULO_TAPA * apertura)
    p.translate(-bis.x(), -bis.y() - dh * 0.03 * apertura)
    p.drawPixmap(QRectF(ox, oy, dw, dh * seam), tapa, QRectF(tapa.rect()))
    p.restore()
    p.restore()


SEAM_PNG = 0.44        # fracción de la altura donde se parte tapa/cuerpo en cofre_icono.png
SEAM_VEC = 0.48        # idem para el cofre dibujado en código
ANGULO_TAPA = 30       # grados que se levanta la tapa


def moneda(p, x, y, r, giro, alfa):
    ancho = max(0.6, abs(math.cos(giro)) * r)
    p.setPen(QPen(c("#7A4A06", alfa), 1.1))
    g = QLinearGradient(x, y - r, x, y + r)
    g.setColorAt(0, c("#FFE680", alfa))
    g.setColorAt(1, c("#E19A12", alfa))
    p.setBrush(QBrush(g))
    p.drawEllipse(QPointF(x, y), ancho, r)


def estrella(p, x, y, r, color):
    path = QPainterPath()
    for i in range(8):
        ang = math.pi / 4 * i - math.pi / 2
        rr = r if i % 2 == 0 else r * 0.28
        pt = QPointF(x + math.cos(ang) * rr, y + math.sin(ang) * rr)
        path.moveTo(pt) if i == 0 else path.lineTo(pt)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPath(path)


_fuente = None


def familia_numero():
    global _fuente
    if _fuente is None:
        fams = set(QFontDatabase.families())
        _fuente = next((f for f in ("Segoe UI Black", "Arial Black", "Segoe UI") if f in fams), "Sans Serif")
    return _fuente


def dibujar(p, w, h, texto, t=1.0, chispas=(), icono=None, monedas=()):
    """Dibuja el HUD. 't' es el progreso de la animación de abrir (0..1; 1 = reposo)."""
    flash = max(0.0, 1 - t / 0.55)
    apertura = abrir_curva(t)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
    panel = QRectF(M, M, w - 2 * M, h - 2 * M)

    # sombra suave
    p.setPen(Qt.NoPen)
    for i in range(8, 0, -1):
        p.setBrush(c("#000000", int(6 + (9 - i) * 2)))
        p.drawRoundedRect(panel.adjusted(-i * 0.8, -i * 0.5 + 3, i * 0.8, i * 0.8 + 3), ARC + i, ARC + i)

    # cuerpo translúcido
    g = QLinearGradient(0, panel.top(), 0, panel.bottom())
    g.setColorAt(0, QColor(72, 85, 88, 205))
    g.setColorAt(1, QColor(42, 52, 56, 218))
    p.setBrush(QBrush(g))
    p.drawRoundedRect(panel, ARC, ARC)
    g = QLinearGradient(0, panel.top(), 0, panel.center().y())      # brillo superior
    g.setColorAt(0, QColor(255, 255, 255, 30))
    g.setColorAt(1, QColor(255, 255, 255, 0))
    p.setBrush(QBrush(g))
    p.drawRoundedRect(QRectF(panel.left(), panel.top(), panel.width(), panel.height() / 2), ARC, ARC)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 40), 1.2))
    p.drawRoundedRect(panel, ARC, ARC)

    # medidas
    icon_w, icon_h, gap = 92, 84, 12
    f_label = QFont("Segoe UI", 11, QFont.Bold)
    f_label.setPixelSize(16)
    label_w = QFontMetricsF(f_label).horizontalAdvance("cofres")
    disponible = panel.width() - 52 - icon_w - gap - 8 - label_w
    tam = 80
    while True:
        f_num = QFont(familia_numero())
        f_num.setPixelSize(tam)
        f_num.setBold(True)
        fm = QFontMetricsF(f_num)
        if fm.horizontalAdvance(texto) <= disponible or tam <= 26:
            break
        tam -= 2
    num_w = fm.horizontalAdvance(texto)
    cap = fm.capHeight()
    bloque = icon_w + gap + num_w + 8 + label_w
    x0 = (w - bloque) / 2
    base = h / 2 + cap / 2

    # cofre (con pequeño salto al sumar)
    salto = 0
    ix, iy = x0, (h - icon_h) / 2 + salto
    limite = QPainterPath()
    limite.addRoundedRect(panel, ARC, ARC)
    dibujar_cofre(p, ix, iy, icon_w, icon_h, flash, icono, apertura, limite)

    # chispas
    for dx, dy, r, fase in chispas:
        u = max(0.0, min(1.0, t * 1.5 - fase))
        if 0 < u < 1:
            estrella(p, ix + icon_w / 2 + dx * (0.5 + u), iy + icon_h / 2 + dy * (0.5 + u),
                     r * (1 - u * 0.4), c("#FFE98A", int(230 * (1 - u))))

    # monedas que saltan del cofre (recortadas al panel)
    if t < 1 and monedas:
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(panel, ARC, ARC)
        p.setClipPath(clip)
        s_ = max(0.0, t - 0.10) * 1.25
        origen_x, origen_y = ix + icon_w * 0.5, iy + icon_h * 0.42
        alfa = int(255 * min(1.0, (1 - t) * 4))
        for vx, vy, giro, r in monedas:
            if s_ > 0:
                moneda(p, origen_x + vx * s_, origen_y + vy * s_ + 130 * s_ * s_, r, giro * s_, alfa)
        p.restore()

    # número
    nx = x0 + icon_w + gap
    path = QPainterPath()
    path.addText(0, 0, f_num, texto)
    escala = 1 + 0.08 * flash
    p.save()
    p.translate(nx + num_w / 2, base)
    p.scale(escala, escala)
    p.translate(-num_w / 2, 0)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(c("#000000", 95), 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))   # sombra
    p.translate(0, 2.5)
    p.drawPath(path)
    p.translate(0, -2.5)
    p.setPen(QPen(c("#3C2604", 225), 5.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))  # contorno
    p.drawPath(path)
    g = QLinearGradient(0, -cap, 0, 0)
    g.setColorAt(0, mezcla(ORO_1, QColor("white"), flash * 0.6))
    g.setColorAt(0.55, ORO_1)
    g.setColorAt(1, mezcla(ORO_2, ORO_1, flash * 0.6))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawPath(path)
    p.restore()

    # "cofres"
    lx = nx + num_w + 8
    p.setFont(f_label)
    p.setPen(c("#000000", 150))
    p.drawText(QPointF(lx + 1, base + 1), "cofres")
    p.setPen(QColor("white"))
    p.drawText(QPointF(lx, base), "cofres")


# ------------------------------------------------------------------ estado y lectura de chat.txt
def cargar_estado():
    try:
        with open(ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def guardar_estado(d):
    try:
        tmp = ESTADO + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, ESTADO)
    except Exception:
        pass


class ColaChat:
    """Lee solo las líneas nuevas de chat.txt (empieza desde el final actual)."""

    def __init__(self, ruta):
        self.ruta = ruta
        self.pos = os.path.getsize(ruta) if os.path.exists(ruta) else 0

    def nuevas(self):
        try:
            tam = os.path.getsize(self.ruta)
        except OSError:
            return []
        if tam < self.pos:                       # el archivo se vació/rotó
            self.pos = 0
        if tam == self.pos:
            return []
        with open(self.ruta, "rb") as f:
            f.seek(self.pos)
            datos = f.read()
        i = datos.rfind(b"\n")
        if i < 0:
            return []
        self.pos += i + 1
        return datos[: i + 1].decode("utf-8", "ignore").splitlines()


# ------------------------------------------------------------------ ventana
class Contador(QWidget):
    def __init__(self, chat, captura=False, croma="#00FF00"):
        super().__init__()
        self.captura = captura
        self.croma = QColor(croma)
        if captura:
            # Modo captura (TikTok LIVE Studio / OBS): ventana normal con fondo SOLIDO.
            # Sin Qt.Tool (para que salga en la lista de ventanas) y sin transparencia.
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                                | Qt.WindowDoesNotAcceptFocus)
            self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Contador de cofres")
        self.resize(W, H)
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip("Arrastra: mover · Rueda: +1/-1 · Doble click: +1 · Click derecho: menú")

        est = cargar_estado()
        self.total = int(est.get("total", 0))
        self.setWindowOpacity(1.0 if captura else max(0.3, min(1.0, float(est.get("opacidad", 1.0)))))
        pant = QApplication.primaryScreen().availableGeometry()
        x, y = est.get("x", 40), est.get("y", 80)
        if not (pant.left() - W + 60 <= x <= pant.right() - 60 and pant.top() <= y <= pant.bottom() - 60):
            x, y = pant.left() + 40, pant.top() + 80
        self.move(x, y)

        ruta_icono = os.path.join(AQUI, "cofre_icono.png")      # si existe, tiene prioridad sobre el incrustado
        self.icono = QPixmap(ruta_icono) if os.path.exists(ruta_icono) else QPixmap()
        if self.icono.isNull():
            self.icono = QPixmap()
            self.icono.loadFromData(base64.b64decode(ICONO_B64))

        self.t = 1.0
        self.chispas = []
        self.monedas = []
        self.anim = QVariantAnimation(self)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setDuration(1300)
        self.anim.setEasingCurve(QEasingCurve.Linear)
        self.anim.valueChanged.connect(self._tick)
        self._drag = None

        self.cola = ColaChat(chat)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._leer)
        self.timer.start(150)
        QTimer.singleShot(600, self.probar)          # al arrancar se abre una vez, para ver que funciona

    def probar(self):
        """Reproduce la animación de abrir sin cambiar el número."""
        self.chispas = [(random.uniform(-48, 48), random.uniform(-36, 36),
                         random.uniform(3, 6), random.uniform(0, 0.35)) for _ in range(7)]
        self.monedas = [(random.uniform(-75, 75), random.uniform(-155, -95),
                         random.uniform(6, 14), random.uniform(4.0, 6.0)) for _ in range(9)]
        self.anim.stop()
        self.anim.start()

    # --- lógica
    def aplicar(self, orden):
        antes = self.total
        if orden == "+1":
            self.total += 1
        elif orden == "-1":
            self.total = max(0, self.total - 1)
        elif orden == "RESET":
            self.total = 0
        else:
            return
        self._guardar()
        if self.total > antes:
            self.chispas = [(random.uniform(-48, 48), random.uniform(-36, 36),
                             random.uniform(3, 6), random.uniform(0, 0.35)) for _ in range(7)]
            self.monedas = [(random.uniform(-75, 75), random.uniform(-155, -95),
                             random.uniform(6, 14), random.uniform(4.0, 6.0)) for _ in range(9)]
            self.anim.stop()
            self.anim.start()
        self.update()

    def _leer(self):
        for linea in self.cola.nuevas():
            m = PATRON.match(linea.strip())
            if m:
                self.aplicar(m.group(1))

    def _tick(self, v):
        self.t = float(v)
        self.update()

    def _guardar(self):
        guardar_estado({"total": self.total, "x": self.x(), "y": self.y(),
                        "opacidad": round(self.windowOpacity(), 2)})

    # --- eventos
    def paintEvent(self, _):
        p = QPainter(self)
        if self.captura:
            p.fillRect(self.rect(), self.croma)
        dibujar(p, self.width(), self.height(), str(self.total), self.t, self.chispas, self.icono, self.monedas)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._drag is not None:
            self._drag = None
            self._guardar()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.aplicar("+1")

    def wheelEvent(self, e):
        self.aplicar("+1" if e.angleDelta().y() > 0 else "-1")

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction("Probar animación", self.probar)
        m.addSeparator()
        m.addAction("+1", lambda: self.aplicar("+1"))
        m.addAction("−1", lambda: self.aplicar("-1"))
        m.addSeparator()
        m.addAction("Más opaco", lambda: self._opacidad(0.1))
        m.addAction("Más transparente", lambda: self._opacidad(-0.1))
        m.addSeparator()
        m.addAction("Reiniciar a 0…", self._reiniciar)
        m.addAction("Cerrar (el conteo se conserva)", QApplication.quit)
        m.exec(e.globalPos())

    def _opacidad(self, d):
        if self.captura:
            return
        self.setWindowOpacity(max(0.3, min(1.0, self.windowOpacity() + d)))
        self._guardar()

    def _reiniciar(self):
        r = QMessageBox.question(self, "Contador de cofres", "¿Reiniciar el contador de cofres a 0?")
        if r == QMessageBox.Yes:
            self.aplicar("RESET")

    def closeEvent(self, e):
        self._guardar()
        super().closeEvent(e)


# Tu cofre recortado, incrustado: así funciona sin necesitar cofre_icono.png.
ICONO_B64 = (
    "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"
    "sR/wlSszzGGGuizgAAAABJRU5ErkJggg=="
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat", default="chat.txt", help="ruta de chat.txt (por defecto, la carpeta actual)")
    ap.add_argument("--detector", action="store_true",
                    help="arranca también el detector de sonido y los atajos F9/F10")
    ap.add_argument("--umbral", type=float, default=0.50)
    ap.add_argument("--rel-db", type=float, default=15.0)
    ap.add_argument("--tecla", default="E,CLIC", help="tecla de interactuar ('none' = contar todo sonido de cofre)")
    ap.add_argument("--ventana", type=float, default=3.0, help="segundos entre pulsar la tecla y oír el cofre")
    ap.add_argument("--captura", action="store_true",
                    help="fondo solido y ventana normal para que TikTok LIVE Studio / OBS la capture")
    ap.add_argument("--croma", default="#00FF00", help="color de fondo en modo captura (aplica Chroma Key en LIVE Studio)")
    args = ap.parse_args()

    chat = os.path.abspath(args.chat)
    if not os.path.exists(chat):
        open(chat, "a", encoding="utf-8").close()

    proc = None
    if args.detector:
        # El detector corre como proceso aparte (igual que "python cofre_detector.py"): más fiable que un hilo.
        cmd = [sys.executable, "-u", os.path.join(AQUI, "cofre_detector.py"), "--chat", chat,
               "--umbral", str(args.umbral), "--rel-db", str(args.rel_db),
               "--tecla", args.tecla, "--ventana", str(args.ventana)]
        try:
            log = open(os.path.join(AQUI, "cofre_detector.log"), "w", encoding="utf-8")
            proc = subprocess.Popen(cmd, cwd=AQUI, stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=0x08000000 if os.name == "nt" else 0)   # sin ventana de consola
        except Exception as e:
            print(f"[COFRE] No se pudo iniciar el detector: {e}")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    if proc is not None:
        app.aboutToQuit.connect(proc.terminate)                  # el detector se cierra con el contador
    v = Contador(chat, args.captura, args.croma)
    v.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
