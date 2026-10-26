"""Render real QML frames and check coalesced changes, mute, boost and no motion."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
import tempfile
from pathlib import Path
from PyQt6.QtCore import QUrl, QObject, pyqtSlot, qInstallMessageHandler, QMetaObject
from PyQt6.QtWidgets import QApplication
from PyQt6.QtQuick import QQuickView
from PyQt6.QtQml import QQmlComponent
from PyQt6.QtTest import QTest
from PyQt6.QtGui import QImage, QPainter, QColor
from manage import ROOT, stage

messages = []
qInstallMessageHandler(lambda kind, context, message: messages.append(message))
app = QApplication([])
view = QQuickView()
view.setColor(QColor("#06101c"))
view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
view.setSource(QUrl.fromLocalFile(str(ROOT / "VolumeCard.qml")))
assert not view.errors(), [e.toString() for e in view.errors()]
view.resize(264, 72)
view.show()
card = view.rootObject()
card.setProperty("active", True)
card.setProperty("level", 35)
QMetaObject.invokeMethod(card, "notified")
QTest.qWait(500)
assert abs(card.property("displayedLevel") - 35) < 0.01
frames = [("35%", view.grabWindow())]
card.setProperty("level", 80)
QMetaObject.invokeMethod(card, "notified")
assert card.property("direction") == 1
QTest.qWait(50)
assert 35 < card.property("displayedLevel") < 80, "Level did not interpolate"
frames.append(("RISING", view.grabWindow()))
card.setProperty("level", 20)
QMetaObject.invokeMethod(card, "notified")
assert card.property("direction") == -1
QTest.qWait(260)
assert abs(card.property("displayedLevel") - 20) < 0.01, "Rapid changes did not settle"
frames.append(("20%", view.grabWindow()))
card.setProperty("muted", True)
QMetaObject.invokeMethod(card, "notified")
assert card.property("eventKind") == "mute"
QTest.qWait(500)
assert card.property("displayedLevel") == 0
frames.append(("MUTED", view.grabWindow()))
card.setProperty("maximum", 150)
card.setProperty("muted", False)
card.setProperty("level", 125)
QTest.qWait(500)
frames.append(("BOOST", view.grabWindow()))
QMetaObject.invokeMethod(card, "notified")
assert card.property("eventKind") == "unmute"
for value, zone, color in ((0, 0, "#8baabd"), (25, 1, "#7bafff"), (26, 2, "#8be6ff"), (60, 2, "#8be6ff"), (61, 3, "#78ffd0"), (100, 3, "#78ffd0"), (101, 4, "#ffd18b"), (125, 4, "#ffd18b"), (126, 5, "#ff5269")):
    card.setProperty("level", value)
    QMetaObject.invokeMethod(card, "notified")
    assert card.property("zone") == zone
    assert card.property("accent").name() == color
card.setProperty("level", 150)
QMetaObject.invokeMethod(card, "notified")
assert not card.property("limitHit"), "First arrival at max should not imply an extra press"
QTest.qWait(400)
frames.append(("RED ZONE", view.grabWindow()))
beat = card.property("dangerBeat")
QTest.qWait(100)
assert card.property("dangerBeat") != beat, "Danger pulse is not running"
QMetaObject.invokeMethod(card, "notified")
assert card.property("limitHit"), "Repeated maximum notification lost"
QTest.qWait(120)
frames.append(("150% / LIMIT HIT", view.grabWindow()))
QTest.qWait(180)
QMetaObject.invokeMethod(card, "notified")
QTest.qWait(30)
assert card.property("pulse") < 0.1, "Repeated limit press did not restart feedback"
card.setProperty("level", 145)
QMetaObject.invokeMethod(card, "notified")
assert not card.property("limitHit") and card.property("direction") == -1
card.setProperty("reducedMotion", True)
QTest.qWait(20)
beat = card.property("dangerBeat")
QTest.qWait(160)
assert card.property("dangerBeat") == beat and card.property("alarm") == 0
frames.append(("RED / REDUCED MOTION", view.grabWindow()))
card.setProperty("reducedMotion", False)
card.setProperty("active", False)
QTest.qWait(20)
beat = card.property("dangerBeat")
QTest.qWait(160)
assert card.property("dangerBeat") == beat, "Hidden danger pulse still running"
card.setProperty("active", True)
card.setProperty("reducedMotion", True)
card.setProperty("level", 60)
QTest.qWait(20)
assert card.property("displayedLevel") == 60
assert card.property("pulse") == 1
card.setProperty("active", False)
assert card.property("reveal") == 1 and card.property("pulse") == 1

class Translation(QObject):
    @pyqtSlot(str, str, result=str)
    @pyqtSlot(str, str, 'QVariant', result=str)
    def i18nc(self, context, text, value=""):
        return text.replace("%1", str(value))

translation = Translation()
view.engine().rootContext().setContextObject(translation)
with tempfile.TemporaryDirectory(prefix="jarvis-volume-verify-") as tmp:
    stage(Path(tmp))
    view.engine().addImportPath(tmp)
    component = QQmlComponent(view.engine())
    component.setData(b"import org.kde.plasma.workspace.osd\nOsd {}", QUrl())
    root = component.create()
    assert root, [e.toString() for e in component.errors()]
    assert root.property("jarvisVolumeVersion") == "3.0", "Native override was not loaded"
    for icon, progress, value in (("audio-volume-high", True, 80), ("audio-volume-muted", False, "Audio Muted"), ("video-display-brightness", True, 70), ("input-touchpad-off", False, "Touchpad Off")):
        root.setProperty("icon", icon)
        root.setProperty("showingProgress", progress)
        root.setProperty("osdValue", value)
        QTest.qWait(20)
        assert root.property("volume") == icon.startswith("audio-volume-")
    volume_card = root.findChild(QObject, "volumeCard")
    assert volume_card
    root.setProperty("icon", "audio-volume-high")
    root.setProperty("showingProgress", True)
    root.setProperty("osdMaxValue", 150)
    root.setProperty("osdValue", 140)
    root.setProperty("animateOpacity", True)
    QTest.qWait(20)
    root.setProperty("animateOpacity", False)
    root.setProperty("osdValue", 150)
    root.setProperty("animateOpacity", True)
    assert not volume_card.property("limitHit")
    root.setProperty("animateOpacity", False)
    root.setProperty("animateOpacity", True)
    assert volume_card.property("limitHit"), "Native repeated key path lost limit warning"
    root.deleteLater()
    QTest.qWait(20)

output = ROOT.parent / "validation/volume-storyboard.png"
output.parent.mkdir(exist_ok=True)
sheet = QImage(264, 97 * len(frames), QImage.Format.Format_ARGB32)
sheet.fill(QColor("#06101c"))
p = QPainter(sheet)
for i, (label, frame) in enumerate(frames):
    assert not frame.isNull()
    p.setPen(QColor("#9bbbd2")); p.drawText(18, i * 97 + 19, label)
    p.drawImage(0, i * 97 + 25, frame)
p.end()
assert sheet.save(str(output))
errors = [m for m in messages if any(s in m for s in ("ReferenceError", "TypeError", "is not", "Cannot assign", "Binding loop"))]
assert not errors, errors
print("PASS: real QML rendering, smooth/rapid levels, all five color zones, direction, mute/unmute, repeated max, danger pulse, reduced motion, cleanup and native fallback.")
print(output)
