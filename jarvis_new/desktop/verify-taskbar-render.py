import importlib.util,json,tempfile
from pathlib import Path
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage,QPainter,QColor
from PyQt6.QtCore import QRectF
root=Path('/mnt/data/jarvis-voice-butler/jarvis_new/desktop')
spec=importlib.util.spec_from_file_location('slice',root/'taskbar-slice.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
app=QApplication([]);mod.QIcon.setThemeSearchPaths(['/usr/share/icons',str(Path.home()/'.local/share/icons')]);mod.QIcon.setThemeName('stark-os');w=mod.Slice()
d=json.loads((root/'kwin/effects/jarvis-energy/validation/taskbar-live.json').read_text())[1]['status']['last'];d['app']='org.kde.kcalc'
w.Play(json.dumps(d));assert w.isVisible();w.Cancel();assert not w.timer.isActive()
with tempfile.TemporaryDirectory() as tmp:
 home=Path(tmp);(home/'.config').mkdir();(home/'.config/kwinrc').write_text('[Effect-jarvis-energy]\nReducedMotion=true\n')
 with patch.object(Path,'home',return_value=home):w.Play(json.dumps(d))
 assert w.reduced and w.timer.isSingleShot();w.tick();assert not w.isVisible()
w.Play(json.dumps(d));w.timer.stop()
class Frozen:
 def __init__(self,ms):self.ms=ms
 def elapsed(self):return self.ms
out=QImage(1000,440,QImage.Format.Format_ARGB32);out.fill(QColor('#091019'));p=QPainter(out)
for i,(label,phase) in enumerate([('DELIBERATE SCAN',.18),('LOCK',.40),('SCREEN-SPACE CUT',.55),('CODE SHARDS',.79)]):
 ms=(w.scanDuration*phase/.30 if phase<.30 else w.scanDuration+(phase-.30)/.70*1100)*w.factor
 w.clock=Frozen(ms);w.setGeometry(w.slashBox if phase>=.36 and phase<.64 else w.targetBox)
 w.update();app.processEvents();pix=w.grab();p.drawText(15+i*250,22,label);p.drawPixmap(QRectF(i*250+5,35,240,360),pix,QRectF(pix.rect()))
p.end();out.save(str(root/'kwin/effects/jarvis-energy/validation/taskbar-choreography.png'));w.Cancel()
print('PASS: event overlay starts/cancels; reduced-motion static cue self-cleans; four code-rendered storyboard stages.')
