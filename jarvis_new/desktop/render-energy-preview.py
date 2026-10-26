#!/usr/bin/env python3
"""Synthetic shader validation, not a desktop recording. Run with uv Python 3.11, moderngl and pillow."""
from pathlib import Path
import struct,moderngl
from PIL import Image,ImageDraw
root=Path(__file__).resolve().parent/'kwin/effects/jarvis-energy'
ctx=moderngl.create_standalone_context(backend='egl')
s=(root/'contents/shaders/energy.frag').read_text().replace('#include "colormanagement.glsl"','');s=s[:s.index('    color=encodingToNits')]+'fragColor=color;}'
p=ctx.program(vertex_shader='#version 140\nin vec2 position;out vec2 texcoord0;void main(){gl_Position=vec4(position,0,1);texcoord0=(position+1)*0.5;}',fragment_shader=s)
v=ctx.simple_vertex_array(p,ctx.buffer(struct.pack('8f',-1,-1,1,-1,-1,1,1,1)),'position')
w,h=960,540;f=ctx.simple_framebuffer((w,h),components=4);f.use()
src=Image.new('RGBA',(560,300),'#142434');d=ImageDraw.Draw(src);d.rectangle((0,0,559,35),fill='#37516a');d.text((20,12),'OWNED TEST / LEFT                         RIGHT',fill='white')
for y in range(60,280,28):d.text((30,y),'SYSTEM RECONSTRUCTION  /  WINDOW CONTENT',fill='#d0e4ef');d.line((280,40,280,299),fill='#38aace',width=3)
t=ctx.texture(src.size,4,src.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes());t.use()
for k,val in {'sampler':0,'canvasSize':(w,h),'sourceRect':(200,100,560,300),'target':(480,522),'screenRect':(0,0,w,h),'panelRect':(30,505,900,34),'binScale':1}.items():
 if k in p:p[k].value=val
out=root/'validation';frames=[]
for mode,phases in [(0,[.2,.5,.8]),(1,[.09,.27,.43,.67,.83,.94])]:
 for phase in phases:
  p['mode'].value=mode;p['phase'].value=phase;f.clear();v.render(moderngl.TRIANGLE_STRIP)
  im=Image.frombytes('RGBA',(w,h),f.read(components=4)).transpose(Image.Transpose.FLIP_TOP_BOTTOM);bg=Image.new('RGBA',(w,h),'#070b12');bg.alpha_composite(im);frames.append(bg.convert('RGB'))
sheet=Image.new('RGB',(w*3,h*3))
for i,im in enumerate(frames):sheet.paste(im,((i%3)*w,(i//3)*h))
sheet.save(out/'orbit-synthetic.png');print('Compiled shader and rendered synthetic phases')
