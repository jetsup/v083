"""V380 Studio - PySide6 desktop app."""
import sys
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QProgressBar, QScrollArea,
                               QSlider, QStackedWidget, QVBoxLayout, QWidget)

from ..client import V380Error
from ..dewarp import Dewarper
from ..hub import Hub
from . import theme
from .theme import C, icon, pixmap
from .widgets import (Chip, Pulse, Radar, Thumb, Toast, VideoView, button, label, shadow)

SNAP_DIR = Path.home() / "Pictures" / "V380 Studio"


def to_qimage(frame: np.ndarray) -> QImage:
    h, w = frame.shape[:2]
    return QImage(frame.data, w, h, w * 3, QImage.Format_BGR888)


class Job(QThread):
    """Run fn(*args) off the UI thread."""
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(float, str, list)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            self.done.emit(self.fn(*self.args))
        except V380Error as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- login
class LoginDialog(QDialog):
    def __init__(self, hub: Hub, dev: dict, parent=None, error=""):
        super().__init__(parent)
        self.hub, self.dev, self.cam_id = hub, dev, dev.get("cam_id") or ""
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 30, 30, 40)
        card = QFrame(objectName="card")
        card.setStyleSheet(f"#card{{background:{C['card']};border:1px solid {C['border_hi']};border-radius:24px;}}")
        shadow(card, 60, alpha=190, dy=18)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(34, 32, 34, 28)
        lay.setSpacing(0)

        top = QHBoxLayout()
        badge = QLabel()
        badge.setFixedSize(52, 52)
        badge.setAlignment(Qt.AlignCenter)
        badge.setPixmap(pixmap("lock", "white", 24, 1.8))
        badge.setStyleSheet(f"background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {C['accent']},stop:1 {C['accent2']});"
                            "border-radius:16px;")
        top.addWidget(badge)
        top.addStretch()
        close = button("", "icon", "x", C["muted"], 16)
        close.clicked.connect(self.reject)
        top.addWidget(close)
        lay.addLayout(top)
        lay.addSpacing(18)
        lay.addWidget(label("Sign in to camera", "h1"))
        lay.itemAt(lay.count() - 1).widget().setStyleSheet("font-size:24px;font-weight:750;")
        lay.addSpacing(6)
        who = dev.get("name") or dev["ip"]
        lay.addWidget(label(f"{who}  ·  {dev['ip']}", "muted"))
        lay.addSpacing(22)

        self.id_edit = None
        if not self.cam_id:
            lay.addWidget(self._caption("Camera ID", "id"))
            self.id_edit = QLineEdit(placeholderText="8-digit ID shown in the V380 app")
            lay.addWidget(self.id_edit)
            lay.addSpacing(14)

        lay.addWidget(self._caption("Username", "user"))
        self.user = QLineEdit("admin")
        lay.addWidget(self.user)
        lay.addSpacing(14)
        lay.addWidget(self._caption("Password", "key"))
        self.pw = QLineEdit(echoMode=QLineEdit.Password, placeholderText="Camera password")
        self.eye = self.pw.addAction(icon("eye", C["muted"], 18), QLineEdit.TrailingPosition)
        self.eye.triggered.connect(self._toggle_eye)
        self.pw.returnPressed.connect(self.submit)
        lay.addWidget(self.pw)
        lay.addSpacing(16)

        self.remember = QCheckBox("Remember on this computer")
        self.remember.setChecked(True)
        lay.addWidget(self.remember)
        if self.cam_id:
            hint = label(f"Saved against camera ID {self.cam_id}, so it still works if its IP changes.", "faint", True)
            lay.addSpacing(4)
            lay.addWidget(hint)

        self.err = QLabel(error)
        self.err.setWordWrap(True)
        self.err.setStyleSheet(f"color:{C['bad']};font-weight:600;font-size:12.5px;")
        self.err.setVisible(bool(error))
        lay.addSpacing(10)
        lay.addWidget(self.err)
        lay.addSpacing(18)
        row = QHBoxLayout()
        row.setSpacing(10)
        cancel = button("Cancel", "ghost")
        cancel.clicked.connect(self.reject)
        self.go = button("Connect", "primary", "arrow", "white", 16)
        self.go.setLayoutDirection(Qt.RightToLeft)
        self.go.clicked.connect(self.submit)
        self.go.setDefault(True)
        row.addWidget(cancel)
        row.addWidget(self.go, 1)
        lay.addLayout(row)
        self.setFixedWidth(470)
        self.pw.setFocus()

    def _caption(self, text, ico):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 0, 0, 7)
        h.setSpacing(7)
        i = QLabel()
        i.setPixmap(pixmap(ico, C["muted"], 14, 1.9))
        t = label(text.upper(), "eyebrow")
        h.addWidget(i)
        h.addWidget(t)
        h.addStretch()
        return w

    def _toggle_eye(self):
        show = self.pw.echoMode() == QLineEdit.Password
        self.pw.setEchoMode(QLineEdit.Normal if show else QLineEdit.Password)
        self.eye.setIcon(icon("eye_off" if show else "eye", C["muted"], 18))

    def submit(self):
        cam_id = self.cam_id or (self.id_edit.text().strip() if self.id_edit else "")
        if not self.pw.text():
            return self._error("Enter the camera password.")
        if not cam_id.isdigit():
            return self._error("Enter the numeric camera ID.")
        self.go.setEnabled(False)
        self.go.setText("Verifying…")
        self.err.hide()
        self.job = Job(self.hub.login, self.dev["key"], self.user.text().strip() or "admin",
                       self.pw.text(), self.remember.isChecked(), cam_id, self.dev["ip"])
        self.job.done.connect(self._ok)
        self.job.failed.connect(lambda m: self._error(m[0].upper() + m[1:] if m else "Failed"))
        self.job.start()

    def _ok(self, cam_id):
        self.cam_id = cam_id
        self.accept()

    def _error(self, msg):
        self.go.setEnabled(True)
        self.go.setText("Connect")
        self.err.setText(msg)
        self.err.show()
        self.pw.setFocus()
        self.pw.selectAll()


# --------------------------------------------------------------------------- cards
class CameraCard(QFrame):
    clicked = Signal(dict)

    def __init__(self, dev: dict):
        super().__init__()
        self.dev = dev
        self.session = None
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(330, 292)
        shadow(self, 36, alpha=110, dy=8)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(0)
        self.thumb = Thumb(296, 168)
        self.thumb.locked = not dev["saved"]
        lay.addWidget(self.thumb)
        lay.addSpacing(14)
        head = QHBoxLayout()
        name = label(dev["name"])
        name.setStyleSheet("font-size:15.5px;font-weight:700;")
        head.addWidget(name)
        head.addStretch()
        arrow = QLabel()
        arrow.setPixmap(pixmap("arrow", C["muted"], 18, 1.9))
        head.addWidget(arrow)
        lay.addLayout(head)
        lay.addSpacing(3)
        sub = f"{dev['ip']}  ·  ID {dev['cam_id']}" if dev["cam_id"] else dev["ip"]
        lay.addWidget(label(sub, "muted"))
        lay.addSpacing(12)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        if dev["kind"] == "v380":
            chips.addWidget(Chip("Saved login" if dev["saved"] else "Sign-in needed",
                                 "ok" if dev["saved"] else "warn", "shield" if dev["saved"] else "lock"))
            chips.addWidget(Chip("V380", "accent"))
        else:
            chips.addWidget(Chip("RTSP / ONVIF", "muted", "wifi"))
            chips.addWidget(Chip("Not supported yet", "warn"))
        chips.addStretch()
        lay.addLayout(chips)

    def attach(self, session):
        self.session = session
        self.thumb.live = True

    def refresh_thumb(self):
        s = self.session
        if s is not None and s.frame is not None:
            self.thumb.set_frame(to_qimage(s.frame).copy())

    def enterEvent(self, e):
        self.setProperty("hover", True)
        self.style().polish(self)

    def leaveEvent(self, e):
        self.setProperty("hover", False)
        self.style().polish(self)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.dev)


class DeviceRow(QFrame):
    def __init__(self, dev):
        super().__init__()
        self.setObjectName("row")
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 12, 16, 12)
        h.setSpacing(14)
        cam = dev["is_camera"]
        i = QLabel()
        i.setFixedSize(40, 40)
        i.setAlignment(Qt.AlignCenter)
        i.setPixmap(pixmap("camera" if cam else "devices", C["accent"] if cam else C["muted"], 20, 1.8))
        i.setStyleSheet(f"background:{'rgba(124,140,255,.12)' if cam else C['surface']};border-radius:12px;")
        h.addWidget(i)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = label(dev["name"] if cam else dev["ip"])
        t.setStyleSheet("font-weight:650;font-size:14px;")
        col.addWidget(t)
        col.addWidget(label(f"{dev['ip']}  ·  {dev['mac'] or 'MAC unknown'}", "faint"))
        h.addLayout(col)
        h.addStretch()
        for s in dev["services"][:5]:
            h.addWidget(Chip(s, "accent" if s.startswith("V380") else "muted"))
        if cam:
            h.addWidget(Chip("Camera", "ok", "camera"))


# --------------------------------------------------------------------------- pages
class CamerasPage(QWidget):
    openCamera = Signal(dict)
    scanRequested = Signal()

    def __init__(self):
        super().__init__()
        self.cards = []
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 34, 40, 0)
        head = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(6)
        col.addWidget(label("YOUR NETWORK", "eyebrow"))
        col.addWidget(label("Cameras", "h1"))
        self.sub = label("Scanning for cameras on your network…", "muted")
        col.addWidget(self.sub)
        head.addLayout(col)
        head.addStretch()
        self.scan_btn = button("Scan network", "primary", "radar", "white", 18)
        self.scan_btn.clicked.connect(self.scanRequested)
        head.addWidget(self.scan_btn, 0, Qt.AlignTop)
        root.addLayout(head)
        root.addSpacing(18)
        self.bar = QProgressBar(textVisible=False)
        self.bar.setRange(0, 1000)
        root.addWidget(self.bar)
        root.addSpacing(10)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        # empty / scanning state
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.setAlignment(Qt.AlignCenter)
        self.radar = Radar(230)
        ev.addWidget(self.radar, 0, Qt.AlignCenter)
        self.empty_t = label("Looking for cameras…")
        self.empty_t.setStyleSheet("font-size:19px;font-weight:700;")
        self.empty_t.setAlignment(Qt.AlignCenter)
        ev.addWidget(self.empty_t)
        self.empty_s = label("Make sure the camera is powered on and on the same Wi-Fi.", "muted")
        self.empty_s.setAlignment(Qt.AlignCenter)
        ev.addWidget(self.empty_s)
        self.stack.addWidget(empty)
        # results
        scroll = QScrollArea(widgetResizable=True)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(0, 8, 0, 30)
        self.grid.setSpacing(22)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        scroll.setWidget(self.body)
        self.stack.addWidget(scroll)

    def set_scanning(self, on, frac=0.0, msg=""):
        self.scan_btn.setEnabled(not on)
        self.scan_btn.setText("Scanning…" if on else "Scan network")
        self.radar.active = on
        self.bar.setValue(int(frac * 1000) if on else 0)
        if on:
            self.sub.setText(msg or "Scanning…")

    def show_devices(self, devs):
        for c in self.cards:
            c.setParent(None)
            c.deleteLater()
        self.cards = []
        cams = [d for d in devs if d["is_camera"]]
        if not cams:
            self.empty_t.setText("No cameras found")
            self.empty_s.setText(f"{len(devs)} device(s) responded, none look like a camera. "
                                 "Check power and Wi-Fi, then scan again.")
            self.stack.setCurrentIndex(0)
            self.sub.setText("No cameras found on your network")
            return
        n = len(cams)
        self.sub.setText(f"{n} camera{'s' if n != 1 else ''} found  ·  {len(devs)} devices on your network")
        for d in cams:
            card = CameraCard(d)
            card.clicked.connect(self.openCamera)
            self.cards.append(card)
        self._layout()
        self.stack.setCurrentIndex(1)

    def _layout(self):
        while self.grid.count():
            self.grid.takeAt(0)
        cols = max(1, (self.width() - 80) // 352)
        for i, c in enumerate(self.cards):
            self.grid.addWidget(c, i // cols, i % cols)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.cards:
            self._layout()


class DevicesPage(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 34, 40, 0)
        root.addWidget(label("ON YOUR NETWORK", "eyebrow"))
        root.addSpacing(6)
        root.addWidget(label("All devices", "h1"))
        root.addSpacing(6)
        self.sub = label("Run a scan to list every device that answered.", "muted")
        root.addWidget(self.sub)
        root.addSpacing(18)
        scroll = QScrollArea(widgetResizable=True)
        body = QWidget()
        self.col = QVBoxLayout(body)
        self.col.setContentsMargins(0, 0, 0, 30)
        self.col.setSpacing(10)
        self.col.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

    def show_devices(self, devs):
        while self.col.count() > 1:
            w = self.col.takeAt(0).widget()
            if w:
                w.deleteLater()
        for d in devs:
            self.col.insertWidget(self.col.count() - 1, DeviceRow(d))
        n = sum(d["is_camera"] for d in devs)
        self.sub.setText(f"{len(devs)} devices responded  ·  {n} camera{'s' if n != 1 else ''}")


class ViewerPage(QWidget):
    back = Signal()
    forget = Signal(dict)
    snapshot = Signal(str)

    def __init__(self):
        super().__init__()
        self.hub = self.session = self.dev = None
        self.dw = Dewarper(enabled=False)
        self._seq = 0
        self._stamp = 0.0
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 26, 32, 28)
        root.setSpacing(16)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.back_btn = button("", "icon", "back", C["text"], 18)
        self.back_btn.clicked.connect(self.back)
        top.addWidget(self.back_btn)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label("")
        self.title.setStyleSheet("font-size:20px;font-weight:750;")
        self.meta = label("", "muted")
        col.addWidget(self.title)
        col.addWidget(self.meta)
        top.addLayout(col)
        self.dot = Pulse(C["warn"])
        self.state = label("Connecting", "muted")
        top.addSpacing(8)
        top.addWidget(self.dot)
        top.addWidget(self.state)
        top.addStretch()

        seg = QFrame(objectName="segWrap")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(4, 4, 4, 4)
        sl.setSpacing(2)
        self.raw_btn = button("Raw fisheye", "seg", checkable=True)
        self.flat_btn = button("Dewarped", "seg", "globe", C["muted"], 15, checkable=True)
        self.raw_btn.setChecked(True)
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for b in (self.raw_btn, self.flat_btn):
            grp.addButton(b)
            sl.addWidget(b)
        self.flat_btn.toggled.connect(self._mode)
        top.addWidget(seg)
        self.snap_btn = button("Snapshot", "ghost", "camera", C["text"], 17)
        self.snap_btn.clicked.connect(self.take_snapshot)
        self.full_btn = button("", "icon", "expand", C["text"], 18)
        self.forget_btn = button("", "icon", "trash", C["bad"], 18)
        self.forget_btn.setToolTip("Forget saved login")
        self.forget_btn.clicked.connect(lambda: self.forget.emit(self.dev))
        for w in (self.snap_btn, self.full_btn, self.forget_btn):
            top.addWidget(w)
        root.addLayout(top)

        mid = QHBoxLayout()
        mid.setSpacing(18)
        self.video = VideoView()
        self.video.dragged.connect(self._drag)
        self.video.wheeled.connect(self._wheel)
        mid.addWidget(self.video, 1)

        self.panel = QFrame(objectName="panel")
        self.panel.setFixedWidth(250)
        pl = QVBoxLayout(self.panel)
        pl.setContentsMargins(20, 20, 20, 20)
        pl.setSpacing(4)
        hd = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(pixmap("sliders", C["accent"], 18, 1.9))
        hd.addWidget(ic)
        hd.addWidget(label("Virtual lens", "h2"))
        hd.addStretch()
        pl.addLayout(hd)
        pl.addWidget(label("Drag the picture to look around, scroll to zoom.", "faint", True))
        pl.addSpacing(12)
        self.sliders = {}
        for key, name, lo, hi in (("yaw", "Pan", -180, 180), ("pitch", "Tilt", -89, 89),
                                  ("roll", "Rotate", -180, 180), ("fov", "Field of view", 40, 150)):
            row = QHBoxLayout()
            row.addWidget(label(name, "muted"))
            row.addStretch()
            val = label("0°")
            val.setStyleSheet("font-weight:700;")
            row.addWidget(val)
            pl.addLayout(row)
            s = QSlider(Qt.Horizontal)
            s.setRange(lo, hi)
            s.valueChanged.connect(lambda v, k=key: self._slider(k, v))
            pl.addWidget(s)
            pl.addSpacing(8)
            self.sliders[key] = (s, val)
        reset = button("Reset view", "ghost", "refresh", C["text"], 16)
        reset.clicked.connect(self._reset)
        pl.addWidget(reset)
        pl.addStretch()
        self.stats = label("", "faint", True)
        pl.addWidget(self.stats)
        mid.addWidget(self.panel)
        root.addLayout(mid, 1)
        self.panel.hide()

        self.timer = QTimer(self, interval=15, timeout=self._tick)
        self._sync()

    # ---- session ----
    def open(self, hub, dev, session):
        self.hub, self.dev, self.session = hub, dev, session
        self.title.setText(dev["name"])
        self.meta.setText(f"{dev['ip']}  ·  ID {dev['cam_id']}  ·  {dev['mac']}")
        self._seq = 0
        self.video.image = None
        self.video.set_message("Connecting…", "Opening a secure session with the camera")
        self.timer.start()

    def close_session(self):
        self.timer.stop()
        if self.session and self.hub:
            self.hub.release(self.session)
        self.session = None

    def _tick(self):
        s = self.session
        if not s:
            return
        tone = {"live": (C["ok"], "Live"), "connecting": (C["warn"], "Connecting"),
                "error": (C["bad"], "Reconnecting"), "auth_failed": (C["bad"], "Login rejected")}
        col, txt = tone.get(s.status, (C["muted"], s.status.title()))
        self.dot.set_color(col)
        self.state.setText(txt)
        if s.status == "auth_failed":
            self.video.set_message("Login rejected", "The saved password no longer works. Go back and sign in again.")
            return
        if s.status == "error" and s.frame is None:
            self.video.set_message("Can't reach the camera", s.error or "Retrying…")
        if s.seq != self._seq and s.frame is not None:
            self._seq = s.seq
            self.video.set_message("")
            out = self.dw.apply(s.frame)
            self.video.set_frame(to_qimage(out).copy())
            now = time.time()
            if now - self._stamp > 0.5:
                self._stamp = now
                w, h = s.size
                self.stats.setText(f"{w}×{h}  ·  {s.fps:.0f} fps\nHEVC over LAN")

    # ---- dewarp controls ----
    def _mode(self, flat):
        self.dw.enabled = flat
        self.panel.setVisible(flat)
        self.video.draggable = flat
        self.video.setCursor(Qt.OpenHandCursor if flat else Qt.ArrowCursor)
        self.flat_btn.setIcon(icon("globe", C["text"] if flat else C["muted"], 15))
        self._sync()
        self._seq = -1

    def _slider(self, key, v):
        setattr(self.dw, key, float(v))
        self.sliders[key][1].setText(f"{v}°" if key != "fov" else f"{v}°")
        self._seq = -1

    def _sync(self):
        for k, (s, val) in self.sliders.items():
            s.blockSignals(True)
            v = int(round(getattr(self.dw, k)))
            s.setValue(v)
            s.blockSignals(False)
            val.setText(f"{v}°")

    def _reset(self):
        self.dw.reset()
        self._sync()
        self._seq = -1

    def _drag(self, dx, dy):
        k = self.dw.fov / max(self.video.width(), 1)
        self.dw.pan(dyaw=-dx * k, dpitch=-dy * k)
        self._sync()
        self._seq = -1

    def _wheel(self, d):
        if self.dw.enabled:
            self.dw.pan(dzoom=-d * 4)
            self._sync()
            self._seq = -1

    def take_snapshot(self):
        s = self.session
        if not s or s.frame is None:
            return
        import cv2
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        path = SNAP_DIR / f"{self.dev['cam_id']}_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
        cv2.imwrite(str(path), self.dw.apply(s.frame))
        self.snapshot.emit(str(path))


# --------------------------------------------------------------------------- shell
class MainWindow(QMainWindow):
    def __init__(self, hub: Hub):
        super().__init__()
        self.hub = hub
        self.setWindowTitle("V380 Studio")
        self.setWindowIcon(icon("camera", C["accent"], 64))
        self.resize(1320, 840)
        self.setMinimumSize(1000, 680)
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        self.sidebar = QFrame(objectName="sidebar")
        self.sidebar.setFixedWidth(236)
        sv = QVBoxLayout(self.sidebar)
        sv.setContentsMargins(20, 26, 20, 22)
        sv.setSpacing(6)
        brand = QHBoxLayout()
        lg = QLabel()
        lg.setPixmap(theme.logo_pixmap(40))
        brand.addWidget(lg)
        bt = QVBoxLayout()
        bt.setSpacing(0)
        bt.addWidget(label("V380 Studio", "brand"))
        bt.addWidget(label("Camera control", "brandSub"))
        brand.addLayout(bt)
        brand.addStretch()
        sv.addLayout(brand)
        sv.addSpacing(30)
        sv.addWidget(label("  WORKSPACE", "eyebrow"))
        sv.addSpacing(6)
        self.nav_cam = button("  Cameras", "nav", "grid", C["text"], 19, True)
        self.nav_dev = button("  All devices", "nav", "devices", C["muted"], 19, True)
        self.nav_cam.setChecked(True)
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for b in (self.nav_cam, self.nav_dev):
            grp.addButton(b)
            sv.addWidget(b)
        self.nav_cam.clicked.connect(lambda: self._nav(0))
        self.nav_dev.clicked.connect(lambda: self._nav(1))
        sv.addStretch()
        self.net = QFrame(objectName="panel")
        nl = QVBoxLayout(self.net)
        nl.setContentsMargins(14, 12, 14, 12)
        top = QHBoxLayout()
        self.net_dot = Pulse(C["muted"], 8)
        top.addWidget(self.net_dot)
        self.net_t = label("Idle")
        self.net_t.setStyleSheet("font-weight:650;font-size:13px;")
        top.addWidget(self.net_t)
        top.addStretch()
        nl.addLayout(top)
        self.net_s = label("Not scanned yet", "faint", True)
        nl.addWidget(self.net_s)
        sv.addWidget(self.net)
        h.addWidget(self.sidebar)

        self.pages = QStackedWidget()
        h.addWidget(self.pages, 1)
        self.cams = CamerasPage()
        self.devs = DevicesPage()
        self.viewer = ViewerPage()
        for p in (self.cams, self.devs, self.viewer):
            self.pages.addWidget(p)
        self.cams.scanRequested.connect(self.scan)
        self.cams.openCamera.connect(self.open_camera)
        self.viewer.back.connect(self.leave_viewer)
        self.viewer.forget.connect(self.forget)
        self.viewer.snapshot.connect(lambda p: self.toast.show_message(f"Saved  {Path(p).name}"))
        self.viewer.full_btn.clicked.connect(self.toggle_full)
        QShortcut(QKeySequence("F11"), self, activated=self.toggle_full)
        QShortcut(QKeySequence("Esc"), self, activated=self._esc)
        self.toast = Toast(root)
        self.thumb_timer = QTimer(self, interval=700, timeout=self._thumbs)
        self.thumb_timer.start()
        self.devices, self._held = [], []
        QTimer.singleShot(250, self.scan)

    # ---- navigation ----
    def _nav(self, i):
        if self.pages.currentWidget() is self.viewer:
            self.viewer.close_session()
            self._render(self.hub.refresh())
        self.nav_cam.setIcon(icon("grid", C["text"] if i == 0 else C["muted"], 19))
        self.nav_dev.setIcon(icon("devices", C["text"] if i == 1 else C["muted"], 19))
        self.pages.setCurrentIndex(i)

    def toggle_full(self):
        full = not self.isFullScreen()
        self.showFullScreen() if full else self.showNormal()
        self.sidebar.setVisible(not full)
        self.viewer.full_btn.setIcon(icon("shrink" if full else "expand", C["text"], 18))

    def _esc(self):
        if self.isFullScreen():
            self.toggle_full()

    # ---- scanning ----
    def scan(self):
        if getattr(self, "scan_job", None) and self.scan_job.isRunning():
            return
        self.cams.stack.setCurrentIndex(0) if not self.devices else None
        self.cams.empty_t.setText("Looking for cameras…")
        self.cams.set_scanning(True, 0.02, "Scanning your network…")
        self.net_dot.set_color(C["accent"])
        self.net_t.setText("Scanning")
        self.scan_job = Job(self._do_scan)
        self.progress_bridge = _Bridge()
        self.progress_bridge.sig.connect(self._progress)
        self.scan_job.done.connect(self._scanned)
        self.scan_job.failed.connect(lambda m: (self.cams.set_scanning(False), self.toast.show_message(m, "bad")))
        self.scan_job.start()

    def _do_scan(self):
        return self.hub.scan(lambda f, m, d: self.progress_bridge.sig.emit(f, m, len(d)))

    def _progress(self, frac, msg, n):
        self.cams.set_scanning(True, frac, f"{msg}  ·  {n} device(s) so far")
        self.net_s.setText(msg)

    def _scanned(self, devs):
        self.cams.set_scanning(False)
        self._render(devs)
        ncam = sum(d["is_camera"] for d in devs)
        self.net_dot.set_color(C["ok"] if ncam else C["warn"])
        self.net_t.setText(f"{ncam} camera{'s' if ncam != 1 else ''}")
        self.net_s.setText(f"{len(devs)} devices on your network")

    def _thumbs(self):
        if self.pages.currentWidget() is self.viewer:
            return
        for c in self.cams.cards:
            c.refresh_thumb()

    # ---- opening a camera ----
    def open_camera(self, dev):
        if dev["kind"] != "v380":
            self.toast.show_message("RTSP / ONVIF cameras aren't supported yet.", "warn")
            return
        dev = self.hub.devices.get(dev["key"], dev)
        if self.hub.acquire_possible(dev):
            self._enter(dev)
        else:
            self._login(dev)

    def _login(self, dev, error=""):
        dlg = LoginDialog(self.hub, dev, self, error)
        if dlg.exec() != QDialog.Accepted:
            return
        remembered = dlg.remember.isChecked()
        self.toast.show_message("Signed in" + ("  ·  login saved" if remembered else ""))
        self._render(self.hub.refresh())
        self._enter(self.hub.devices.get(dlg.cam_id) or self.hub.devices[dev["key"]])

    def _enter(self, dev):
        s = self.hub.acquire(dev["key"])
        if s is None:
            return self._login(dev)
        self.viewer.open(self.hub, dev, s)
        self.pages.setCurrentWidget(self.viewer)

    def leave_viewer(self):
        if self.isFullScreen():
            self.toggle_full()
        self._nav(0)

    def forget(self, dev):
        self.hub.forget(dev["key"])
        self.toast.show_message("Saved login removed", "warn")
        self.leave_viewer()

    def _render(self, devs):
        """Show devices; hold a live session for every camera that has a login."""
        for s in self._held:
            self.hub.release(s)
        self._held = []
        self.devices = devs
        self.devs.show_devices(devs)
        self.cams.show_devices(devs)
        for card in self.cams.cards:
            if card.dev["saved"] and card.dev["kind"] == "v380":
                s = self.hub.acquire(card.dev["key"])
                if s:
                    card.attach(s)
                    self._held.append(s)

    def closeEvent(self, e):
        self.hub.shutdown()
        super().closeEvent(e)


class _Bridge(QObject):
    sig = Signal(float, str, int)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("V380 Studio")
    f = QFont()
    f.setFamilies(["Inter", "Segoe UI", "Noto Sans", "Helvetica Neue"])
    f.setPixelSize(13)
    app.setFont(f)
    app.setStyleSheet(theme.qss())
    win = MainWindow(Hub())
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
