#!/usr/bin/env python3
"""Offline NVIDIA/GLES shader regression for the cross-monitor portal."""
from pathlib import Path
import struct

import moderngl
from PIL import Image, ImageDraw, ImageChops

ROOT=Path(__file__).resolve().parent
shader=(ROOT/'kwin/effects/jarvis-energy/contents/shaders/energy.frag').read_text()
shader=shader.replace('#include "colormanagement.glsl"', '')
shader=shader[:shader.index('    color=encodingToNits')]+ '    fragColor=color; }\n'
ctx=moderngl.create_standalone_context(backend='egl')
p=ctx.program(
    vertex_shader='#version 140\nin vec2 position;out vec2 texcoord0;'
                  'void main(){gl_Position=vec4(position,0,1);'
                  'texcoord0=(position+1)*0.5;}',
    fragment_shader=shader)
v=ctx.simple_vertex_array(
    p,ctx.buffer(struct.pack('8f',-1,-1,1,-1,-1,1,1,1)),'position')
w,h=640,230
frame=ctx.simple_framebuffer((w,h),components=4)
frame.use()
src=Image.new('RGBA',(180,130),'#143f59')
draw=ImageDraw.Draw(src)
draw.rectangle((0,0,179,24),fill='#28749a')
draw.text((8,7),'JARVIS / WINDOW',fill='#ffffff')
for i in range(3):
    draw.rectangle((10,35+i*28,160,50+i*28),outline='#3ac5fa',width=2)
tex=ctx.texture(src.size,4,src.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes())
tex.filter=(moderngl.NEAREST,moderngl.NEAREST)
tex.use()
config={'sampler':0,'canvasSize':(w,h),'sourceRect':(25,38,180,130),
        'portalFrom':(25,38,180,130),'portalTo':(435,38,180,130),
        'target':(610,160),'screenRect':(8,8,240,210),
        'dragFade':1,'gravityFrom':(320,115),
        'gravityTo':(320,115),'gravityPeriod':4200,'gravityPhase':0,
        'commanded':0,'mode':6}
for k,value in config.items():
    if k in p:p[k].value=value

def render(t):
    p['phase'].value=t
    frame.clear()
    v.render(moderngl.TRIANGLE_STRIP)
    return Image.frombytes('RGBA',(w,h),frame.read(components=4)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)

origin=Image.new('RGBA',(w,h))
origin.paste(src,(25,38))
finish=Image.new('RGBA',(w,h))
finish.paste(src,(435,38))
assert ImageChops.difference(render(0),origin).getbbox() is None, 'original source pixels changed'
assert ImageChops.difference(render(1),finish).getbbox() is None, 'destination did not fully reconstruct'
for phase in (.10,.25,.45,.56,.73,.87,.94):
    frame_image=render(phase)
    assert frame_image.getchannel('A').getbbox(), f'blank transition at {phase}'
# Source monitor perimeter must light up before cross-display transfer.
orbit=render(.50).getchannel('A')
for side in [(8,8,248,24),(8,202,248,218),(8,8,24,218),(232,8,248,218)]:
    assert max(orbit.crop(side).getdata())>10, ('source screen side absent',side)
assert max(orbit.crop((65,65,190,155)).getdata()) == 0, 'orbit did not leave window interior'
assert max(orbit.crop((280,10,630,220)).getdata()) == 0, 'transfer began before source orbit'
middle=render(.75)
assert max(middle.getchannel('A').crop((260,10,390,220)).getdata())>10, 'no cross-monitor stream'
# Reference hue: luminous cyan, rather than dark royal-blue motes.
visible=[(r,g,b) for r,g,b,a in render(.50).getdata() if a>90]
assert visible and all(g>b*.70 and g>r for r,g,b in visible), 'reference cyan hue lost'
flash=render(.09)
assert any(g>205 and b>205 and g>r for r,g,b,a in flash.getdata()), 'bright split flash absent'
# Source monitor trace must remain finite.
assert max(orbit.crop((280,0,620,12)).getdata()) == 0
# Reverse direction, negative desktop origin and a resized destination.
p['screenRect'].value=(400,8,232,210)
p['portalFrom'].value=(435,38,180,130)
p['portalTo'].value=(25,25,210,160)
reverse_start=Image.new('RGBA',(w,h)); reverse_start.paste(src,(435,38))
assert ImageChops.difference(render(0),reverse_start).getbbox() is None
for phase in (.13,.28,.49,.70,.79,.91):
    assert render(phase).getchannel('A').getbbox(), ('reverse blank',phase)
assert render(1).getchannel('A').getbbox() == (25,25,235,185)
p['screenRect'].value=config['screenRect']
p['portalFrom'].value=config['portalFrom']
p['portalTo'].value=config['portalTo']
phases=[0,.13,.28,.42,.57,.70,.79,.91,1]
board=Image.new('RGB',(w*3,(h+26)*3),'#07111e')
board_draw=ImageDraw.Draw(board)
for i,phase in enumerate(phases):
    x=(i%3)*w;y=(i//3)*(h+26)
    board_draw.text((x+12,y+7),f'PARTICLE PORTAL  {phase:.0%}',fill='#a0deff')
    board.paste(render(phase),(x,y+26),render(phase))
dest=ROOT/'validation/particle-portal-storyboard.png'
board.resize((1280,512)).save(dest)
print('PASS: portal shader compiles on',ctx.info['GL_RENDERER'])
print('PASS: original and reconstructed content match exactly')
print('PASS: source split, all four source monitor edges, orbit before transfer, final rebuild')
print('STORYBOARD:', dest)
