#!/usr/bin/env python3
"""Finite, local cyan task-icon accents using the existing event-driven bridges."""
import configparser
import json
import math
import time
import sys
import subprocess
from pathlib import Path
from PyQt6.QtCore import Qt, QTimer, QElapsedTimer, QRectF, QPointF, pyqtSlot, pyqtClassInfo
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtDBus import QDBusConnection


def ease(t):
    t=max(0,min(1,t));return t*t*(3-2*t)


class Accent(QWidget):
    def __init__(self, opening):
        super().__init__(None,Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.X11BypassWindowManagerHint|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.WindowTransparentForInput|Qt.WindowType.WindowDoesNotAcceptFocus)
        self.opening=opening
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle('Jarvis cinematic task accent')
        self.timer=QTimer(self);self.timer.setInterval(16);self.timer.timeout.connect(self.tick)
        self.clock=QElapsedTimer();self.locked=False;self.last={};self.duration=1;self.command_token=""
    @pyqtSlot()
    def Cancel(self):
        self.timer.stop();self.hide()
    @pyqtSlot()
    def Quit(self):
        self.Cancel();QApplication.quit()
    @pyqtSlot(bool)
    def lock(self,locked):
        self.locked=locked
        if locked:self.Cancel()
    @pyqtSlot(result=str)
    def Status(self):
        return json.dumps({'visible':self.isVisible(),'timerActive':self.timer.isActive(),'last':self.last})
    @pyqtSlot(str)
    def Play(self,payload):
        self.Cancel()
        try:
            c=configparser.RawConfigParser(strict=False);c.read(Path.home()/'.config/kwinrc')
            g=configparser.RawConfigParser(strict=False);g.read(Path.home()/'.config/kdeglobals')
            factor=g.getfloat('KDE','AnimationDurationFactor',fallback=1)
            if self.locked or factor<=0 or c.getboolean('Effect-jarvis-energy','ReducedMotion',fallback=False) or not c.getboolean('Taskbar-Slice','Enabled',fallback=True):return
            d=json.loads(payload);s=d['screen'];panel=d['panel']
            x=float(d['x'])+float(d['width'])/2;y=float(d['y'])+float(d['height'])/2
            if not all(math.isfinite(float(v)) for v in [x,y,s['x'],s['y'],s['width'],s['height'],factor]):return
            if not(s['x']<=x<s['x']+s['width'] and s['y']<=y<s['y']+s['height']):return
            if not(panel['x']<=x<=panel['x']+panel['width'] and panel['y']<=y<=panel['y']+panel['height']):return
            name=d['app'];icon=QIcon()
            paths=[Path(name)] if name.startswith('/') else [Path.home()/'.local/share/applications'/(name+'.desktop'),Path('/usr/share/applications')/(name+'.desktop')]
            for path in paths:
                if path.is_file():
                    entry=configparser.RawConfigParser(strict=False);entry.read(path)
                    icon=QIcon.fromTheme(entry.get('Desktop Entry','Icon',fallback=''));break
            if icon.isNull():icon=QIcon.fromTheme(name)
            if icon.isNull():return
            self.size=min(40,max(20,min(float(d['width']),float(d['height']))-8))
            self.pix=icon.pixmap(round(self.size),round(self.size))
            left=max(s['x'],min(x-64,s['x']+s['width']-128));top=max(s['y'],min(y-64,s['y']+s['height']-128))
            self.setGeometry(round(left),round(top),round(min(128,s['width'])),round(min(128,s['height'])))
            self.cx=x-self.x();self.cy=y-self.y();self.vertical=panel['height']>panel['width']
            self.direction=-1 if (x<s['x']+s['width']/2 if self.vertical else y<s['y']+s['height']/2) else 1
            self.duration=(580 if self.opening else 440)*factor
            request=json.loads(c.get('Effect-jarvis-energy','ActionRequest',fallback='{}'))
            commanded=(not self.opening and request.get('action')=='close'
                and request.get('token') and request['token']!=self.command_token
                and time.time()*1000<request.get('expires',0)<time.time()*1000+10000
                and request.get('pid')==d.get('pid') and request.get('caption')==d.get('caption'))
            if commanded:
                self.duration=1450;self.command_token=request['token']
            d['commanded']=bool(commanded);d['durationMs']=self.duration
            self.last=d;self.clock.start();self.show();self.timer.start();self.update()
        except (ValueError,KeyError,TypeError,OverflowError,configparser.Error):
            self.Cancel()
    def phase(self):return min(1,self.clock.elapsed()/max(1,self.duration))
    def tick(self):
        if self.phase()>=1:self.Cancel()
        else:self.update()
    def draw_icon(self,p,x,y,w,h,t):
        beat=int(t*9)
        for row in range(4):
            shift=(4 if row%2 else -4) if beat in (1,4,6) and row in (1,2) else 0
            p.drawPixmap(QRectF(x+shift,y+row*h/4,w,h/4),self.pix,QRectF(0,row*self.pix.height()/4,self.pix.width(),self.pix.height()/4))
    def paintEvent(self,event):
        if not self.last:return
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t=self.phase();e=ease(t);n=self.size;cx=self.cx;cy=self.cy
        alpha=math.sin(t*math.pi)
        # Local brackets assemble around the real slot; nothing moves other icons.
        radius=n/2+4+(1-e)*8 if self.opening else n/2+4-e*12
        p.setPen(QPen(QColor(*( (99,185,255) if self.opening else (110,192,255) ),round(220*alpha)),1.2))
        for sx in [-1,1]:
            for sy in [-1,1]:
                q=QPointF(cx+sx*radius,cy+sy*radius)
                p.drawLine(q,QPointF(q.x()-sx*7,q.y()));p.drawLine(q,QPointF(q.x(),q.y()-sy*7))
        if self.opening:
            offset=-self.direction*10*(1-e)
            dx=offset if self.vertical else 0;dy=0 if self.vertical else offset
            p.setOpacity(ease(t/.2)*(1-ease((t-.55)/.45))*.85)
            self.draw_icon(p,cx-n/2+dx,cy-n/2+dy,n,n,t)
        else:
            p.setOpacity(1-e)
            h=n*(1-e*.18);w=n*(1-e*.18)
            self.draw_icon(p,cx-w/2,cy-h/2,w,h,t)
        p.setOpacity(1)
        # Finite horizontal signal fragments replace circular pulses.
        beat=int(t*9)
        if beat in (1,4,6):
            p.setPen(QPen(QColor(*( (120,205,255) if self.opening else (154,216,255) ),round(200*alpha)),1.3))
            for row in range(3):
                shift=(1 if (row+beat)%2 else -1)*(4+row*3)
                yy=cy-n*.4+row*n*.4
                p.drawLine(QPointF(cx-n*.65+shift,yy),QPointF(cx+n*.45+shift,yy))
        p.setPen(QPen(QColor(210,250,255,round(240*alpha)),1.1))
        yy=cy-n/2+n*e
        p.drawLine(QPointF(cx-n*.6,yy),QPointF(cx+n*.6,yy))
        p.end()


@pyqtClassInfo('D-Bus Interface','org.jarvis.TaskbarSlice')
class CloseAccent(Accent):
    def __init__(self):super().__init__(False)
    @pyqtSlot(str)
    def Play(self,payload):super().Play(payload)
    @pyqtSlot()
    def Cancel(self):super().Cancel()
    @pyqtSlot()
    def Quit(self):super().Quit()
    @pyqtSlot(result=str)
    def Status(self):return super().Status()



@pyqtClassInfo('D-Bus Interface','org.jarvis.TaskbarPuppet')
class OpenAccent(Accent):
    def __init__(self):super().__init__(True)
    @pyqtSlot(str)
    def Play(self,payload):super().Play(payload)
    @pyqtSlot()
    def Cancel(self):super().Cancel()
    @pyqtSlot()
    def Quit(self):super().Quit()
    @pyqtSlot(result=str)
    def Status(self):return super().Status()
    @pyqtSlot(str, result=bool)
    def PrepareGeometry(self,payload):
        try:
            sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
            from system.cinematic import mark_geometry
            return bool(mark_geometry(json.loads(payload)))
        except Exception:return False
    @pyqtSlot(str,str,str)
    def compositor_changed(self,name,old,new):
        if name=='org.kde.KWin' and new:
            QTimer.singleShot(1500,self.repair_compositor)
    def repair_compositor(self):
        subprocess.Popen(['/usr/bin/python',str(Path(__file__).with_name('manage.py')),'start'],
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)



if __name__=='__main__':
    opening=len(sys.argv)>1 and sys.argv[1]=='open'
    app=QApplication(sys.argv);app.setQuitOnLastWindowClosed(False);app.setApplicationName('jarvis-cinematic-taskbar')
    bus=QDBusConnection.sessionBus();service='org.jarvis.TaskbarPuppet' if opening else 'org.jarvis.TaskbarSlice'
    if not bus.registerService(service):raise SystemExit('Taskbar service already running')
    overlay=OpenAccent() if opening else CloseAccent()
    bus.registerObject('/Puppet' if opening else '/Slice',overlay,QDBusConnection.RegisterOption.ExportAllSlots)
    bus.connect('org.freedesktop.ScreenSaver','/ScreenSaver','org.freedesktop.ScreenSaver','ActiveChanged',overlay.lock)
    if opening:
        bus.interface().serviceOwnerChanged.connect(overlay.compositor_changed)
    app.screenRemoved.connect(lambda _:overlay.Cancel());app.exec()
