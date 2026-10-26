"""Exercise Jarvis's real marker and window-control path on one owned window."""
import os,sys,json,subprocess
from pathlib import Path
from PyQt6.QtWidgets import QApplication,QWidget,QLabel,QVBoxLayout
from PyQt6.QtCore import QTimer,Qt
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from system import cinematic,kwin_windows
app=QApplication([]);app.setQuitOnLastWindowClosed(False)
name='jarvis-command-check';app.setApplicationName(name);app.setDesktopFileName(name)
entry=Path.home()/'.local/share/applications'/(name+'.desktop')
entry.write_text('[Desktop Entry]\nType=Application\nName=Jarvis command check\nExec=false\nIcon=system-file-manager\n')
w=QWidget();w.setWindowTitle('Jarvis command owned check');w.resize(620,360)
w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
w.setStyleSheet('background:#081a30;color:#ddf3ff;font-size:24px')
QVBoxLayout(w).addWidget(QLabel('JARVIS // COMMAND SEQUENCE\n\nBlue acquisition · glass release'))
records=[]
def sample(label):
 records.append({'phase':label,'taskbar':json.loads(subprocess.check_output(['qdbus','org.jarvis.TaskbarSlice','/Slice','org.jarvis.TaskbarSlice.Status'],text=True)),'debug':subprocess.check_output(['qdbus','org.kde.KWin','/Effects','org.kde.kwin.Effects.debug','jarvis-energy',''],text=True)})
def act(action):
 result=kwin_windows.act(action,'Jarvis command owned check')
 records.append({'action':action,'found':bool(result)})
assert cinematic.mark_launch([name])
QTimer.singleShot(100,w.show)
QTimer.singleShot(1000,lambda:sample('open-900ms'))
QTimer.singleShot(1700,lambda:act('focus'))
QTimer.singleShot(2600,lambda:sample('focus-900ms'))
QTimer.singleShot(3200,lambda:act('close'))
QTimer.singleShot(4300,lambda:sample('close-1100ms'))
QTimer.singleShot(6000,lambda:sample('idle'))
QTimer.singleShot(6300,app.quit)
try:app.exec()
finally:w.close();entry.unlink(missing_ok=True)
(Path(__file__).parent/'validation/command-live.json').write_text(json.dumps(records,indent=2))
print(json.dumps(records,indent=2))
