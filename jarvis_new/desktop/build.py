#!/usr/bin/env python3
"""Generate the STARK OS desktop theme from design/STARK_OS.md.

One script renders every themed piece of the KDE Plasma desktop so the
whole machine matches the Jarvis HUD: the colour scheme, the Plasma
desktop theme (panel, popups, tooltips, taskbar, controls), the Aurorae
window decoration, the Konsole scheme and profile, the wallpapers and the
splash rings. Outputs land next to this file and are committed; install.sh
only copies and applies them. Re-run after changing a token:

    python3 desktop/build.py            # SVGs, colours, Konsole, splash art
    python3 desktop/build.py --wallpaper  # also re-render the wallpapers (Inkscape)

Plasma frame SVGs are 9-slice: each element (topleft, top, ..., center)
is a group whose invisible bounding rect sets its size; corners stay
fixed, borders tile or stretch, the centre fills. Everything here is
drawn with plain rects, polygons and gradients so QtSvg renders it the
same way Inkscape does.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------- tokens

VOID = (1, 5, 9)
INK = (3, 16, 26)
DEEP = (6, 24, 38)
RAISE = (11, 36, 54)
RAISE_HOVER = (15, 48, 71)
BLUEPRINT = (10, 42, 68)
CYAN = (95, 227, 255)
CYAN_HOT = (216, 248, 255)
CYAN_DIM = (42, 143, 176)
SELECT = (18, 84, 106)
TEXT = (217, 244, 255)
TEXT_DIM = (127, 166, 184)
TEXT_FAINT = (70, 103, 122)
AMBER = (255, 179, 71)
RED = (255, 77, 94)
GREEN = (109, 255, 179)
LINE_SOLID = (22, 65, 79)
LINE_STRONG_SOLID = (43, 127, 149)


def hexc(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def csv(rgb: tuple[int, int, int]) -> str:
    return ",".join(str(channel) for channel in rgb)


# ------------------------------------------------------------ svg sheet


class Sheet:
    """An SVG document of named 9-slice parts laid out on a grid."""

    def __init__(self) -> None:
        self.defs: list[str] = []
        self.body: list[str] = []
        self._gid = 0
        self._x = 0
        self._y = 0
        self._row_h = 0
        self.width = 0
        self.height = 0

    # -- gradients
    def linear(self, stops, x1=0.0, y1=0.0, x2=1.0, y2=0.0) -> str:
        self._gid += 1
        gid = f"g{self._gid}"
        st = "".join(
            f'<stop offset="{o:g}" stop-color="{hexc(c)}" stop-opacity="{a:g}"/>'
            for o, c, a in stops
        )
        self.defs.append(
            f'<linearGradient id="{gid}" x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" '
            f'y2="{y2:g}">{st}</linearGradient>'
        )
        return f"url(#{gid})"

    def radial(self, stops, cx, cy, r) -> str:
        self._gid += 1
        gid = f"g{self._gid}"
        st = "".join(
            f'<stop offset="{o:g}" stop-color="{hexc(c)}" stop-opacity="{a:g}"/>'
            for o, c, a in stops
        )
        self.defs.append(
            f'<radialGradient id="{gid}" gradientUnits="userSpaceOnUse" '
            f'cx="{cx:g}" cy="{cy:g}" r="{r:g}" fx="{cx:g}" fy="{cy:g}">{st}'
            "</radialGradient>"
        )
        return f"url(#{gid})"

    # -- placement
    def _slot(self, w: float, h: float, gap: int = 6) -> tuple[float, float]:
        if self._x + w > 900:
            self._x = 0
            self._y += self._row_h + gap
            self._row_h = 0
        x, y = self._x, self._y
        self._x += w + gap
        self._row_h = max(self._row_h, h)
        self.width = max(self.width, x + w)
        self.height = max(self.height, y + h)
        return x, y

    def part(self, pid: str, w: float, h: float, prims: list[str]) -> None:
        """One element: invisible bounds rect + its primitives (local coords)."""
        x, y = self._slot(w, h)
        inner = "".join(prims)
        self.body.append(
            f'<g id="{pid}" transform="translate({x:g},{y:g})">'
            f'<rect x="0" y="0" width="{w:g}" height="{h:g}" fill="#000000" '
            f'fill-opacity="0.001"/>{inner}</g>'
        )

    def hint(self, hid: str, w: float, h: float) -> None:
        x, y = self._slot(max(w, 1), max(h, 1))
        self.body.append(
            f'<rect id="{hid}" x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" '
            'fill="#000000" fill-opacity="0.001"/>'
        )

    def svg(self) -> str:
        w = math.ceil(self.width) + 4
        h = math.ceil(self.height) + 4
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">\n'
            f"<defs>{''.join(self.defs)}</defs>\n" + "\n".join(self.body) + "\n</svg>\n"
        )


def rect(x, y, w, h, fill, alpha=1.0) -> str:
    if w <= 0 or h <= 0:
        return ""
    return (
        f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" fill="{fill}" '
        f'fill-opacity="{alpha:g}"/>'
    )


def poly(points, fill, alpha=1.0) -> str:
    pts = " ".join(f"{x:g},{y:g}" for x, y in points)
    return f'<polygon points="{pts}" fill="{fill}" fill-opacity="{alpha:g}"/>'


def line(x1, y1, x2, y2, color, alpha=1.0, width=1.0) -> str:
    return (
        f'<path d="M {x1:g} {y1:g} L {x2:g} {y2:g}" fill="none" stroke="{color}" '
        f'stroke-opacity="{alpha:g}" stroke-width="{width:g}" stroke-linecap="square"/>'
    )


# ------------------------------------------------------------ 9-slice frame

PARTS = (
    "topleft",
    "top",
    "topright",
    "left",
    "center",
    "right",
    "bottomleft",
    "bottom",
    "bottomright",
)


class Frame:
    """A frame drawn at a nominal size, then cut into the 9 Plasma parts.

    t/r/b/l: border thicknesses (corner sizes follow). Primitives added with
    the helpers below are expressed in full-frame coordinates; rects are
    clipped into every part they touch, shapes must sit inside one part.
    """

    MID = 16

    def __init__(self, t: int, r: int, b: int, l: int) -> None:  # noqa: E741
        self.bt, self.br, self.bb, self.bl = t, r, b, l
        self.width = l + self.MID + r
        self.height = t + self.MID + b
        self.rects: list[tuple] = []  # (x, y, w, h, fill, alpha)
        self.shapes: list[tuple] = []  # (bbox, svg)
        self.part_fill: dict[str, list[str]] = {p: [] for p in PARTS}

    def bounds(self, part: str) -> tuple[float, float, float, float]:
        xs = {
            "left": (0, self.bl),
            "mid": (self.bl, self.MID),
            "right": (self.bl + self.MID, self.br),
        }
        ys = {
            "top": (0, self.bt),
            "mid": (self.bt, self.MID),
            "bottom": (self.bt + self.MID, self.bb),
        }
        col = "left" if "left" in part else "right" if "right" in part else "mid"
        row = (
            "top"
            if part.startswith("top")
            else "bottom"
            if part.startswith("bottom")
            else "mid"
        )
        return xs[col][0], ys[row][0], xs[col][1], ys[row][1]

    # -- drawing helpers (full-frame coords)
    def fill(
        self, color, alpha, chamfer: int = 0, chamfer_br: int | None = None
    ) -> None:
        """Body fill; chamfer cuts the top-left and bottom-right corners."""
        k_tl = chamfer
        k_br = chamfer if chamfer_br is None else chamfer_br
        for p in PARTS:
            _x, _y, w, h = self.bounds(p)
            if p == "topleft" and k_tl:
                self.part_fill[p].append(
                    poly([(k_tl, 0), (w, 0), (w, h), (0, h), (0, k_tl)], color, alpha)
                )
            elif p == "bottomright" and k_br:
                self.part_fill[p].append(
                    poly(
                        [(0, 0), (w, 0), (w, h - k_br), (w - k_br, h), (0, h)],
                        color,
                        alpha,
                    )
                )
            else:
                self.part_fill[p].append(rect(0, 0, w, h, color, alpha))

    def r(self, x, y, w, h, color, alpha=1.0) -> None:
        self.rects.append((x, y, w, h, color, alpha))

    def shape(self, svg: str, bbox: tuple[float, float, float, float]) -> None:
        self.shapes.append((bbox, svg))

    def border(
        self, color, alpha, chamfer: int = 0, sides: str = "trbl", diag_alpha=None
    ) -> None:
        """1px hairline on the chosen sides, following the chamfer cuts."""
        k = chamfer
        width, height = self.width, self.height
        if "t" in sides:
            self.r(k, 0, width - k, 1, color, alpha)
        if "l" in sides:
            self.r(0, k, 1, height - k, color, alpha)
        if "b" in sides:
            self.r(0, height - 1, width - k, 1, color, alpha)
        if "r" in sides:
            self.r(width - 1, 0, 1, height - k, color, alpha)
        if k:
            da = alpha if diag_alpha is None else diag_alpha
            self.shape(line(0.5, k, k, 0.5, color, da), (0, 0, k, k))
            self.shape(
                line(width - k, height - 0.5, width - 0.5, height - k, color, da),
                (width - k, height - k, k, k),
            )

    def brackets(
        self, color, alpha, length: int, thick: int = 1, corners=("tr", "bl")
    ) -> None:
        width, height, b, s = self.width, self.height, length, thick
        if "tl" in corners:
            self.r(0, 0, b, s, color, alpha)
            self.r(0, 0, s, b, color, alpha)
        if "tr" in corners:
            self.r(width - b, 0, b, s, color, alpha)
            self.r(width - s, 0, s, b, color, alpha)
        if "bl" in corners:
            self.r(0, height - s, b, s, color, alpha)
            self.r(0, height - b, s, b, color, alpha)
        if "br" in corners:
            self.r(width - b, height - s, b, s, color, alpha)
            self.r(width - s, height - b, s, b, color, alpha)

    def chamfer_accent(
        self, color, alpha, chamfer: int, arm: int, stroke_width: float = 1.5
    ) -> None:
        """Bright diagonal + short arms on the chamfered corners (TL, BR)."""
        k, width, height = chamfer, self.width, self.height
        self.shape(line(0.75, k, k, 0.75, color, alpha, stroke_width), (0, 0, k, k))
        self.r(k, 0, arm - k, 1, color, alpha)
        self.r(0, k, 1, arm - k, color, alpha)
        self.shape(
            line(
                width - k,
                height - 0.75,
                width - 0.75,
                height - k,
                color,
                alpha,
                stroke_width,
            ),
            (width - k, height - k, k, k),
        )
        self.r(width - arm, height - 1, arm - k, 1, color, alpha)
        self.r(width - 1, height - arm, 1, arm - k, color, alpha)

    def bar(self, side: str, thick: int, color, alpha) -> None:
        width, height = self.width, self.height
        if side == "bottom":
            self.r(0, height - thick, width, thick, color, alpha)
        elif side == "top":
            self.r(0, 0, width, thick, color, alpha)
        elif side == "left":
            self.r(0, 0, thick, height, color, alpha)
        elif side == "right":
            self.r(width - thick, 0, thick, height, color, alpha)

    # -- output
    def emit(
        self, sheet: Sheet, prefix: str, extra: dict[str, list[str]] | None = None
    ) -> None:
        pre = f"{prefix}-" if prefix else ""
        for p in PARTS:
            x0, y0, w, h = self.bounds(p)
            prims = list(self.part_fill[p])
            for x, y, rw, rh, color, alpha in self.rects:
                ix0, iy0 = max(x, x0), max(y, y0)
                ix1, iy1 = min(x + rw, x0 + w), min(y + rh, y0 + h)
                if ix1 > ix0 and iy1 > iy0:
                    prims.append(
                        rect(ix0 - x0, iy0 - y0, ix1 - ix0, iy1 - iy0, color, alpha)
                    )
            for (bx, by, bw, bh), svg in self.shapes:
                cx, cy = bx + bw / 2, by + bh / 2
                if x0 <= cx < x0 + w and y0 <= cy < y0 + h:
                    prims.append(f'<g transform="translate({-x0:g},{-y0:g})">{svg}</g>')
            if extra and p in extra:
                prims.extend(extra[p])
            sheet.part(pre + p, w, h, prims)


def hints(
    sheet: Sheet, prefix: str, top: int, right: int, bottom: int, left: int
) -> None:
    pre = f"{prefix}-" if prefix else ""
    sheet.hint(pre + "hint-top-margin", 4, top)
    sheet.hint(pre + "hint-bottom-margin", 4, bottom)
    sheet.hint(pre + "hint-left-margin", left, 4)
    sheet.hint(pre + "hint-right-margin", right, 4)


def shadow(sheet: Sheet, size: int = 16, reach: int = 10, alpha: float = 0.55) -> None:
    """Soft dark drop shadow (dialogs, tooltips, the floating panel)."""
    s = size
    dark = [(0, VOID, alpha), (0.55, VOID, alpha * 0.35), (1, VOID, 0)]
    # Corners: radial from the inner corner point.
    for name, cx, cy in (
        ("shadow-topleft", s, s),
        ("shadow-topright", 0, s),
        ("shadow-bottomleft", s, 0),
        ("shadow-bottomright", 0, 0),
    ):
        sheet.part(name, s, s, [rect(0, 0, s, s, sheet.radial(dark, cx, cy, s))])
    edges = {
        "shadow-top": (32, s, (0, 1, 0, 0)),
        "shadow-bottom": (32, s, (0, 0, 0, 1)),
        "shadow-left": (s, 32, (1, 0, 0, 0)),
        "shadow-right": (s, 32, (0, 0, 1, 0)),
    }
    for name, (w, h, (x1, y1, x2, y2)) in edges.items():
        grad = sheet.linear(dark, x1, y1, x2, y2)
        sheet.part(name, w, h, [rect(0, 0, w, h, grad)])
    sheet.part("shadow-center", 32, 32, [])
    sheet.hint("shadow-hint-top-margin", 2, reach)
    sheet.hint("shadow-hint-bottom-margin", 2, reach)
    sheet.hint("shadow-hint-left-margin", reach, 2)
    sheet.hint("shadow-hint-right-margin", reach, 2)


# ------------------------------------------------------------ plasma theme

C = hexc


def panel_background(fill_alpha: float, with_shadow: bool = True) -> str:
    sh = Sheet()
    f = Frame(6, 6, 6, 6)
    f.fill(C(INK), fill_alpha, chamfer=5)
    # Top hairline: dim at the ends, bright in the middle (stretched edge).
    f.r(5, 0, 1, 1, C(CYAN), 0.22)  # corner run-in
    f.r(f.width - 6, 0, 6, 1, C(CYAN), 0.22)
    f.border(C(CYAN), 0.18, chamfer=5, sides="lbr", diag_alpha=0.6)
    f.shape(line(0.5, 5, 5, 0.5, C(CYAN), 0.6), (0, 0, 5, 5))
    f.brackets(C(CYAN), 0.85, 6, 1, corners=("tr", "bl"))
    top_grad = sh.linear(
        [
            (0, CYAN, 0.22),
            (0.35, CYAN, 0.45),
            (0.5, CYAN, 0.95),
            (0.65, CYAN, 0.45),
            (1, CYAN, 0.22),
        ]
    )
    glow = sh.linear([(0, CYAN, 0.0), (0.5, CYAN, 0.10), (1, CYAN, 0.0)])
    f.emit(
        sh,
        "",
        extra={
            "top": [rect(0, 0, Frame.MID, 1, top_grad), rect(0, 1, Frame.MID, 2, glow)],
        },
    )
    hints(sh, "", 4, 4, 4, 4)
    sh.hint("hint-stretch-borders", 2, 2)
    if with_shadow:
        shadow(sh)
    return sh.svg()


def dialog_background(fill_alpha: float, margin: int = 6, brackets: bool = True) -> str:
    sh = Sheet()
    f = Frame(12, 12, 12, 12)
    f.fill(C(DEEP), fill_alpha, chamfer=10)
    f.border(C(CYAN), 0.30, chamfer=10)
    f.chamfer_accent(C(CYAN), 0.85, 10, 12)
    if brackets:
        f.brackets(C(CYAN), 0.85, 12, 2, corners=("tr", "bl"))
    # A whisper of blueprint light along the top edge.
    f.r(12, 1, Frame.MID, 1, C(CYAN), 0.06)
    f.emit(sh, "")
    hints(sh, "", margin, margin, margin, margin)
    shadow(sh)
    return sh.svg()


def widget_background(fill_alpha: float) -> str:
    sh = Sheet()
    f = Frame(12, 12, 12, 12)
    f.fill(C(DEEP), fill_alpha, chamfer=10)
    f.border(C(CYAN), 0.24, chamfer=10)
    f.chamfer_accent(C(CYAN), 0.8, 10, 12)
    f.brackets(C(CYAN), 0.8, 12, 2, corners=("tr", "bl"))
    f.emit(sh, "")
    hints(sh, "", 12, 12, 12, 12)
    shadow(sh, alpha=0.4)
    return sh.svg()


def tooltip(fill_alpha: float) -> str:
    sh = Sheet()
    f = Frame(6, 6, 6, 6)
    f.fill(C(DEEP), fill_alpha, chamfer=5)
    f.border(C(CYAN), 0.38, chamfer=5, diag_alpha=0.8)
    f.emit(sh, "")
    hints(sh, "", 6, 8, 6, 8)
    shadow(sh, alpha=0.5)
    return sh.svg()


def tasks_svg() -> str:
    sh = Sheet()
    sides = {"": "bottom", "north-": "top", "west-": "left", "east-": "right"}
    for loc, side in sides.items():
        states = {
            "normal": {"line": (CYAN_DIM, 0.85, 1)},
            "minimized": {"line": (TEXT_FAINT, 0.9, 1)},
            "focus": {"fill": (CYAN, 0.10), "line": (CYAN, 0.95, 2)},
            "attention": {"fill": (AMBER, 0.12), "line": (AMBER, 0.95, 2)},
            "hover": {"fill": (CYAN, 0.06), "brackets": True},
            "progress": {"fill": (CYAN, 0.16)},
        }
        for state, spec in states.items():
            f = Frame(4, 4, 4, 4)
            if "fill" in spec:
                col, a = spec["fill"]
                f.fill(C(col), a)
            if "line" in spec:
                col, a, th = spec["line"]
                f.bar(side, th, C(col), a)
            if spec.get("brackets"):
                f.brackets(C(CYAN), 0.85, 4, 1, corners=("tl", "tr", "bl", "br"))
            prefix = f"{loc}{state}"
            f.emit(sh, prefix)
            hints(sh, prefix, 4, 4, 4, 4)
    # Group expander: a small cyan chevron on the panel-facing edge.
    for side, pts in {
        "bottom": [(0, 0), (8, 0), (4, 4)],
        "top": [(0, 4), (8, 4), (4, 0)],
        "left": [(4, 0), (4, 8), (0, 4)],
        "right": [(0, 0), (0, 8), (4, 4)],
    }.items():
        w = 8 if side in ("top", "bottom") else 4
        h = 4 if side in ("top", "bottom") else 8
        sh.part(f"group-expander-{side}", w, h, [poly(pts, C(CYAN), 0.9)])
    return sh.svg()


def viewitem_svg() -> str:
    sh = Sheet()
    for state, fa, ba in (
        ("normal", 0.0, 0.0),
        ("hover", 0.08, 0.28),
        ("selected", 0.16, 0.45),
        ("selected+hover", 0.22, 0.6),
    ):
        f = Frame(4, 4, 4, 4)
        if fa:
            f.fill(C(CYAN), fa)
            f.border(C(CYAN), ba)
        if state.startswith("selected"):
            f.bar("left", 2, C(CYAN), 0.95)
        f.emit(sh, state)
    sh.hint("hint-tile-center", 4, 4)
    return sh.svg()


def button_svg() -> str:
    sh = Sheet()

    def surface(prefix, fill, fa, border_a, diag_a, margin=6):
        f = Frame(5, 5, 5, 5)
        f.fill(C(fill), fa, chamfer=3)
        f.border(C(CYAN), border_a, chamfer=3, diag_alpha=diag_a)
        f.emit(sh, prefix)
        hints(sh, prefix, margin, margin, margin, margin)

    surface("normal", RAISE, 0.95, 0.30, 0.55)
    surface("pressed", SELECT, 1.0, 0.85, 1.0)
    surface("focus-background", RAISE_HOVER, 1.0, 0.75, 1.0)
    surface("toolbutton-pressed", CYAN, 0.18, 0.6, 0.9)

    # Flat (tool) button hover is a full surface: its margins pad content.
    f = Frame(5, 5, 5, 5)
    f.fill(C(CYAN), 0.08, chamfer=3)
    f.border(C(CYAN), 0.35, chamfer=3, diag_alpha=0.7)
    f.emit(sh, "toolbutton-hover")
    hints(sh, "toolbutton-hover", 6, 6, 6, 6)

    # Hover / focus rings sit just outside the button: target-lock brackets.
    def ring(prefix, border_a, br_a):
        f = Frame(6, 6, 6, 6)
        f.border(C(CYAN), border_a)
        f.brackets(C(CYAN), br_a, 6, 1, corners=("tl", "tr", "bl", "br"))
        f.emit(sh, prefix)
        hints(sh, prefix, 2, 2, 2, 2)

    ring("hover", 0.25, 0.9)
    ring("focus", 0.6, 1.0)
    ring("toolbutton-focus", 0.45, 0.9)
    return sh.svg()


def lineedit_svg() -> str:
    sh = Sheet()
    f = Frame(5, 5, 5, 5)
    f.fill(C(INK), 0.95, chamfer=3)
    f.border(C(CYAN), 0.28, chamfer=3, diag_alpha=0.5)
    f.emit(sh, "base")
    hints(sh, "base", 6, 6, 6, 6)
    for prefix, ba, br in (("hover", 0.5, 0.0), ("focus", 0.9, 1.0)):
        f = Frame(6, 6, 6, 6)
        f.border(C(CYAN), ba)
        if br:
            f.brackets(C(CYAN), br, 6, 1, corners=("tl", "tr", "bl", "br"))
        f.emit(sh, prefix)
        hints(sh, prefix, 1, 1, 1, 1)
    sh.hint("hint-tile-center", 4, 4)
    return sh.svg()


def frame_svg() -> str:
    sh = Sheet()
    for prefix, fill, fa, ba in (
        ("plain", INK, 0.0, 0.22),
        ("raised", DEEP, 0.9, 0.3),
        ("sunken", INK, 0.85, 0.22),
    ):
        f = Frame(5, 5, 5, 5)
        if fa:
            f.fill(C(fill), fa, chamfer=4)
        f.border(C(CYAN), ba, chamfer=4)
        f.emit(sh, prefix)
        hints(sh, prefix, 4, 4, 4, 4)
    sh.hint("hint-tile-center", 4, 4)
    return sh.svg()


def scrollbar_svg() -> str:
    sh = Sheet()
    for orient in ("vertical", "horizontal"):
        f = Frame(2, 2, 2, 2)
        f.fill(C(CYAN), 0.04)
        f.emit(sh, f"background-{orient}")
    for prefix, a in (("slider", 0.42), ("mouseover-slider", 0.85)):
        f = Frame(2, 2, 2, 2)
        f.fill(C(CYAN), a)
        f.emit(sh, prefix)
    sh.hint("hint-scrollbar-size", 8, 8)
    sh.hint("hint-tile-center", 4, 4)
    return sh.svg()


def hexagon(cx, cy, r) -> list[tuple[float, float]]:
    return [
        (
            cx + r * math.cos(math.radians(60 * i - 90)),
            cy + r * math.sin(math.radians(60 * i - 90)),
        )
        for i in range(6)
    ]


def slider_svg() -> str:
    sh = Sheet()
    for prefix, a in (("groove", 0.28), ("groove-highlight", 0.95)):
        f = Frame(2, 2, 2, 2)
        f.fill(C(CYAN), a)
        f.emit(sh, prefix)
    size = 18

    def handle(fill_a, ring_a, dot_a):
        pts = hexagon(size / 2, size / 2, size / 2 - 1.2)
        d = "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in pts) + " Z"
        return [
            f'<path d="{d}" fill="{C(INK)}" fill-opacity="{fill_a:g}" stroke="{C(CYAN)}" '
            f'stroke-opacity="{ring_a:g}" stroke-width="1.4"/>',
            f'<circle cx="{size / 2:g}" cy="{size / 2:g}" r="2.6" fill="{C(CYAN_HOT)}" '
            f'fill-opacity="{dot_a:g}"/>',
        ]

    for orient in ("horizontal", "vertical"):
        sh.part(f"{orient}-slider-handle", size, size, handle(1.0, 0.9, 0.85))
        sh.part(f"{orient}-slider-hover", size, size, handle(0.0, 1.0, 1.0))
        sh.part(f"{orient}-slider-focus", size, size, handle(0.0, 1.0, 1.0))
        sh.part(
            f"{orient}-slider-shadow",
            size,
            size,
            [
                f'<circle cx="{size / 2:g}" cy="{size / 2 + 1:g}" r="{size / 2 - 1:g}" fill="#000000" fill-opacity="0.25"/>'
            ],
        )
    sh.hint("hint-handle-size", size, size)
    sh.hint("hint-tile-center", 4, 4)
    return sh.svg()


def bar_meter_svg() -> str:
    sh = Sheet()
    f = Frame(2, 2, 2, 2)
    f.fill(C(INK), 0.9)
    f.border(C(CYAN), 0.25)
    f.emit(sh, "bar-inactive")
    f = Frame(2, 2, 2, 2)
    f.fill(C(CYAN), 0.85)
    f.r(0, 0, f.width, 1, C(CYAN_HOT), 0.9)
    f.emit(sh, "bar-active")
    sh.hint("hint-bar-size", 6, 6)
    sh.hint("hint-tile-center", 4, 4)
    return sh.svg()


def tabbar_svg() -> str:
    sh = Sheet()
    for loc, side in (
        ("north", "bottom"),
        ("south", "top"),
        ("west", "right"),
        ("east", "left"),
    ):
        f = Frame(4, 4, 4, 4)
        f.fill(C(CYAN), 0.08)
        f.bar(side, 2, C(CYAN), 0.95)
        prefix = f"{loc}-active-tab"
        f.emit(sh, prefix)
        hints(sh, prefix, 4, 6, 4, 6)
    sh.hint("hint-tile-center", 4, 4)
    return sh.svg()


def plasmoidheading_svg() -> str:
    sh = Sheet()
    f = Frame(4, 4, 4, 4)
    f.fill(C(CYAN), 0.04)
    f.r(0, f.height - 1, f.width, 1, C(CYAN), 0.42)
    f.emit(sh, "header")
    f = Frame(4, 4, 4, 4)
    f.fill(C(CYAN), 0.03)
    f.r(0, 0, f.width, 1, C(CYAN), 0.3)
    f.emit(sh, "footer")
    hints(sh, "", 4, 4, 4, 4)
    sh.hint("hint-stretch-borders", 2, 2)
    return sh.svg()


def menubaritem_svg() -> str:
    sh = Sheet()
    for prefix, fa, bar_a in (
        ("normal", 0.0, 0.0),
        ("hover", 0.10, 0.8),
        ("pressed", 0.2, 1.0),
    ):
        f = Frame(3, 3, 3, 3)
        if fa:
            f.fill(C(CYAN), fa)
            f.bar("bottom", 2, C(CYAN), bar_a)
        f.emit(sh, prefix)
        hints(sh, prefix, 3, 6, 3, 6)
    return sh.svg()


def listitem_svg() -> str:
    sh = Sheet()
    for prefix, fa, ba in (
        ("normal", 0.0, 0.0),
        ("hover", 0.07, 0.3),
        ("pressed", 0.16, 0.55),
    ):
        f = Frame(4, 4, 4, 4)
        if fa:
            f.fill(C(CYAN), fa)
            f.border(C(CYAN), ba)
        if prefix == "pressed":
            f.bar("left", 2, C(CYAN), 0.95)
        f.emit(sh, prefix)
        hints(sh, prefix, 4, 6, 4, 6)
    f = Frame(4, 4, 4, 4)
    f.r(0, f.height - 1, f.width, 1, C(CYAN), 0.35)
    f.emit(sh, "section")
    hints(sh, "section", 4, 6, 4, 6)
    sh.part("separator", 32, 1, [rect(0, 0, 32, 1, C(CYAN), 0.22)])
    sh.hint("hint-tile-center", 4, 4)
    return sh.svg()


def plasma_theme(root: Path) -> None:
    files = {
        "widgets/panel-background.svg": panel_background(0.90),
        "translucent/widgets/panel-background.svg": panel_background(0.78),
        "opaque/widgets/panel-background.svg": panel_background(1.0),
        "solid/widgets/panel-background.svg": panel_background(1.0),
        "dialogs/background.svg": dialog_background(0.97),
        "translucent/dialogs/background.svg": dialog_background(0.86),
        "opaque/dialogs/background.svg": dialog_background(1.0),
        "solid/dialogs/background.svg": dialog_background(1.0),
        "widgets/background.svg": widget_background(0.84),
        "translucent/widgets/background.svg": widget_background(0.72),
        "solid/widgets/background.svg": widget_background(1.0),
        "widgets/translucentbackground.svg": widget_background(0.6),
        "widgets/tooltip.svg": tooltip(0.97),
        "translucent/widgets/tooltip.svg": tooltip(0.9),
        "opaque/widgets/tooltip.svg": tooltip(1.0),
        "solid/widgets/tooltip.svg": tooltip(1.0),
        "widgets/tasks.svg": tasks_svg(),
        "widgets/viewitem.svg": viewitem_svg(),
        "widgets/button.svg": button_svg(),
        "widgets/lineedit.svg": lineedit_svg(),
        "widgets/frame.svg": frame_svg(),
        "widgets/scrollbar.svg": scrollbar_svg(),
        "widgets/slider.svg": slider_svg(),
        "widgets/bar_meter_horizontal.svg": bar_meter_svg(),
        "widgets/tabbar.svg": tabbar_svg(),
        "widgets/plasmoidheading.svg": plasmoidheading_svg(),
        "widgets/menubaritem.svg": menubaritem_svg(),
        "widgets/listitem.svg": listitem_svg(),
    }
    if root.exists():
        shutil.rmtree(root)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    meta = {
        "KPlugin": {
            "Id": "stark-os",
            "Name": "Stark OS",
            "Description": "Jarvis HUD glass: cyan hairlines, chamfers and corner brackets on deep ink",
            "Authors": [{"Name": "Jarvis"}],
            "License": "MIT",
            "Version": "1.0",
            "EnabledByDefault": True,
        },
        "X-Plasma-API": "5.0",
    }
    (root / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    (root / "plasmarc").write_text(
        "[Settings]\nFallbackTheme=default\n\n[ContrastEffect]\nenabled=true\n"
        "contrast=0.9\nintensity=0.9\nsaturation=1.4\n\n[AdaptiveTransparency]\nenabled=true\n"
    )
    (root / "colors").write_text(color_scheme())


# ------------------------------------------------------------ colour scheme


def color_scheme() -> str:
    common = {
        "ForegroundNormal": TEXT,
        "ForegroundInactive": TEXT_DIM,
        "ForegroundActive": CYAN,
        "ForegroundLink": CYAN,
        "ForegroundVisited": CYAN_DIM,
        "ForegroundNegative": RED,
        "ForegroundNeutral": AMBER,
        "ForegroundPositive": GREEN,
        "DecorationFocus": CYAN,
        "DecorationHover": CYAN_DIM,
    }
    groups = {
        "Window": {"BackgroundNormal": DEEP, "BackgroundAlternate": RAISE},
        "View": {"BackgroundNormal": INK, "BackgroundAlternate": DEEP},
        "Button": {"BackgroundNormal": RAISE, "BackgroundAlternate": RAISE_HOVER},
        "Tooltip": {"BackgroundNormal": DEEP, "BackgroundAlternate": RAISE},
        "Complementary": {"BackgroundNormal": INK, "BackgroundAlternate": DEEP},
        "Header": {"BackgroundNormal": DEEP, "BackgroundAlternate": INK},
        "Header][Inactive": {"BackgroundNormal": INK, "BackgroundAlternate": INK},
        "Selection": {
            "BackgroundNormal": SELECT,
            "BackgroundAlternate": (13, 64, 82),
            "ForegroundNormal": CYAN_HOT,
            "ForegroundInactive": TEXT,
            "ForegroundActive": CYAN_HOT,
            "ForegroundLink": CYAN_HOT,
            "ForegroundVisited": TEXT,
        },
    }
    out = [
        "[ColorEffects:Disabled]",
        "Color=3,16,26",
        "ColorAmount=0.35",
        "ColorEffect=3",
        "ContrastAmount=0.55",
        "ContrastEffect=1",
        "IntensityAmount=0.1",
        "IntensityEffect=2",
        "",
        "[ColorEffects:Inactive]",
        "ChangeSelectionColor=true",
        "Color=3,16,26",
        "ColorAmount=0.025",
        "ColorEffect=2",
        "ContrastAmount=0.1",
        "ContrastEffect=2",
        "Enable=false",
        "IntensityAmount=0",
        "IntensityEffect=0",
        "",
    ]
    for name, vals in groups.items():
        merged = dict(common)
        merged.update(vals)
        out.append(f"[Colors:{name}]")
        for k in sorted(merged):
            out.append(f"{k}={csv(merged[k])}")
        out.append("")
    out += [
        "[General]",
        "ColorScheme=StarkOS",
        "Name=Stark OS",
        "accentActiveTitlebar=false",
        "shadeSortColumn=true",
        "",
        "[KDE]",
        "contrast=4",
        "",
        "[WM]",
        f"activeBackground={csv(DEEP)}",
        f"activeBlend={csv(CYAN)}",
        f"activeForeground={csv(CYAN_HOT)}",
        f"inactiveBackground={csv(INK)}",
        f"inactiveBlend={csv(INK)}",
        f"inactiveForeground={csv(TEXT_FAINT)}",
        "",
    ]
    return "\n".join(out)


# ------------------------------------------------------------ aurorae


TITLE_H = 30  # TitleEdgeTop 7 + TitleHeight 16 + TitleEdgeBottom 7


def aurorae(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    sh = Sheet()
    for variant, active, maximized in (
        ("decoration", True, False),
        ("decoration-inactive", False, False),
        ("decoration-maximized", True, True),
        ("decoration-maximized-inactive", False, True),
    ):
        k = 0 if maximized else 8
        side = 0 if maximized else 1
        f = Frame(TITLE_H, 16, side, 120)
        # Only the title row is painted: the window body parts stay clear
        # (translucent clients such as the HUD must not show a slab).
        body = C(DEEP if active else INK)
        for p in ("topleft", "top", "topright"):
            _x0, _y0, w, h = f.bounds(p)
            if p == "topleft" and k:
                f.part_fill[p].append(
                    poly([(k, 0), (w, 0), (w, h), (0, h), (0, k)], body, 1.0)
                )
            else:
                f.part_fill[p].append(rect(0, 0, w, h, body, 1.0))
        line_a = 0.30 if active else 0.14
        f.r(0, TITLE_H - 1, f.width, 1, C(CYAN), line_a)  # title underline
        if not maximized:
            f.r(k, 0, f.width - k, 1, C(CYAN), line_a)
            f.r(0, k, 1, f.height - k, C(CYAN), line_a)
            f.r(f.width - 1, 0, 1, f.height, C(CYAN), line_a)
            f.r(0, f.height - 1, f.width, 1, C(CYAN), line_a)
            f.shape(
                line(0.5, k, k, 0.5, C(CYAN), 0.9 if active else 0.3, 1.2), (0, 0, k, k)
            )
        if active:
            # The bright cyan segment under the title (left) and a bracket
            # in the top-right corner: the HUD's instrument frame.
            seg = sh.linear([(0, CYAN, 1.0), (0.7, CYAN, 0.85), (1, CYAN, 0.0)])
            f.shape(rect(0, TITLE_H - 2, 110, 2, seg), (0, TITLE_H - 2, 110, 2))
            if not maximized:
                f.r(f.width - 12, 0, 12, 2, C(CYAN), 0.9)
                f.r(f.width - 2, 0, 2, 12, C(CYAN), 0.9)
        # Small wedge mark at the far left of the bar, before the title.
        f.shape(
            poly([(12, 19), (20, 11), (20, 19)], C(CYAN), 0.9 if active else 0.35),
            (12, 11, 8, 8),
        )
        f.emit(sh, variant)
    # Mask: the full window shape (chamfer at top-left) for blur/shadows.
    m = Frame(TITLE_H, 16, 1, 120)
    m.fill("#000000", 1.0, chamfer=8, chamfer_br=0)
    m.emit(sh, "mask")
    (root / "decoration.svg").write_text(sh.svg())

    glyphs = {
        "close": lambda c, a, w: [
            line(4, 4, 12, 12, c, a, w),
            line(12, 4, 4, 12, c, a, w),
        ],
        "minimize": lambda c, a, w: [line(4, 11, 12, 11, c, a, w)],
        "maximize": lambda c, a, w: [
            f'<rect x="4.5" y="4.5" width="7" height="7" fill="none" stroke="{c}" stroke-opacity="{a:g}" stroke-width="{w:g}"/>',
            line(4, 3, 7, 3, c, a, 1),
            line(9, 13, 12, 13, c, a, 1),
        ],
        "restore": lambda c, a, w: [
            f'<rect x="3.5" y="6.5" width="6" height="6" fill="none" stroke="{c}" stroke-opacity="{a:g}" stroke-width="{w:g}"/>',
            f'<path d="M 6.5 6.5 V 3.5 H 12.5 V 9.5 H 9.5" fill="none" stroke="{c}" stroke-opacity="{a:g}" stroke-width="{w:g}"/>',
        ],
        "alldesktops": lambda c, a, w: [
            f'<circle cx="8" cy="8" r="3" fill="none" stroke="{c}" stroke-opacity="{a:g}" stroke-width="{w:g}"/>',
            f'<circle cx="8" cy="8" r="1" fill="{c}" fill-opacity="{a:g}"/>',
        ],
        "keepabove": lambda c, a, w: [
            f'<path d="M 4 10 L 8 6 L 12 10" fill="none" stroke="{c}" stroke-opacity="{a:g}" stroke-width="{w:g}"/>'
        ],
        "keepbelow": lambda c, a, w: [
            f'<path d="M 4 6 L 8 10 L 12 6" fill="none" stroke="{c}" stroke-opacity="{a:g}" stroke-width="{w:g}"/>'
        ],
        "shade": lambda c, a, w: [
            line(4, 6, 12, 6, c, a, w),
            line(4, 9, 12, 9, c, a, w),
        ],
        "help": lambda c, a, w: [
            f'<path d="M 6 6 A 2 2 0 1 1 8 8 V 9.5" fill="none" stroke="{c}" stroke-opacity="{a:g}" stroke-width="{w:g}"/>',
            f'<circle cx="8" cy="12" r="0.9" fill="{c}" fill-opacity="{a:g}"/>',
        ],
    }
    for name, glyph in glyphs.items():
        b = Sheet()
        hot = RED if name == "close" else CYAN
        hex_pts = hexagon(8, 8, 7.4)
        hex_d = "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in hex_pts) + " Z"

        def plate(color, fa, sa, hex_d=hex_d):
            return (
                f'<path d="{hex_d}" fill="{C(color)}" fill-opacity="{fa:g}" '
                f'stroke="{C(color)}" stroke-opacity="{sa:g}" stroke-width="1"/>'
            )

        states = {
            "active": [*glyph(C(TEXT), 0.9, 1.4)],
            "hover": [
                plate(hot, 0.16, 0.9),
                *glyph(C(CYAN_HOT if name != "close" else RED), 1.0, 1.5),
            ],
            "pressed": [plate(hot, 0.32, 1.0), *glyph(C(CYAN_HOT), 1.0, 1.5)],
            "inactive": [*glyph(C(TEXT_FAINT), 0.9, 1.3)],
            "hover-inactive": [plate(hot, 0.12, 0.7), *glyph(C(TEXT), 0.9, 1.4)],
            "deactivated": [*glyph(C(TEXT_FAINT), 0.5, 1.2)],
            "deactivated-inactive": [*glyph(C(TEXT_FAINT), 0.35, 1.2)],
        }
        for state, prims in states.items():
            b.part(f"{state}-center", 16, 16, prims)
        (root / f"{name}.svg").write_text(b.svg())

    rc = f"""[General]
ActiveTextColor={csv(CYAN_HOT)}
InactiveTextColor={csv(TEXT_FAINT)}
ActiveFocusedTabColor={csv(CYAN)}
ActiveUnfocusedTabColor={csv(CYAN_DIM)}
InactiveFocusedTabColor={csv(TEXT_FAINT)}
InactiveUnfocusedTabColor={csv(TEXT_FAINT)}
TitleAlignment=Left
TitleVerticalAlignment=Center
UseTextShadow=false
LeftButtons=
RightButtons=IAX
Shadow=false
Animation=160
DecorationPosition=0

[Layout]
BorderLeft=1
BorderRight=1
BorderBottom=1
TitleEdgeTop=7
TitleEdgeBottom=7
TitleEdgeLeft=30
TitleEdgeRight=10
TitleEdgeTopMaximized=7
TitleEdgeBottomMaximized=7
TitleEdgeLeftMaximized=30
TitleEdgeRightMaximized=10
TitleBorderLeft=8
TitleBorderRight=8
TitleHeight=16
ButtonWidth=16
ButtonHeight=16
ButtonSpacing=8
ButtonMarginTop=0
ExplicitButtonSpacer=8
PaddingTop=0
PaddingBottom=0
PaddingLeft=0
PaddingRight=0
"""
    (root / "stark-osrc").write_text(rc)
    (root / "metadata.desktop").write_text(
        "[Desktop Entry]\nName=Stark OS\nComment=Jarvis HUD window frame\n"
        "X-KDE-PluginInfo-Author=Jarvis\nX-KDE-PluginInfo-Name=stark-os\n"
        "X-KDE-PluginInfo-Version=1.0\nX-KDE-PluginInfo-License=MIT\n"
        "X-KDE-PluginInfo-EnabledByDefault=true\n"
    )


# ------------------------------------------------------------ konsole


def konsole(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    palette = [
        (DEEP, (16, 48, 68), (4, 18, 28)),  # black
        (RED, (255, 122, 134), (176, 54, 66)),
        (GREEN, (160, 255, 205), (76, 178, 125)),
        (AMBER, (255, 205, 130), (178, 125, 50)),
        (CYAN_DIM, (90, 180, 220), (30, 100, 123)),  # blue
        ((180, 140, 255), (205, 180, 255), (126, 98, 178)),  # magenta
        (CYAN, CYAN_HOT, (66, 159, 178)),
        (TEXT, (255, 255, 255), TEXT_DIM),
    ]
    out = []
    out.append(f"[Background]\nColor={csv(INK)}\n")
    out.append(f"[BackgroundIntense]\nColor={csv(DEEP)}\n")
    out.append(f"[BackgroundFaint]\nColor={csv(INK)}\n")
    out.append(f"[Foreground]\nColor={csv(TEXT)}\n")
    out.append(f"[ForegroundIntense]\nColor={csv(CYAN_HOT)}\n")
    out.append(f"[ForegroundFaint]\nColor={csv(TEXT_DIM)}\n")
    for i, (n, hi, lo) in enumerate(palette):
        out.append(f"[Color{i}]\nColor={csv(n)}\n")
        out.append(f"[Color{i}Intense]\nColor={csv(hi)}\n")
        out.append(f"[Color{i}Faint]\nColor={csv(lo)}\n")
    out.append(
        "[General]\nAnchor=0.5,0.5\nBlur=true\nColorRandomization=false\n"
        "Description=Stark OS\nFillStyle=Tile\nOpacity=0.94\nWallpaper=\n"
        "WallpaperFlipType=NoFlip\nWallpaperOpacity=1\n"
    )
    (root / "StarkOS.colorscheme").write_text("\n".join(out))
    (root / "StarkOS.profile").write_text(
        "[Appearance]\nColorScheme=StarkOS\nFont=Share Tech Mono,10,-1,5,50,0,0,0,0,0\n"
        "LineSpacing=1\n\n[Cursor Options]\nCursorShape=1\nUseCustomCursorColor=true\n"
        f"CustomCursorColor={csv(CYAN)}\nCustomCursorTextColor={csv(INK)}\n\n"
        "[General]\nName=Stark OS\nParent=FALLBACK/\nTerminalMargin=10\nTerminalCenter=false\n\n"
        "[Interaction Options]\nTextEditorCmd=6\n\n[Scrolling]\nScrollBarPosition=1\n\n"
        "[Terminal Features]\nBlinkingCursorEnabled=true\n"
    )


# ------------------------------------------------------------ splash art


def splash_rings(root: Path) -> None:
    """Ring layers for Splash.qml (each rotates on its own)."""
    root.mkdir(parents=True, exist_ok=True)
    size = 520
    c = size / 2

    def ring_svg(body: str) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f"{body}</svg>\n"
        )

    def arc(r, a0, a1, width, color, alpha):
        x0, y0 = c + r * math.cos(math.radians(a0)), c + r * math.sin(math.radians(a0))
        x1, y1 = c + r * math.cos(math.radians(a1)), c + r * math.sin(math.radians(a1))
        large = 1 if (a1 - a0) % 360 > 180 else 0
        return (
            f'<path d="M {x0:.2f} {y0:.2f} A {r} {r} 0 {large} 1 {x1:.2f} {y1:.2f}" fill="none" '
            f'stroke="{C(color)}" stroke-opacity="{alpha:g}" stroke-width="{width:g}"/>'
        )

    # Outer tick ring.
    ticks = []
    for i in range(120):
        a = math.radians(i * 3)
        r0 = 246 if i % 10 else 236
        ticks.append(
            f'<line x1="{c + r0 * math.cos(a):.2f}" y1="{c + r0 * math.sin(a):.2f}" '
            f'x2="{c + 252 * math.cos(a):.2f}" y2="{c + 252 * math.sin(a):.2f}" '
            f'stroke="{C(CYAN)}" stroke-opacity="{0.8 if i % 10 == 0 else 0.35}" stroke-width="{1.6 if i % 10 == 0 else 1}"/>'
        )
    (root / "ring-ticks.svg").write_text(ring_svg("".join(ticks)))
    # Segmented arcs (medium ring).
    segs = "".join(arc(205, a, a + 50, 3, CYAN, 0.75) for a in range(0, 360, 72))
    segs += "".join(arc(196, a + 30, a + 40, 8, CYAN, 0.55) for a in range(0, 360, 90))
    (root / "ring-segments.svg").write_text(ring_svg(segs))
    # Inner fast arcs.
    inner = arc(150, 0, 100, 2, CYAN_HOT, 0.9) + arc(150, 180, 260, 2, CYAN_HOT, 0.9)
    inner += arc(138, 40, 330, 1, CYAN, 0.4)
    (root / "ring-inner.svg").write_text(ring_svg(inner))
    # Static core: thin full circles + crosshair.
    core = (
        f'<circle cx="{c}" cy="{c}" r="228" fill="none" stroke="{C(CYAN)}" stroke-opacity="0.18" stroke-width="1"/>'
        f'<circle cx="{c}" cy="{c}" r="170" fill="none" stroke="{C(CYAN)}" stroke-opacity="0.25" stroke-width="1"/>'
        f'<circle cx="{c}" cy="{c}" r="96" fill="none" stroke="{C(CYAN)}" stroke-opacity="0.5" stroke-width="1.2"/>'
        f'<circle cx="{c}" cy="{c}" r="60" fill="{C(CYAN)}" fill-opacity="0.06" stroke="{C(CYAN)}" stroke-opacity="0.85" stroke-width="2"/>'
        f'<line x1="{c - 260}" y1="{c}" x2="{c - 110}" y2="{c}" stroke="{C(CYAN)}" stroke-opacity="0.25"/>'
        f'<line x1="{c + 110}" y1="{c}" x2="{c + 260}" y2="{c}" stroke="{C(CYAN)}" stroke-opacity="0.25"/>'
    )
    (root / "ring-core.svg").write_text(ring_svg(core))


# ------------------------------------------------------------ wallpaper


def wallpaper_svg(screen: int, width: int = 1920, height: int = 1080) -> str:
    """Dark blueprint: grid, faint reactor rings, ticks, coordinates, vignette."""
    parts = []
    cx = width * (0.62 if screen == 0 else 0.38)
    cy = height * 0.46
    parts.append(
        f'<defs><radialGradient id="glow" cx="{cx}" cy="{cy}" r="{width * 0.75}" gradientUnits="userSpaceOnUse">'
        f'<stop offset="0" stop-color="{C(BLUEPRINT)}" stop-opacity="0.9"/>'
        f'<stop offset="0.45" stop-color="{C(INK)}" stop-opacity="1"/>'
        f'<stop offset="1" stop-color="{C(VOID)}" stop-opacity="1"/></radialGradient>'
        '<radialGradient id="vig" cx="50%" cy="50%" r="75%">'
        f'<stop offset="0.6" stop-color="{C(VOID)}" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="{C(VOID)}" stop-opacity="0.85"/></radialGradient>'
        f'<radialGradient id="core" cx="{cx}" cy="{cy}" r="260" gradientUnits="userSpaceOnUse">'
        f'<stop offset="0" stop-color="{C(CYAN)}" stop-opacity="0.10"/>'
        f'<stop offset="1" stop-color="{C(CYAN)}" stop-opacity="0"/></radialGradient></defs>'
    )
    parts.append(f'<rect width="{width}" height="{height}" fill="url(#glow)"/>')
    # 40px grid with brighter major lines every 200px.
    grid = []
    for x in range(0, width + 1, 40):
        a = 0.075 if x % 200 == 0 else 0.035
        grid.append(
            f'<line x1="{x + 0.5}" y1="0" x2="{x + 0.5}" y2="{height}" stroke-opacity="{a}"/>'
        )
    for y in range(0, height + 1, 40):
        a = 0.075 if y % 200 == 0 else 0.035
        grid.append(
            f'<line x1="0" y1="{y + 0.5}" x2="{width}" y2="{y + 0.5}" stroke-opacity="{a}"/>'
        )
    parts.append(f'<g stroke="{C(CYAN)}" stroke-width="1">{"".join(grid)}</g>')
    # Grid crosses at major intersections.
    cross = []
    for x in range(200, width, 200):
        for y in range(200, height, 200):
            cross.append(
                f'<path d="M {x - 5} {y + 0.5} H {x + 6} M {x + 0.5} {y - 5} V {y + 6}"/>'
            )
    parts.append(
        f'<g stroke="{C(CYAN)}" stroke-opacity="0.22" stroke-width="1" fill="none">{"".join(cross)}</g>'
    )
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="260" fill="url(#core)"/>')

    def arc(r, a0, a1, width, alpha, color=CYAN):
        x0, y0 = (
            cx + r * math.cos(math.radians(a0)),
            cy + r * math.sin(math.radians(a0)),
        )
        x1, y1 = (
            cx + r * math.cos(math.radians(a1)),
            cy + r * math.sin(math.radians(a1)),
        )
        large = 1 if (a1 - a0) % 360 > 180 else 0
        return (
            f'<path d="M {x0:.2f} {y0:.2f} A {r} {r} 0 {large} 1 {x1:.2f} {y1:.2f}" fill="none" '
            f'stroke="{C(color)}" stroke-opacity="{alpha:g}" stroke-width="{width:g}"/>'
        )

    rings = []
    for r, a in ((420, 0.06), (340, 0.09), (250, 0.12), (150, 0.16), (78, 0.22)):
        rings.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{C(CYAN)}" stroke-opacity="{a}" stroke-width="1"/>'
        )
    for a0 in range(0, 360, 60):
        rings.append(arc(300, a0 + 8, a0 + 44, 3, 0.16))
    for a0 in (20, 200):
        rings.append(arc(200, a0, a0 + 110, 2, 0.24))
    rings.append(arc(112, 300, 300 + 250, 1.5, 0.3))
    # Tick ring.
    for i in range(180):
        ang = math.radians(i * 2)
        r0 = 372 if i % 15 else 360
        a = 0.28 if i % 15 == 0 else 0.11
        rings.append(
            f'<line x1="{cx + r0 * math.cos(ang):.2f}" y1="{cy + r0 * math.sin(ang):.2f}" '
            f'x2="{cx + 380 * math.cos(ang):.2f}" y2="{cy + 380 * math.sin(ang):.2f}" '
            f'stroke="{C(CYAN)}" stroke-opacity="{a}" stroke-width="1"/>'
        )
    # Crosshair leader lines.
    rings.append(
        f'<path d="M {cx - 560} {cy + 0.5} H {cx - 440} M {cx + 440} {cy + 0.5} H {cx + 560}" stroke="{C(CYAN)}" stroke-opacity="0.18"/>'
    )
    rings.append(
        f'<path d="M {cx + 0.5} {cy - 520} V {cy - 440} M {cx + 0.5} {cy + 440} V {cy + 520}" stroke="{C(CYAN)}" stroke-opacity="0.18"/>'
    )
    parts.append("".join(rings))
    # Coordinates / labels (tiny, faint).
    labels = []
    font = 'font-family="Share Tech Mono" font-size="11" letter-spacing="1.5"'
    for x in range(200, width, 200):
        labels.append(f'<text x="{x + 6}" y="{height - 52}" {font}>X{x:04d}</text>')
    for y in range(200, height, 200):
        labels.append(f'<text x="14" y="{y - 6}" {font}>Y{y:04d}</text>')
    sector = "01" if screen == 0 else "02"
    labels.append(
        f'<text x="{cx + 392}" y="{cy - 12}" {font}>REACTOR OUTPUT // NOMINAL</text>'
        f'<text x="{cx + 392}" y="{cy + 8}" {font}>CORE FLUX 3.0 GJ/s</text>'
        f'<text x="40" y="56" font-family="Rajdhani" font-weight="600" font-size="13" letter-spacing="4">STARK OS // SECTOR {sector}</text>'
        f'<text x="40" y="74" {font}>J.A.R.V.I.S. DESKTOP GRID</text>'
    )
    parts.append(f'<g fill="{C(CYAN)}" fill-opacity="0.32">{"".join(labels)}</g>')
    # Corner brackets of the "screen instrument".
    m, b = 24, 40
    br = (
        f"M {m} {m + b} V {m} H {m + b} "
        f"M {width - m - b} {m} H {width - m} V {m + b} "
        f"M {width - m} {height - m - b} V {height - m} H {width - m - b} "
        f"M {m + b} {height - m} H {m} V {height - m - b}"
    )
    parts.append(
        f'<path d="{br}" fill="none" stroke="{C(CYAN)}" stroke-opacity="0.35" stroke-width="2"/>'
    )
    parts.append(f'<rect width="{width}" height="{height}" fill="url(#vig)"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        + "".join(parts)
        + "</svg>\n"
    )


def wallpapers(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    inkscape = shutil.which("inkscape")
    if not inkscape:
        raise SystemExit(
            "Inkscape is required to regenerate PNG wallpapers; existing files were kept."
        )
    for screen, name in ((0, "stark-os-sector-01"), (1, "stark-os-sector-02")):
        svg = root / f"{name}.svg"
        svg.write_text(wallpaper_svg(screen))
        if inkscape:
            subprocess.run(
                [
                    inkscape,
                    str(svg),
                    "--export-type=png",
                    f"--export-filename={root / (name + '.png')}",
                    "-w",
                    "1920",
                    "-h",
                    "1080",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


# ------------------------------------------------------------ main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wallpaper", action="store_true", help="also render wallpapers using Inkscape"
    )
    args = parser.parse_args()
    plasma_theme(HERE / "plasma" / "desktoptheme" / "stark-os")
    (HERE / "colors").mkdir(exist_ok=True)
    (HERE / "colors" / "StarkOS.colors").write_text(color_scheme())
    aurorae(HERE / "aurorae" / "stark-os")
    konsole(HERE / "konsole")
    splash_rings(
        HERE / "look-and-feel" / "org.jarvis.starkos" / "contents" / "splash" / "images"
    )
    # Supplemental assets preserve the original HUD and desktop generators.
    import build_plasma_extras
    import build_app_extras

    build_plasma_extras.main([])
    build_app_extras.main()
    if args.wallpaper:
        wallpapers(HERE / "wallpapers")
    print("STARK OS theme generated in", HERE)


if __name__ == "__main__":
    main()
