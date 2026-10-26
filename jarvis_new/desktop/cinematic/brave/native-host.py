#!/usr/bin/python3
"""Input-transparent native Brave tab-bar accents, fed only by our extension.

Native Messaging stdin/stdout carry bounded JSON; stdout is never used for logs.
The helper never reads page content, synthesizes input, or moves browser windows.
"""
import configparser
import json
import math
import os
import struct
import sys
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'xcb'
from PyQt6.QtCore import QElapsedTimer, QPointF, QRectF, QSocketNotifier, Qt, QTimer, pyqtSlot, pyqtClassInfo
from PyQt6.QtDBus import QDBusConnection
from PyQt6.QtGui import QColor, QPainter, QPen, QLinearGradient
from PyQt6.QtWidgets import QApplication, QWidget

KINDS = {'open', 'close', 'switch', 'loading', 'ready', 'pin', 'mute', 'move', 'zoom'}


def send(payload):
    data = json.dumps(payload, separators=(',', ':')).encode()
    sys.stdout.buffer.write(struct.pack('<I', len(data)) + data)
    sys.stdout.buffer.flush()


def geometry(payload):
    w = payload.get('window', {})
    if not w.get('focused') or w.get('state') in ('minimized', 'fullscreen'):
        return None
    values = [w.get(k) for k in ('left', 'top', 'width', 'height')]
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        return None
    x, y, width, height = values
    if not (160 <= width <= 16384 and 100 <= height <= 16384):
        return None
    return round(x), round(y), round(width), 42


@pyqtClassInfo('D-Bus Interface', 'org.jarvis.BraveMotion')
class Bar(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.X11BypassWindowManagerHint | Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle('Jarvis Brave tab-bar accent')
        self.clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.tick)
        self.kind = 'open'
        self.duration = 1000
        self.locked = False
        self.active_window = {}
        self.reported_ready = False
        self.buffer = bytearray()
        self.notifier = QSocketNotifier(sys.stdin.fileno(), QSocketNotifier.Type.Read, self)
        self.notifier.activated.connect(self.read)
        os.set_blocking(sys.stdin.fileno(), False)
        bus = QDBusConnection.sessionBus()
        if not bus.registerService('org.jarvis.BraveMotion'):
            raise RuntimeError('Another native Brave motion helper is already running')
        bus.registerObject('/Motion', self, QDBusConnection.RegisterOption.ExportAllSlots)
        bus.connect('org.freedesktop.ScreenSaver', '/ScreenSaver', 'org.freedesktop.ScreenSaver', 'ActiveChanged', self.lock)
        # A helper launched while locked starts suppressed as well.
        from PyQt6.QtDBus import QDBusInterface
        reply = QDBusInterface('org.freedesktop.ScreenSaver', '/ScreenSaver', 'org.freedesktop.ScreenSaver', bus).call('GetActive')
        if reply.arguments():
            self.locked = reply.arguments()[0] is True
        QTimer.singleShot(0, self.load_geometry)
        bus.interface().serviceOwnerChanged.connect(self.compositor_changed)

    def load_geometry(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from kwin_bridge_loader import load, unload
        try:
            unload('jarvis-brave-motion-geometry')
            load(Path(__file__).with_name('native-geometry.js'), 'jarvis-brave-motion-geometry')
        except Exception:
            # Without authoritative compositor coordinates, skip native accents.
            self.active_window = {}

    @pyqtSlot(str, str, str)
    def compositor_changed(self, name, old, new):
        if name == 'org.kde.KWin':
            self.active_window = {}
            self.cancel()
            if new:
                QTimer.singleShot(1500, self.load_geometry)

    @pyqtSlot(str)
    def SetWindow(self, payload):
        try:
            self.active_window = json.loads(payload)
        except ValueError:
            self.active_window = {}
        self.cancel()
        if not self.reported_ready:
            self.reported_ready = True
            send({'ready': True, 'version': '1.2.0'})

    @pyqtSlot(result=str)
    def Status(self):
        return json.dumps({'window': self.active_window, 'visible': self.isVisible(), 'kind': self.kind, 'timerActive': self.timer.isActive()})

    @pyqtSlot()
    def Quit(self):
        QApplication.quit()

    def cleanup(self):
        self.cancel()
        try:
            from kwin_bridge_loader import unload
            unload('jarvis-brave-motion-geometry')
        except Exception:
            pass

    @pyqtSlot(bool)
    def lock(self, locked):
        self.locked = locked
        if locked:
            self.cancel()

    def cancel(self):
        active = self.isVisible() or self.timer.isActive()
        self.timer.stop()
        self.hide()
        if active:
            send({'visible': False, 'kind': self.kind})

    def read(self):
        try:
            data = os.read(sys.stdin.fileno(), 65536)
            if not data:
                self.cancel()
                QApplication.quit()
                return
            self.buffer.extend(data)
            while len(self.buffer) >= 4:
                length = struct.unpack('<I', self.buffer[:4])[0]
                if length > 16384:
                    raise ValueError('Oversized message')
                if len(self.buffer) < 4 + length:
                    return
                payload = json.loads(self.buffer[4:4 + length])
                del self.buffer[:4 + length]
                self.play(payload)
        except BlockingIOError:
            pass
        except (ValueError, OSError, TypeError, KeyError, AttributeError, configparser.Error):
            self.cancel()
            QApplication.quit()

    def play(self, payload):
        self.cancel()
        if payload.get('kind') == 'cancel':
            send({'visible': False})
            return
        # Browser coordinates are often (0,0) under Wayland. Use KWin's actual
        # active client rectangle and reject stale events from another window.
        rect = geometry({'window': self.active_window})
        browser = payload.get('window', {})
        if not browser.get('focused') or abs(browser.get('width', 0) - self.active_window.get('outerWidth', 0)) > 24:
            rect = None
        if self.locked or payload.get('quiet') or payload.get('kind') not in KINDS or rect is None:
            send({'visible': False, 'suppressed': True, 'window': self.active_window, 'browser': browser})
            return
        kde = configparser.RawConfigParser(strict=False)
        kde.read(Path.home() / '.config/kdeglobals')
        kwin = configparser.RawConfigParser(strict=False)
        kwin.read(Path.home() / '.config/kwinrc')
        factor = kde.getfloat('KDE', 'AnimationDurationFactor', fallback=1)
        if not math.isfinite(factor) or factor <= 0 or kwin.getboolean('Effect-jarvis-energy', 'ReducedMotion', fallback=False):
            send({'visible': False, 'suppressed': True})
            return
        x, y, width, height = rect
        if not any(screen.geometry().intersects(QRectF(x, y, width, height).toRect()) for screen in QApplication.screens()):
            return
        self.kind = payload['kind']
        self.duration = min(1800, max(250, (1150 if self.kind in ('open', 'close') else 760) * factor))
        self.setGeometry(x, y, width, height)
        self.clock.start()
        self.show()
        self.timer.start()
        self.update()
        send({'visible': True, 'kind': self.kind, 'geometry': list(rect), 'durationMs': self.duration, 'inputTransparent': True})

    def tick(self):
        if self.clock.elapsed() >= self.duration:
            self.cancel()
        else:
            self.update()

    def paintEvent(self, event):
        t = min(1, max(0, self.clock.elapsed() / self.duration))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        a = math.sin(math.pi * t)
        e = 1 - (1 - t) ** 3
        # A narrow native strip accent, not an invented per-tab rectangle.
        left, right, top, bottom = 8.0, width - 8.0, 3.0, 37.0
        p.setPen(QPen(QColor(105, 190, 255, round(180 * a)), 1.2))
        reach = 19 + 16 * (1 - e if self.kind != 'close' else e)
        for x, dx in [(left, 1), (right, -1)]:
            for y, dy in [(top, 1), (bottom, -1)]:
                p.drawLine(QPointF(x, y), QPointF(x + dx * reach, y))
                p.drawLine(QPointF(x, y), QPointF(x, y + dy * 7))
        if self.kind == 'close':
            start, end = width * e / 2, width * (1 - e / 2)
        elif self.kind in ('switch', 'move', 'loading'):
            start, end = -width * .3 + width * 1.3 * e, width * 1.3 * e
        else:
            start, end = left, left + (right - left) * e
        gradient = QLinearGradient(start, 0, max(start + 1, end), 0)
        gradient.setColorAt(0, QColor(60, 150, 255, 0))
        gradient.setColorAt(.5, QColor(125, 205, 255, round(240 * a)))
        gradient.setColorAt(1, QColor(205, 240, 255, 0))
        p.fillRect(QRectF(start, 1, max(0, end - start), 3), gradient)
        # Short staggered bottom fragments make open/close legible on a dark bar.
        count = 12
        for i in range(count):
            phase = max(0, min(1, (t - i * .018) / .78))
            alpha = math.sin(math.pi * phase) * a
            cx = left + (right - left) * (i + .5) / count
            span = min(42, width / 30) * (1 - phase if self.kind == 'close' else phase)
            p.setPen(QPen(QColor(135, 205, 255, round(165 * alpha)), 1.4))
            p.drawLine(QPointF(cx - span, bottom), QPointF(cx + span, bottom))
        p.end()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setApplicationName('jarvis-brave-motion')
    app.setQuitOnLastWindowClosed(False)
    bar = Bar()
    app.aboutToQuit.connect(bar.cleanup)
    app.screenRemoved.connect(lambda _: bar.cancel())
    send({'starting': True, 'version': '1.2.0'})
    app.exec()
