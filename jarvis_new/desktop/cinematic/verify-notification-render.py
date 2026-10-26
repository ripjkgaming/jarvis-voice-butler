"""Render the notification shader on EGL; verify endpoints and save a storyboard."""
from pathlib import Path
import struct
import moderngl
from PIL import Image, ImageDraw, ImageChops
root=Path(__file__).resolve().parent
shader=(root/'kwin/effects/jarvis-surfaces/contents/shaders/notification.frag').read_text()
shader=shader.replace('#include "colormanagement.glsl"','')
shader=shader[:shader.index('    color=encodingToNits')]+'fragColor=color;}'
ctx=moderngl.create_standalone_context(backend='egl')
p=ctx.program(vertex_shader='#version 140\nin vec2 position;out vec2 texcoord0;void main(){gl_Position=vec4(position,0,1);texcoord0=(position+1)*0.5;}',fragment_shader=shader)
vao=ctx.simple_vertex_array(p,ctx.buffer(struct.pack('8f',-1,-1,1,-1,-1,1,1,1)),'position')
w,h=420,160
source=Image.new('RGBA',(w,h))
d=ImageDraw.Draw(source)
d.rounded_rectangle((12,12,408,148),radius=12,fill='#102637',outline='#3c7189')
d.text((32,34),'J A R V I S   /   SYSTEM ONLINE',fill='#7fd7ff')
d.text((32,70),'Notification effects upgraded',fill='#f0f8ff')
d.text((32,102),'Energy scan / illuminated edges',fill='#94b5c9')
texture=ctx.texture((w,h),4,source.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes())
texture.filter=(moderngl.NEAREST,moderngl.NEAREST);texture.use()
p['sampler'].value=0;p['canvasSize'].value=(w,h);p['sourceRect'].value=(12,12,396,136)
p['launchSize'].value=(1920,1080);p['clickOrigin'].value=(0.35,0.45)
fbo=ctx.simple_framebuffer((w,h),components=4);fbo.use()
def render(closing,phase):
    p['closing'].value=closing;p['phase'].value=phase;fbo.clear();vao.render(moderngl.TRIANGLE_STRIP)
    return Image.frombytes('RGBA',(w,h),fbo.read(components=4)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
for sign in (-1,1):
    p['edgeSign'].value=sign
    for closing,phase in ((0,1),(1,0),(2,0)):
        assert ImageChops.difference(render(closing,phase),source).getbbox(alpha_only=False) is None, 'source endpoint changed'
    for closing,phase in ((0,0),(1,1),(2,1)):
        assert render(closing,phase).getchannel('A').getbbox() is None
# The card must still cover its interior after reaching full-screen geometry,
# then reveal from the click while the far corner retains the glass layer.
assert render(2,.60).getpixel((150,73))[3] >= 150, 'Full-screen glass disappeared early'
revealing=render(2,.85)
assert revealing.getpixel((150,73))[3] == 0, 'Click origin did not reveal destination'
assert revealing.getpixel((395,135))[3] > 100, 'Reveal lost its outward progression'
sheet=Image.new('RGB',(w*4,(h+24)*3),'#050e18');d=ImageDraw.Draw(sheet)
for row in range(3):
    for col,phase in enumerate((.15,.35,.6,.85)):
        im=render(row,phase)
        assert im.getchannel('A').getbbox() is not None
        bg=Image.new('RGBA',(w,h),'#050e18');bg.alpha_composite(im)
        sheet.paste(bg.convert('RGB'),(col*w,row*(h+24)+24))
        d.text((col*w+12,row*(h+24)+6),('ARRIVAL','DEPARTURE','CLICK')[row]+f' {phase:.0%}',fill='#9cdfff')
path=root/'validation/notification-click-storyboard.png';sheet.save(path)
print('PASS: GPU shader compiled; both edge directions preserve exact source/transparent endpoints; intermediate frames render.')
print(path)
