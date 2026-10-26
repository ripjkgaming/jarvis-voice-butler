#!/usr/bin/python3
"""Window-drag particles drawn out of process.

KWin's JS effect API crashed the compositor (animate()/window wrappers), so the
compositor only runs drag-kwin.js, a read-only reporter. This helper owns all
drawing: one input-transparent translucent overlay per screen, particles kept in
global desktop coordinates so they flow across monitor seams without any reset.
A fault here can only end this process.
"""
import json
import math
import os
import random
import sys
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'xcb'
from PyQt6.QtCore import QObject, QElapsedTimer, QPointF, QRectF, Qt, QTimer, pyqtSlot, pyqtClassInfo
from PyQt6.QtDBus import QDBusConnection
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QApplication, QWidget

MAX = 900
COLORS = ['#5fe3ff', '#8fd0ff', '#ba8fff', '#e6fcff']
RETURN_BURST = 150   # extra motes pulled in from the surroundings on release
RETURN_MAX_S = 1.15  # hard cap for one mote's flight home


def smooth(x):
    x = 0.0 if x < 0 else 1.0 if x > 1 else x
    return x * x * (3 - 2 * x)


class Mote:
    __slots__ = ('x', 'y', 'vx', 'vy', 'age', 'life', 'size', 'ci', 'phase',
                 'mode', 'tx', 'ty', 'delay', 'k', 'tr', 'a0', 'swirl',
                 'alpha', 'scale')

    def __init__(self, x, y, vx, vy, life, size, ci):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.age, self.life, self.size, self.ci = 0.0, life, size, ci
        self.phase = random.uniform(0, 6.283)
        self.mode = 0            # 0 = shed while dragging, 1 = flying home
        self.tx = self.ty = self.delay = self.k = self.tr = self.a0 = self.swirl = 0.0
        self.alpha, self.scale = 0.9, 1.0


class Field:
    """Particle state in global coordinates."""

    def __init__(self):
        self.p = []
        self.last = None     # last reported frame rect
        self.home = None     # final window rect while particles return to it
        self.flash = 0.0     # rim shimmer as the window reforms
        self.time = 0.0
        self.dragging = False
        self.cursor = (0.0, 0.0)

    def begin(self, g):
        self.last = g
        self.home = None
        self.flash = 0.0
        self.dragging = True

    def retarget(self, g):
        """Keep returning motes and the rim attached to the committed frame."""
        if self.home is None or self.home == g:
            return
        x, y, w, h = self.home
        nx, ny, nw, nh = g
        for m in self.p:
            if m.mode == 1:
                m.tx = nx + (m.tx - x) * nw / w
                m.ty = ny + (m.ty - y) * nh / h
        self.home = g

    def emit(self, g, cursor, dt):
        if self.last is None:
            self.last = g
            return
        lx, ly = self.last[0], self.last[1]
        dx, dy = g[0] - lx, g[1] - ly
        speed = math.hypot(dx, dy)
        self.last = g
        self.home = None
        if speed < 0.5:
            return
        x, y, w, h = g
        n = min(26, 3 + int(speed * 0.5))
        for _ in range(n):
            if len(self.p) >= MAX - RETURN_BURST:
                break
            # Spawn on the trailing edges (opposite the motion), bias to corners.
            if random.random() < abs(dx) / (abs(dx) + abs(dy) + 1e-6):
                px = x + (w if dx < 0 else 0) + random.uniform(-3, 3)
                py = y + random.uniform(0, h)
            else:
                py = y + (h if dy < 0 else 0) + random.uniform(-3, 3)
                px = x + random.uniform(0, w)
            ang = random.uniform(-0.9, 0.9)
            mag = random.uniform(10, 70)
            vx = -dx / max(dt, 0.008) * 0.12 + math.cos(ang) * mag * 0.4
            vy = -dy / max(dt, 0.008) * 0.12 + math.sin(ang) * mag * 0.4 - 6
            self.p.append(Mote(px, py, vx, vy, random.uniform(0.7, 1.7),
                               random.uniform(5, 14), random.randrange(len(COLORS))))

    def _target(self, rect):
        # Rebuild from the rim inward: edges first, then the interior fills.
        x, y, w, h = rect
        if random.random() < 0.55:
            side = random.randrange(4)
            u = random.random()
            return (x + u * w, y + (0 if side == 0 else h) + random.uniform(-2, 2)) if side < 2 \
                else (x + (0 if side == 2 else w) + random.uniform(-2, 2), y + u * h)
        return x + random.uniform(0.03, 0.97) * w, y + random.uniform(0.03, 0.97) * h

    def _send_home(self, m, rect, base_delay):
        m.mode = 1
        m.tx, m.ty = self._target(rect)
        m.a0 = max(m.alpha, 0.35)
        m.k = random.uniform(70, 150)
        dist = math.hypot(m.tx - m.x, m.ty - m.y)
        # Nearer motes lead, far ones trail: the cloud contracts like a wave.
        m.delay = base_delay + min(0.2, dist / 4500) + random.uniform(0, 0.08)
        m.swirl = random.choice((-1, 1)) * random.uniform(500, 1500)
        m.tr = 0.0

    def release(self, g):
        """Window dropped: every mote swirls back and reassembles into g."""
        self.dragging = False
        self.last = None
        self.home = g
        x, y, w, h = g
        for m in self.p:
            self._send_home(m, g, 0.0)
        for _ in range(RETURN_BURST):
            if len(self.p) >= MAX:
                break
            # Mist hanging just outside the window, drawn in from all around.
            ang = random.uniform(0, math.tau)
            r = random.uniform(40, 260)
            px = x + w / 2 + math.cos(ang) * (w / 2 + r)
            py = y + h / 2 + math.sin(ang) * (h / 2 + r)
            m = Mote(px, py, 0.0, 0.0, 1.0, random.uniform(4, 11), random.randrange(len(COLORS)))
            m.alpha = 0.0
            self._send_home(m, g, random.uniform(0.05, 0.3))
            m.a0 = 0.0
            self.p.append(m)

    def step(self, dt):
        self.time += dt
        self.flash = max(0.0, self.flash - dt * 1.7)
        damp = math.exp(-2.2 * dt)
        keep = []
        for m in self.p:
            m.age += dt
            if m.mode == 0:
                if m.age >= m.life:
                    continue
                # Slow turbulence so the shed dust drifts instead of flying straight.
                m.vx += math.sin(m.y * 0.013 + self.time * 3.1 + m.ci) * 55 * dt
                m.vy += math.cos(m.x * 0.011 + self.time * 2.6 + m.ci) * 55 * dt
                m.vx *= damp
                m.vy *= damp
                m.x += m.vx * dt
                m.y += m.vy * dt
                t = m.age / m.life
                twinkle = 0.78 + 0.22 * math.sin(self.time * 17 + m.phase)
                m.alpha = (1 - t) * (1 - t) * 0.9 * twinkle
                m.scale = 1 - 0.4 * t
                keep.append(m)
                continue
            m.tr += dt
            if m.tr < m.delay:
                # Hang in place and breathe while waiting for its turn.
                m.vx *= damp
                m.vy *= damp
                m.x += m.vx * dt
                m.y += m.vy * dt
                m.alpha = m.a0 * (0.8 + 0.2 * math.sin(self.time * 14 + m.phase))
                keep.append(m)
                continue
            t = m.tr - m.delay
            dx, dy = m.tx - m.x, m.ty - m.y
            dist = math.hypot(dx, dy)
            speed = math.hypot(m.vx, m.vy)
            if t > RETURN_MAX_S or (t > 0.2 and dist < 3 and speed < 40):
                self.flash = min(1.0, self.flash + 0.012)
                continue
            c = 2 * math.sqrt(m.k) * 0.8       # slightly underdamped: settles with a hint of overshoot
            sw = m.swirl * math.exp(-t * 4.5) / max(dist, 30) * min(dist, 220)
            ax = m.k * dx - c * m.vx - dy / max(dist, 1e-3) * sw
            ay = m.k * dy - c * m.vy + dx / max(dist, 1e-3) * sw
            m.vx += ax * dt
            m.vy += ay * dt
            m.x += m.vx * dt
            m.y += m.vy * dt
            rise = smooth(t / 0.14)
            peak = 0.95 * rise + m.a0 * (1 - rise)
            prox = smooth(dist / 45) ** 0.7                # dissolve into the surface
            tail = 1.0 if t < RETURN_MAX_S - 0.25 else smooth((RETURN_MAX_S - t) / 0.25)
            m.alpha = peak * prox * tail * (0.85 + 0.15 * math.sin(self.time * 20 + m.phase))
            m.scale = 0.45 + 0.55 * smooth(dist / 90)
            keep.append(m)
        self.p = keep


class Overlay(QWidget):
    def __init__(self, screen, field, sprites):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.X11BypassWindowManagerHint | Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle('Jarvis drag particles')
        self.field, self.sprites = field, sprites
        self.setGeometry(screen.geometry())

    def paintEvent(self, _):
        g = self.geometry()
        ox, oy, w, h = g.x(), g.y(), g.width(), g.height()
        qp = QPainter(self)
        qp.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        for m in self.field.p:
            lx, ly = m.x - ox, m.y - oy
            if lx < -m.size or ly < -m.size or lx > w + m.size or ly > h + m.size:
                continue
            if m.alpha <= 0.01:
                continue
            s_ = m.size * m.scale
            qp.setOpacity(min(1.0, m.alpha))
            qp.drawPixmap(QRectF(lx - s_, ly - s_, 2 * s_, 2 * s_), self.sprites[m.ci],
                          QRectF(self.sprites[m.ci].rect()))
        f = self.field
        if f.home is not None and f.flash > 0.01:
            # Rim shimmer: the window "locks in" as the motes land.
            hx, hy, hw, hh = f.home
            r = QRectF(hx - ox, hy - oy, hw, hh)
            for width, a in ((9, 0.10), (4, 0.22), (1.5, 0.55)):
                col = QColor('#9fefff')
                col.setAlphaF(min(1.0, a * f.flash))
                qp.setOpacity(1.0)
                qp.setPen(QPen(col, width))
                qp.setBrush(Qt.BrushStyle.NoBrush)
                qp.drawRoundedRect(r, 8, 8)
        qp.end()


def sprite(color):
    px = QPixmap(64, 64)
    px.fill(Qt.GlobalColor.transparent)
    qp = QPainter(px)
    g = QRadialGradient(QPointF(32, 32), 32)
    c = QColor(color)
    g.setColorAt(0, QColor(255, 255, 255, 255))
    g.setColorAt(0.12, c)
    c2 = QColor(c)
    c2.setAlpha(70)
    g.setColorAt(0.5, c2)
    c3 = QColor(c)
    c3.setAlpha(0)
    g.setColorAt(1, c3)
    qp.setPen(Qt.PenStyle.NoPen)
    qp.setBrush(g)
    qp.drawEllipse(QPointF(32, 32), 32, 32)
    qp.end()
    return px


@pyqtClassInfo('D-Bus Interface', 'org.jarvis.DragFx')
class App(QObject):
    def __init__(self, qapp):
        super().__init__()
        self.qapp = qapp
        self.field = Field()
        self.sprites = [sprite(c) for c in COLORS]
        self.overlays = []
        self.clock = QElapsedTimer()
        self.last_report = 0.0
        self.window_id = None
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(8)
        self.timer.timeout.connect(self.tick)
        self.locked = False
        bus = QDBusConnection.sessionBus()
        if not bus.registerService('org.jarvis.DragFx'):
            raise RuntimeError('drag fx helper already running')
        bus.registerObject('/Drag', self, QDBusConnection.RegisterOption.ExportAllSlots)
        bus.connect('org.freedesktop.ScreenSaver', '/ScreenSaver', 'org.freedesktop.ScreenSaver', 'ActiveChanged', self.lock)
        qapp.screenAdded.connect(self.rebuild)
        qapp.screenRemoved.connect(self.rebuild)
        self.rebuild()
        QTimer.singleShot(0, self.load_script)
        bus.interface().serviceOwnerChanged.connect(self.owner_changed)

    @pyqtSlot(bool)
    def lock(self, locked):
        self.locked = locked
        if locked:
            self.field.p.clear()
            self.field.home = None
            self.field.flash = 0.0

    def rebuild(self, *_):
        for o in self.overlays:
            o.hide()
            o.deleteLater()
        self.overlays = [Overlay(s, self.field, self.sprites) for s in self.qapp.screens()]

    def load_script(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from kwin_bridge_loader import load, unload
        try:
            unload('jarvis-dragfx')
            load(Path(__file__).with_name('drag-kwin.js'), 'jarvis-dragfx')
        except Exception as exc:
            print('dragfx: script load failed:', exc, file=sys.stderr)

    @pyqtSlot(str, str, str)
    def owner_changed(self, name, old, new):
        if name == 'org.kde.KWin' and new:
            QTimer.singleShot(1500, self.load_script)

    @pyqtSlot(str)
    def Report(self, payload):
        if self.locked:
            return
        try:
            d = json.loads(payload)
            g = (float(d['x']), float(d['y']), float(d['w']), float(d['h']))
            cur = (float(d['cx']), float(d['cy']))
            kind = d['s']
        except (ValueError, KeyError, TypeError):
            return
        if not all(math.isfinite(v) for v in g + cur) or g[2] < 1 or g[3] < 1:
            return
        now = self.clock.nsecsElapsed() / 1e9 if self.clock.isValid() else 0.0
        if kind == 'start':
            self.window_id = d.get('id')
            self.field.begin(g)
        elif d.get('id') != self.window_id:
            return
        elif kind == 'step':
            if not self.field.dragging:
                return
            self.field.emit(g, cur, max(now - self.last_report, 0.004))
        elif kind == 'end':
            if not self.field.dragging:
                return
            self.field.release(g)
        elif kind == 'target':
            if self.field.home is None or (not self.field.p and self.field.flash <= 0.01):
                return
            self.field.retarget(g)
        else:
            return
        self.last_report = now
        if not self.timer.isActive():
            self.clock.restart() if not self.clock.isValid() else None
            self.tick_prev = self.clock.nsecsElapsed()
            self.timer.start()
            for o in self.overlays:
                o.show()

    def tick(self):
        t = self.clock.nsecsElapsed()
        dt = min(0.05, (t - self.tick_prev) / 1e9)
        self.tick_prev = t
        self.field.step(dt)
        for o in self.overlays:
            o.update()
        if not self.field.p and self.field.last is None and self.field.flash <= 0.01:
            self.timer.stop()
            for o in self.overlays:
                o.hide()

    @pyqtSlot()
    def Quit(self):
        self.qapp.quit()

    @pyqtSlot(result=str)
    def Status(self):
        return json.dumps({'particles': len(self.field.p), 'screens': len(self.overlays), 'active': self.timer.isActive(),
                           'home': self.field.home, 'window_id': self.window_id})

    def cleanup(self):
        try:
            from kwin_bridge_loader import unload
            unload('jarvis-dragfx')
        except Exception:
            pass


def main():
    qapp = QApplication(sys.argv)
    app = App(qapp)
    app.clock.start()
    qapp.aboutToQuit.connect(app.cleanup)
    return qapp.exec()


if __name__ == '__main__':
    sys.exit(main())
