#!/usr/bin/env python3
"""Add STARK OS Plasma controls using the installed Plasma SVG contracts.

Run after build.py (which recreates the desktop theme). Never writes settings,
launches applications, or changes authentication. All geometry uses build.py's
original Claude design tokens and frame helpers.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from build import (
    CYAN, CYAN_HOT, DEEP, INK, RAISE, RED, SELECT, TEXT_DIM,
    Frame, Sheet, bar_meter_svg, hexc as C, hints, line, poly, rect,
)

HERE = Path(__file__).resolve().parent


def circle(cx, cy, r, fill="none", stroke=None, opacity=1.0):
    stroke_attrs = f' stroke="{stroke}" stroke-width="1"' if stroke else ""
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}"{stroke_attrs} opacity="{opacity}"/>'


def checkmarks():
    sh = Sheet()
    sh.part("checkbox", 16, 16, [
        poly([(3, 0), (16, 0), (16, 13), (13, 16), (0, 16), (0, 3)], C(SELECT)),
        '<path d="M 3.5 8 L 6.5 11 L 12.5 5" fill="none" stroke="' + C(CYAN_HOT) + '" stroke-width="2"/>',
    ])
    sh.part("radiobutton", 16, 16, [circle(8, 8, 7.5, C(SELECT), C(CYAN)), circle(8, 8, 3, C(CYAN_HOT))])
    return sh.svg()


def radiobutton():
    sh = Sheet()
    sh.hint("hint-size", 16, 16)
    for name, fill, stroke in (("normal", C(INK), C(TEXT_DIM)), ("checked", C(SELECT), C(CYAN))):
        sh.part(name, 16, 16, [circle(8, 8, 7.5, fill, stroke)])
    for name, opacity in (("hover", 0.65), ("focus", 1.0)):
        sh.part(name, 20, 20, [circle(10, 10, 9.5, "none", C(CYAN), opacity)])
    sh.part("symbol", 6, 6, [circle(3, 3, 3, C(CYAN_HOT))])
    sh.part("shadow", 18, 18, [circle(9, 10, 7.5, "#000000", opacity=0.25)])
    return sh.svg()


def switch():
    sh = Sheet()
    for name, fill, border in (("inactive", INK, TEXT_DIM), ("active", SELECT, CYAN)):
        # Full-height left/right/center are Plasma's three-piece switch track.
        sh.part(name + "-left", 4, 16, [poly([(4, 0), (4, 16), (0, 12), (0, 4)], C(fill)), line(.5, 4, 3.5, .5, C(border)), line(.5, 4, .5, 12, C(border)), line(.5, 12, 3.5, 15.5, C(border))])
        sh.part(name + "-center", 16, 16, [rect(0, 0, 16, 16, C(fill)), line(0, .5, 16, .5, C(border)), line(0, 15.5, 16, 15.5, C(border))])
        sh.part(name + "-right", 4, 16, [poly([(0, 0), (4, 4), (4, 12), (0, 16)], C(fill)), line(.5, .5, 3.5, 4, C(border)), line(3.5, 4, 3.5, 12, C(border)), line(3.5, 12, .5, 15.5, C(border))])
    for name, fill, border in (("handle", DEEP, TEXT_DIM), ("handle-active", DEEP, CYAN), ("handle-hover", RAISE, CYAN), ("handle-pressed", SELECT, CYAN_HOT)):
        sh.part(name, 22, 22, [
            f'<path d="M 5 .5 H 21.5 V 17 L 17 21.5 H .5 V 5 Z" fill="{C(fill)}" stroke="{C(border)}"/>',
            line(8, 8, 8, 14, C(CYAN_HOT), .8), line(13, 8, 13, 14, C(CYAN_HOT), .8),
        ])
    sh.part("handle-focus", 26, 26, [f'<path d="M .5 7 V .5 H 7 M 19 25.5 H 25.5 V 19" fill="none" stroke="{C(CYAN)}"/>'])
    sh.part("handle-shadow", 24, 24, [rect(1, 2, 22, 22, "#000000", .25)])
    sh.hint("hint-bar-size", 38, 16)
    sh.hint("hint-stretch-borders", 1, 1)
    return sh.svg()


def pager():
    sh = Sheet()
    for name, fill, border in (("normal", INK, .3), ("hover", RAISE, .65), ("active", SELECT, 1.0)):
        f = Frame(4, 4, 4, 4)
        f.fill(C(fill), 1, chamfer=3)
        f.border(C(CYAN), border, chamfer=3)
        if name == "active":
            f.bar("bottom", 2, C(CYAN), 1)
        f.emit(sh, name)
    sh.hint("hint-tile-center", 1, 1)
    return sh.svg()


def arrows():
    sh = Sheet()
    for name, points in {"down": "3 5 8 10 13 5", "up": "3 11 8 6 13 11", "left": "10 3 5 8 10 13", "right": "6 3 11 8 6 13"}.items():
        sh.part(name + "-arrow", 16, 16, [f'<polyline points="{points}" fill="none" stroke="{C(CYAN)}" stroke-width="1.5"/>'])
    return sh.svg()


def separators():
    sh = Sheet()
    sh.part("horizontal-line", 32, 1, [rect(0, 0, 32, 1, C(CYAN), .3)])
    sh.part("vertical-line", 1, 32, [rect(0, 0, 1, 32, C(CYAN), .3)])
    return sh.svg()


def toolbar():
    sh = Sheet()
    f = Frame(4, 4, 4, 4)
    f.fill(C(DEEP), 1)
    f.bar("bottom", 1, C(CYAN), .3)
    f.emit(sh, "")
    hints(sh, "", 4, 4, 4, 4)
    sh.hint("hint-tile-center", 1, 1)
    return sh.svg()


def calendar():
    sh = Sheet()
    sh.part("event", 16, 16, [poly([(16, 0), (16, 16), (0, 16)], C(CYAN))])
    return sh.svg()


def busy():
    sh = Sheet()
    # Rotation speed and reduced-motion behavior remain native Plasma controls.
    for name, size in (("busywidget", 32), ("22-22-busywidget", 22), ("16-16-busywidget", 16)):
        c, r = size / 2, size / 2 - 2
        sh.part(name, size, size, [
            circle(c, c, r, "none", C(CYAN), .2),
            f'<path d="M {c} 2 A {r} {r} 0 0 1 {c + r} {c}" fill="none" stroke="{C(CYAN_HOT)}" stroke-width="2"/>',
            f'<path d="M {c} {size - 2} A {r} {r} 0 0 1 2 {c}" fill="none" stroke="{C(CYAN)}" stroke-width="2"/>',
            circle(c, c, r * .55, "none", C(CYAN), .4),
        ])
    sh.hint("hint-rotation-angle", 30, 30)
    return sh.svg()


def actionbutton():
    sh = Sheet()
    for prefix, size in (("", 32), ("24-24-", 24), ("22-22-", 22), ("16-16-", 16)):
        for name, fill, stroke in (("normal", DEEP, TEXT_DIM), ("pressed", SELECT, CYAN_HOT), ("hover", RAISE, CYAN), ("focus", DEEP, CYAN)):
            c = size / 2
            sh.part(prefix + name, size, size, [circle(c, c, c - 1, C(fill), C(stroke))])
    return sh.svg()


def overlays():
    sh = Sheet()
    for action in ("add", "remove", "open"):
        for state, fill in (("normal", DEEP), ("hover", RAISE), ("pressed", SELECT)):
            color = C(RED if action == "remove" else CYAN)
            glyph = [line(4, 8, 12, 8, color, width=1.5)]
            if action == "add":
                glyph += [line(8, 4, 8, 12, color, width=1.5)]
            elif action == "open":
                glyph = [f'<path d="M 4 12 L 12 4 M 6 4 H 12 V 10" fill="none" stroke="{color}" stroke-width="1.5"/>']
            sh.part(action + "-" + state, 16, 16, [circle(8, 8, 7.5, C(fill), color), *glyph])
    return sh.svg()


def scrollwidget():
    sh = Sheet()
    f = Frame(4, 4, 4, 4)
    f.border(C(CYAN), .3)
    f.emit(sh, "border")
    return sh.svg()


def glowbar():
    sh = Sheet()
    f = Frame(4, 4, 4, 4)
    f.fill(C(CYAN), .1)
    f.border(C(CYAN), .65)
    f.emit(sh, "")
    sh.hint("hint-glow-radius", 4, 4)
    sh.hint("hint-tile-center", 1, 1)
    return sh.svg()


def generate(root: Path) -> list[Path]:
    outputs = {
        "checkmarks": checkmarks(), "radiobutton": radiobutton(), "switch": switch(),
        "pager": pager(), "arrows": arrows(), "line": separators(), "toolbar": toolbar(),
        "calendar": calendar(), "busywidget": busy(), "bar_meter_vertical": bar_meter_svg(),
        "actionbutton": actionbutton(), "action-overlays": overlays(),
        "scrollwidget": scrollwidget(), "glowbar": glowbar(),
    }
    dest = root / "widgets"
    dest.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in outputs.items():
        path = dest / (name + ".svg")
        path.write_text(text)
        paths.append(path)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "plasma/desktoptheme/stark-os")
    args = parser.parse_args(argv)
    paths = generate(args.output)
    print(f"Generated {len(paths)} additional Plasma assets in {args.output}")


if __name__ == "__main__":
    main()
