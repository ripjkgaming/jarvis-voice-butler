import subprocess,json
from pathlib import Path
from PyQt6.QtWidgets import QApplication,QWidget,QLabel,QVBoxLayout
from PyQt6.QtCore import QTimer,Qt
app=QApplication([]);app.setApplicationName('jarvis-shot-owned');app.setDesktopFileName('jarvis-shot-owned');app.setQuitOnLastWindowClosed(False)
entry=Path.home()/'.local/share/applications/jarvis-shot-owned.desktop';entry.write_text('[Desktop Entry]\nType=Application\nName=Owned precision shot test\nExec=false\nIcon=kcalc\n')
w=QWidget();w.setWindowTitle('Owned precision shot test');w.resize(400,180);w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating);l=QVBoxLayout(w);l.addWidget(QLabel('OWNED ICON CLOSE TEST'))
records=[]
captured=False
def capture():subprocess.run(['spectacle','-b','-n','-f','-o','/tmp/jarvis-orbit-work/shot-live.png'],check=True,timeout=10)
def sample(label):
 global captured
 d=json.loads(subprocess.check_output(['qdbus','org.jarvis.TaskbarSlice','/Slice','org.jarvis.TaskbarSlice.Status'],text=True));records.append({'phase':label,'status':d})
 if not captured and d.get('timerActive') and d.get('last',{}).get('app')=='jarvis-shot-owned':
  captured=True
  count=len(d['last'].get('scan',[1]));QTimer.singleShot(round((count*430+250)*.8)-100,capture)
QTimer.singleShot(100,w.show);QTimer.singleShot(1000,w.showMinimized);QTimer.singleShot(1300,w.showNormal);QTimer.singleShot(2100,w.close);
for stamp in range(2200,6000,250):QTimer.singleShot(stamp,lambda stamp=stamp:sample(str(stamp)))
QTimer.singleShot(7000,lambda:sample('settled'));QTimer.singleShot(7200,app.quit)
try:app.exec()
finally:entry.unlink(missing_ok=True)
print(json.dumps(records,indent=2))
