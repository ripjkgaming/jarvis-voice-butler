#!/usr/bin/env python3
"""Finite, click-through task-open puppet; existing close overlay is untouched."""
import importlib.util,json,sys,math
from pathlib import Path
from PyQt6.QtCore import Qt,QPointF,QRectF,pyqtSlot,pyqtClassInfo
from PyQt6.QtGui import QPainter,QPen,QColor,QPainterPath,QLinearGradient,QBrush
from PyQt6.QtWidgets import QApplication
from PyQt6.QtDBus import QDBusConnection
spec=importlib.util.spec_from_file_location('taskbar_slice',Path(__file__).with_name('taskbar-slice.py'))
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
def ease(x):
 x=max(0,min(1,x));return x*x*(3-2*x)
@pyqtClassInfo('D-Bus Interface','org.jarvis.TaskbarPuppet')
class Puppet(base.Slice):
 @pyqtSlot(str)
 def Play(self,payload):
  super().Play(payload)
  if not self.timer.isActive():return
  d=json.loads(payload);s=d['screen'];self.screen=s;self.duration=2300*self.factor
  # A narrow vertical strip; never a full-screen input surface.
  x=self.screenX;left=max(s['x'],min(x-105,s['x']+s['width']-210))
  self.setGeometry(round(left),round(s['y']),round(min(210,s['width'])),round(s['height']))
  self.setWindowTitle('Jarvis taskbar puppet transient icon copy')
  self.detached=False;self.clock.restart()
  if self.reduced:self.setGeometry(self.targetBox)
 @pyqtSlot(result=str)
 def Status(self):return super().Status()
 @pyqtSlot()
 def Cancel(self):super().Cancel()
 @pyqtSlot()
 def Quit(self):super().Quit()
 def phase(self):return min(1,self.clock.elapsed()/max(1,self.duration))
 def tick(self):
  if self.reduced or self.phase()>=1:self.Cancel();return
  # Swing spans only a short top strip; reallocate once, no full-screen surface.
  if self.phase()>.80 and not self.detached:
   s=self.screen;self.setGeometry(round(s['x']),round(s['y']),round(s['width']),180);self.detached=True
  self.update()
 def paintEvent(self,event):
  if not hasattr(self,'screen'):return
  p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
  t=self.phase();s=self.screen;x=self.screenX-self.x();dest=self.screenY-self.y();n=self.size
  if self.reduced:
   p.setPen(QPen(QColor('#ff6578'),1));p.drawRect(QRectF(x-n/2-3,dest-n/2-3,n+6,n+6));p.end();return
  descend=ease(t/.18);pull=ease((t-.18)/.36);settle=ease((t-.54)/.08)
  release=ease((t-.62)/.18);retreat=ease((t-.72)/.18)
  top=s['y']-self.y()
  handY=top-48+126*descend-12*pull-110*retreat
  iconY=dest+48*(1-pull)-3*pull*(1-settle)
  # A transient copy settles into the persistent slot, then fades exactly there.
  if .14<t<.64:
   p.setOpacity(ease((t-.14)/.08)*(1-ease((t-.58)/.06)))
   p.drawPixmap(QRectF(x-n/2,iconY-n/2,n,n),self.pix,QRectF(self.pix.rect()));p.setOpacity(1)
  def filament(path,a,b,opacity=1):
   # Three cheap vector strokes, with fading ends; no blur surface.
   for width,strength in [(4.0,.12),(1.25,.78),(.45,.95)]:
    g=QLinearGradient(a,b)
    g.setColorAt(0,QColor(255,70,100,round(50*strength*opacity)))
    g.setColorAt(.25,QColor(255,75,100,round(95*strength*opacity)))
    g.setColorAt(.55,QColor(255,90,110,round(65*strength*opacity)))
    g.setColorAt(.82,QColor(255,145,155,round(245*strength*opacity)))
    g.setColorAt(1,QColor(255,210,215,round(160*strength*opacity)))
    p.setPen(QPen(QBrush(g),width,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap));p.drawPath(path)
  for sign in [-1,1]:
   finger=x+sign*(18-5*pull+8*release)
   a=QPointF(finger,handY+14)
   endY=(handY+14)+(iconY-n*.42-handY-14)*ease(t/.22)
   endY=endY+(top+105-endY)*release
   b=QPointF(x+sign*n*.30,endY)
   if t<.80:
    sag=sign*(9*(1-pull)+2+18*release)
    path=QPainterPath(a)
    path.cubicTo(QPointF(a.x()+sag,a.y()+(b.y()-a.y())*.32),QPointF(b.x()+sag,b.y()-(b.y()-a.y())*.22),b)
    filament(path,a,b,ease(t/.10))
   else:
    swing=ease((t-.80)/.20);edge=s['x']-self.x() if self.screenX<s['x']+s['width']/2 else s['x']+s['width']-self.x()
    tip=x+(edge-x)*swing
    a=QPointF(tip,top+22+sign*3)
    b=QPointF(tip+(x-tip)*.20,top+105*(1-swing)+sign*5)
    path=QPainterPath(a);path.cubicTo(QPointF(a.x()+(x-tip)*.07,top+120*(1-swing)),QPointF(b.x(),b.y()+12),b)
    filament(path,a,b,1-ease((t-.94)/.06))
  # Faint articulated code silhouette: wrist, palm and three curling fingers.
  if t<.90:
   p.setOpacity((1-retreat)*.32);font=p.font();font.setFamily('monospace');font.setPixelSize(9);p.setFont(font);p.setPen(QColor('#ff8092'))
   for text,dx,dy in [('010',-10,-30),('1::0',-13,-20),('{01::10}',-25,-10),('10::01',-20,0)]:p.drawText(QPointF(x+dx,handY+dy),text)
   # Five finite code fingers; the two controlled tethers meet fingertip glyphs.
   for index,dx in enumerate([-28,-16,0,16,28]):
    length=[7,19,23,19,9][index]
    bend=(3*pull-5*release)*(1 if dx>0 else -1)
    p.drawText(QPointF(x+dx-3,handY+7),'1')
    p.drawText(QPointF(x+dx+bend-3,handY+length),'0')
    if abs(dx)==16:p.drawText(QPointF(x+dx+bend-3,handY+length+5),'>')
   p.setOpacity(1)
  p.end()
if __name__=='__main__':
 app=QApplication(sys.argv);app.setQuitOnLastWindowClosed(False);app.setApplicationName('jarvis-taskbar-puppet')
 bus=QDBusConnection.sessionBus()
 if not bus.registerService('org.jarvis.TaskbarPuppet'):raise SystemExit('Puppet already running')
 overlay=Puppet();bus.registerObject('/Puppet',overlay,QDBusConnection.RegisterOption.ExportAllSlots)
 bus.connect('org.freedesktop.ScreenSaver','/ScreenSaver','org.freedesktop.ScreenSaver','ActiveChanged',overlay.lock)
 app.screenRemoved.connect(lambda _:overlay.Cancel());app.exec()
