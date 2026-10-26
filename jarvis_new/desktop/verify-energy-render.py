from pathlib import Path
exec(Path(__file__).with_name('render-energy-preview.py').read_text())
from PIL import ImageChops
for phase in [.141,.2,.3,.4,.519,.55,.60,.649,.681,.72,.80,.879,.93,.969,1.0]:
 p['mode'].value=1;p['phase'].value=phase;f.clear();v.render(moderngl.TRIANGLE_STRIP)
 alpha=Image.frombytes('RGBA',(w,h),f.read(components=4)).transpose(Image.Transpose.FLIP_TOP_BOTTOM).getchannel('A')
 forbidden=Image.new('L',(w,h),255);draw=ImageDraw.Draw(forbidden)
 if .68<=phase<.88:
  draw.rectangle((14,489,946,539),fill=0)
 else:
  draw.rectangle((0,0,w-1,h-1),fill=0);draw.rectangle((18,18,w-19,h-19),fill=255)
 if phase==1:assert alpha.getbbox() is None
 else:assert ImageChops.multiply(alpha,forbidden).getbbox() is None,phase
print('PASS: GPU-rendered filaments remain on monitor edges / panel perimeter, no interior orbit; final frame transparent.')
