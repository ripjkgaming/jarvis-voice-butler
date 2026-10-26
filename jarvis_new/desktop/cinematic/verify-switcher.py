"""Short native Alt+Tab check restricted to two owned windows; restore prior focus."""
import os,subprocess,tempfile
from pathlib import Path
from PyQt6.QtWidgets import QApplication,QWidget,QLabel,QVBoxLayout
from PyQt6.QtCore import QTimer,Qt
from evdev import UInput,ecodes
import kwin_bridge_loader
app=QApplication([]);app.setApplicationName('jarvis-switcher-check');app.setQuitOnLastWindowClosed(False)
windows=[]
count=int(os.environ.get("JARVIS_CHECK_WINDOWS","2"))
for i in range(count):
 w=QWidget();w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating);w.setWindowTitle('Jarvis switcher check '+str(i));w.resize(900 if os.environ.get('JARVIS_CHECK_BRIGHT') else 420,600 if os.environ.get('JARVIS_CHECK_BRIGHT') else 160);w.setStyleSheet('background:#ffffff;color:#111111;font-size:22px' if os.environ.get('JARVIS_CHECK_BRIGHT') else 'background:#102333;color:#ccefff;font-size:22px');QVBoxLayout(w).addWidget(QLabel('JARVIS / SWITCHER CHECK'));windows.append(w)
keyboard=UInput({ecodes.EV_KEY:[ecodes.KEY_LEFTALT,ecodes.KEY_ESC]},name='Jarvis owned animation test')
name='jarvis-cinematic-switcher-check-'+str(os.getpid());path=Path(tempfile.gettempdir())/(name+'.js')
path.write_text('''var previous=workspace.activeWindow;
var owned=workspace.windowList().filter(function(w){return w.pid===PID;});
if(owned.length>=2)workspace.activeWindow=owned[0];
workspace.windowRemoved.connect(function(w){if(w.pid===PID && !workspace.windowList().some(function(v){return v!==w && v.pid===PID && !v.deleted;})){if(previous&&!previous.deleted)workspace.activeWindow=previous;}});
'''.replace('PID',str(os.getpid())))
def press():keyboard.write(ecodes.EV_KEY,ecodes.KEY_LEFTALT,1);keyboard.syn()
def switch():subprocess.run(['qdbus','org.kde.kglobalaccel','/component/kwin','org.kde.kglobalaccel.Component.invokeShortcut','Walk Through Windows of Current Application'],check=True)
def release():
 keyboard.write(ecodes.EV_KEY,ecodes.KEY_ESC,1);keyboard.syn();keyboard.write(ecodes.EV_KEY,ecodes.KEY_ESC,0);keyboard.write(ecodes.EV_KEY,ecodes.KEY_LEFTALT,0);keyboard.syn()
for i,w in enumerate(windows):QTimer.singleShot(50+i*50,w.show)
QTimer.singleShot(500,lambda:kwin_bridge_loader.load(path,name));QTimer.singleShot(750,press)
for i in range(max(2,count-1)):QTimer.singleShot(880+i*60,switch)
if os.environ.get('JARVIS_CHECK_CAPTURE'):
 QTimer.singleShot(1250,lambda:subprocess.run(['spectacle','-b','-n','-f','-o',os.environ['JARVIS_CHECK_CAPTURE']],check=True,timeout=8))
QTimer.singleShot(1450,release)
for i,w in enumerate(reversed(windows)):QTimer.singleShot(1650+i*35,w.close)
QTimer.singleShot(2200,app.quit)
try:app.exec()
finally:
 release();keyboard.close();kwin_bridge_loader.unload(name);path.unlink(missing_ok=True)
print('Native owned-app switcher check finished; Alt released and previous focus restored.')
