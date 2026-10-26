import subprocess,json,os,tempfile
from pathlib import Path
from PyQt6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel
from PyQt6.QtCore import QTimer,Qt
app=QApplication([]);app.setApplicationName('jarvis-energy-preview')
w=QWidget();w.setWindowTitle('Jarvis Energy — owned temporary test');w.resize(560,300)
w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
w.setStyleSheet('background:#101722;color:#e7f1ff;font-size:24px')
l=QVBoxLayout(w);l.addWidget(QLabel('J A R V I S  /  E N E R G Y\n\nOWNED ANIMATION TEST\n\nNo other windows are controlled.'))
records=[]
def sample(phase):
 d=subprocess.check_output(['qdbus','org.kde.KWin','/Effects','org.kde.kwin.Effects.debug','jarvis-energy',''],text=True)
 records.append({'phase':phase,'debug':d})
def at(t,f):QTimer.singleShot(t,Qt.TimerType.PreciseTimer,f)
def restore():
 if os.environ.get('QT_QPA_PLATFORM') == 'xcb':
  w.showNormal();return
 # Wayland has no client request to unminimize; act only on our PID + exact title.
 name='jarvis-energy-owned-restore-'+str(os.getpid())
 path=Path(tempfile.gettempdir())/(name+'.js')
 path.write_text('for (const w of workspace.stackingOrder) { if (w.pid === '+str(os.getpid())+' && w.caption === "Jarvis Energy — owned temporary test") w.minimized=false; }')
 base=['qdbus','org.kde.KWin','/Scripting']
 try:
  sid=subprocess.check_output(base+['org.kde.kwin.Scripting.loadScript',str(path),name],text=True).strip()
  subprocess.run(['qdbus','org.kde.KWin','/Scripting/Script'+sid,'org.kde.kwin.Script.run'],check=True,stdout=subprocess.DEVNULL)
 finally:
  subprocess.run(base+['org.kde.kwin.Scripting.unloadScript',name],stdout=subprocess.DEVNULL)
  path.unlink(missing_ok=True)
at(100,w.show)
for t in [220,360,800]:at(t,lambda t=t:sample('open-'+str(t)))
at(1100,w.showMinimized)
for t in [1180,1360,1820]:at(t,lambda t=t:sample('minimize-'+str(t)))
at(2100,restore)
for t in [2180,2360,2820]:at(t,lambda t=t:sample('restore-'+str(t)))
at(3100,w.showMinimized);at(3230,restore);at(3310,lambda:sample('rapid-reverse'));at(3850,lambda:sample('reverse-settled'))
at(4100,w.close)
for t in [4180,4400,4920,5500,5900]:at(t,lambda t=t:sample('close-'+str(t)))
at(6500,lambda:sample("close-final-cleanup"))
at(6750,app.quit)
app.setQuitOnLastWindowClosed(False);app.exec()
print(json.dumps(records,indent=2))
