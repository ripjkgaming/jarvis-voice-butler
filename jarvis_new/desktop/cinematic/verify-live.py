"""Exercise only owned temporary windows and their task entries on the current desktop."""
import json,os,subprocess
from pathlib import Path
from PyQt6.QtWidgets import QApplication,QWidget,QLabel,QVBoxLayout,QMenu
from PyQt6.QtCore import QTimer,Qt,QPoint
app=QApplication([]);app.setQuitOnLastWindowClosed(False)
name='jarvis-cinematic-check-'+str(os.getpid());app.setApplicationName(name);app.setDesktopFileName(name)
entry=Path.home()/'.local/share/applications'/(name+'.desktop')
entry.write_text('[Desktop Entry]\nType=Application\nName=Jarvis cinematic check\nExec=false\nIcon=system-file-manager\n')
windows=[];records=[]
for i in range(2):
 w=QWidget();w.setWindowTitle('Jarvis cinematic — owned check '+str(i));w.resize(480,250)
 w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating);w.setStyleSheet('background:#102333;color:#d9faff;font-size:20px')
 layout=QVBoxLayout(w);layout.addWidget(QLabel('J.A.R.V.I.S.\n\nDESKTOP ANIMATION CHECK\n\nWindow '+str(i+1)));windows.append(w)
menu=QMenu(windows[0]);menu.addAction('System console');menu.addAction('Animation check');menu.addAction('Close menu')
def sample(label):
 record={'phase':label}
 for service,path in [('TaskbarPuppet','/Puppet'),('TaskbarSlice','/Slice')]:
  record[service]=json.loads(subprocess.check_output(['qdbus','org.jarvis.'+service,path,'org.jarvis.'+service+'.Status'],text=True))
 for effect in ['jarvis-energy','jarvis-surfaces']:
  record[effect]=subprocess.check_output(['qdbus','org.kde.KWin','/Effects','org.kde.kwin.Effects.debug',effect,''],text=True)
 records.append(record)
def at(ms,fn):QTimer.singleShot(ms,Qt.TimerType.PreciseTimer,fn)
at(100,windows[0].show)
for t in [180,280,400,600,900]:at(t,lambda t=t:sample('first-open-'+str(t)))
at(1100,windows[1].show);at(1200,lambda:sample('second-open'));at(1600,windows[1].close);at(1720,lambda:sample('first-close-group-remains'))
at(2000,lambda:menu.popup(windows[0].mapToGlobal(QPoint(40,70))));at(2060,lambda:sample('menu-open'));at(2300,menu.hide)
at(2700,windows[0].showMaximized);at(2760,lambda:sample('maximize'));at(3100,windows[0].showNormal);at(3160,lambda:sample('unmaximize'))
at(3700,windows[0].close)
for t in [3740,3800,3900,4400]:at(t,lambda t=t:sample('last-close-'+str(t)))
at(4700,app.quit)
try:app.exec()
finally:entry.unlink(missing_ok=True)
Path(__file__).with_name('validation').joinpath('live-desktop-'+os.environ.get('QT_QPA_PLATFORM','default')+'.json').write_text(json.dumps({'app':name,'records':records},indent=2))
print('Owned desktop checks completed')
