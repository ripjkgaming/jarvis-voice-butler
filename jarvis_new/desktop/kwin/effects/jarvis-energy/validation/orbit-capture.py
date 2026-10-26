import subprocess,os
from PyQt6.QtWidgets import QApplication,QWidget,QLabel,QVBoxLayout
from PyQt6.QtCore import QTimer,Qt
app=QApplication([]);app.setApplicationName('jarvis-energy-preview')
bg=QWidget(None,Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint)
bg.setWindowTitle('Owned animation evidence backdrop');bg.setGeometry(0,0,1920,1024);bg.setStyleSheet('background:#070b12');bg.show()
w=QWidget(None,Qt.WindowType.Window|Qt.WindowType.WindowStaysOnTopHint);w.setWindowTitle('Jarvis Energy — owned frozen capture');w.setGeometry(500,150,560,300);w.setStyleSheet('background:#101b29;color:#e7f1ff;font-size:23px');l=QVBoxLayout(w);l.addWidget(QLabel('J A R V I S  /  E N E R G Y\n\nOWNED VISUAL TEST\n\nTemporary window'))
def show():
 w.show();w.raise_();w.activateWindow();w.repaint()
QTimer.singleShot(200,show)
QTimer.singleShot(600,w.repaint)
kind=os.environ.get('CAPTURE_KIND','close')
if kind=='close':QTimer.singleShot(1000,w.close)
if kind=='minimize':QTimer.singleShot(1000,w.showMinimized)
if kind=='restore':
 QTimer.singleShot(650,w.showMinimized);QTimer.singleShot(1250,w.showNormal)
def capture():
 subprocess.run(['spectacle','-b','-n','-f','-o',os.environ['CAPTURE_OUTPUT']],timeout=10,check=True)
QTimer.singleShot(1800,capture);QTimer.singleShot(2400,app.quit)
app.setQuitOnLastWindowClosed(False);app.exec()
