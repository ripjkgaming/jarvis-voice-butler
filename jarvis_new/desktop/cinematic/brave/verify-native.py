"""Geometry and real Qt paint regression checks; creates no visible windows."""
import importlib.util
import os
from pathlib import Path

root = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('native_motion', root / 'native-host.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

base = {'focused': True, 'state': 'normal', 'left': -1000, 'top': 30, 'width': 900, 'height': 700}
assert module.geometry({'window':base}) == (-1000, 30, 900, 42)
for key, value in [('focused',False), ('state','fullscreen'), ('state','minimized'), ('width',float('nan')), ('left',float('inf')), ('width',50000), ('height',0)]:
    assert module.geometry({'window':{**base,key:value}}) is None, (key,value)

app = QApplication([])
class Clock:
    value = 0
    def elapsed(self): return self.value
class Probe(QWidget):
    paintEvent = module.Bar.paintEvent
probe = Probe()
probe.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
probe.resize(900,42)
probe.duration = 1000
probe.clock = Clock()
frames = {}
for kind in ['open','close','switch','pin','mute','move','zoom']:
    probe.kind = kind
    for t in [0, .4, 1]:
        probe.clock.value = t*1000
        pix = QPixmap(900,42)
        pix.fill(Qt.GlobalColor.transparent)
        probe.render(pix)
        img = pix.toImage()
        alpha = sum(img.pixelColor(x,y).alpha() for y in range(42) for x in range(900))
        if t in [0,1]: assert alpha == 0, (kind,t,alpha)
        else:
            assert alpha > 0, kind
            frames[kind] = pix
    if kind in ['open','close']:
        frames[kind].save(str(root.parent / 'validation' / ('brave-native-'+kind+'.png')))
assert frames['open'].toImage() != frames['close'].toImage()
print('PASS: invalid/hidden/fullscreen geometry suppressed; negative origins; seven native paint modes; transparent endpoints.')
