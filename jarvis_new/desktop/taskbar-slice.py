#!/usr/bin/env python3
"""Event-driven, input-transparent task-icon copy. Four fragments, no idle repaint."""
import configparser,json,math,sys
from pathlib import Path
from PyQt6.QtCore import Qt,QTimer,QElapsedTimer,QRectF,QRect,QPointF,pyqtSlot,pyqtClassInfo
from PyQt6.QtGui import QColor,QIcon,QPainter,QPen
from PyQt6.QtWidgets import QApplication,QWidget
from PyQt6.QtDBus import QDBusConnection

@pyqtClassInfo('D-Bus Interface','org.jarvis.TaskbarSlice')
class Slice(QWidget):
    def __init__(self):
        super().__init__(None,Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.X11BypassWindowManagerHint|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.WindowTransparentForInput|Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle('Jarvis taskbar transient icon copy')
        self.timer=QTimer(self);self.timer.setInterval(33);self.timer.timeout.connect(self.tick)
        self.clock=QElapsedTimer();self.locked=False
        self.last={}
    @pyqtSlot()
    def Cancel(self):
        self.timer.stop();self.hide()
    @pyqtSlot(bool)
    def lock(self,locked):
        self.locked=locked
        if locked:self.Cancel()
    @pyqtSlot(result=str)
    def Status(self):
        return json.dumps({'visible':self.isVisible(),'timerActive':self.timer.isActive(),'last':self.last})
    @pyqtSlot()
    def Quit(self):
        self.Cancel();QApplication.quit()
    @pyqtSlot(str)
    def Play(self,payload):
        self.Cancel()
        try:
            c=configparser.RawConfigParser(strict=False);c.read(Path.home()/'.config/kwinrc')
            g=configparser.RawConfigParser(strict=False);g.read(Path.home()/'.config/kdeglobals')
            factor=g.getfloat('KDE','AnimationDurationFactor',fallback=1)
            if self.locked or not c.getboolean('Taskbar-Slice','Enabled',fallback=True):return
            self.reduced=factor<=0 or c.getboolean('Effect-jarvis-energy','ReducedMotion',fallback=False)
            factor=max(.01,factor)
            d=json.loads(payload);s=d['screen'];x=float(d['x'])+float(d['width'])/2;y=float(d['y'])+float(d['height'])/2
            if not(s['x']<=x<=s['x']+s['width'] and s['y']<=y<=s['y']+s['height']):return
            name=d['app'];icon=QIcon()
            paths=[Path(name)] if name.startswith('/') else [Path.home()/'.local/share/applications'/(name+'.desktop'),Path('/usr/share/applications')/(name+'.desktop')]
            for path in paths:
                if path.is_file():
                    entry=configparser.RawConfigParser(strict=False);entry.read(path)
                    icon=QIcon.fromTheme(entry.get('Desktop Entry','Icon',fallback=''));break
            if icon.isNull():icon=QIcon.fromTheme(name)
            if icon.isNull():return
            self.size=min(40,max(20,min(float(d['width']),float(d['height']))-8));self.pix=icon.pixmap(round(self.size),round(self.size))
            left=max(s['x'],round(x-90));top=max(s['y'],round(y-70));bottom=s['y']+s['height']
            self.setGeometry(int(left),int(top),int(min(180,s['x']+s['width']-left)),int(bottom-top))
            self.cx=x-left;self.cy=y-top;self.fall=bottom-y+self.size
            self.targetBox=self.geometry();self.screenX=x;self.screenY=y
            panel=d['panel'];self.panel=panel
            pl=max(s['x'],panel['x']-24);pt=max(s['y'],panel['y']-24)
            self.setGeometry(int(pl),int(pt),int(min(s['x']+s['width']-pl,panel['width']+48)),int(min(s['y']+s['height']-pt,panel['height']+48)))
            self.sweeping=True;self.slashing=False
            self.scan=d.get('scan',[{'x':x,'y':y}])
            self.dwell=max(120,min(600,c.getint('Taskbar-Slice','ScanDwellMs',fallback=260)))
            self.move=max(80,min(400,c.getint('Taskbar-Slice','ScanMoveMs',fallback=170)))
            self.beat=min(self.dwell+self.move,6000/max(1,len(self.scan)))
            self.scanDuration=self.beat*len(self.scan);self.factor=factor
            self.duration=(self.scanDuration+1100)*factor;self.last=d
            sl=max(s['x'],x-320);st=max(s['y'],y-280)
            self.slashBox=QRect(int(sl),int(st),int(min(640,s['x']+s['width']-sl)),int(min(360,s['y']+s['height']-st)))
            self.clock.start()
            if self.reduced:self.setGeometry(self.targetBox)
            self.show();self.timer.setSingleShot(self.reduced);self.timer.start(220 if self.reduced else 33);self.update()
        except (ValueError,KeyError,TypeError,configparser.Error):return
    def tick(self):
        if self.reduced or self.clock.elapsed()>=self.duration:self.Cancel()
        else:
            t=self.phase()
            if self.sweeping and t>=.36:
                self.setGeometry(self.slashBox);self.sweeping=False;self.slashing=True
            if self.slashing and t>=.64:
                self.setGeometry(self.targetBox);self.slashing=False
            self.update()
    def phase(self):
        elapsed=self.clock.elapsed()/self.factor
        return .30*elapsed/self.scanDuration if elapsed<self.scanDuration else min(1,.30+.70*(elapsed-self.scanDuration)/1100)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t=self.phase();n=self.size
        cx=self.screenX-self.x();cy=self.screenY-self.y()
        p.setPen(QPen(QColor('#54dfff'),1.5))
        if self.reduced:
            p.drawRect(QRectF(cx-n/2-3,cy-n/2-3,n+6,n+6));p.end();return
        if t<.36:
            elapsed=self.clock.elapsed()/self.factor
            index=min(len(self.scan)-1,int(elapsed/self.beat));local=elapsed-index*self.beat
            previous=self.scan[max(0,index-1)];point=self.scan[index]
            travel=min(1,local/min(self.move,self.beat*.45));travel=travel*travel*(3-2*travel)
            rx=previous['x']+(point['x']-previous['x'])*travel-self.x()
            ry=previous['y']+(point['y']-previous['y'])*travel-self.y()
            p.save();p.translate(rx,ry);p.rotate(self.clock.elapsed()*.16)
            p.drawArc(QRectF(-22,-22,44,44),20*16,115*16);p.drawArc(QRectF(-22,-22,44,44),200*16,115*16)
            p.drawLine(QPointF(-29,0),QPointF(-18,0));p.drawLine(QPointF(18,0),QPointF(29,0));p.restore()
        if .30<t<.63:
            radius=n/2+5+8*max(0,1-(t-.30)/.12)
            for sx in [-1,1]:
                for sy in [-1,1]:
                    q=QPointF(cx+sx*radius,cy+sy*radius)
                    p.drawLine(q,QPointF(q.x()-sx*8,q.y()));p.drawLine(q,QPointF(q.x(),q.y()-sy*8))
        fall=max(0,(t-.62)/.38)
        code=min(1,fall*2.5)
        for row in range(2):
            for col in range(2):
                sign=-1 if col==0 else 1
                p.save();p.translate(cx+sign*fall*20,cy+self.fall*fall*fall)
                p.rotate(sign*fall*(16+row*10))
                rect=QRectF(-n/2+col*n/2,-n/2+row*n/2,n/2,n/2)
                p.setOpacity(1-code)
                p.drawPixmap(rect,self.pix,QRectF(col*self.pix.width()/2,row*self.pix.height()/2,self.pix.width()/2,self.pix.height()/2))
                p.setOpacity(code*(1 if t<.95 else (1-t)/.05));p.setPen(QColor('#7be7ff'))
                font=p.font();font.setFamily('monospace');font.setPixelSize(10);p.setFont(font)
                p.drawText(rect,Qt.AlignmentFlag.AlignCenter,['01','{}','/>','10'][row*2+col]);p.restore()
        if .45<t<.64:
            # One cyan precision shot into the locked copied icon, no screen flash.
            f=min(1,(t-.45)/.10)
            start=QPointF(cx+130,cy-90);end=QPointF(cx,cy)
            tip=start+(end-start)*f
            fade=max(0,1-(t-.55)/.09) if t>.55 else 1
            p.setPen(QPen(QColor(40,190,255,round(55*fade)),6));p.drawLine(start,tip)
            p.setPen(QPen(QColor(70,225,255,round(230*fade)),1.8));p.drawLine(start,tip)
            p.setPen(QPen(QColor(225,250,255,round(255*fade)),.65));p.drawLine(start,tip)
            if t>.55:
                impact=(t-.55)/.09;radius=3+impact*23
                p.setPen(QPen(QColor(80,220,255,round(210*(1-impact))),1.2))
                p.drawEllipse(QPointF(cx,cy),radius,radius)
        if .55<t<.58:
            p.setPen(QPen(QColor('#e0fbff'),1.1))
            p.drawLine(QPointF(cx-5,cy),QPointF(cx+5,cy))
            p.drawLine(QPointF(cx,cy-5),QPointF(cx,cy+5))
        p.end()

if __name__=='__main__':
    app=QApplication(sys.argv);app.setQuitOnLastWindowClosed(False);app.setApplicationName('jarvis-taskbar-slice')
    bus=QDBusConnection.sessionBus()
    if not bus.registerService('org.jarvis.TaskbarSlice'):raise SystemExit('Taskbar slice already running')
    overlay=Slice();bus.registerObject('/Slice',overlay,QDBusConnection.RegisterOption.ExportAllSlots)
    bus.connect('org.freedesktop.ScreenSaver','/ScreenSaver','org.freedesktop.ScreenSaver','ActiveChanged',overlay.lock)
    app.screenRemoved.connect(lambda _:overlay.Cancel())
    app.exec()
