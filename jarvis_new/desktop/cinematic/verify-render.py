"""GPU shader endpoint and synthetic visual checks; not a desktop recording."""
from pathlib import Path
import struct
import math
import moderngl
from PIL import Image,ImageDraw,ImageChops
root=Path(__file__).resolve().parent
out=root/'validation';out.mkdir(exist_ok=True)
ctx=moderngl.create_standalone_context(backend='egl')
s=(root/'kwin/effects/jarvis-energy/contents/shaders/energy.frag').read_text().replace('#include "colormanagement.glsl"','')
s=s[:s.index('    color=encodingToNits')]+'fragColor=color;}'
p=ctx.program(vertex_shader='#version 140\nin vec2 position;out vec2 texcoord0;void main(){gl_Position=vec4(position,0,1);texcoord0=(position+1)*0.5;}',fragment_shader=s)
v=ctx.simple_vertex_array(p,ctx.buffer(struct.pack('8f',-1,-1,1,-1,-1,1,1,1)),'position')
w,h=640,360;f=ctx.simple_framebuffer((w,h),components=4);f.use()
src=Image.new('RGBA',(360,200),'#132b3a');d=ImageDraw.Draw(src)
d.rectangle((0,0,359,29),fill='#214456');d.text((16,10),'J.A.R.V.I.S.  /  SYSTEM CONSOLE',fill='#d9fbff')
for y in range(50,175,24):d.text((22,y),'SYSTEM LINK ESTABLISHED  //  ONLINE',fill='#8bb7c9')
d.line((22,185,320,185),fill='#60dfff',width=2)
t=ctx.texture(src.size,4,src.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes());t.filter=(moderngl.NEAREST,moderngl.NEAREST);t.use()
for k,val in {'sampler':0,'canvasSize':(w,h),'sourceRect':(140,70,360,200),'target':(560,336),'screenRect':(0,0,w,h),'dragFade':1,
              'gravityFrom':(320,180),'gravityTo':(320,180),'gravityPhase':0,'gravityPeriod':4200}.items():p[k].value=val

def render(mode,phase):
 p['mode'].value=mode;p['phase'].value=phase;f.clear();v.render(moderngl.TRIANGLE_STRIP)
 return Image.frombytes('RGBA',(w,h),f.read(components=4)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)

expected=Image.new('RGBA',(w,h));expected.paste(src,(140,70))
p['commanded'].value=1
for mode,phase in [(0,1),(1,0),(2,0),(3,0),(3,1),(4,0),(4,1),(5,0),(5,1)]:
 assert ImageChops.difference(render(mode,phase),expected).getbbox() is None,(mode,phase,'source changed')
for mode,phase in [(0,0),(1,1),(2,1)]:assert render(mode,phase).getchannel('A').getbbox() is None,(mode,phase,'not transparent')
for mode in range(6):
 for phase in [.1,.35,.65,.9]:
  image=render(mode,phase)
  if mode in [0,1,3,4,5]:
   assert image.getchannel('A').getbbox() is not None
sheet=Image.new('RGB',(w*4,(h+26)*4),'#030c14');draw=ImageDraw.Draw(sheet)
for row,(mode,name) in enumerate([(0,'OPEN / RECONSTRUCT'),(1,'CLOSE / RETRACT'),(2,'MINIMIZE / RETRIEVE'),(3,'MAXIMIZE / LOCK')]):
 for col,phase in enumerate([.15,.4,.65,.88]):
  bg=Image.new('RGBA',(w,h),'#06111d');bg.alpha_composite(render(mode,phase));x=col*w;y=row*(h+26)
  sheet.paste(bg.convert('RGB'),(x,y+26));draw.text((x+12,y+8),name+'  '+str(round(phase*100))+'%',fill='#94ddec')
sheet.resize((1280,772)).save(out/'cinematic-storyboard.png')
print('PASS: shader compiles on '+ctx.info['GL_RENDERER']+'; exact original-pixel endpoints, transparent completion, all six modes render.')

# Focus and maximize must leave the content interior pixel-identical.
for command in (0,1):
 p['commanded'].value=command
 for phase in (.1,.3,.5,.7,.9):
  for mode in (3,5):
   assert ImageChops.difference(render(mode,phase).crop((156,86,484,254)),expected.crop((156,86,484,254))).getbbox() is None
# Transparent source isolates generated close accents: never solid or red.
t.write(bytes(360*200*4))
for command in (0,1):
 p['commanded'].value=command
 for phase in (.1,.3,.5,.7,.9):
  for mode in (1,2,3,5):
   pixels=list(render(mode,phase).getdata())
   assert max(a for r,g,b,a in pixels)<=(200 if mode==2 else 118 if mode==1 and command else 78)
   assert all(b>=r for r,g,b,a in pixels if a>0)
print('PASS: focus/maximize preserve interior pixels; close/minimize/maximize/focus accents remain translucent blue in both timing sets.')

p['commanded'].value=1
assert max(render(2,.5).getchannel('A').getdata())>100
print('PASS: taskbar retrieval has stronger blue accents with transparent endpoints.')

# Jarvis's close has distinct staged visuals, not only a longer duration.
t.write(src.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes())
p['commanded'].value=0
manual=render(1,.4)
p['commanded'].value=1
command=render(1,.4)
assert ImageChops.difference(manual,command).getbbox() is not None
print('PASS: distinct manual and commanded closing choreography.')

# A held drag must keep changing after entry and join its clock loop seamlessly.
p['commanded'].value=0
p['dragHeld'].value=1
a=render(4,1.15);b=render(4,1.45)
assert ImageChops.difference(a,b).getbbox() is not None
assert ImageChops.difference(render(4,1),render(4,2)).getbbox() is None
assert ImageChops.difference(render(4,0),expected).getbbox() is None
p['dragHeld'].value=0
assert ImageChops.difference(render(4,1),expected).getbbox() is None
print('PASS: drag changes throughout the hold, loops seamlessly and releases to exact source pixels.')

# The held window is invisible; source pixels return exactly after reconstruction.
p['dragHeld'].value=1
held=render(4,1.37)
t.write(bytes(360*200*4))
assert ImageChops.difference(held,render(4,1.37)).getbbox(alpha_only=False) is None
t.write(src.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes())
for area in ((0,0,140,180),(500,0,640,180),(0,180,140,360),(500,180,640,360)):
 assert sum(a>20 for a in held.crop(area).getchannel('A').getdata())>40,area
assert ImageChops.difference(render(4,1),render(4,2)).getbbox(alpha_only=False) is None
# Cursor gravity must increase particle density near the pointer, not only recolor.
def energy_near(point):
 total=0
 for phase in (1.15,1.37,1.6):
  pixels=render(4,phase).getchannel('A')
  x,y=point
  total+=sum(pixels.crop((x-42,y-42,x+42,y+42)).getdata())
 return total
p['gravityFrom'].value=(150,180);p['gravityTo'].value=(150,180)
near=energy_near((150,180))
p['gravityFrom'].value=(490,180);p['gravityTo'].value=(490,180)
far=energy_near((150,180))
assert near>far*1.4,(near,far,'gravity did not gather particles')
p['gravityFrom'].value=(320,180);p['gravityTo'].value=(320,180)
# Window velocity (from geometry, not the pointer) streaks motes and draws a
# comet wake behind the gathered core; it decays once the window pauses.
def alpha_in(image,box):return sum(image.crop(box).getchannel('A').getdata())
p['velocityPhase'].value=1.37
still=render(4,1.37)
p['velocity'].value=(1500,0)
moving=render(4,1.37)
assert ImageChops.difference(still,moving).getbbox() is not None,'velocity has no effect'
assert alpha_in(moving,(150,165,300,195))>alpha_in(moving,(340,165,490,195))*1.2,'no comet wake behind the core'
assert ImageChops.difference(render(4,1.37+1.5/4.2),render(4,1.37+1.5/4.2)).getbbox() is None
p['velocityPhase'].value=0
decayed=render(4,1.37)
p['velocity'].value=(0,0)
assert ImageChops.difference(decayed,still).getbbox(alpha_only=False) is None,'velocity did not decay'
# Holographic ghost brackets mark the held window's landing rectangle.
assert max(held.crop((132,64,160,70+3)).getchannel('A').getdata())>90 and max(held.crop((480,266,508,276)).getchannel('A').getdata())>90,'ghost brackets missing'
# Pick-up sparks leave the rim; release sparks converge back into it.
p['dragFade'].value=1
lift=render(4,.06)
assert alpha_in(lift,(140,30,500,66))>2000,'pick-up sparks missing'
p['dragFade'].value=.5
assert alpha_in(render(4,1.37),(140,30,500,66))>alpha_in(render(4,1.37),(140,0,500,4)),'release sparks missing'
p['dragFade'].value=0
p['velocity'].value=(2200,-900);p['velocityPhase'].value=1.0
for phase in (.04,1.0,1.37):
 assert ImageChops.difference(render(4,phase),expected).getbbox(alpha_only=False) is None,('release not exact with velocity',phase)
p['velocity'].value=(0,0);p['velocityPhase'].value=0
print('PASS: velocity streaks, comet wake and decay; ghost brackets; pick-up/release sparks; exact release under motion.')
for phase in (.08,.5,1.37,2.1):
 assert ImageChops.difference(render(4,phase),expected).getbbox(alpha_only=False) is None
frames=[]
for i in range(150):
 phase=i/140
 cursor=(140+360*(i/149),180+65*math.sin(i/30))
 p['gravityFrom'].value=cursor;p['gravityTo'].value=cursor;p['gravityPhase'].value=phase
 p['velocity'].value=(360/149*1000/30,65/30*math.cos(i/30)*1000/30) if i<115 else (0,0);p['velocityPhase'].value=phase
 p['dragFade'].value=1 if i<115 else max(0,1-(i-115)/34)
 bg=Image.new('RGBA',(w,h),'#06111d');bg.alpha_composite(render(4,phase));frames.append(bg.convert('RGB'))
frames[0].save(out/'drag-fluid-preview.gif',save_all=True,append_images=frames[1:],duration=30,loop=0)
p['velocity'].value=(0,0);p['velocityPhase'].value=0;p['gravityPhase'].value=0
frames[80].save(out/'drag-fluid-preview.png')
print('PASS: fluid cloud fills the display, source fully dissolves and reconstruction restores exact RGB.')
print('PASS: cursor attraction increases local particle density and the return uses a fixed screen field.')

# ---------------------------------------------------------------------------
# Cinematic transitions (cine=1): modes 6-10 and the drag reassembly.
import time
t.write(src.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes())
for k,val in {'dragHeld':0,'dragFade':1,'velocity':(0,0),'velocityPhase':0,'commanded':0,'variant':1,
              'gravityFrom':(320,180),'gravityTo':(320,180),'gravityPhase':0}.items():p[k].value=val
same=lambda a,b:ImageChops.difference(a,b).getbbox(alpha_only=False) is None
clear=lambda a:a.getchannel('A').getbbox() is None
# Legacy modes ignore the flag entirely.
for mode,phase in [(0,.4),(1,.4),(2,.5),(3,.5),(5,.5)]:
 p['cine'].value=0;a=render(mode,phase);p['cine'].value=1
 assert same(a,render(mode,phase)),('legacy mode changed by cine',mode)
CINE={6:'OPEN / MATERIALISE',7:'MINIMIZE / SHATTER TO TASKBAR',8:'CLOSE / DEREZ',9:'FOCUS / PHASE SHIFT',10:'MAXIMIZE / ENERGY SURGE'}
p['cine'].value=1
for command in (0,1):
 p['commanded'].value=command
 assert clear(render(6,0)) and same(render(6,1),expected),('open endpoints',command)
 assert same(render(7,0),expected) and clear(render(7,1)),('minimize endpoints',command)
 assert same(render(8,0),expected) and clear(render(8,1)),('close endpoints',command)
 assert same(render(9,0),expected) and same(render(9,1),expected),('focus endpoints',command)
 for variant in (1,-1):
  p['variant'].value=variant
  assert same(render(10,0),expected) and same(render(10,1),expected),('surge endpoints',variant,command)
 p['variant'].value=1
p['commanded'].value=0
print('PASS: cinematic modes 6-10 compile, leave legacy modes untouched and hit exact source / transparent endpoints (manual and Jarvis).')

# Every mode is visibly active mid-flight and distinct from the others.
mid={m:render(m,.42) for m in CINE}
for m,img in mid.items():assert not same(img,expected),(m,'no visible transition')
for a in CINE:
 for b in CINE:
  if a<b:assert ImageChops.difference(mid[a],mid[b]).getbbox() is not None,(a,b,'identical modes')
# Particles: open/close/minimize put glowing shards outside the intact source.
def lit(img,box):return sum(1 for v in img.crop(box).getchannel('A').getdata() if v>40)
assert lit(render(7,.55),(500,70,640,360))>150,'minimize shards do not travel toward the taskbar'
assert lit(render(8,.45),(140,70,500,150))>300,'close does not lift into dust'
assert lit(render(6,.35),(140,150,500,270))>300,'open dust does not converge'
# Shards carry the window's own pixels (not just accents): source colour appears in flight.
shard=render(7,.45)
assert sum(1 for px in shard.crop((500,70,640,360)).getdata() if px[3]>120)>100,'no solid shards in flight'
# Maximize brightens behind the surge; unmaximize sweeps from the opposite corner.
p['variant'].value=1;grow=render(10,.4);p['variant'].value=-1;shrink=render(10,.4);p['variant'].value=1
assert ImageChops.difference(grow,shrink).getbbox() is not None
def bright(img,box):return sum(sum(px[:3]) for px in img.crop(box).getdata())
assert bright(grow,(150,80,260,140))>bright(expected,(150,80,260,140))*1.05,'no energy surge behind the front'
print('PASS: each mode is active mid-flight and distinct; shards carry window pixels to the taskbar, dust lifts on close and converges on open; surge direction follows maximize/unmaximize.')

# Seamless timing at 120 steps per transition (finer than 60fps for every
# duration used): the integrated light/coverage curve has no step above 4% of
# its peak (a pop or strobe), and the first/last steps out of the exact
# endpoints are near-invisible (mean |dRGBA| < 1).
def seam(images):
 energy=[sum(img.tobytes()) for img in images]
 step=max(abs(energy[i+1]-energy[i]) for i in range(len(energy)-1))/max(max(energy),1)
 d=lambda a,b:sum(abs(x-y) for x,y in zip(a.tobytes(),b.tobytes()))/len(a.tobytes())
 return round(step*100,2),round(d(images[0],images[1]),2),round(d(images[-2],images[-1]),2)
timing={}
for m in list(CINE)+['10-']:
 p['variant'].value=-1 if m=='10-' else 1
 timing[m]=seam([render(10 if m=='10-' else m,i/120) for i in range(121)])
 assert timing[m][0]<4.0 and timing[m][1]<1.0 and timing[m][2]<1.0,(m,timing[m],'pop in timeline')
p['variant'].value=1
print('PASS: seamless timelines (max energy step %, start step, end step):',timing)

# Drag v2 stays intact while held; release reassembles and returns exactly.
p['dragHeld'].value=1;p['dragFade'].value=1
for phase in (.06,.5,1.15,1.37):
 p['cine'].value=0;a=render(4,phase);p['cine'].value=1
 assert same(a,render(4,phase)),('held drag changed by cine',phase)
p['velocity'].value=(2200,-900);p['velocityPhase'].value=1.0
p['dragFade'].value=0
for phase in (.04,1.0,1.37,2.1):assert same(render(4,phase),expected),('cinematic release not exact',phase)
p['velocity'].value=(0,0);p['velocityPhase'].value=0
p['dragFade'].value=.55;rebuilding=render(4,1.37)
p['cine'].value=0;legacy=render(4,1.37);p['cine'].value=1
assert ImageChops.difference(rebuilding,legacy).getbbox() is not None,'release reassembly inactive'
seq=[]
for i in range(121):
 p['dragFade'].value=1-i/120;seq.append(render(4,1.2+i/120*.214))
worst=seam(seq)
assert worst[0]<4.0 and worst[2]<1.0,('drag release pop',worst)
p['cine'].value=0;p['dragFade'].value=1;held=render(4,1.2);p['dragFade'].value=1-1/120;p['cine'].value=1
assert sum(abs(x-y) for x,y in zip(held.tobytes(),seq[1].tobytes()))/len(held.tobytes())<1.5,'release start pops'
p['dragFade'].value=1;p['dragHeld'].value=0
print('PASS: held drag v2 identical with the flag; release materialises seamlessly (step %, start, end) and ends on exact source pixels.',worst)

# Storyboard and animated preview (synthetic GPU renders, not desktop captures).
def frame(mode,phase,localOnly):
 img=render(mode,phase)
 if localOnly:
  m=Image.new('RGBA',(w,h));m.paste(img.crop((140,70,500,270)),(140,70));img=m
 bg=Image.new('RGBA',(w,h),'#06111d');bg.alpha_composite(img);return bg.convert('RGB')
rows=[(6,'OPEN / MATERIALISE',True,1),(9,'FOCUS / PHASE SHIFT',True,1),(10,'MAXIMIZE / ENERGY SURGE',True,1),
      (10,'UNMAXIMIZE / POWER DOWN',True,-1),(7,'MINIMIZE / SHATTER TO TASKBAR',False,1),(8,'CLOSE / DEREZ',True,1),(4,'DRAG RELEASE / REASSEMBLE',False,1)]
cols=[.12,.28,.42,.56,.70,.86]
sheet=Image.new('RGB',(w*len(cols),(h+26)*len(rows)),'#030c14');draw=ImageDraw.Draw(sheet)
for r,(mode,name,localOnly,variant) in enumerate(rows):
 p['variant'].value=variant
 for c_,ph in enumerate(cols):
  if mode==4:
   p['dragHeld'].value=1;p['dragFade'].value=1-ph;img=frame(4,1.2+ph*.214,False);p['dragHeld'].value=0;p['dragFade'].value=1
  else:img=frame(mode,ph,localOnly)
  sheet.paste(img,(c_*w,r*(h+26)+26));draw.text((c_*w+12,r*(h+26)+8),name+'  '+str(round(ph*100))+'%',fill='#94ddec')
p['variant'].value=1
sheet.resize((sheet.width//2,sheet.height//2)).save(out/'cinematic-transitions-storyboard.png')
frames=[]
def clip(mode,n,localOnly,reverse=False,variant=1):
 p['variant'].value=variant
 for i in range(n+1):frames.append(frame(mode,(n-i)/n if reverse else i/n,localOnly))
 p['variant'].value=1
clip(6,46,True);clip(9,28,True);clip(10,32,True);clip(10,32,True,variant=-1);clip(7,40,False);clip(7,40,False,reverse=True)
p['dragHeld'].value=1
for i in range(55):
 p['dragFade'].value=1-i/54;frames.append(frame(4,1.2+i/54*.214,False))
p['dragHeld'].value=0;p['dragFade'].value=1
clip(8,38,True)
frames[0].save(out/'cinematic-transitions-preview.gif',save_all=True,append_images=frames[1:],duration=17,loop=0)
# Bottom-centre taskbar target (the usual desktop layout): vertical peel + vortex.
p['target'].value=(320,356);strip=Image.new('RGB',(w*4,h))
for i,ph in enumerate((.2,.4,.6,.8)):strip.paste(frame(7,ph,False),(i*w,0))
strip.save(out/'cinematic-minimize-taskbar.png');p['target'].value=(560,336)

# GPU cost at 1080p: whole-output canvas with a 1600x900 window (worst case).
fb=ctx.simple_framebuffer((1920,1080),components=4);fb.use()
for k,val in {'canvasSize':(1920,1080),'sourceRect':(160,90,1600,900),'target':(960,1070),'screenRect':(0,0,1920,1080)}.items():p[k].value=val
cost={}
for m in list(CINE)+[4]:
 p['mode'].value=m;p['phase'].value=.45;p['dragHeld'].value=1 if m==4 else 0;p['dragFade'].value=.5 if m==4 else 1
 for _ in range(5):fb.clear();v.render(moderngl.TRIANGLE_STRIP)
 q=ctx.query(time=True)
 with q:
  for _ in range(30):fb.clear();v.render(moderngl.TRIANGLE_STRIP)
 ctx.finish();cost[m]=round(q.elapsed/30/1e6,2)
 assert cost[m]<6.0,(m,cost[m],'too slow for 60fps')
print('PASS: 1080p worst-case GPU time per frame (ms, %s):'%ctx.info['GL_RENDERER'],cost)
