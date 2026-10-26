#!/usr/bin/env python3
"""Native Xcursor artwork. Animation never changes geometry or the hotspot."""
import argparse
import math
from pathlib import Path
import struct

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath
from PyQt6.QtGui import QPen, QFont, QGuiApplication

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent / 'icons/jarvis-prism-cursors'
SIZES = (24, 32, 40, 48, 64, 96)
FRAMES = 32
DELAY = 60
ANIMATED = {'left_ptr', 'pointer', 'wait', 'progress', 'help'}
ALIASES = {
    'left_ptr': ['default', 'arrow', 'top_left_arrow'],
    'pointer': ['hand1', 'hand2', 'pointing_hand', '9d800788f1b08800ae810202380a0822'],
    'text': ['xterm', 'ibeam'],
    'vertical-text': ['vertical_text'],
    'crosshair': ['cross', 'tcross'],
    'wait': ['watch'],
    'progress': ['left_ptr_watch', '00000000000000020006000e7e9ffc3f'],
    'help': ['question_arrow', 'whats_this', 'left_ptr_help', '5c6cd98b3f3ebcb1f9c7f1c204630408'],
    'move': ['fleur', 'all-scroll', 'size_all', '4498f0e0c1937ffe01fd06f973665830', '9081237383d90e509aa00f00170e968f'],
    'grab': ['openhand', 'open_hand'],
    'grabbing': ['closedhand', 'closed_hand'],
    'ew-resize': ['size_hor', 'h_double_arrow', 'sb_h_double_arrow', 'e-resize', 'w-resize', 'left_side', 'right_side'],
    'ns-resize': ['size_ver', 'v_double_arrow', 'sb_v_double_arrow', 'n-resize', 's-resize', 'top_side', 'bottom_side'],
    'nesw-resize': ['size_bdiag', 'fd_double_arrow', 'ne-resize', 'sw-resize', 'top_right_corner', 'bottom_left_corner'],
    'nwse-resize': ['size_fdiag', 'bd_double_arrow', 'nw-resize', 'se-resize', 'top_left_corner', 'bottom_right_corner'],
    'col-resize': ['split_h', 'split-h'],
    'row-resize': ['split_v', 'split-v'],
    'not-allowed': ['forbidden', 'crossed_circle', 'no-drop', '03b6e0fcb3499374a867c041f52298f0'],
    'copy': ['dnd-copy', '1081e37283d90000800003c07f3ef6bf', '6407b0e94181790501fd1e167b474872'],
    'alias': ['dnd-link', '3085a0e285430894940527032f8b26df', 'a2a266d0498c3104214a47bd64ab0fc8'],
    'zoom-in': [],
    'zoom-out': [],
}


def hotspot(kind, size):
    xy = (3, 2) if kind in {'left_ptr', 'progress', 'help', 'copy', 'alias'} else (13, 4) if kind == 'pointer' else (16, 16)
    return tuple(round(v * size / 32) for v in xy)


def render(kind, size, frame=0):
    image = QImage(size * 4, size * 4, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size * 4 / 32, size * 4 / 32)
    phase = frame / FRAMES
    pulse = .5 - .5 * math.cos(phase * math.tau)
    ink = '#04101f'
    ice = '#e8fbff'
    blue = '#53caff'

    def pen(color, width):
        p.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))

    def line(x1, y1, x2, y2):
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def path(points):
        shape = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            shape.lineTo(*point)
        shape.closeSubpath()
        return shape

    def glass(points):
        shape = path(points)
        grad = QLinearGradient(3, 2, 24, 29)
        for stop, col in [(0, '#f3fdff'), (.32, '#b7efff'), (.55, '#329df0'), (1, '#143d70')]:
            grad.setColorAt(stop, QColor(col))
        p.setBrush(grad)
        pen(ink, 2.5)
        p.drawPath(shape)
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen(ice, .65)
        p.drawPath(shape)
        return shape

    if kind in {'left_ptr', 'progress', 'help', 'copy', 'alias'}:
        shape = glass([(3, 2), (3, 25), (9, 19), (13.5, 28), (17, 26.3), (12.6, 17.8), (22, 17.8)])
        p.save()
        p.setClipPath(shape)
        # Fixed dark facet and fine circuit rail; illumination fades in/out at the loop seam.
        p.fillPath(path([(5, 7), (5, 20), (9, 16), (17, 16)]), QColor('#153c63'))
        pen('#50c6ef', .7)
        line(5, 8, 5, 19)
        line(5, 19, 9, 15.5)
        if kind in ANIMATED:
            sheen_y = 7 + phase * 18
            sheen = QLinearGradient(0, sheen_y - 3, 0, sheen_y + 3)
            sheen.setColorAt(0, QColor(151, 236, 255, 0))
            sheen.setColorAt(.5, QColor(151, 236, 255, round(105 * math.sin(math.pi * phase) ** 2)))
            sheen.setColorAt(1, QColor(151, 236, 255, 0))
            p.fillRect(QRectF(3, sheen_y - 3, 20, 6), sheen)
            pen(QColor(204, 250, 255, round(220 * math.sin(math.pi * phase) ** 2)), 1.2)
            y = 8 + phase * 10
            line(5, y, 5, min(20, y + 3))
        pen('#d9fbff', .75)
        line(10.5, 18.8, 14.4, 26)
        p.restore()
    elif kind in {'pointer', 'grab', 'grabbing'}:
        if kind == 'pointer':
            points = [(11, 16), (11, 6), (13, 4), (15, 6), (15, 13), (18, 12), (21, 14), (24, 14), (27, 17), (25, 27), (12, 27), (6, 19), (6, 17), (8, 16), (11, 19)]
        elif kind == 'grab':
            points = [(9, 17), (7, 10), (9, 8), (12, 15), (11, 6), (14, 5), (16, 14), (17, 6), (20, 7), (20, 15), (23, 10), (26, 12), (24, 23), (21, 27), (12, 27), (5, 20), (5, 17), (7, 16)]
        else:
            points = [(7, 14), (9, 11), (12, 12), (14, 10), (17, 11), (20, 11), (23, 13), (25, 15), (24, 24), (21, 27), (11, 27), (6, 20), (6, 17), (9, 16), (13, 19)]
        glass(points)
        pen(ink, 1)
        line(16, 16, 16, 20)
        line(20, 16, 20, 20)
        pen(QColor(191, 246, 255, round(160 + 80 * pulse)), 1)
        line(13, 24, 22, 24)
    elif kind in {'text', 'vertical-text'}:
        p.translate(16, 16)
        if kind == 'vertical-text':
            p.rotate(90)
        for col, width in [(ink, 3.5), (ice, 1.35)]:
            pen(col, width)
            line(0, -11, 0, 11)
            line(-4, -11, 4, -11)
            line(-4, 11, 4, 11)
        pen(blue, 1.35)
        line(-3, -11, 3, -11)
        line(-3, 11, 3, 11)
    elif kind in {'crosshair', 'move', 'ew-resize', 'ns-resize', 'nesw-resize', 'nwse-resize', 'col-resize', 'row-resize'}:
        p.translate(16, 16)
        p.rotate({'ns-resize': 90, 'nesw-resize': -45, 'nwse-resize': 45, 'row-resize': 90}.get(kind, 0))
        for col, width in [(ink, 3.8), (ice, 1.4)]:
            pen(col, width)
            if kind == 'crosshair':
                for a, b, c, d in [(-11, 0, -3, 0), (3, 0, 11, 0), (0, -11, 0, -3), (0, 3, 0, 11)]:
                    line(a, b, c, d)
                # Transparent center leaves the exact target visible.
            else:
                line(-11, 0, 11, 0)
                for sign in (-1, 1):
                    line(sign * 11, 0, sign * 7, -4)
                    line(sign * 11, 0, sign * 7, 4)
                if kind == 'move':
                    line(0, -11, 0, 11)
                    for sign in (-1, 1):
                        line(0, sign * 11, -4, sign * 7)
                        line(0, sign * 11, 4, sign * 7)
                if kind in {'col-resize', 'row-resize'}:
                    line(-2, -7, -2, 7)
                    line(2, -7, 2, 7)
        if kind != 'crosshair':
            pen(blue, 1.4)
            line(-2, 0, 2, 0)
    elif kind == 'not-allowed':
        p.setBrush(QColor('#172131'))
        pen(ink, 4)
        p.drawEllipse(QRectF(5, 5, 22, 22))
        pen('#ff9b8d', 2)
        p.drawEllipse(QRectF(5, 5, 22, 22))
        line(8, 8, 24, 24)
    elif kind in {'zoom-in', 'zoom-out'}:
        p.setBrush(Qt.BrushStyle.NoBrush)
        for col, width in [(ink, 4), (ice, 1.6)]:
            pen(col, width)
            p.drawEllipse(QRectF(7, 7, 18, 18))
            line(23, 23, 28, 28)
            line(12, 16, 20, 16)
            if kind == 'zoom-in':
                line(16, 12, 16, 20)
    if kind in {'wait', 'progress'}:
        x, y = (23, 4) if kind == 'progress' else (10, 6)
        for i in range(5):
            energy = (.5 + .5 * math.cos(math.tau * (phase - i / 5))) ** 4
            pen(ink, 3.7)
            line(x, y + i * 4, x + 5, y + i * 4)
            pen(QColor(80 + round(150 * energy), 160 + round(90 * energy), 255), 1.6)
            line(x, y + i * 4, x + 5, y + i * 4)
        if kind == 'wait':
            pen('#6bcfff', 1)
            line(20, 6, 22, 6)
            line(22, 6, 22, 10)
            line(8, 18, 8, 22)
    if kind in {'help', 'copy', 'alias'}:
        p.setBrush(QColor('#092039'))
        pen(ice, .8)
        p.drawRoundedRect(QRectF(20, 19, 11, 11), 2, 2)
        pen('#a1efff', 1.3)
        if kind == 'copy':
            line(22.5, 24.5, 28.5, 24.5)
            line(25.5, 21.5, 25.5, 27.5)
        elif kind == 'alias':
            line(22.5, 27, 28, 22)
            line(24, 22, 28, 22)
            line(28, 22, 28, 26)
        else:
            q = QPainterPath(QPointF(23, 22.5))
            q.cubicTo(23, 19.5, 29.5, 20.5, 27, 24)
            q.lineTo(25.5, 25.3)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(q)
            p.drawPoint(QPointF(25.5, 28))
    p.end()
    return image.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def build(kind, static=False):
    chunks = []
    count = FRAMES if kind in ANIMATED and not static else 1
    for size in SIZES:
        for frame in range(count):
            im = render(kind, size, frame)
            header = struct.pack('<9I', 36, 0xfffd0002, size, 1, size, size, *hotspot(kind, size), DELAY)
            chunks.append((size, header + im.bits().asstring(im.sizeInBytes())))
    offset = 16 + 12 * len(chunks)
    toc = []
    for size, chunk in chunks:
        toc.append(struct.pack('<3I', 0xfffd0002, size, offset))
        offset += len(chunk)
    return struct.pack('<4I', 0x72756358, 16, 0x10000, len(chunks)) + b''.join(toc) + b''.join(c for _, c in chunks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--static', action='store_true', help='Build without animation for reduced motion')
    args = parser.parse_args()
    app = QGuiApplication([])
    (OUT / 'cursors').mkdir(parents=True, exist_ok=True)
    (OUT / 'index.theme').write_text('[Icon Theme]\nName=Jarvis Prism\nComment=Precision glass cursors with contained cyan light effects\nInherits=breeze_cursors\n')
    for kind, aliases in ALIASES.items():
        (OUT / 'cursors' / kind).write_bytes(build(kind, args.static))
        for name in aliases:
            target = OUT / 'cursors' / name
            target.unlink(missing_ok=True)
            target.symlink_to(kind)
    sheet = QImage(960, 560, QImage.Format.Format_ARGB32)
    sheet.fill(QColor('#09121f'))
    p = QPainter(sheet)
    p.setPen(QColor('#e4f7ff'))
    p.setFont(QFont('sans-serif', 22))
    p.drawText(32, 43, 'JARVIS / PRISM')
    p.setFont(QFont('sans-serif', 10))
    p.setPen(QColor('#89aec5'))
    p.drawText(33, 68, 'NATIVE PRECISION  /  CYAN LIGHT SWEEP  /  FIXED CLICK POINTS')
    for index, kind in enumerate(ALIASES):
        x, y = 32 + (index % 6) * 154, 90 + (index // 6) * 106
        p.fillRect(x, y, 138, 66, QColor('#192b40'))
        p.fillRect(x + 69, y, 69, 66, QColor('#edf3f7'))
        p.drawImage(x + 14, y + 8, render(kind, 48, 9))
        p.drawImage(x + 83, y + 8, render(kind, 48, 9))
        p.setPen(QColor('#b6d0df'))
        p.drawText(x, y + 85, kind)
    p.setPen(QColor('#89aec5'))
    p.drawText(33, 545, '24 / 32 / 40 / 48 / 64 / 96 px   •   Stable text, resize and precision tools   •   Preview enlarged')
    p.end()
    sheet.save(str(ROOT / 'preview.png'))
    print(f'Built {len(ALIASES)} roles in {len(SIZES)} sizes; {"static" if args.static else "32-frame light effects"}.')


if __name__ == '__main__':
    main()
