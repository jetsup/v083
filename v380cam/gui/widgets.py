"""Reusable premium widgets."""
import math

from PySide6.QtCore import (QEasingCurve, QPointF, QSize, QPropertyAnimation, QRectF, Qt, QTimer, Signal)
from PySide6.QtGui import (QColor, QConicalGradient, QFont, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QPushButton, QWidget)

from . import theme
from .theme import C, icon, pixmap


def shadow(widget, blur=40, color="#000000", alpha=140, dy=10):
    fx = QGraphicsDropShadowEffect(widget)
    col = QColor(color)
    col.setAlpha(alpha)
    fx.setBlurRadius(blur)
    fx.setOffset(0, dy)
    fx.setColor(col)
    widget.setGraphicsEffect(fx)
    return fx


def label(text, name=None, wrap=False):
    l = QLabel(text)
    if name:
        l.setObjectName(name)
    l.setWordWrap(wrap)
    return l


def button(text="", name="ghost", ico=None, color=None, size=18, checkable=False):
    b = QPushButton(text)
    b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    b.setCheckable(checkable)
    if ico:
        b.setIcon(icon(ico, color or C["text"], size))
        b.setIconSize(QSize(size, size))
    return b


TONES = {"ok": (C["ok"], "53,214,160"), "warn": (C["warn"], "255,181,71"),
         "bad": (C["bad"], "255,93,122"), "accent": (C["accent"], "124,140,255"),
         "muted": (C["muted"], "140,149,169")}


class Chip(QFrame):
    """Tinted pill with optional icon."""
    def __init__(self, text, tone="muted", ico=None):
        super().__init__()
        col, rgb = TONES[tone]
        self.setStyleSheet(f"QFrame{{background:rgba({rgb},.12);border:1px solid rgba({rgb},.28);"
                           f"border-radius:10px;}} QLabel{{background:transparent;border:none;color:{col};"
                           f"font-size:11.5px;font-weight:700;}}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 10 if text else 8, 3)
        lay.setSpacing(5)
        if ico:
            i = QLabel()
            i.setPixmap(pixmap(ico, col, 13, 2))
            lay.addWidget(i)
        if text:
            lay.addWidget(QLabel(text))


class Pulse(QWidget):
    """Small pulsing status dot."""
    def __init__(self, color=C["ok"], size=10):
        super().__init__()
        self.color, self._t = QColor(color), 0.0
        self.setFixedSize(size + 10, size + 10)
        self.timer = QTimer(self, interval=40, timeout=self._tick)
        self.timer.start()

    def set_color(self, color):
        self.color = QColor(color)

    def _tick(self):
        self._t = (self._t + 0.04) % 1.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        halo = QColor(self.color)
        halo.setAlphaF(0.45 * (1 - self._t))
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        r = 4 + 5 * self._t
        p.drawEllipse(c, r, r)
        p.setBrush(self.color)
        p.drawEllipse(c, 4, 4)


class Radar(QWidget):
    """Animated radar sweep for scanning / empty states."""
    def __init__(self, size=220):
        super().__init__()
        self.setFixedSize(size, size)
        self.angle, self.active = 0.0, True
        self.dots = [(0.55, 0.9), (0.72, 3.4), (0.35, 5.0)]
        self.timer = QTimer(self, interval=25, timeout=self._tick)
        self.timer.start()

    def _tick(self):
        self.angle = (self.angle + (3.2 if self.active else 0.8)) % 360
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = min(self.width(), self.height())
        c = QPointF(self.width() / 2, self.height() / 2)
        R = w / 2 - 4
        glow = QRadialGradient(c, R)
        glow.setColorAt(0, QColor(124, 140, 255, 46))
        glow.setColorAt(1, QColor(124, 140, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, R, R)
        pen = QPen(QColor(124, 140, 255, 60), 1)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for k in (1.0, 0.72, 0.44, 0.16):
            p.drawEllipse(c, R * k, R * k)
        p.drawLine(QPointF(c.x() - R, c.y()), QPointF(c.x() + R, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - R), QPointF(c.x(), c.y() + R))
        sweep = QConicalGradient(c, -self.angle)
        sweep.setColorAt(0.0, QColor(178, 108, 255, 150))
        sweep.setColorAt(0.16, QColor(124, 140, 255, 0))
        sweep.setColorAt(1.0, QColor(124, 140, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(sweep)
        p.drawEllipse(c, R, R)
        for rr, a in self.dots:                     # blips light up as the sweep passes
            ang = math.degrees(a) % 360
            diff = (self.angle - ang) % 360
            alpha = max(0.0, 1 - diff / 220) if self.active else 0.25
            col = QColor(C["ok"])
            col.setAlphaF(alpha)
            p.setBrush(col)
            p.drawEllipse(QPointF(c.x() + R * rr * math.cos(a), c.y() - R * rr * math.sin(a)), 4, 4)
        p.setBrush(QColor(C["accent"]))
        p.drawEllipse(c, 5, 5)


class Thumb(QWidget):
    """Rounded live preview with gradient placeholder."""
    def __init__(self, w=296, h=170):
        super().__init__()
        self.setFixedSize(w, h)
        self.image = None
        self.live = False
        self.locked = False

    def set_frame(self, qimage):
        self.image = qimage
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.setClipPath(path)
        g = QLinearGradient(0, 0, r.width(), r.height())
        g.setColorAt(0, QColor("#1B2133"))
        g.setColorAt(1, QColor("#10131C"))
        p.fillRect(r, g)
        if self.image is not None:
            iw, ih = self.image.width(), self.image.height()
            s = max(r.width() / iw, r.height() / ih)          # cover
            dw, dh = iw * s, ih * s
            p.drawImage(QRectF((r.width() - dw) / 2, (r.height() - dh) / 2, dw, dh), self.image)
            shade = QLinearGradient(0, r.height() * 0.5, 0, r.height())
            shade.setColorAt(0, QColor(0, 0, 0, 0))
            shade.setColorAt(1, QColor(0, 0, 0, 120))
            p.fillRect(r, shade)
        else:
            ico = pixmap("lock" if self.locked else "cctv", C["faint"], 44, 1.4)
            p.drawPixmap(int((r.width() - 44) / 2), int((r.height() - 44) / 2 - 8), ico)
            p.setPen(QColor(C["faint"]))
            f = QFont(self.font())
            f.setPixelSize(12)
            p.setFont(f)
            p.drawText(QRectF(0, r.height() / 2 + 20, r.width(), 24), Qt.AlignHCenter,
                       "Sign in to preview" if self.locked else "No preview")
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(.5, .5, -.5, -.5), 14, 14)
        if self.live:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(QRectF(12, 12, 54, 22), 11, 11)
            p.setBrush(QColor(C["bad"]))
            p.drawEllipse(QPointF(24, 23), 3.5, 3.5)
            p.setPen(QColor("white"))
            f = QFont(self.font())
            f.setPixelSize(11)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(32, 12, 36, 22), Qt.AlignVCenter, "LIVE")


class VideoView(QWidget):
    """Aspect-fit video surface. Emits drag/wheel for virtual PTZ."""
    dragged = Signal(float, float)
    wheeled = Signal(float)

    def __init__(self):
        super().__init__()
        self.image = None
        self.message = ""
        self.sub = ""
        self._last = None
        self.setMinimumSize(480, 320)
        self.setCursor(Qt.OpenHandCursor)
        self.draggable = False

    def set_frame(self, img: QImage):
        self.image = img
        self.update()

    def set_message(self, title, sub=""):
        self.message, self.sub = title, sub
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 20, 20)
        p.setClipPath(path)
        p.fillRect(r, QColor("#05060A"))
        if self.image is not None and not self.image.isNull():
            iw, ih = self.image.width(), self.image.height()
            s = min(r.width() / iw, r.height() / ih)
            dw, dh = iw * s, ih * s
            p.drawImage(QRectF((r.width() - dw) / 2, (r.height() - dh) / 2, dw, dh), self.image)
        if self.message:
            p.fillRect(r, QColor(5, 6, 10, 200 if self.image is not None else 255))
            p.setPen(QColor(C["text"]))
            f = QFont(self.font())
            f.setPixelSize(17)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(0, r.height() / 2 - 24, r.width(), 28), Qt.AlignHCenter, self.message)
            f.setPixelSize(13)
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(0, r.height() / 2 + 8, r.width(), 24), Qt.AlignHCenter, self.sub)
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(.5, .5, -.5, -.5), 20, 20)

    def mousePressEvent(self, e):
        self._last = e.position()
        if self.draggable:
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._last is not None and self.draggable:
            d = e.position() - self._last
            self._last = e.position()
            self.dragged.emit(d.x(), d.y())

    def mouseReleaseEvent(self, _):
        self._last = None
        self.setCursor(Qt.OpenHandCursor if self.draggable else Qt.ArrowCursor)

    def wheelEvent(self, e):
        self.wheeled.emit(e.angleDelta().y() / 120.0)


class Toast(QLabel):
    """Bottom-centre notification that fades away."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setStyleSheet(f"#toast{{background:{C['card_hi']};border:1px solid {C['border_hi']};"
                           f"border-radius:14px;padding:12px 18px;font-weight:600;}}")
        self.fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.fx)
        self.anim = QPropertyAnimation(self.fx, b"opacity", self)
        self.anim.setDuration(350)
        self.timer = QTimer(self, singleShot=True, timeout=self._out)
        self.hide()

    def show_message(self, text, tone="ok"):
        col = {"ok": C["ok"], "bad": C["bad"], "warn": C["warn"]}.get(tone, C["accent"])
        self.setStyleSheet(f"#toast{{background:{C['card_hi']};border:1px solid {col};"
                           f"border-radius:14px;padding:12px 18px;font-weight:600;}}")
        self.setText(text)
        self.adjustSize()
        p = self.parentWidget()
        self.move((p.width() - self.width()) // 2, p.height() - self.height() - 28)
        self.show()
        self.raise_()
        self.anim.stop()
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        try:
            self.anim.finished.disconnect()
        except RuntimeError:
            pass
        self.anim.start()
        self.timer.start(3200)

    def _out(self):
        self.anim.stop()
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.finished.connect(self.hide)
        self.anim.start()
