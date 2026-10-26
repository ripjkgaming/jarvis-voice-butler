"""GPU checks for the resize trace (mode 11), fullscreen deploy (mode 12) and the
desktop-space drag field (mode 4). Synthetic shader renders only, no desktop.

Set JARVIS_RENDER_SHEET=1 to also write validation/resize-fullscreen-storyboard.png.
"""
import os
import struct
from pathlib import Path

import moderngl
from PIL import Image, ImageChops, ImageDraw

root = Path(__file__).resolve().parent
ctx = moderngl.create_standalone_context(backend='egl')
s = (root / 'kwin/effects/jarvis-energy/contents/shaders/energy.frag').read_text().replace('#include "colormanagement.glsl"', '')
s = s[:s.index('    color=encodingToNits')] + 'fragColor=color;}'
p = ctx.program(vertex_shader='#version 140\nin vec2 position;out vec2 texcoord0;void main(){gl_Position=vec4(position,0,1);texcoord0=(position+1)*0.5;}', fragment_shader=s)
vao = ctx.simple_vertex_array(p, ctx.buffer(struct.pack('8f', -1, -1, 1, -1, -1, 1, 1, 1)), 'position')

src = Image.new('RGBA', (360, 200), '#132b3a')
d = ImageDraw.Draw(src)
d.rectangle((0, 0, 359, 29), fill='#214456')
for y in range(50, 175, 24):
    d.line((22, y, 320, y), fill='#8bb7c9', width=2)
tex = ctx.texture(src.size, 4, src.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes())
tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
tex.use()


def setu(name, value):
    if name in p:
        p[name].value = value


def render(size, **u):
    fb = ctx.simple_framebuffer(size, components=4)
    fb.use()
    for k, v in {'sampler': 0, 'commanded': 0, 'cine': 1, 'variant': 0, 'dragHeld': 0, 'dragFade': 1,
                 'canvasSize': size, 'desktopOrigin': (0, 0), 'edges': (0, 0, 0, 0), 'velocity': (0, 0),
                 'velocityPhase': 0, 'gravityPhase': 0, 'gravityPeriod': 4200, **u}.items():
        setu(k, v)
    fb.clear()
    vao.render(moderngl.TRIANGLE_STRIP)
    return Image.frombytes('RGBA', size, fb.read(components=4)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)


W, H = 640, 360
win = (140, 70, 360, 200)
expected = Image.new('RGBA', (W, H))
expected.paste(src, (140, 70))
base = dict(sourceRect=win, frameRect=win, target=(560, 336), screenRect=(0, 0, W, H))


def alpha_sum(img, box):
    return sum(img.crop(box).getchannel('A').getdata())


def accent(img, box):
    """Blue-white overlay energy: blue minus the source's blue in the box."""
    a, b = img.crop(box), expected.crop(box)
    return sum(max(0, x[2] - y[2]) for x, y in zip(a.get_flattened_data() if hasattr(a, 'get_flattened_data') else a.getdata(), b.get_flattened_data() if hasattr(b, 'get_flattened_data') else b.getdata()))


# ---- Resize trace, mode 11 -------------------------------------------------
# Released (dragFade 0, held clock at an integer): exactly the source.
for ph in (2.0, 3.0):
    img = render((W, H), mode=11, phase=ph, dragHeld=1, dragFade=0, edges=(0, 0, 1, 1), **base)
    assert ImageChops.difference(img, expected).getbbox() is None, ('resize release must be the exact source', ph)
# Held, dragging the right and bottom edges: the right edge runs hot, the
# pinned left edge only keeps its bracket corners.
img = render((W, H), mode=11, phase=1.37, dragHeld=1, dragFade=1, edges=(0, 0, 1, 1), variant=1, **base)
right = accent(img, (494, 120, 506, 220))
left = accent(img, (134, 120, 146, 220))
assert right > 4 * max(left, 1), ('dragged edge must dominate the pinned edge', right, left)
# The trace hugs the frame and never touches the interior content in the middle.
# Content away from the dragged edges is untouched (the window spans 140..500 x 70..270).
assert accent(img, (160, 82, 300, 112)) == 0, 'resize must keep the content crisp'
# Different look from the drag field: no particles away from the frame.
assert alpha_sum(img, (10, 10, 120, 60)) == 0, 'resize must not paint drag particles'
# Edge choice follows the uniform: dragging the left edge flips the balance.
img2 = render((W, H), mode=11, phase=1.37, dragHeld=1, dragFade=1, edges=(1, 0, 0, 0), variant=-1, **base)
assert accent(img2, (134, 120, 146, 220)) > 4 * max(accent(img2, (494, 120, 506, 220)), 1)
print('PASS: resize trace follows the dragged edges, keeps content crisp and releases to the exact source.')

# ---- Fullscreen deploy, mode 12 --------------------------------------------
for var in (1, -1):
    for ph in (0.0, 1.0):
        img = render((W, H), mode=12, phase=ph, variant=var, **base)
        assert ImageChops.difference(img, expected).getbbox() is None, ('fullscreen endpoints must be exact', var, ph)
    mid = render((W, H), mode=12, phase=.45, variant=var, **base)
    assert ImageChops.difference(mid, expected).getbbox() is not None, ('fullscreen must animate', var)
    # Never paints outside the window's own canvas region beyond the glow.
    assert alpha_sum(mid, (0, 0, 100, 40)) == 0
print('PASS: fullscreen deploy animates both directions and starts/ends on the exact source.')

# ---- Drag field, mode 4: desktop-space continuity ----------------------------
# Two canvases over the same desktop area (as before/after a monitor crossing
# grows the canvas) must draw the same motes where they overlap.
common = dict(mode=4, phase=.37, dragHeld=1, dragFade=1, gravityPhase=0.0)
A = render((W, H), desktopOrigin=(1000, 0), sourceRect=win, frameRect=win, target=(0, 0), screenRect=(0, 0, W, H),
           gravityFrom=(320, 180), gravityTo=(320, 180), **common)
shift = 160
winB = (win[0] + shift, win[1], win[2], win[3])
B = render((W + shift, H), desktopOrigin=(1000 - shift, 0), sourceRect=winB, frameRect=winB, target=(0, 0),
           screenRect=(0, 0, W + shift, H), gravityFrom=(320 + shift, 180), gravityTo=(320 + shift, 180), **common)
cropA = A.crop((40, 40, W - 40, H - 40))
cropB = B.crop((40 + shift, 40, W + shift - 40, H - 40))
diff = ImageChops.difference(cropA, cropB)
worst = max(max(px) for px in diff.getdata())
assert worst <= 2, ('drag field must be continuous across canvas changes', worst)
assert alpha_sum(cropA, (0, 0, cropA.width, cropA.height)) > 0, 'drag field must draw motes'
print('PASS: drag particles live in desktop space and stay identical when the canvas spans another monitor.')

if os.environ.get('JARVIS_RENDER_SHEET'):
    rows = [('RESIZE  right+bottom', [render((W, H), mode=11, phase=x, dragHeld=1, dragFade=1, edges=(0, 0, 1, 1), variant=1, **base) for x in (.2, .9, 1.6)]
             + [render((W, H), mode=11, phase=2.0, dragHeld=1, dragFade=.5, edges=(0, 0, 1, 1), **base)]),
            ('FULLSCREEN  enter', [render((W, H), mode=12, phase=x, variant=1, **base) for x in (.15, .35, .6, .85)]),
            ('FULLSCREEN  exit', [render((W, H), mode=12, phase=x, variant=-1, **base) for x in (.15, .35, .6, .85)])]
    sheet = Image.new('RGB', (W * 4, (H + 26) * len(rows)), '#030c14')
    draw = ImageDraw.Draw(sheet)
    for r, (name, imgs) in enumerate(rows):
        draw.text((12, r * (H + 26) + 7), name, fill='#bdf4ff')
        for c, im in enumerate(imgs):
            bg = Image.new('RGBA', (W, H), '#06111d')
            bg.alpha_composite(im)
            sheet.paste(bg.convert('RGB'), (c * W, r * (H + 26) + 26))
    sheet.save(root / 'validation/resize-fullscreen-storyboard.png')
    print('Wrote validation/resize-fullscreen-storyboard.png')
