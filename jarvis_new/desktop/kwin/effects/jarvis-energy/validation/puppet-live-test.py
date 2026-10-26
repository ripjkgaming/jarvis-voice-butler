import os,subprocess,json
from pathlib import Path
from PyQt6.QtWidgets import QApplication,QWidget,QLabel,QVBoxLayout
from PyQt6.QtCore import QTimer,Qt
app=QApplication([]);app.setApplicationName('jarvis-puppet-owned');app.setDesktopFileName('jarvis-puppet-owned');app.setQuitOnLastWindowClosed(False)
entry=Path.home()/'.local/share/applications/jarvis-puppet-owned.desktop';entry.write_text('[Desktop Entry]\nType=Application\nName=Owned puppet test\nExec=false\nIcon=kcalc\n')
bg=QWidget(None,Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint);bg.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating);bg.setGeometry(0,0,1920,1024);bg.setStyleSheet('background:#070b12');bg.show()
windows=[];records=[]
for n in range(2):
 w=QWidget();w.setWindowTitle('Owned puppet test '+str(n));w.resize(400,180);w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating);w.setStyleSheet('background:#101b29;color:white');l=QVBoxLayout(w);l.addWidget(QLabel('OWNED TASKBAR OPEN TEST'));windows.append(w)
def sample(label):
 s=subprocess.check_output(['qdbus','org.jarvis.TaskbarPuppet','/Puppet','org.jarvis.TaskbarPuppet.Status'],text=True);records.append({'phase':label,'status':json.loads(s)})
def capture():subprocess.run(['spectacle','-b','-n','-f','-o','/tmp/jarvis-orbit-work/puppet-live.png'],check=True,timeout=10)
QTimer.singleShot(100,windows[0].show)
for t in [300,600,900,1700]:QTimer.singleShot(t,lambda t=t:sample(str(t)))
QTimer.singleShot(1200,capture);QTimer.singleShot(1900,windows[1].show);QTimer.singleShot(2300,lambda:sample('second-window-no-repeat'))
QTimer.singleShot(2600,windows[0].close);QTimer.singleShot(2900,windows[1].close);QTimer.singleShot(4500,lambda:sample('settled'));QTimer.singleShot(4700,app.quit)
try:app.exec()
finally:entry.unlink(missing_ok=True)
print(json.dumps(records,indent=2))
