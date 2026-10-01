#!/usr/bin/env python3
"""Original STARK OS instrument icons, with optical sizes and Breeze fallback.

Pure standard-library SVG generation; optional --preview uses CairoSVG/Pillow.
Only writes assets to OUTPUT/icons/stark-os. Never changes live settings.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from xml.sax.saxutils import escape

INK = "#03101a"
# Icon-only optical palette. Keep the desktop/HUD palette unchanged: small
# pictograms need a brighter glass body and etching than full-size panels.
DEEP = "#317da2"
RAISE = "#52aacb"
INSET = "#183e56"
CYAN = "#9aeaff"
HOT = "#edfcff"
DIM = "#6fccea"
LINE = "#327590"
STRONG = "#5bb6d7"
AMBER = "#ffb347"
RED = "#ff4d5e"
GREEN = "#6dffb3"


def path(d, fill="none", stroke=CYAN, width=1.5, extra=""):
    return f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{width}" {extra}/>'


def circle(x, y, r, fill="none", stroke=CYAN, width=1.5, extra=""):
    return f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{width}" {extra}/>'


def rect(x, y, w, h, fill="none", stroke=CYAN, width=1.5, rx=0):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>'


def line(x1, y1, x2, y2, color=CYAN, width=1.5):
    return path(f"M{x1} {y1}L{x2} {y2}", stroke=color, width=width)


def point(r, angle, cx=64, cy=64):
    a = math.radians(angle)
    return (round(cx + r * math.cos(a), 3), round(cy + r * math.sin(a), 3))


def arc(r, start, end, color=CYAN, width=2, cx=64, cy=64):
    p1, p2 = point(r, start, cx, cy), point(r, end, cx, cy)
    return path(
        f"M{p1[0]} {p1[1]}A{r} {r} 0 {int(end - start > 180)} 1 {p2[0]} {p2[1]}",
        stroke=color,
        width=width,
    )


def ticks(r, count=48, length=3, color=DIM, cx=64, cy=64):
    result = ""
    for i in range(count):
        a = i * 360 / count
        x1, y1 = point(r, a, cx, cy)
        x2, y2 = point(r - (length + 2 if i % 4 == 0 else length), a, cx, cy)
        result += line(x1, y1, x2, y2, color, 1 if i % 4 else 1.5)
    return result


def screws(points):
    return "".join(
        circle(x, y, 2.2, INSET, STRONG, 0.8) + line(x - 1, y, x + 1, y, DIM, 0.65)
        for x, y in points
    )


def chamfer(x, y, w, h, cut=8):
    return f"M{x + cut} {y}H{x + w}V{y + h - cut}L{x + w - cut} {y + h}H{x}V{y + cut}Z"


def shell(d, highlight=None):
    # Dark outer contour retains icon separation on light and dark backgrounds.
    return (
        path(d, "none", INK, 4)
        + path(d, "url(#metal)", CYAN, 1.7)
        + (path(highlight, stroke=HOT, width=1) if highlight else "")
    )


def grid(x, y, w, h, step=8):
    return "".join(
        line(a, y, a, y + h, LINE, 0.7) for a in range(x, x + w + 1, step)
    ) + "".join(line(x, a, x + w, a, LINE, 0.7) for a in range(y, y + h + 1, step))


def circuit(x=20, y=96):
    return (
        path(f"M{x} {y}h15l5-5h12m6 0h10l5 5h17", stroke=STRONG, width=1)
        + circle(x, y, 1.7, INSET, DIM, 0.9)
        + circle(x + 90, y, 1.7, INSET, DIM, 0.9)
        + line(x + 27, y + 5, x + 54, y + 5, LINE, 1)
    )


def reactor():
    out = circle(64, 64, 56, "url(#metal)", INK, 4)
    out += circle(64, 64, 55, "url(#radial)", DIM, 1.4)
    out += circle(64, 64, 52, "none", STRONG, 0.75) + ticks(50, 60, 3)
    out += circle(64, 64, 43, INSET, CYAN, 0.8)
    for a in range(0, 360, 60):
        out += arc(42, a + 5, a + 48, CYAN, 3.5) + arc(37, a + 8, a + 44, DIM, 1.3)
        out += arc(55, a + 7, a + 34, HOT, 1.3)
    out += circle(64, 64, 33, "url(#glass)", STRONG, 1)
    out += ticks(31, 24, 2.3, CYAN) + circle(64, 64, 25, INSET, DIM, 1)
    out += path("M64 42L84 77H44Z", "url(#core)", CYAN, 2)
    out += path("M64 49L78 74H50Z", RAISE, HOT, 1) + path(
        "M64 55L73 71H55Z", HOT, "none"
    )
    out += "".join(circle(*point(47, a), 2, INSET, HOT, 0.7) for a in (0, 90, 180, 270))
    return out


def folder(kind="folder"):
    out = shell(
        "M15 30H46L57 40H109L115 46V101L106 111H17L10 104V36Z", "M16 30H46L57 40H109"
    )
    out += path("M18 36H43L53 46H105V98H18Z", RAISE, STRONG, 1)
    out += path("M20 40H43M21 43H40", stroke=DIM, width=1)
    out += path(
        "M12 49H48L56 44H111L117 51L109 109H17L10 103Z", "none", INK, 3.5
    )
    out += path(
        "M12 49H48L56 44H111L117 51L109 109H17L10 103Z", "url(#glass)", CYAN, 1.5
    )
    out += path("M16 54H47L57 49H108M16 103H104L109 70", stroke=HOT, width=0.8)
    out += path("M21 60H42M21 64H34M87 59H104M94 63H103", stroke=STRONG, width=1)
    out += screws([(20, 99), (102, 99)])
    out += path("M27 99h15l4-4h16m7 0h8l4 4h14", stroke=STRONG, width=1)
    symbols = {
        "folder": path("M46 72H79M46 77H71M46 82H75", stroke=CYAN, width=2)
        + rect(34, 70, 6, 15, INSET, DIM, 1),
        "documents": path("M51 60H69L77 68V88H51Z", INSET, CYAN, 1.7)
        + path("M69 60V69H77M56 74H71M56 79H71M56 84H67", stroke=CYAN, width=1.3),
        "download": path(
            "M64 59V81M54 72L64 82L74 72M50 84V90H78V84", stroke=HOT, width=3
        ),
        "pictures": rect(46, 61, 37, 28, INSET, CYAN, 1.5)
        + circle(73, 68, 3, HOT, "none")
        + path("M47 85L57 72L68 83L74 77L82 86", "url(#core)", CYAN, 1.2),
        "music": path("M62 81V64L79 60V78M62 68L79 64", stroke=HOT, width=2.5)
        + '<ellipse cx="57" cy="83" rx="6" ry="4" fill="#9aeaff"/><ellipse cx="74" cy="80" rx="6" ry="4" fill="#9aeaff"/>',
        "videos": rect(44, 62, 40, 28, INSET, DIM, 1.2)
        + path("M60 68L73 76L60 84Z", HOT, "none")
        + "".join(
            rect(x, y, 3, 2, CYAN, "none")
            for x in (48, 55, 62, 69, 76)
            for y in (64, 86)
        ),
        "projects": path("M48 78L64 68L80 78L64 88Z", RAISE, CYAN, 1.5)
        + path("M48 71L64 61L80 71L64 81ZM48 64L64 54L80 64L64 74Z", INSET, HOT, 1.5),
        "school": path(
            "M42 71L64 60L86 71L64 82ZM51 77V85Q64 94 77 85V77M85 72V88", INSET, CYAN, 1.8
        ),
    }
    return out + symbols.get(kind, symbols["folder"])


def document(kind="text"):
    out = shell("M31 11H77L103 37V109L95 117H27V15Z", "M32 12H76M28 20V110")
    out += path("M77 12V37H102", "url(#bevel)", CYAN, 1.4)
    out += path("M82 21V32H93", stroke=HOT, width=1)
    out += path("M35 22H66M35 27H54", stroke=STRONG, width=1.5)
    out += path("M35 105H83M35 110H60", stroke=DIM, width=1)
    out += rect(88, 103, 7, 7, INSET, STRONG, 0.8)
    if kind == "text":
        out += rect(35, 44, 24, 24, "url(#glass)", DIM, 1)
        out += path("M40 62L47 49L54 62M43 57H51", stroke=HOT, width=1.8)
        out += path(
            "M65 46H94M65 52H88M65 58H94M65 64H82M35 78H94M35 85H94M35 92H77",
            stroke=CYAN,
            width=1.5,
        )
    elif kind == "code":
        out += path(
            "M49 48L38 61L49 74M79 48L90 61L79 74M70 42L58 81", stroke=HOT, width=2.8
        )
        out += path("M37 90H58M65 90H91M37 96H79", stroke=DIM, width=1.3)
    elif kind == "spreadsheet":
        out += rect(36, 45, 57, 50, INSET, CYAN, 1.5)
        out += rect(36, 45, 57, 10, RAISE, "none")
        out += path(
            "M52 45V95M72 45V95M36 55H93M36 68H93M36 81H93", stroke=DIM, width=1
        )
        out += rect(54, 70, 16, 9, RAISE, HOT, 1)
    elif kind == "presentation":
        out += rect(36, 45, 56, 41, INSET, CYAN, 1.5)
        out += path(
            "M44 77V68H52V77M56 77V61H64V77M68 77V53H76V77", RAISE, CYAN, 1.5
        )
        out += path("M51 98L64 87L77 98M64 87V98", stroke=HOT, width=1.5)
    elif kind == "image":
        out += rect(36, 44, 57, 49, INSET, CYAN, 1.5) + circle(
            77, 57, 6, "url(#core)", CYAN, 1
        )
        out += path("M37 85L53 62L70 84L79 72L92 87V92H37Z", "url(#glass)", HOT, 1.5)
    elif kind == "pdf":
        out += path(
            "M43 85C71 54 58 29 55 45C49 71 99 76 85 70C71 62 33 80 41 87Z",
            "none",
            HOT,
            2.5,
        )
        out += path("M38 96H91", stroke=DIM)
    elif kind == "archive":
        out += rect(58, 42, 15, 43, INSET, DIM, 1)
        out += "".join(
            rect(59 if i % 2 else 66, 44 + i * 5, 6, 4, CYAN, "none") for i in range(7)
        )
        out += rect(58, 83, 15, 14, RAISE, HOT, 1.4) + rect(
            62, 87, 7, 6, INSET, CYAN, 1
        )
    elif kind == "new":
        out += path("M64 49V88M44 69H84", stroke=HOT, width=4)
    elif kind == "audio":
        out += path("M56 82V52L84 46V76M56 59L84 53", stroke=HOT, width=3)
        out += '<ellipse cx="48" cy="84" rx="8" ry="6" fill="#9aeaff"/><ellipse cx="76" cy="78" rx="8" ry="6" fill="#9aeaff"/>'
    elif kind == "video":
        out += rect(36, 47, 57, 43, INSET, CYAN, 1.5) + path(
            "M56 57L76 68L56 80Z", HOT, "none"
        )
        out += "".join(
            rect(x, y, 5, 3, DIM, "none")
            for x in (41, 52, 63, 74, 85)
            for y in (49, 85)
        )
    return out


def terminal():
    out = shell(chamfer(10, 21, 108, 89, 10), "M20 22H117M11 32V99")
    out += path("M11 37H117M17 103H104L111 96", stroke=DIM, width=1)
    out += "".join(
        circle(x, 29, 2, CYAN if x == 22 else DIM, "none") for x in (22, 31, 40)
    )
    out += path("M94 28H109M100 31H109", stroke=STRONG, width=1)
    out += rect(18, 44, 92, 50, INSET, LINE, 1) + grid(21, 47, 84, 42, 7)
    out += path("M30 55L44 68L30 81M54 81H74", stroke=HOT, width=4)
    out += path("M57 54H95M57 61H79M83 61H99M79 68H97", stroke=DIM, width=1.1)
    out += rect(92, 83, 9, 3, CYAN, "none") + screws([(21, 102), (108, 102)])
    return out


def gear():
    points = []
    for i in range(12):
        for d, r in ((-11, 45), (-8, 55), (8, 55), (11, 45)):
            points.append(point(r, i * 30 + d))
    shape = "M" + "L".join(f"{x} {y}" for x, y in points) + "Z"
    out = shell(shape) + circle(64, 64, 38, INSET, CYAN, 1.2) + ticks(34, 36, 2)
    out += circle(64, 64, 26, "url(#glass)", DIM, 1.5) + circle(64, 64, 15, INSET, HOT, 2)
    out += circle(64, 64, 11, "url(#radial)", STRONG, 1)
    for a in range(0, 360, 60):
        x, y = point(45, a)
        out += circle(x, y, 2, INSET, HOT, 0.75) + arc(39, a + 5, a + 25, CYAN, 2.5)
    return out


def globe():
    out = circle(64, 64, 51, "url(#radial)", INK, 4) + circle(
        64, 64, 50, "url(#radial)", CYAN, 1.7
    )
    out += '<ellipse cx="64" cy="64" rx="25" ry="49" fill="none" stroke="#6fccea" stroke-width="1.2"/>'
    out += '<ellipse cx="64" cy="64" rx="42" ry="49" fill="none" stroke="#327590" stroke-width="1"/>'
    out += path(
        "M17 45Q64 26 111 45M14 64H114M17 83Q64 102 111 83M64 15V113",
        stroke=DIM,
        width=1.2,
    )
    out += path(
        "M28 41L45 30L60 33L66 42L57 49L60 61L48 67L38 62L36 50Z", RAISE, CYAN, 1
    )
    out += path(
        "M72 57L88 49L99 60L91 73L80 76L77 94L68 104L65 80Z", RAISE, CYAN, 1
    )
    out += arc(55, 189, 254, HOT, 1.8) + arc(55, 10, 75, HOT, 1.8)
    out += circle(111, 94, 3, CYAN, INK, 1.5) + line(93, 90, 108, 94, CYAN, 1)
    return out


def mail():
    out = shell("M11 38L20 29H108L117 38V96L108 105H11Z", "M20 30H107M12 39V96")
    out += path("M16 39L64 76L112 39", "url(#glass)", CYAN, 1.8)
    out += path("M16 99L49 72M112 99L79 72", stroke=DIM, width=1.5)
    out += path("M23 39L64 69L104 39M23 97H49M80 97H104", stroke=HOT, width=0.9)
    out += path("M24 21H48M24 17H40M82 114H105", stroke=STRONG, width=1)
    out += rect(57, 84, 14, 7, INSET, STRONG, 1) + circle(64, 87.5, 1.5, CYAN, "none")
    return out


def camera():
    out = shell(
        "M10 42L19 33H39L46 21H79L86 33H109L117 41V99L107 108H10Z",
        "M19 34H39L47 22H79M11 43V99",
    )
    out += path("M44 29H80M15 47H34M98 46H113M16 99H37", stroke=STRONG, width=1)
    out += rect(94, 39, 15, 7, RAISE, CYAN, 1) + rect(20, 40, 11, 6, INSET, DIM, 1)
    out += circle(65, 71, 30, INSET, CYAN, 1.6) + ticks(27, 36, 2, DIM, 65, 71)
    out += circle(65, 71, 21, "url(#radial)", HOT, 1.4) + circle(
        65, 71, 16, INSET, DIM, 1
    )
    for a in range(0, 360, 60):
        p1, p2, p3 = (
            point(15, a, 65, 71),
            point(7, a + 60, 65, 71),
            point(15, a + 120, 65, 71),
        )
        out += path(
            f"M{p1[0]} {p1[1]}L{p2[0]} {p2[1]}L{p3[0]} {p3[1]}", stroke=CYAN, width=0.9
        )
    out += circle(60, 66, 3, HOT, "none") + screws([(20, 97), (107, 97)])
    return out


def computer(kind="computer"):
    out = shell(chamfer(10, 17, 108, 75, 8), "M18 18H117M11 25V81")
    out += rect(18, 25, 92, 54, INSET, STRONG, 1) + grid(21, 28, 84, 46, 7)
    if kind == "monitor":
        out += path(
            "M23 59H37L43 45L53 69L63 38L72 58H82L88 50H105", stroke=HOT, width=2.5
        )
    else:
        out += circle(64, 52, 18, "url(#radial)", DIM, 1) + arc(14, 15, 250, CYAN, 2.5)
        out += path("M64 43L73 59H55Z", HOT, "none")
        out += path("M25 33H40M25 37H34M89 66H103M94 70H103", stroke=DIM, width=1)
    out += path("M54 93V106H74V93M39 113L46 106H82L89 113Z", "url(#metal)", CYAN, 1.2)
    out += circle(64, 85, 2, CYAN, "none") + path("M94 85H107", stroke=STRONG, width=1)
    return out


def drive():
    out = shell(chamfer(24, 9, 80, 110, 8), "M32 10H103M25 18V108")
    out += circle(64, 54, 30, "url(#bevel)", CYAN, 1.2) + circle(
        64, 54, 24, "url(#metal)", DIM, 0.8
    )
    out += circle(64, 54, 17, "none", STRONG, 1) + circle(64, 54, 7, INSET, HOT, 1.6)
    out += path("M89 89L63 57L69 51L94 82Z", "url(#metal)", HOT, 1.3) + circle(
        91, 88, 7, INSET, DIM, 1.2
    )
    out += circle(91, 88, 3, "url(#core)", CYAN, 0.7)
    out += rect(34, 100, 41, 10, INSET, DIM, 1)
    out += "".join(line(x, 102, x, 108, CYAN, 1.4) for x in range(38, 73, 5))
    out += screws([(33, 19), (94, 19), (33, 111), (94, 111)])
    return out


def phone():
    out = shell(chamfer(35, 7, 58, 114, 9), "M44 8H92M36 17V111")
    out += rect(41, 23, 46, 79, INSET, STRONG, 1) + grid(43, 25, 42, 74, 7)
    out += path("M54 16H70", stroke=CYAN, width=2) + circle(77, 16, 1.5, CYAN, "none")
    out += circle(64, 64, 16, "url(#radial)", CYAN, 1)
    out += path("M64 54V74M56 61L64 53L72 61M53 85H75M53 90H65", stroke=CYAN, width=1.7)
    out += path("M54 111H74", stroke=HOT, width=2)
    return out


def printer():
    out = path("M33 14H94V57H33Z", "url(#metal)", INK, 4) + path(
        "M33 14H94V57H33Z", "url(#metal)", CYAN, 1.5
    )
    out += path("M41 25H83M41 32H75", stroke=DIM, width=1.5)
    out += shell(chamfer(10, 45, 108, 55, 8), "M18 46H117M11 53V90")
    out += path("M20 61H108M21 84H108", stroke=STRONG, width=1)
    out += circle(101, 54, 2.5, GREEN, INK, 0.7)
    out += path("M30 73H98L103 116H25Z", "url(#glass)", CYAN, 1.5)
    out += path("M40 88H88M39 96H89M39 104H75", stroke=HOT, width=1.4)
    out += screws([(20, 91), (108, 91)])
    return out


def calculator():
    out = shell(chamfer(25, 9, 78, 111, 8), "M33 10H102M26 18V110")
    out += rect(34, 20, 60, 25, INSET, CYAN, 1.3) + path(
        "M66 26H75V39H66ZM81 26H89V39H81Z", stroke=HOT, width=1.6
    )
    out += path("M40 39H52M40 34H47", stroke=DIM, width=1.4)
    for y in (56, 74, 92):
        for x in (35, 55, 75):
            out += rect(x, y, 16, 13, "url(#glass)", DIM, 1)
            if x == 75:
                out += line(x + 4, y + 6, x + 12, y + 6, HOT, 1.3)
                if y == 56:
                    out += line(x + 8, y + 2, x + 8, y + 10, HOT, 1.3)
            else:
                out += rect(x + 6, y + 5, 4, 3, CYAN, "none")
    out += path("M35 112H92", stroke=STRONG, width=1)
    return out


def home():
    out = path(
        "M10 58L64 12L118 58L110 68L100 60V114H28V60L18 68Z", "url(#metal)", INK, 4
    )
    out += path("M10 58L64 12L118 58L110 68L64 30L18 68Z", "url(#glass)", CYAN, 1.4)
    out += path("M28 57V114H100V57M34 65V106H45M84 106H94V65", stroke=DIM, width=1.4)
    out += path("M52 114V72L59 65H77V114", "url(#glass)", CYAN, 1.5)
    out += circle(71, 91, 1.8, HOT, "none") + path("M37 74H44V87H37Z", INSET, HOT, 1.2)
    out += path("M84 74H92V87H84Z", INSET, HOT, 1.2) + path(
        "M26 59L64 26L103 59", stroke=HOT, width=0.8
    )
    return out


def trash():
    out = shell("M28 37H100L94 111L85 117H39L33 110Z", "M30 42L35 108")
    out += path("M46 28V15H82V28M21 29H106L111 39H17Z", "url(#glass)", INK, 4)
    out += path("M46 28V15H82V28M21 29H106L111 39H17Z", "url(#glass)", CYAN, 1.4)
    out += path(
        "M52 26V21H76V26M42 50L46 103M64 50V103M86 50L82 103", stroke=DIM, width=2
    )
    out += path("M45 53L49 97M66 53V97M86 53L82 97", stroke=HOT, width=0.6)
    out += path("M44 108H83", stroke=STRONG, width=1)
    return out


def archive():
    out = shell("M15 40L64 16L114 40V101L65 120L15 100Z", "M16 40L64 17L113 40")
    out += path(
        "M15 40L64 63L114 40M64 64V118M29 48V89L52 99V59", stroke=DIM, width=1.5
    )
    out += path("M45 25L92 50L92 75L79 80V57L32 32Z", "url(#glass)", CYAN, 1.3)
    out += "".join(
        path(f"M{78 + i * 0.3} {59 + i * 5}l12-5", stroke=HOT, width=2)
        for i in range(4)
    )
    out += path(
        "M74 97L102 86M74 103L92 96M27 85L44 93M27 90L37 95", stroke=STRONG, width=1.2
    )
    return out


def headphones():
    out = path("M21 76V63C21 8 107 8 107 63V76", "none", INK, 14)
    out += path("M21 76V63C21 8 107 8 107 63V76", "none", DIM, 9)
    out += path("M24 59C24 15 104 15 104 59", stroke=HOT, width=1.8)
    out += shell(chamfer(13, 66, 29, 45, 7)) + shell(chamfer(86, 66, 29, 45, 7))
    out += rect(31, 70, 10, 36, INSET, CYAN, 1) + rect(87, 70, 10, 36, INSET, CYAN, 1)
    out += "".join(
        line(20, y, 25, y, DIM, 1.2) + line(103, y, 109, y, DIM, 1.2)
        for y in range(76, 104, 5)
    )
    return out


def microphone():
    out = shell(
        "M45 27C45 1 83 1 83 27V66C83 92 45 92 45 66Z", "M49 25C49 6 79 6 79 25"
    )
    out += "".join(path(f"M52 {y}H76", stroke=DIM, width=1.3) for y in range(28, 70, 6))
    out += path(
        "M34 61V67C34 107 94 107 94 67V61M64 98V114M47 116H81", stroke=INK, width=6
    )
    out += path(
        "M34 61V67C34 107 94 107 94 67V61M64 98V114M47 116H81", stroke=CYAN, width=2.2
    )
    out += circle(64, 21, 2, HOT, "none")
    return out


def clock():
    out = circle(64, 64, 53, "url(#metal)", INK, 4) + circle(
        64, 64, 52, "url(#metal)", DIM, 1.5
    )
    out += circle(64, 64, 46, INSET, CYAN, 1) + ticks(43, 60, 3)
    out += path("M64 35V64L84 80", stroke=HOT, width=3.5) + line(
        64, 64, 36, 81, CYAN, 1
    )
    out += circle(64, 64, 4, INSET, HOT, 1.5) + arc(49, 186, 267, HOT, 1.2)
    return out


def calendar():
    out = shell(chamfer(15, 23, 98, 90, 8), "M23 24H112M16 31V102")
    out += path("M16 46H112M37 16V33M91 16V33", stroke=CYAN, width=3)
    out += path("M37 16V31M91 16V31", stroke=HOT, width=1)
    for row in range(3):
        for col in range(4):
            x, y = 29 + col * 20, 58 + row * 17
            out += rect(
                x,
                y,
                10,
                9,
                RAISE if (col, row) == (2, 1) else INSET,
                HOT if (col, row) == (2, 1) else DIM,
                1,
            )
    out += path("M27 105H54M91 105H102", stroke=STRONG, width=1)
    return out


def code():
    out = shell("M17 36L30 27L59 49L94 13L114 23V106L94 116L59 81L30 101L17 92L44 65Z")
    out += path("M94 14V115L59 80L94 93V37L59 50Z", "url(#glass)", CYAN, 1.4)
    out += path("M30 28L72 65L30 100L18 92L45 65L18 36Z", RAISE, HOT, 1.1)
    out += path("M100 26V103M105 31V97M26 37L58 65L27 90", stroke=DIM, width=1)
    out += path("M94 19L109 27V101", stroke=HOT, width=0.8)
    return out


def brave():
    out = shell(
        "M34 15L49 22H79L94 15L104 37L115 50L108 87L88 110L64 120L40 110L20 87L13 50L24 37Z"
    )
    out += path(
        "M36 32L49 29H79L93 33L101 52L92 74L83 76L84 93L64 107L44 93L45 76L36 74L27 52Z",
        "url(#glass)",
        CYAN,
        1.4,
    )
    out += path(
        "M34 47L52 40L59 55L49 66L37 59ZM94 47L76 40L69 55L79 66L91 59Z", INSET, HOT, 1
    )
    out += path(
        "M50 75L64 68L78 75L70 86H58ZM58 86L64 96L70 86M46 87L52 95M82 87L76 95",
        INSET,
        CYAN,
        1.5,
    )
    out += path("M23 51L28 81L45 102M105 51L100 81L83 102", stroke=STRONG, width=1)
    return out


def discord():
    out = shell(
        "M31 30L49 24L53 33H75L79 24L97 30C109 47 117 77 113 94L91 106L82 94C70 98 58 98 46 94L37 106L15 94C11 77 19 47 31 30Z"
    )
    out += path("M37 42Q64 32 91 42M28 86Q64 105 100 86", stroke=CYAN, width=1.6)
    out += '<ellipse cx="45" cy="68" rx="8" ry="11" fill="#183e56" stroke="#edfcff" stroke-width="2"/><ellipse cx="83" cy="68" rx="8" ry="11" fill="#183e56" stroke="#edfcff" stroke-width="2"/>'
    out += '<ellipse cx="45" cy="67" rx="3" ry="6" fill="#9aeaff"/><ellipse cx="83" cy="67" rx="3" ry="6" fill="#9aeaff"/>'
    out += path(
        "M31 44L25 59M97 44L103 59M26 90L34 95M94 95L102 90", stroke=DIM, width=1
    )
    return out


def chat():
    out = shell(
        "M21 20H98L112 34V88L100 100H55L33 117V100H14V28Z", "M22 21H97M15 30V88"
    )
    out += path("M23 30H94L102 38V82L94 90H51L42 97V90H24Z", INSET, STRONG, 1)
    out += "".join(circle(x, 61, 4, "url(#core)", CYAN, 1) for x in (42, 64, 86))
    out += path("M30 39H51M77 81H94M31 81H40", stroke=DIM, width=1.1)
    return out


def obs():
    out = circle(64, 64, 54, "url(#metal)", INK, 4) + circle(
        64, 64, 53, "url(#metal)", DIM, 1.4
    )
    for rotation in (0, 120, 240):
        out += (
            f'<g transform="rotate({rotation} 64 64)">'
            + path(
                "M64 18C40 14 26 32 30 52C33 67 47 70 58 67C40 60 42 38 58 34C68 30 79 36 83 42C84 30 77 21 64 18Z",
                "url(#glass)",
                HOT,
                1.2,
            )
            + "</g>"
        )
    out += circle(64, 64, 7, INSET, CYAN, 1) + ticks(49, 36, 2)
    return out


def steam():
    out = circle(64, 64, 54, "url(#radial)", INK, 4) + circle(
        64, 64, 53, "url(#radial)", DIM, 1.4
    )
    out += path(
        "M19 78L48 91L69 70L93 56L78 32L57 55L49 72L26 60Z", "url(#metal)", CYAN, 1.5
    )
    out += circle(82, 46, 21, INSET, HOT, 2) + circle(82, 46, 13, RAISE, DIM, 1.3)
    out += circle(48, 84, 14, INSET, HOT, 2) + path(
        "M19 75L49 88C59 93 64 78 54 74L24 62", "url(#glass)", CYAN, 1.3
    )
    out += arc(49, 168, 258, HOT, 1) + arc(49, 6, 56, CYAN, 1)
    return out


def pen():
    out = document("text")
    out += path("M52 101L59 79L100 24L113 34L72 89Z", "url(#metal)", INK, 4)
    out += path("M52 101L59 79L100 24L113 34L72 89Z", "url(#metal)", CYAN, 1.4)
    out += path(
        "M59 79L72 89M94 32L107 43M98 35L63 82M52 101L62 96", stroke=HOT, width=1.5
    )
    return out


def library():
    out = path(
        "M18 24L49 15L58 110L28 116ZM61 17H88V112H61ZM91 32L109 28L120 106L102 110Z",
        "url(#metal)",
        INK,
        4,
    )
    out += path(
        "M18 24L49 15L58 110L28 116ZM61 17H88V112H61ZM91 32L109 28L120 106L102 110Z",
        "url(#metal)",
        CYAN,
        1.4,
    )
    out += path(
        "M24 37L49 29M28 88L53 82M65 34H84M65 96H84M97 45L108 42M105 95L116 92",
        stroke=HOT,
        width=1.2,
    )
    out += path("M35 45L38 78M73 43V85M103 52L108 84", stroke=DIM, width=2)
    return out


def shield():
    out = shell("M64 9L109 27V62C109 90 90 108 64 120C38 108 19 90 19 62V27Z")
    out += path(
        "M64 17L101 33V62C101 86 84 102 64 111C44 102 27 86 27 62V33Z", INSET, STRONG, 1
    )
    out += path("M49 61V48C49 27 79 27 79 48V61", "none", CYAN, 3)
    out += path(chamfer(42, 57, 44, 35, 5), "url(#glass)", HOT, 1.5)
    out += circle(64, 71, 4, INSET, CYAN, 1) + path("M62 74L61 83H67L66 74", CYAN, "none")
    return out


def simple(kind, large=True):
    """Sculpted standalone system/action shapes, no repeated icon backplate."""
    color = (
        AMBER
        if kind == "warning"
        else RED
        if kind == "error"
        else GREEN
        if kind == "ok"
        else CYAN
    )
    if kind in ("information", "ok", "error", "warning"):
        if kind == "warning":
            d = "M64 10L119 108H9Z"
            out = shell(d) + path(d, "url(#metal)", color, 2)
            out += path("M64 32L103 101H25Z", "none", color, 0.7)
            out += path("M64 47V77M64 89V93", stroke=HOT, width=5)
        elif kind == "error":
            d = "M42 12H86L116 42V86L86 116H42L12 86V42Z"
            out = shell(d) + path(d, "url(#metal)", color, 2)
            out += path(
                "M46 18H82L110 46V82L82 110H46L18 82V46Z", stroke=color, width=0.7
            )
            out += path("M44 44L84 84M44 84L84 44", stroke=HOT, width=4)
        else:
            out = (
                circle(64, 64, 53, "url(#metal)", INK, 4)
                + circle(64, 64, 52, "url(#metal)", color, 2)
                + circle(64, 64, 45, "none", color, 0.7)
            )
            out += (
                path("M64 56V92M64 34V39", stroke=HOT, width=5)
                if kind == "information"
                else path("M35 65L55 85L96 42", stroke=HOT, width=4)
            )
        return out
    drawings = {
        "find": circle(52, 52, 34, "url(#radial)", CYAN, 2)
        + circle(52, 52, 27, INSET, DIM, 1)
        + path("M77 77L88 79L115 105L105 115L79 88Z", "url(#metal)", HOT, 1.6)
        + path("M37 37H47M37 37V47M60 67H67V60", stroke=CYAN, width=1.1),
        "refresh": arc(43, 20, 305, CYAN, 5)
        + path("M87 16L98 42L72 39", "url(#glass)", HOT, 2)
        + arc(35, 57, 275, DIM, 1)
        + ticks(50, 36, 2),
        "save": shell("M22 13H98L115 30V114H13V22Z")
        + path("M33 14V48H91V14M31 114V69H98V114", "url(#glass)", CYAN, 1.7)
        + rect(73, 19, 11, 23, INSET, HOT, 1)
        + path("M40 81H87M40 90H87M40 99H75", stroke=DIM, width=1.5),
        "close": path(
            "M25 17L64 56L103 17L111 25L72 64L111 103L103 111L64 72L25 111L17 103L56 64L17 25Z",
            "url(#glass)",
            HOT,
            1.5,
        ),
        "menu": "".join(
            path(chamfer(17, y, 94, 13, 4), "url(#glass)", CYAN, 1.4)
            for y in (25, 58, 91)
        ),
        "next": path(
            "M13 55H78L53 30L64 19L110 64L64 109L53 98L78 73H13Z",
            "url(#glass)",
            HOT,
            1.6,
        ),
        "previous": '<g transform="translate(128 0) scale(-1 1)">'
        + path(
            "M13 55H78L53 30L64 19L110 64L64 109L53 98L78 73H13Z",
            "url(#glass)",
            HOT,
            1.6,
        )
        + "</g>",
        "up": '<g transform="rotate(-90 64 64)">'
        + path(
            "M13 55H78L53 30L64 19L110 64L64 109L53 98L78 73H13Z",
            "url(#glass)",
            HOT,
            1.6,
        )
        + "</g>",
        "wifi": path(
            "M13 39Q64-1 115 39M27 57Q64 26 101 57M42 76Q64 57 86 76",
            stroke=CYAN,
            width=7,
        )
        + circle(64, 97, 8, "url(#core)", HOT, 1.5),
        "bluetooth": path("M62 10V118L96 87L29 36M29 93L96 42Z", "url(#glass)", HOT, 3),
        "volume": path("M14 48H38L64 24V104L38 80H14Z", "url(#glass)", CYAN, 2)
        + path("M80 45Q98 64 80 83M94 31Q125 64 94 97", stroke=HOT, width=3)
        + path("M41 51L54 40V88L41 77", stroke=DIM, width=1.2),
        "battery": shell(chamfer(13, 36, 96, 59, 7))
        + rect(110, 53, 9, 24, RAISE, CYAN, 1.5)
        + "".join(rect(x, 45, 19, 41, "url(#glass)", CYAN, 1) for x in (23, 49, 75)),
        "power": arc(45, -60, 240, CYAN, 5)
        + path("M64 9V60", stroke=HOT, width=6)
        + arc(36, -42, 222, DIM, 1),
        "network": rect(44, 11, 40, 30, "url(#glass)", CYAN, 1.6)
        + path("M64 41V70M25 84V70H103V84", stroke=HOT, width=2.5)
        + "".join(rect(x, 84, 28, 26, "url(#glass)", CYAN, 1.5) for x in (11, 50, 89)),
    }
    out = drawings[kind]
    # Contrast contour behind open silhouettes without a background plate.
    if kind in (
        "find", "refresh", "close", "menu", "next", "previous", "up",
        "wifi", "bluetooth", "volume", "power", "network",
    ):
        import re

        dark = re.sub(r'stroke="#[a-fA-F0-9]{6}"', 'stroke="#03101a"', out)
        dark = re.sub(r'fill="[^"]+"', 'fill="none"', dark)
        dark = re.sub(
            r'stroke-width="([\d.]+)"',
            lambda m: f'stroke-width="{float(m[1]) + 3}"',
            dark,
        )
        out = dark + out
    return out


# A unique artwork is defined once; aliases match actual freedesktop/KDE icon names.
ART = {
    "reactor": reactor,
    "folder": folder,
    **{
        "folder-" + k: (lambda k=k: folder(k))
        for k in (
            "documents",
            "download",
            "pictures",
            "music",
            "videos",
            "projects",
            "school",
        )
    },
    **{
        "document-" + k: (lambda k=k: document(k))
        for k in (
            "text",
            "code",
            "spreadsheet",
            "presentation",
            "image",
            "pdf",
            "archive",
            "new",
            "audio",
            "video",
        )
    },
    "terminal": terminal,
    "gear": gear,
    "globe": globe,
    "mail": mail,
    "camera": camera,
    "computer": computer,
    "monitor": lambda: computer("monitor"),
    "drive": drive,
    "phone": phone,
    "printer": printer,
    "calculator": calculator,
    "home": home,
    "trash": trash,
    "archive": archive,
    "headphones": headphones,
    "microphone": microphone,
    "clock": clock,
    "calendar": calendar,
    "code": code,
    "brave": brave,
    "discord": discord,
    "chat": chat,
    "obs": obs,
    "steam": steam,
    "pen": pen,
    "library": library,
    "shield": shield,
    **{
        k: (lambda k=k: simple(k))
        for k in (
            "information",
            "warning",
            "error",
            "ok",
            "find",
            "refresh",
            "save",
            "close",
            "menu",
            "next",
            "previous",
            "up",
            "wifi",
            "bluetooth",
            "volume",
            "battery",
            "power",
            "network",
        )
    },
}

ENTRIES: dict[str, str] = {}


def names(context, artwork, *aliases):
    for name in aliases:
        ENTRIES[f"{context}/{name}"] = artwork


names(
    "apps",
    "reactor",
    "jarvis",
    "start-here",
    "start-here-kde",
    "org.jarvis.Jarvis",
    "org.jarvis.Command",
    "jarvis-command",
)
names("apps", "folder-projects", "jarvis-projects")
names("apps", "folder-school", "jarvis-school")
names("apps", "document-text", "jarvis-drafts")
names("places", "folder", "folder", "folder-open")
for kind in ("documents", "download", "pictures", "music", "videos"):
    names("places", "folder-" + kind, "folder-" + kind)
names("places", "folder-download", "folder-downloads")
names("places", "folder-projects", "folder-development")
names("places", "home", "user-home", "folder-home")
names("places", "trash", "user-trash", "user-trash-full")
names("devices", "computer", "computer", "video-display")
names(
    "devices",
    "drive",
    "drive-harddisk",
    "drive-harddisk-solidstate",
    "drive-removable-media",
)
names("devices", "phone", "phone", "smartphone")
names("devices", "printer", "printer")
names("devices", "headphones", "audio-headphones", "audio-headset")
names("devices", "microphone", "audio-input-microphone")
names("devices", "camera", "camera-photo", "camera-web")
names("devices", "network", "network-wired")
names("devices", "wifi", "network-wireless")
names("devices", "battery", "battery")
names(
    "apps",
    "terminal",
    "utilities-terminal",
    "konsole",
    "org.kde.konsole",
    "kitty",
    "org.wezfurlong.wezterm",
)
names(
    "apps",
    "folder",
    "system-file-manager",
    "org.kde.dolphin",
    "dolphin",
    "org.gnome.Nautilus",
)
names(
    "apps",
    "gear",
    "preferences-system",
    "systemsettings",
    "org.kde.systemsettings",
    "preferences-desktop",
    "preferences-desktop-theme",
)
names("apps", "globe", "web-browser", "internet-web-browser", "help-browser")
names("apps", "brave", "brave-browser", "com.brave.Browser")
names(
    "apps",
    "mail",
    "internet-mail",
    "kmail",
    "org.kde.kmail",
    "thunderbird",
    "org.mozilla.Thunderbird",
)
names(
    "apps",
    "camera",
    "spectacle",
    "org.kde.spectacle",
    "accessories-screenshot",
    "org.kde.kamoso",
)
names("apps", "calculator", "accessories-calculator", "kcalc", "org.kde.kcalc")
names(
    "apps",
    "monitor",
    "utilities-system-monitor",
    "org.kde.plasma-systemmonitor",
    "ksysguard",
    "hwinfo",
)
names("apps", "archive", "ark", "org.kde.ark", "utilities-file-archiver")
names("apps", "document-image", "gwenview", "org.kde.gwenview")
names("apps", "document-pdf", "okular", "org.kde.okular")
names(
    "apps",
    "pen",
    "kate",
    "org.kde.kate",
    "accessories-text-editor",
    "org.gnome.TextEditor",
)
names("apps", "code", "vscode", "code", "visual-studio-code", "com.visualstudio.code")
names("apps", "discord", "discord", "com.discordapp.Discord")
names("apps", "chat", "chatgpt", "org.openai.ChatGPT", "internet-chat")
names("apps", "obs", "com.obsproject.Studio", "obs", "obs-studio")
names("apps", "steam", "steam", "com.valvesoftware.Steam")
names("apps", "phone", "kdeconnect", "org.kde.kdeconnect")
names("apps", "headphones", "multimedia-audio-player", "elisa", "org.kde.elisa")
names(
    "apps",
    "calendar",
    "x-office-calendar",
    "org.kde.kalendar",
    "org.kde.merkuro.calendar",
)
names(
    "apps",
    "clock",
    "preferences-system-time",
    "org.kde.kclock",
    "preferences-system-time-date",
)
names("apps", "library", "libreoffice-startcenter")
names("apps", "document-text", "libreoffice-writer")
names("apps", "document-spreadsheet", "libreoffice-calc")
names("apps", "document-presentation", "libreoffice-impress")
names("apps", "document-image", "libreoffice-draw")
names(
    "apps",
    "shield",
    "kwalletmanager",
    "preferences-system-privacy",
    "preferences-system-network-proxy",
)
names("apps", "bluetooth", "preferences-system-bluetooth")
names("apps", "volume", "preferences-desktop-sound")
names("apps", "network", "preferences-system-network")
names("apps", "find", "kfind", "baloo")
names(
    "mimetypes",
    "document-text",
    "text-x-generic",
    "text-plain",
    "x-office-document",
    "application-vnd.oasis.opendocument.text",
)
names(
    "mimetypes",
    "document-code",
    "text-x-script",
    "text-x-python",
    "text-html",
    "application-json",
    "text-x-csrc",
    "text-x-c++src",
)
names(
    "mimetypes",
    "document-spreadsheet",
    "x-office-spreadsheet",
    "application-vnd.oasis.opendocument.spreadsheet",
)
names("mimetypes", "document-presentation", "x-office-presentation")
names("mimetypes", "document-pdf", "application-pdf")
names("mimetypes", "document-image", "image-x-generic")
names("mimetypes", "document-audio", "audio-x-generic")
names("mimetypes", "document-video", "video-x-generic")
names(
    "mimetypes",
    "document-archive",
    "application-zip",
    "application-x-compressed-tar",
    "application-x-7z-compressed",
    "package-x-generic",
)
for name, artwork in {
    "document-new": "document-new",
    "document-save": "save",
    "edit-find": "find",
    "view-refresh": "refresh",
    "go-next": "next",
    "go-previous": "previous",
    "go-up": "up",
    "window-close": "close",
    "application-menu": "menu",
    "system-shutdown": "power",
}.items():
    names("actions", artwork, name)
for name, artwork in {
    "dialog-information": "information",
    "dialog-warning": "warning",
    "dialog-error": "error",
    "dialog-ok": "ok",
    "dialog-password": "shield",
    "network-wireless-connected-100": "wifi",
    "audio-volume-high": "volume",
    "battery-full": "battery",
}.items():
    names("status", artwork, name)


def small(kind):
    """Optically simplified 32-unit drawings for 16/22px; no tiny instrument marks."""
    out = ""
    if kind.startswith("folder"):
        out = path("M3 8H12L15 11H28V26H3Z", DEEP, CYAN, 1.7) + path(
            "M3 13H28", stroke=DIM
        )
        k = kind.removeprefix("folder-")
        out += {
            "download": path("M16 15V22M12 19L16 23L20 19", stroke=HOT, width=1.7),
            "pictures": path("M7 23L12 17L18 23L22 19L26 23", stroke=HOT, width=1.5),
            "documents": path("M10 17H23M10 21H20", stroke=HOT, width=1.5),
            "music": path("M14 23V17L21 15V21", stroke=HOT, width=1.8)
            + circle(12, 23, 2, HOT, "none")
            + circle(19, 21, 2, HOT, "none"),
            "videos": path("M13 16L21 20L13 24Z", HOT, "none"),
            "school": path("M8 18L16 14L24 18L16 22ZM11 21V24H21V21", INSET, HOT, 1.2),
            "projects": path(
                "M9 20L16 16L23 20L16 24ZM9 16L16 12L23 16L16 20Z", INSET, HOT, 1.1
            ),
        }.get(k, path("M7 18H15M7 22H20", stroke=HOT, width=1.5))
    elif kind.startswith("document") or kind == "pen":
        out = path("M7 3H20L26 9V29H7Z", DEEP, CYAN, 1.5) + path(
            "M20 3V10H26", stroke=DIM, width=1.2
        )
        k = kind.removeprefix("document-")
        glyph = {
            "new": path("M16 14V24M11 19H21", stroke=HOT, width=1.8),
            "code": path(
                "M13 14L10 18L13 22M20 14L23 18L20 22M18 13L15 24",
                stroke=HOT,
                width=1.3,
            ),
            "image": path("M10 23L14 17L18 22L21 19L24 24H10Z", INSET, HOT, 1.2)
            + circle(21, 14, 1.5, HOT, "none"),
            "spreadsheet": path(
                "M10 14H23V25H10ZM16 14V25M10 19H23", stroke=HOT, width=1.2
            ),
            "presentation": path(
                "M10 14H23V23H10ZM16 23V27M12 20V18M16 20V16M20 20V15",
                stroke=HOT,
                width=1.2,
            ),
            "pdf": path(
                "M10 24C21 12 14 11 15 16C16 22 24 20 22 19C18 17 9 22 10 24Z",
                stroke=HOT,
                width=1.2,
            ),
            "archive": path("M16 12V22M13 22H19V26H13Z", stroke=HOT, width=1.5),
            "audio": path("M16 23V16L22 14V21", stroke=HOT, width=1.6)
            + circle(14, 24, 2, HOT, "none")
            + circle(20, 22, 2, HOT, "none"),
            "video": path("M13 14L23 19L13 25Z", HOT, "none"),
        }.get(k, path("M11 15H22M11 20H22M11 25H18", stroke=HOT, width=1.4))
        out += glyph
        if kind == "pen":
            out += path("M15 28L17 21L27 8L30 11L20 24Z", DEEP, HOT, 1.3)
    else:
        out = {
            "reactor": circle(16, 16, 13, DEEP, CYAN, 1.5)
            + circle(16, 16, 9, INSET, DIM, 1)
            + path("M16 9L22 21H10Z", HOT, CYAN, 1),
            "terminal": path("M5 4H29V25L25 29H3V6Z", DEEP, CYAN, 1.5)
            + path("M7 11L13 16L7 22M17 22H24", stroke=HOT, width=1.8),
            "gear": circle(16, 16, 10, DEEP, CYAN, 3)
            + circle(16, 16, 4, INSET, HOT, 1.5)
            + path(
                "M16 2V6M16 26V30M2 16H6M26 16H30M6 6L9 9M23 23L26 26M6 26L9 23M23 9L26 6",
                stroke=CYAN,
                width=3,
            ),
            "globe": circle(16, 16, 13, DEEP, CYAN, 1.5)
            + path(
                "M3 16H29M6 9H26M6 23H26M16 3C5 12 5 20 16 29C27 20 27 12 16 3Z",
                stroke=DIM,
                width=1.3,
            ),
            "brave": path(
                "M9 4L13 6H19L23 4L29 13L26 24L16 30L6 24L3 13Z", DEEP, CYAN, 1.5
            )
            + path(
                "M8 12L13 11L14 16L10 18ZM24 12L19 11L18 16L22 18ZM12 21L16 19L20 21L16 26Z",
                INSET,
                HOT,
                1,
            ),
            "mail": path(
                "M3 7H29V26H3ZM3 7L16 18L29 7M3 26L12 16M29 26L20 16", DEEP, CYAN, 1.5
            ),
            "camera": path("M3 9H10L12 5H20L23 9H29V27H3Z", DEEP, CYAN, 1.5)
            + circle(16, 18, 6, INSET, HOT, 1.5)
            + circle(16, 18, 3, DEEP, DIM, 1),
            "computer": path("M3 4H29V23H3ZM16 23V28M10 28H22M3 20H29", DEEP, CYAN, 1.5)
            + path("M16 8L21 17H11Z", HOT, "none"),
            "monitor": path("M3 4H29V23H3ZM16 23V28M10 28H22", DEEP, CYAN, 1.5)
            + path("M6 15H10L13 9L17 19L21 12H26", stroke=HOT, width=1.5),
            "drive": path("M7 2H26V30H7Z", DEEP, CYAN, 1.5)
            + circle(16, 12, 6, INSET, DIM, 1)
            + circle(16, 12, 2, INSET, HOT, 1)
            + path("M16 12L24 22M11 26H19", stroke=HOT, width=1.5),
            "phone": rect(9, 2, 14, 28, DEEP, CYAN, 1.5, 2)
            + path("M13 5H19M11 24H21M14 27H18", stroke=HOT, width=1.2),
            "printer": path(
                "M8 11V3H24V11M3 11H29V25H3ZM8 20H24V30H8Z", DEEP, CYAN, 1.5
            )
            + path("M11 24H21M11 27H18", stroke=HOT, width=1),
            "calculator": rect(7, 2, 19, 28, DEEP, CYAN, 1.5)
            + rect(10, 5, 13, 6, INSET, HOT, 1)
            + path(
                "M11 16H13M18 16H22M11 21H13M18 21H22M11 26H13M18 26H22",
                stroke=CYAN,
                width=2,
            ),
            "home": path(
                "M2 14L16 3L30 14M7 11V29H25V11M13 29V19H19V29", DEEP, CYAN, 1.5
            ),
            "trash": path(
                "M7 9H25M11 9V5H21V9M9 9L10 29H22L23 9M14 13V24M18 13V24",
                DEEP,
                CYAN,
                1.5,
            ),
            "archive": path(
                "M3 9L16 3L29 9V26L16 31L3 26ZM3 9L16 15L29 9M16 15V31M10 6L23 12V19",
                DEEP,
                CYAN,
                1.5,
            ),
            "headphones": path(
                "M5 20V15C5 0 27 0 27 15V20M4 18H10V29H4ZM22 18H28V29H22Z",
                DEEP,
                CYAN,
                1.5,
            ),
            "microphone": rect(11, 2, 10, 19, DEEP, CYAN, 1.5, 5)
            + path("M7 16V18C7 30 25 30 25 18V16M16 27V31", stroke=HOT, width=1.5),
            "clock": circle(16, 16, 13, DEEP, CYAN, 1.5)
            + path("M16 7V16L23 21", stroke=HOT, width=2),
            "calendar": rect(4, 6, 24, 24, DEEP, CYAN, 1.5)
            + path(
                "M4 12H28M10 2V9M22 2V9M9 17H12M19 17H22M9 24H12M19 24H22",
                stroke=HOT,
                width=2,
            ),
            "code": path(
                "M4 9L8 6L15 12L24 3L29 6V26L24 29L15 20L8 26L4 23L11 16ZM24 9L17 16L24 23Z",
                DEEP,
                CYAN,
                1.5,
            ),
            "discord": path(
                "M8 7L12 6L13 8H19L20 6L24 7L29 24L23 27L20 24H12L9 27L3 24Z",
                DEEP,
                CYAN,
                1.5,
            )
            + circle(11, 17, 2, HOT, "none")
            + circle(21, 17, 2, HOT, "none"),
            "chat": path("M4 4H25L29 8V24H13L7 29V24H3V6Z", DEEP, CYAN, 1.5)
            + "".join(circle(x, 14, 1.5, HOT, "none") for x in (9, 16, 23)),
            "obs": circle(16, 16, 13, DEEP, CYAN, 1.5)
            + "".join(
                f'<g transform="rotate({a} 16 16)">'
                + path(
                    "M16 5C8 3 5 11 10 15C11 9 18 8 21 12C22 8 19 5 16 5Z", HOT, "none"
                )
                + "</g>"
                for a in (0, 120, 240)
            ),
            "steam": circle(16, 16, 13, DEEP, CYAN, 1.3)
            + circle(21, 11, 5, INSET, HOT, 1.8)
            + circle(11, 23, 4, INSET, HOT, 1.5)
            + path("M17 14L12 19M5 20L12 24", stroke=HOT, width=3),
            "library": path(
                "M3 6L10 4L13 29L6 30ZM15 3H22V29H15ZM25 9H29V29H25Z", DEEP, CYAN, 1.4
            )
            + path("M5 11H10M16 9H21", stroke=HOT, width=1),
            "shield": path("M16 2L28 7V17Q28 25 16 31Q4 25 4 17V7Z", DEEP, CYAN, 1.5)
            + path("M12 15V11C12 5 20 5 20 11V15M10 15H22V24H10Z", INSET, HOT, 1.4),
            "information": circle(16, 16, 13, DEEP, CYAN, 1.5)
            + path("M16 14V25M16 7V9", stroke=HOT, width=2),
            "warning": path("M16 2L31 29H1Z", DEEP, AMBER, 1.5)
            + path("M16 11V21M16 24V26", stroke=HOT, width=2),
            "error": path("M10 2H22L30 10V22L22 30H10L2 22V10Z", DEEP, RED, 1.5)
            + path("M10 10L22 22M10 22L22 10", stroke=HOT, width=2),
            "ok": circle(16, 16, 13, DEEP, GREEN, 1.5)
            + path("M7 16L13 22L25 10", stroke=HOT, width=2),
            "find": circle(12, 12, 8, DEEP, CYAN, 1.8)
            + path("M18 18L29 29", stroke=HOT, width=3),
            "refresh": path(
                "M27 11A12 12 0 1 0 27 22M27 3V11H19", stroke=CYAN, width=2
            ),
            "save": path(
                "M5 3H25L29 7V29H3V5ZM9 3V12H23V3M9 29V19H23V29", DEEP, CYAN, 1.5
            ),
            "close": path("M7 7L25 25M7 25L25 7", stroke=HOT, width=2),
            "menu": path("M4 7H28M4 16H28M4 25H28", stroke=CYAN, width=2),
            "next": path("M3 16H29M19 6L29 16L19 26", stroke=HOT, width=2),
            "previous": path("M29 16H3M13 6L3 16L13 26", stroke=HOT, width=2),
            "up": path("M16 29V3M6 13L16 3L26 13", stroke=HOT, width=2),
            "wifi": path(
                "M3 10Q16 0 29 10M7 16Q16 8 25 16M11 22Q16 18 21 22",
                stroke=CYAN,
                width=2.5,
            )
            + circle(16, 27, 2, HOT, "none"),
            "bluetooth": path("M15 2V30L24 22L7 8M7 24L24 10Z", stroke=HOT, width=1.8),
            "volume": path("M3 12H9L16 5V27L9 20H3Z", DEEP, CYAN, 1.4)
            + path("M21 10Q27 16 21 22M25 5Q35 16 25 27", stroke=HOT, width=1.5),
            "battery": path(
                "M2 9H27V24H2ZM27 13H30V20H27M7 12V21M13 12V21M19 12V21",
                DEEP,
                CYAN,
                1.6,
            ),
            "power": path("M16 2V16M8 7A12 12 0 1 0 24 7", stroke=CYAN, width=2),
            "network": rect(11, 2, 10, 8, DEEP, CYAN, 1.4)
            + path("M16 10V17M6 23V17H26V23", stroke=HOT, width=1.5)
            + "".join(rect(x, 23, 8, 7, DEEP, CYAN, 1.2) for x in (2, 12, 22)),
        }[kind]
    if kind in {
        "find",
        "refresh",
        "close",
        "menu",
        "next",
        "previous",
        "up",
        "wifi",
        "bluetooth",
        "volume",
        "power",
        "network",
        "microphone",
        "headphones",
    }:
        import re

        outline = re.sub(r'stroke="#[a-fA-F0-9]{6}"', 'stroke="#03101a"', out)
        outline = re.sub(
            r'stroke-width="([\d.]+)"',
            lambda m: f'stroke-width="{float(m[1]) + 2}"',
            outline,
        )
        out = outline + out
    return out


DEFS = """<defs>
<linearGradient id="metal" x1="0" y1="0" x2="0.9" y2="1"><stop stop-color="#b4eeff" stop-opacity="0.86"/><stop offset="0.15" stop-color="#69c2e7" stop-opacity="0.76"/><stop offset="0.56" stop-color="#4399c3" stop-opacity="0.66"/><stop offset="1" stop-color="#28698f" stop-opacity="0.8"/></linearGradient>
<linearGradient id="glass" x1="0" y1="0" x2="0.35" y2="1"><stop stop-color="#b4eeff" stop-opacity="0.76"/><stop offset="0.45" stop-color="#65c5ed" stop-opacity="0.5"/><stop offset="1" stop-color="#398fb6" stop-opacity="0.64"/></linearGradient>
<linearGradient id="bevel" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#c8f5ff" stop-opacity="0.92"/><stop offset="0.45" stop-color="#4b9fc7" stop-opacity="0.7"/><stop offset="0.5" stop-color="#89dcf7" stop-opacity="0.86"/><stop offset="1" stop-color="#317da2" stop-opacity="0.82"/></linearGradient>
<linearGradient id="core" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#edfcff"/><stop offset="0.45" stop-color="#9aeaff"/><stop offset="1" stop-color="#6fccea"/></linearGradient>
<radialGradient id="radial"><stop stop-color="#9deaff" stop-opacity="0.8"/><stop offset="0.65" stop-color="#4dadd5" stop-opacity="0.65"/><stop offset="1" stop-color="#276b94" stop-opacity="0.85"/></radialGradient>
</defs>"""


def svg(kind, size=128):
    view = 128 if size >= 32 else 32
    body = ART[kind]() if size >= 32 else small(kind)
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {view} {view}"><title>STARK OS {escape(kind)}</title>{DEFS if view == 128 else ""}<g stroke-linejoin="round" stroke-linecap="round">{body}</g></svg>\n'


def icons(root: Path) -> None:
    """Write a complete named icon overlay, with 16/22px optical-size variants."""
    base = root / "icons/stark-os"
    contexts = {
        "apps": "Applications",
        "places": "Places",
        "devices": "Devices",
        "mimetypes": "MimeTypes",
        "actions": "Actions",
        "status": "Status",
    }
    directories = [
        f"{size}/{context}"
        for size in ("16x16", "22x22", "scalable")
        for context in contexts
    ]
    index = (
        "[Icon Theme]\nName=STARK OS\nComment=Detailed holographic instruments with optical sizes\nInherits=breeze-dark,breeze,hicolor\nDirectories="
        + ",".join(directories)
        + "\n"
    )
    for size, px in (("16x16", 16), ("22x22", 22), ("scalable", 128)):
        for context, label in contexts.items():
            index += f"\n[{size}/{context}]\nSize={px}\nContext={label}\n"
            index += (
                "Type=Scalable\nMinSize=32\nMaxSize=512\n"
                if size == "scalable"
                else "Type=Fixed\n"
            )
        cache = {kind: svg(kind, px) for kind in ART}
        for name, kind in ENTRIES.items():
            target = base / size / f"{name}.svg"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(cache[kind], encoding="utf-8")
    (base / "index.theme").write_text(index, encoding="utf-8")
    (base / "README.md").write_text(
        f"""# STARK OS instrument icons

{len(ART)} original vector designs; {len(ENTRIES)} named icons in each of three optical sizes
(16px, 22px, and a detailed scalable master designed on a 128px grid).

The artwork keeps STARK OS geometry and uses an icon-specific light-blue
hologram palette: translucent ice-blue glass, luminous bevel edges, concentric
reactor rings, fine etched details, and distinct silhouettes for folders,
documents, hardware, and applications. Thin dark keylines separate the bright
silhouettes from light backgrounds. The desktop/HUD palette is unchanged.
Semantic warning, error, and success icons retain amber, red, and green.
16/22px variants use brighter blue bodies and larger, simpler forms without
tiny etch marks, retaining their contrast at native toolbar and tray sizes.
All icons have transparent backgrounds; Qt-compatible vector gradients,
paths, and shapes only. No SVG filters, fonts, animation, scripts, or external
resources are used. The system inherits Breeze for unimplemented names.

Common KDE and installed desktop names are covered, including Dolphin,
Konsole, System Settings, KCalc, Spectacle, Ark, Kate, Gwenview, Okular,
KDE Connect, Plasma System Monitor, Brave, VS Code, Discord, OBS, Steam,
LibreOffice, mail, calendar, and generic documents. Third-party familiar
silhouettes are restyled original vector drawings; ChatGPT uses a neutral
conversation symbol. These are theme artwork, not official brand assets.

Theme lookup cannot replace icons embedded in application canvases, browser
websites, sandbox runtimes without the host theme, tray-provided bitmap pixmaps,
or desktop launchers that specify an absolute icon path. Those need an app's
own supported theme mechanism; this generator does not rewrite apps or
security interfaces. Uncovered named icons inherit Breeze rather than a
misleading generic substitute. Cursors are a separate theme.

Regenerate using `python3 desktop/build_icon_extras.py --output PATH`.
The generator writes only `PATH/icons/stark-os`; it never applies settings.
Preview generation is optional and uses CairoSVG and Pillow.
""",
        encoding="utf-8",
    )


def preview(target: Path):
    import io

    import cairosvg
    from PIL import Image, ImageDraw, ImageFont

    # All unique shapes, at both detailed sizes and both backgrounds. A second
    # full row per group checks exact native small sizes without enlargement.
    kinds = list(ART)
    cell_w, cell_h, columns = 246, 264, 6
    rows = math.ceil(len(kinds) / columns)
    canvas = Image.new("RGB", (columns * cell_w, rows * cell_h + 104), INK)
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype(
            str(
                Path(__file__).resolve().parent.parent
                / "design/fonts/Rajdhani-SemiBold.ttf"
            ),
            15,
        )
        title = ImageFont.truetype(
            str(
                Path(__file__).resolve().parent.parent
                / "design/fonts/Orbitron-Variable.ttf"
            ),
            23,
        )
    except OSError:
        font = ImageFont.load_default()
        title = font
    draw.text((22, 17), "STARK OS / LIGHT-BLUE HOLOGRAM ICONS", fill=HOT, font=title)
    draw.text(
        (22, 54),
        f"{len(ART)} designs  •  {len(ENTRIES)} names  •  128 + 64 px masters  •  22 + 16 px optical variants",
        fill=CYAN,
        font=font,
    )
    for i, kind in enumerate(kinds):
        x = (i % columns) * cell_w
        y = 104 + (i // columns) * cell_h
        draw.rectangle(
            (x + 3, y + 3, x + cell_w - 4, y + cell_h - 4), fill="#061826", outline="#16414f"
        )
        draw.text((x + 12, y + 10), kind.upper(), fill=CYAN, font=font)
        for px, at, bg in (
            (128, (x + 7, y + 35), INK),
            (64, (x + 158, y + 41), INK),
            (64, (x + 158, y + 119), "#e2eaf0"),
        ):
            tile = Image.new("RGBA", (px, px), bg)
            rendered = Image.open(
                io.BytesIO(
                    cairosvg.svg2png(
                        bytestring=svg(kind).encode(), output_width=px, output_height=px
                    )
                )
            ).convert("RGBA")
            tile.alpha_composite(rendered)
            canvas.paste(tile.convert("RGB"), at)
        for index, size in enumerate((22, 16)):
            for j, bg in enumerate((INK, "#e2eaf0")):
                at = (x + 16 + index * 74 + j * 32, y + 202)
                tile = Image.new("RGBA", (28, 28), bg)
                rendered = Image.open(
                    io.BytesIO(
                        cairosvg.svg2png(
                            bytestring=svg(kind, size).encode(),
                            output_width=size,
                            output_height=size,
                        )
                    )
                ).convert("RGBA")
                tile.alpha_composite(rendered, ((28 - size) // 2, (28 - size) // 2))
                canvas.paste(tile.convert("RGB"), at)
        draw.text(
            (x + 16, y + 239),
            "22px dark/light     16px dark/light",
            fill="#7fa6b8",
            font=font,
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target)
    hero = Image.new("RGB", (1200, 640), INK)
    painter = ImageDraw.Draw(hero)
    for x in range(0, 1200, 40):
        painter.line((x, 0, x, 640), fill="#092330", width=1)
    for y in range(0, 640, 40):
        painter.line((0, y, 1200, y), fill="#092330", width=1)
    painter.text(
        (28, 18), "STARK OS / LIGHT-BLUE HOLOGRAM ICONS", fill=HOT, font=title
    )
    painter.text(
        (28, 51),
        "Original vector detail · translucent ice-blue glass · luminous etching · optical sizes",
        fill=CYAN,
        font=font,
    )
    featured = ["reactor", "folder-projects", "terminal", "camera", "gear", "brave"]
    for i, kind in enumerate(featured):
        x = (i % 3) * 400
        y = 84 + (i // 3) * 274
        painter.rectangle((x + 17, y + 4, x + 382, y + 261), fill="#061826", outline="#16414f")
        painter.line((x + 17, y + 24, x + 17, y + 4, x + 37, y + 4), fill=CYAN, width=1)
        rendered = Image.open(
            io.BytesIO(
                cairosvg.svg2png(
                    bytestring=svg(kind).encode(), output_width=210, output_height=210
                )
            )
        ).convert("RGBA")
        hero.paste(rendered, (x + 95, y + 16), rendered)
        label = kind.upper().replace("-", " / ")
        painter.text((x + 24, y + 239), label, fill=CYAN, font=font)
        painter.text((x + 336, y + 239), f"{i + 1:02}", fill="#7fa6b8", font=font)
    hero.save(target.with_name(target.stem + "-detail.png"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).resolve().parent / "app-extras"
    )
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    icons(args.output)
    if args.preview:
        preview(args.preview)
    print(
        f"Generated {len(ART)} designs / {len(ENTRIES)} named icons / {len(ENTRIES) * 3} SVGs in {args.output / 'icons/stark-os'}"
    )
