import sys,json,subprocess,os
from pathlib import Path
from PyQt6.QtWidgets import QApplication,QWidget,QLabel,QVBoxLayout
from PyQt6.QtCore import QTimer,Qt
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from system import kwin_windows
app=QApplication([]);app.setApplicationName('jarvis-minimize-probe');app.setDesktopFileName('jarvis-minimize-probe');app.setQuitOnLastWindowClosed(False)
w=QWidget();w.setWindowTitle('Jarvis minimize owned probe');w.resize(750,450);w.move(500,270);w.setStyleSheet('background:#0b2946;color:#effaff;font-size:28px');QVBoxLayout(w).addWidget(QLabel('JARVIS // MINIMIZE CHECK'));w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
records=[]
def sample(label):
 text=subprocess.check_output(['qdbus','org.kde.KWin','/Effects','org.kde.kwin.Effects.debug','jarvis-energy',''],text=True);records.append({'phase':label,'debug':text});print(label,text,flush=True)
def at(t,f):QTimer.singleShot(t,Qt.TimerType.PreciseTimer,f)
at(100,w.show);at(1400,w.showMinimized);at(1540,lambda:sample('manual minimize +140'))
at(2150,lambda:kwin_windows._dbus("Run","string:0_"+kwin_windows.find("Jarvis minimize owned probe")[0][0],"string:"));at(2290,lambda:sample('manual restore +140'))
at(3100,lambda:kwin_windows.act('minimize','Jarvis minimize owned probe'));at(3650,lambda:sample('Jarvis minimize +550'))
at(4800,lambda:kwin_windows.act('focus','Jarvis minimize owned probe'));at(5350,lambda:sample('Jarvis restore +550'))
at(6400,w.close);at(7200,app.quit)
app.exec();(Path(__file__).parent/'validation/minimize-live.json').write_text(json.dumps(records,indent=2))
for row in records:
 expected=1200 if row['phase'].startswith('Jarvis') else 480
 assert 'Duration: '+str(expected)+'ms' in row['debug'],row['phase']
print('PASS: both manual and Jarvis minimize/restore durations verified on native windows.')
