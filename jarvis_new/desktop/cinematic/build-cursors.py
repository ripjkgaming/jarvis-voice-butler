"""Native animated Xcursor assets: fixed hotspots, blue glass and crimson accents."""
from pathlib import Path
import math
import struct
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QImage, QPainter, QPainterPath, QPen, QColor, QLinearGradient

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'icons/jarvis-electric-cursors'
FRAMES=24
SIZES=(24,32,48,64)

def render(kind, size, frame):
    im=QImage(size*3,size*3,QImage.Format.Format_ARGB32_Premultiplied);im.fill(0)
    p=QPainter(im);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.scale(size*3/32,size*3/32)
    phase=frame/FRAMES
    pulse=.5+.5*math.sin(phase*2*math.pi)
    gradient=QLinearGradient(3,3,24,29)
    gradient.setColorAt(0,QColor('#effaff'));gradient.setColorAt(.45,QColor('#77ccff'));gradient.setColorAt(1,QColor('#216dd9'))
    p.setPen(QPen(QColor('#020916'),2.7,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin));p.setBrush(gradient)
    path=QPainterPath()
    if kind in ('left_ptr','progress','pointer'):
        if kind=='pointer':
            points=[(10,27),(5,18),(7,16),(11,19),(11,5),(14,4),(16,15),(21,14),(27,18),(24,28)]
        else:
            points=[(3,2),(3,26),(9,20),(14,29),(18,27),(13,18),(23,18)]
        path.moveTo(*points[0])
        for point in points[1:]:path.lineTo(*point)
        path.closeSubpath();p.drawPath(path)
        p.setPen(QPen(QColor('#b41438'),1.5));p.drawLine(QPointF(5,10+phase*9),QPointF(5,15+phase*9))
        p.setPen(QPen(QColor(205,244,255,round(125+125*pulse)),1));p.drawLine(QPointF(7,14),QPointF(12,17))
    elif kind=='text':
        p.setBrush(Qt.BrushStyle.NoBrush)
        for width,col in [(4,'#020916'),(1.5,'#a6ddff')]:
            p.setPen(QPen(QColor(col),width));p.drawLine(16,5,16,27);p.drawLine(11,5,21,5);p.drawLine(11,27,21,27)
        p.setPen(QPen(QColor('#bb1639'),2));p.drawPoint(QPointF(16,8+phase*16))
    elif kind in ('crosshair','move','ew-resize','ns-resize','nesw-resize','nwse-resize'):
        p.translate(16,16)
        if kind=='ns-resize':p.rotate(90)
        if kind=='nesw-resize':p.rotate(-45)
        if kind=='nwse-resize':p.rotate(45)
        for width,col in [(4,'#020916'),(1.5,'#a6ddff')]:
            p.setPen(QPen(QColor(col),width));p.drawLine(-11,0,11,0)
            if kind in ('move','crosshair'):p.drawLine(0,-11,0,11)
            if kind!='crosshair':
                p.drawLine(-11,0,-7,-4);p.drawLine(-11,0,-7,4);p.drawLine(11,0,7,-4);p.drawLine(11,0,7,4)
        p.setPen(QPen(QColor(190,16,53,round(150+100*pulse)),2));p.drawPoint(0,0)
    elif kind=='not-allowed':
        p.setBrush(QColor('#360615'));p.setPen(QPen(QColor('#f04b6f'),2));p.drawRoundedRect(5,5,22,22,5,5)
        p.drawLine(10,10,22,22);p.drawLine(10,22,22,10)
    if kind in ('wait','progress'):
        # Segmented data ladder, never a circular ripple.
        x,y=(22,3) if kind=='progress' else (6,8)
        for i in range(4):
            p.setPen(QPen(QColor('#020916'),4));p.drawLine(x,y+i*4,x+6,y+i*4)
            p.setPen(QPen(QColor('#d72a4e') if i==int(phase*4)%4 else QColor('#65bbff'),2));p.drawLine(x,y+i*4,x+6,y+i*4)
    p.end()
    return im.scaled(size,size,Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)

def build(kind):
    chunks=[];toc=[]
    for size in SIZES:
        for frame in range(FRAMES):
            im=render(kind,size,frame)
            hot=(3,2) if kind in ('left_ptr','progress') else (13,5) if kind=='pointer' else (16,16)
            header=struct.pack('<9I',36,0xfffd0002,size,1,size,size,round(hot[0]*size/32),round(hot[1]*size/32),50)
            chunks.append((size,header+im.bits().asstring(im.sizeInBytes())))
    offset=16+12*len(chunks)
    for size,chunk in chunks:toc.append(struct.pack('<3I',0xfffd0002,size,offset));offset+=len(chunk)
    return struct.pack('<4I',0x72756358,16,0x10000,len(chunks))+b''.join(toc)+b''.join(c for _,c in chunks)

aliases={
 'left_ptr':['default','arrow','top_left_arrow'],
 'pointer':['hand2','hand1','pointing_hand','9d800788f1b08800ae810202380a0822'],
 'text':['xterm','ibeam'], 'crosshair':['cross'],
 'wait':['watch'], 'progress':['left_ptr_watch','00000000000000020006000e7e9ffc3f'],
 'move':['fleur','all-scroll','size_all','grab','grabbing','openhand','closedhand'],
 'ew-resize':['size_hor','h_double_arrow','sb_h_double_arrow','col-resize','e-resize','w-resize','left_side','right_side'],
 'ns-resize':['size_ver','v_double_arrow','sb_v_double_arrow','row-resize','n-resize','s-resize','top_side','bottom_side'],
 'nesw-resize':['size_bdiag','fd_double_arrow','ne-resize','sw-resize','top_right_corner','bottom_left_corner'],
 'nwse-resize':['size_fdiag','bd_double_arrow','nw-resize','se-resize','top_left_corner','bottom_right_corner'],
 'not-allowed':['forbidden','crossed_circle','no-drop'],
}
OUT.mkdir(parents=True,exist_ok=True);(OUT/'cursors').mkdir(exist_ok=True)
(OUT/'index.theme').write_text('[Icon Theme]\nName=Jarvis Electric Glass\nComment=Animated blue glass pointers with crimson signal accents\nInherits=breeze_cursors\n')
sheet=QImage(96*6,96*2,QImage.Format.Format_ARGB32);sheet.fill(QColor('#14243b'))
p=QPainter(sheet)
for i,(kind,names) in enumerate(aliases.items()):
    (OUT/'cursors'/kind).write_bytes(build(kind))
    for name in names:
        target=OUT/'cursors'/name;target.unlink(missing_ok=True);target.symlink_to(kind)
    p.drawImage((i%6)*96+16,(i//6)*96+16,render(kind,64,9))
p.end();(ROOT/'validation').mkdir(exist_ok=True);sheet.save(str(ROOT/'validation/cursor-sheet.png'))
print('Built 12 animated cursor roles, four sizes, 24 frames per size; fixed hotspots, 1200ms cycle.')
