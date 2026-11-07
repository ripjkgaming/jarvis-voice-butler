"""Generate LOCKE//LINK icon SVGs + part-based mark glyph source. Output dir = argv[1]."""
import json
import math
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

CYAN, ICE, AMBER, TEAL = "#5fe3ff", "#e8fbff", "#ffb347", "#2ee6d6"
NAVY_HI, NAVY_LO = "#12295c", "#04081a"
C = 54.0  # canvas centre (108 dp adaptive canvas)
RING_R = 22.0
N = 16  # core polygon vertex count (fixed topology)


def f(x):
    return f"{x:.2f}".rstrip("0").rstrip(".")


def polar(r, deg):
    a = math.radians(deg - 90)  # 0 deg = north, clockwise
    return (r * math.cos(a), r * math.sin(a))


def star():
    pts = []
    for k in range(N):
        r = 3.2 if k % 2 else (15.0 if k % 4 == 0 else 8.0)
        pts.append(polar(r, k * 360 / N))
    return pts


def circle(r):
    return [polar(r, k * 360 / N) for k in range(N)]


def tri(corners, split=(6, 5, 5)):
    """16 points on a triangle; every corner is a sample (corners stay sharp)."""
    pts = []
    for i, n in enumerate(split):
        (x0, y0), (x1, y1) = corners[i], corners[(i + 1) % 3]
        for j in range(n):
            t = j / n
            pts.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return pts


GLYPHS = {
    # idle: compass / reactor star (also the launcher icon)
    "star": {
        "ring": {"color": CYAN, "dash": 0},
        "core": {"fill": ICE, "points": star()},
        "sats": [
            {"x": polar(RING_R, d)[0], "y": polar(RING_R, d)[1], "scale": 0.6,
             "rotate": d, "opacity": 0.9}
            for d in (45, 135, 225, 315)
        ],
    },
    # listening: orbit-dot ring (runtime: sats rotate, radius follows mic RMS)
    "orbit": {
        "ring": {"color": CYAN, "dash": 0},
        "core": {"fill": ICE, "points": circle(4.0)},
        "sats": [
            {"x": polar(14, d)[0], "y": polar(14, d)[1], "scale": 1.0, "rotate": 0,
             "opacity": 1.0}
            for d in (0, 90, 180, 270)
        ],
    },
    # thinking: amber triangle / flame
    "flame": {
        "ring": {"color": CYAN, "dash": 0},
        "core": {"fill": AMBER, "points": tri([(0, -12), (10.4, 6), (-10.4, 6)])},
        "sats": [{"x": 0, "y": 0, "scale": 0, "rotate": 0, "opacity": 0} for _ in range(4)],
    },
    # speaking: play triangle in a teal ring
    "play": {
        "ring": {"color": TEAL, "dash": 0},
        "core": {"fill": TEAL, "points": tri([(-6, -10.5), (11.5, 0), (-6, 10.5)])},
        "sats": [{"x": 0, "y": 0, "scale": 0, "rotate": 0, "opacity": 0} for _ in range(4)],
    },
}
MODIFIERS = {
    "locked": {"ring": {"color": ICE, "dash": 0}, "core": {"fill": ICE, "points": circle(2.2)},
               "sats": "zero"},
    "guest": {"ring": {"color": AMBER, "dash": [3.0, 2.2]}, "core": "inherit", "sats": "inherit"},
}


def d_of(points, cx=C, cy=C):
    return "M" + " L".join(f"{f(cx + x)} {f(cy + y)}" for x, y in points) + " Z"


def glow_ring(r, color, w=2.4, cx=C, cy=C):
    # VectorDrawable-safe glow: stacked translucent strokes, no filters.
    s = ""
    for width, op in ((9.0, 0.07), (6.0, 0.13), (3.8, 0.28)):
        s += f'<circle cx="{f(cx)}" cy="{f(cy)}" r="{f(r)}" fill="none" stroke="{color}" stroke-width="{f(width)}" stroke-opacity="{op}"/>'
    s += f'<circle cx="{f(cx)}" cy="{f(cy)}" r="{f(r)}" fill="none" stroke="{color}" stroke-width="{f(w)}"/>'
    return s


def sparkle(x, y, s, color=ICE, op=0.9):
    # 4-point sparkle star
    p = [(0, -s), (s * 0.18, -s * 0.18), (s, 0), (s * 0.18, s * 0.18), (0, s),
         (-s * 0.18, s * 0.18), (-s, 0), (-s * 0.18, -s * 0.18)]
    d = "M" + " L".join(f"{f(x + a)} {f(y + b)}" for a, b in p) + " Z"
    return f'<path d="{d}" fill="{color}" fill-opacity="{op}"/>'


def diamond(x, y, s, rot, color):
    p = [(0, -s), (s * 0.55, 0), (0, s), (-s * 0.55, 0)]
    a = math.radians(rot)
    q = [(x + px * math.cos(a) - py * math.sin(a), y + px * math.sin(a) + py * math.cos(a)) for px, py in p]
    return f'<path d="M{" L".join(f"{f(u)} {f(v)}" for u, v in q)} Z" fill="{color}"/>'


def svg(body, defs="", vb="0 0 108 108", size=108):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="{vb}">'
            + (f"<defs>{defs}</defs>" if defs else "") + body + "</svg>\n")


BG_DEFS = (
    f'<radialGradient id="bg" cx="0.38" cy="0.32" r="0.85">'
    f'<stop offset="0" stop-color="{NAVY_HI}"/><stop offset="1" stop-color="{NAVY_LO}"/></radialGradient>'
    '<radialGradient id="sheen" cx="0.3" cy="0.22" r="0.42">'
    '<stop offset="0" stop-color="#9fdcff" stop-opacity="0.16"/>'
    '<stop offset="1" stop-color="#9fdcff" stop-opacity="0"/></radialGradient>'
)
# sparkles at +-23 dp diagonal from centre: inside the 72 dp visible circle (r 36) for any mask
SPARKLES = (sparkle(C - 23, C - 23, 3.2) + sparkle(C + 23, C - 23, 2.2, op=0.7)
            + sparkle(C + 23, C + 23, 3.0) + sparkle(C - 23, C + 23, 2.0, op=0.65))


def background_body():
    return '<rect width="108" height="108" fill="url(#bg)"/><rect width="108" height="108" fill="url(#sheen)"/>' + SPARKLES


def star_mark(cx=C, cy=C):
    g = GLYPHS["star"]
    body = glow_ring(RING_R, CYAN, cx=cx, cy=cy)
    body += f'<circle cx="{f(cx)}" cy="{f(cy)}" r="18.2" fill="none" stroke="{ICE}" stroke-width="0.6" stroke-opacity="0.45"/>'
    for s in g["sats"]:
        body += diamond(cx + s["x"], cy + s["y"], 2.2, s["rotate"], CYAN)
    # star glow + body
    body += f'<path d="{d_of(g["core"]["points"], cx, cy)}" fill="{CYAN}" fill-opacity="0.22" stroke="{CYAN}" stroke-width="2.6" stroke-opacity="0.18" stroke-linejoin="round"/>'
    body += f'<path d="{d_of(g["core"]["points"], cx, cy)}" fill="{ICE}" fill-opacity="0.92" stroke="{CYAN}" stroke-width="0.7" stroke-linejoin="round"/>'
    body += f'<circle cx="{f(cx)}" cy="{f(cy)}" r="2.6" fill="{ICE}"/>'
    body += f'<circle cx="{f(cx)}" cy="{f(cy)}" r="1.15" fill="{AMBER}"/>'
    return body


def squircle_path(x0, y0, w, n=5.0, steps=96):
    cx, cy, a = x0 + w / 2, y0 + w / 2, w / 2
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        pts.append((cx + a * math.copysign(abs(c) ** (2 / n), c), cy + a * math.copysign(abs(s) ** (2 / n), s)))
    return "M" + " L".join(f"{f(x)} {f(y)}" for x, y in pts) + " Z"


files = {}
files["ic_launcher_background.svg"] = svg(background_body(), BG_DEFS)
files["ic_launcher_foreground.svg"] = svg(star_mark())

# monochrome: one flat path (annulus ring + 4 diamonds + star); launcher tints it
ring_outer, ring_inner = RING_R + 1.4, RING_R - 1.4
def annulus_half(r, sweep):
    return (f"M{f(C)} {f(C - r)} A{f(r)} {f(r)} 0 0 {sweep} {f(C)} {f(C + r)} "
            f"A{f(r)} {f(r)} 0 0 {sweep} {f(C)} {f(C - r)} Z ")


mono = annulus_half(ring_outer, 1) + annulus_half(ring_inner, 0) + d_of(GLYPHS["star"]["core"]["points"])
files["ic_launcher_monochrome.svg"] = svg(f'<path d="{mono}" fill="#ffffff" fill-rule="evenodd"/>')

files["icon-maskable.svg"] = svg(background_body() + star_mark(), BG_DEFS, size=512)
tile = squircle_path(6, 6, 96)
sq_defs = BG_DEFS + f'<clipPath id="tile"><path d="{tile}"/></clipPath>'
files["icon-squircle.svg"] = svg(
    f'<g clip-path="url(#tile)">{background_body()}</g>'
    f'<path d="{tile}" fill="none" stroke="{CYAN}" stroke-opacity="0.35" stroke-width="1"/>'
    + star_mark()
    + sparkle(10, 10, 3.0) + sparkle(98, 98, 2.6),
    sq_defs, size=512)


# morph source (JSON, coordinates relative to mark centre, dp at the 108 canvas)
def rnd(v):
    return round(v, 3)


src = {
    "version": 1,
    "units": "dp on the 108 dp adaptive canvas; origin = mark centre; +y down; angles clockwise from north",
    "topology": {"ring": 1, "satellites": 4, "core_vertices": N,
                 "rule": "every glyph has exactly these parts; unused satellites have scale 0 and opacity 0"},
    "ring_radius": RING_R,
    "palette": {"cyan": CYAN, "ice": ICE, "amber": AMBER, "teal": TEAL, "navy_hi": NAVY_HI, "navy_lo": NAVY_LO},
    "states": {"idle": "star", "listening": "orbit", "thinking": "flame", "speaking": "play"},
    "glyphs": {
        k: {"ring": g["ring"], "core": {"fill": g["core"]["fill"],
                                        "points": [[rnd(x), rnd(y)] for x, y in g["core"]["points"]]},
            "sats": [{kk: rnd(vv) for kk, vv in s.items()} for s in g["sats"]]}
        for k, g in GLYPHS.items()
    },
    "modifiers": {
        "locked": {"ring": MODIFIERS["locked"]["ring"], "core": {"fill": ICE, "points": [[rnd(x), rnd(y)] for x, y in circle(2.2)]},
                   "sats": "all scale 0 opacity 0"},
        "guest": {"ring": MODIFIERS["guest"]["ring"], "core": "unchanged", "sats": "unchanged"},
    },
}
files["mark-glyphs.json"] = json.dumps(src, indent=1) + "\n"

# preview sheet: 5 tiles (star, orbit, flame, play, locked) rendered from the JSON parts
cells = ""
order = [("star", None), ("orbit", None), ("flame", None), ("play", None), ("star", "locked"), ("orbit", "guest")]
for i, (name, mod) in enumerate(order):
    ox, oy = i * 108, 0
    cx, cy = ox + 54, oy + 54
    g = GLYPHS[name]
    ring = g["ring"]
    core = g["core"]
    sats = g["sats"]
    if mod == "locked":
        ring, core, sats = MODIFIERS["locked"]["ring"], MODIFIERS["locked"]["core"], [{"x": 0, "y": 0, "scale": 0, "rotate": 0, "opacity": 0}] * 4
    if mod == "guest":
        ring = MODIFIERS["guest"]["ring"]
    tile_d = squircle_path(ox + 6, oy + 6, 96)
    cells += f'<path d="{tile_d}" fill="url(#bg)"/><path d="{tile_d}" fill="none" stroke="{CYAN}" stroke-opacity="0.3"/>'
    dash = f' stroke-dasharray="{ring["dash"][0]} {ring["dash"][1]}"' if ring["dash"] else ""
    cells += f'<circle cx="{cx}" cy="{cy}" r="{RING_R}" fill="none" stroke="{ring["color"]}" stroke-width="6" stroke-opacity="0.15"{dash}/>'
    cells += f'<circle cx="{cx}" cy="{cy}" r="{RING_R}" fill="none" stroke="{ring["color"]}" stroke-width="2.4"{dash}/>'
    for s in sats:
        if s["opacity"] > 0:
            if name == "star":
                cells += diamond(cx + s["x"], cy + s["y"], 2.2 * s["scale"] / 0.6, s["rotate"], CYAN)
            else:
                cells += f'<circle cx="{f(cx + s["x"])}" cy="{f(cy + s["y"])}" r="{f(2.4 * s["scale"])}" fill="{CYAN}" opacity="{s["opacity"]}"/>'
    cells += f'<path d="{d_of(core["points"], cx, cy)}" fill="{core["fill"]}" stroke="{core["fill"]}" stroke-width="0.6" stroke-linejoin="round"/>'
    label = name if not mod else f"{name}+{mod}"
    cells += f'<text x="{cx}" y="{oy + 118}" fill="{ICE}" font-family="monospace" font-size="8" text-anchor="middle">{label}</text>'
files["mark-glyphs.svg"] = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{108 * len(order) * 2}" height="248" viewBox="0 0 {108 * len(order)} 124">'
                            f'<defs>{BG_DEFS}</defs><rect width="100%" height="100%" fill="#02040c"/>{cells}</svg>\n')

for name, content in files.items():
    (OUT / name).write_text(content)
    print(name, len(content))
