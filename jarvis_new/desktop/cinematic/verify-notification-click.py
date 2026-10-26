"""Exercise the real action bridge with an owned X11 notification and destination."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys
import kwin_bridge_loader

os.environ['QT_QPA_PLATFORM'] = 'xcb'
from PyQt6.QtCore import QTimer, Qt, QObject, pyqtSlot
from PyQt6.QtDBus import QDBusConnection
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
app.setApplicationName('jarvis-notification-click-check')
card = QWidget(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
               Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.X11BypassWindowManagerHint)
card.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
card.setWindowTitle('Jarvis owned click notification')
card.resize(360, 120)
point = QCursor.pos()
card.move(point.x() - 80, point.y() - 50)
card.setStyleSheet('background:#0a1930;color:#dcefff;font-size:18px;padding:14px')
QVBoxLayout(card).addWidget(QLabel('Notification redirect check\nOpening the destination…'))
subprocess.run(['xprop', '-id', str(int(card.winId())), '-f', '_NET_WM_WINDOW_TYPE', '32a',
                '-set', '_NET_WM_WINDOW_TYPE', '_NET_WM_WINDOW_TYPE_NOTIFICATION'], check=True)
siblings = []
if '--stacked' in sys.argv:
    for index in range(2):
        sibling = QWidget(None, card.windowFlags())
        sibling.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        sibling.setWindowTitle('Jarvis owned stacked notification ' + str(index))
        sibling.resize(360, 100)
        sibling.setStyleSheet('background:#581b36;color:white;font-size:18px;padding:14px')
        QVBoxLayout(sibling).addWidget(QLabel('Notification above the expanding card'))
        subprocess.run(['xprop', '-id', str(int(sibling.winId())), '-f', '_NET_WM_WINDOW_TYPE', '32a',
                        '-set', '_NET_WM_WINDOW_TYPE', '_NET_WM_WINDOW_TYPE_NOTIFICATION'], check=True)
        siblings.append(sibling)
destination = QWidget()
destination.setWindowTitle('Jarvis owned notification destination')
destination.resize(500, 260)
destination.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
QVBoxLayout(destination).addWidget(QLabel('Notification destination reached'))
records = []


class Receiver(QObject):
    positioned = False
    @pyqtSlot(str)
    def Cursor(self, payload):
        position = json.loads(payload)
        card.move(position['x'] - 80, position['y'] - 50)
        card.show()
        for index, sibling in enumerate(siblings):
            sibling.move(card.x(), card.y() - (index + 1) * 110)
            sibling.show()
        if self.positioned:
            QTimer.singleShot(35, emit_action)
        self.positioned = True

    @pyqtSlot('uint', str)
    def invoked(self, notification_id, action):
        if notification_id == 0 and action == 'default':
            destination.show()
            QTimer.singleShot(120 if '--before-close' in sys.argv else 0, card.close)


receiver = Receiver()
bus = QDBusConnection.sessionBus()
bus.registerService('org.jarvis.NotificationClickCheck')
bus.registerObject('/Check', receiver, QDBusConnection.RegisterOption.ExportAllSlots)
bus.connect('org.freedesktop.Notifications', '/org/freedesktop/Notifications',
            'org.freedesktop.Notifications', 'ActionInvoked', receiver.invoked)


def emit_action():
    # Plasma's own API emits the genuine action signal; ID 0 has no user action.
    subprocess.run(['qdbus', 'org.freedesktop.Notifications', '/org/freedesktop/Notifications',
                    'org.kde.NotificationManager.InvokeAction', '0', 'default'], check=True)
    for delay in (60, 140, 240, 420, 1000, 1400):
        QTimer.singleShot(delay, sample)
    QTimer.singleShot(1800, app.quit)


def sample():
    records.append(subprocess.check_output(['qdbus', 'org.kde.KWin', '/Effects',
                   'org.kde.kwin.Effects.debug', 'jarvis-surfaces', ''], text=True).strip())


script = tempfile.NamedTemporaryFile(suffix='.js', mode='w', delete=False)
script.write('callDBus("org.jarvis.NotificationClickCheck","/Check","", "Cursor", JSON.stringify(workspace.cursorPos));')
script.close()
kwin_bridge_loader.load(Path(script.name), 'jarvis-notification-click-check')
QTimer.singleShot(200, sample)
def trigger():
    kwin_bridge_loader.unload('jarvis-notification-click-check')
    kwin_bridge_loader.load(Path(script.name), 'jarvis-notification-click-check')

action_at = 250 if "--during-arrival" in sys.argv else 1050
QTimer.singleShot(action_at, trigger)
QTimer.singleShot(10000, app.quit)  # Bound a missing bridge callback.
try:
    app.exec()
finally:
    card.close()
    for sibling in siblings:
        sibling.close()
    destination.close()
    kwin_bridge_loader.unload("jarvis-notification-click-check")
    Path(script.name).unlink()
filename = 'notification-click-arrival-live.json' if '--during-arrival' in sys.argv else 'notification-click-before-close-live.json' if '--before-close' in sys.argv else 'notification-click-live.json'
if '--stacked' in sys.argv:
    filename = filename.replace('-live.json', '-stacked-live.json')
path = Path(__file__).with_name('validation') / filename
path.write_text(json.dumps(records, indent=2) + '\n')
assert any('Duration: 352ms' in row and 'Animation: Scale' in row for row in records), 'Full-screen expansion not observed'
assert records[-1] == 'No window is animated', 'Click tracks did not clean up'
print('PASS: real action signal, retained notification click expansion, destination and cleanup.')
