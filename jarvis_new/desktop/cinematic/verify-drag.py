"""Native interactive move plus Jarvis layout, restricted to an owned window."""
import os,sys,json,subprocess,tempfile
from pathlib import Path
from PyQt6.QtWidgets import QApplication,QWidget,QLabel,QVBoxLayout
from PyQt6.QtCore import QTimer,Qt
from evdev import UInput,ecodes
import kwin_bridge_loader
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from system import kwin_windows,window_layout
app=QApplication([]);app.setApplicationName('jarvis-drag-check');app.setDesktopFileName('jarvis-drag-check');app.setQuitOnLastWindowClosed(False)
w=QWidget();w.setWindowTitle('Jarvis owned drag check');w.resize(680,390);w.move(540,300);w.setStyleSheet('background:#08243e;color:#e5f3ff;font-size:26px');QVBoxLayout(w).addWidget(QLabel('JARVIS // LIVE GLASS\n\nDrag · resize · snap'))
w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
keyboard=UInput({ecodes.EV_KEY:[ecodes.KEY_RIGHT,ecodes.KEY_DOWN,ecodes.KEY_ENTER,ecodes.KEY_ESC]},name='Jarvis owned drag verification')
name='jarvis-drag-verify-'+str(os.getpid());script=Path(tempfile.gettempdir())/(name+'.js')
script.write_text('var previous=workspace.activeWindow; workspace.windowRemoved.connect(function(w){if(w.pid===PID && previous&&!previous.deleted)workspace.activeWindow=previous;});'.replace('PID',str(os.getpid())))
records=[]
def key(code):keyboard.write(ecodes.EV_KEY,code,1);keyboard.syn();keyboard.write(ecodes.EV_KEY,code,0);keyboard.syn()
def sample(label):
 text=subprocess.check_output(['qdbus','org.kde.KWin','/Effects','org.kde.kwin.Effects.debug','jarvis-energy',''],text=True);records.append({'phase':label,'debug':text});print(label,text,flush=True)
def activate():
 hit=kwin_windows.find('Jarvis owned drag check')[0];kwin_windows._dbus('Run','string:0_'+hit[0],'string:')
def move():subprocess.run(['qdbus','org.kde.kglobalaccel','/component/kwin','org.kde.kglobalaccel.Component.invokeShortcut','Window Move'],check=True)
def resize():subprocess.run(['qdbus','org.kde.kglobalaccel','/component/kwin','org.kde.kglobalaccel.Component.invokeShortcut','Window Resize'],check=True)
def at(t,f):QTimer.singleShot(t,Qt.TimerType.PreciseTimer,f)
at(50,lambda:kwin_bridge_loader.load(script,name));at(100,w.show);at(850,activate);at(1200,move)
for i in range(4):at(1450+i*70,lambda:key(ecodes.KEY_RIGHT))
at(1810,lambda:sample('drag-held'))
at(2890,lambda:sample('drag-loop'))
if os.environ.get('JARVIS_DRAG_CAPTURE'):at(1830,lambda:subprocess.run(['spectacle','-b','-n','-f','-o',os.environ['JARVIS_DRAG_CAPTURE']],check=True,timeout=8))
at(3150,lambda:key(ecodes.KEY_ENTER));at(3250,lambda:sample('drag-settle'))
at(3800,w.showMaximized);at(3910,lambda:sample('manual-maximize'))
at(4500,w.showNormal);at(5100,lambda:window_layout.arrange('left',['Jarvis owned drag check']));at(5900,lambda:sample('Jarvis-arrange-early'));at(6550,lambda:sample('Jarvis-arrange-late'))
at(6900,w.showNormal);at(7300,activate);at(7600,resize)
for i in range(3):at(7800+i*70,lambda:key(ecodes.KEY_DOWN))
at(8150,lambda:sample('resize-held'));at(8400,lambda:key(ecodes.KEY_ENTER));at(8500,lambda:sample('resize-settle'))
at(9000,w.close);at(9900,lambda:sample('idle'));at(10300,app.quit)
try:app.exec()
finally:
 key(ecodes.KEY_ESC);keyboard.close();w.close();kwin_bridge_loader.unload(name);script.unlink(missing_ok=True)
(Path(__file__).parent/'validation/drag-live.json').write_text(json.dumps(records,indent=2))
by_phase={r['phase']:r['debug'] for r in records}
for phase in ('drag-held','drag-loop','drag-settle','resize-held','resize-settle'):
 assert 'Animating window: Jarvis owned drag check' in by_phase[phase],phase
assert 'Jarvis owned drag check' not in by_phase['idle']
print('PASS: native drag and resize animate through release and clean up.')
