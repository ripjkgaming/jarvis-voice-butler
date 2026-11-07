"""Render the cinematic jarvis-ascii Plymouth theme.

Everything on screen is a glyph cell from the deck ASCII engine, rasterised
here because Plymouth only draws images. Output:

  frame-N.png   1917x810 scene (213x45 cells), 20 fps. 0..INTRO-1 play once,
                INTRO..INTRO+LOOP-1 loop. INTRO is a multiple of LOOP, so every
                loop-phase motion (reactor, orbits, HUD) runs straight through.
  bar-N.png     progress bar, BAR cells
  px-*.png      1x1 solids the script scales into flashes / fades / hairlines
  overlay.png   vignette + scanlines + grain, jittered by the script
  jarvis-ascii.script  from jarvis-ascii.script.in with the timing filled in

Timeline (s): 0.2 spark -> 1.3 anamorphic flare -> 3.3 warp tunnel ->
DROP flash + shockwave + reactor assembles -> HUD panels -> helmet scan ->
block wordmark assembles from particles -> loop.

usage: python3 gen_frames.py OUT_DIR
"""
import math
import multiprocessing as mp
import os
import sys

DECK = os.path.expanduser("~/deck/lib")
sys.path.insert(0, DECK)
import jarvis_ascii as J  # noqa: E402
import jarvis_launch as L  # noqa: E402
import jarvis_raster as RR  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
COLS, ROWS, PX = 213, 45, 15
SCENE_W, SCENE_H = 213 * 9, 45 * 18  # 1917 x 810
FPS = 20
LOOP = 60
INTRO = 180
BAR = 64
TAU = math.tau
T_SPARK, T_WARP, T_DROP = 0.2, 1.3, 3.3
CX, CY_, R = COLS // 2, 17, 14.5
AX = 2.1

TITLE_ROW, SUB_ROW = 37, 43
FONT = {
    "J": ["    #", "    #", "    #", "#   #", " ### "],
    "A": [" ### ", "#   #", "#####", "#   #", "#   #"],
    "R": ["#### ", "#   #", "#### ", "#  # ", "#   #"],
    "V": ["#   #", "#   #", "#   #", " # # ", "  #  "],
    "I": ["#####", "  #  ", "  #  ", "  #  ", "#####"],
    "S": [" ####", "#    ", " ### ", "    #", "#### "],
    ".": ["#", " ", " ", " ", "#"],
}


def title_cells():
    """Filled cells of the block wordmark, (x, y) from its top-left, 2 cols per pixel."""
    cells, x = [], 0
    for k, ch in enumerate("JARVIS"):
        g = FONT[ch]
        for yy, row in enumerate(g):
            for xx, c in enumerate(row):
                if c == "#":
                    cells += [(x + xx * 2, yy), (x + xx * 2 + 1, yy)]
        x += len(g[0]) * 2 + 2
        if k < 5:
            cells += [(x, 4), (x + 1, 4)]
            x += 4
    return cells, x - 2


TITLE, TITLE_W = title_cells()
TITLE_X = (COLS - TITLE_W) // 2


def hrand(*k):
    h = 2166136261
    for v in k:
        h = ((h ^ (int(v) & 0xFFFFFFFF)) * 16777619) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 2246822519) & 0xFFFFFFFF
    h ^= h >> 13
    return (h & 0xFFFF) / 65535.0


def ease_out(x):
    x = J.clamp(x)
    return 1 - (1 - x) ** 3


def ease_io(x):
    x = J.clamp(x)
    return x * x * (3 - 2 * x)


# ----------------------------------------------------------------- reactor
def reactor(f, cx, cy, R, ph, b):
    """J.reactor with loop-safe rates: ph is loop phase 0..1, b assembly time."""
    a_rim = J.sstep(0.0, 0.8, b)
    a_dash = J.sstep(0.4, 1.0, b)
    a_coil = J.clamp((b - 0.7) / 0.7) * 10
    a_inner = J.sstep(1.2, 1.5, b)
    a_tri = J.sstep(1.4, 1.9, b)
    a_core = J.sstep(1.7, 2.2, b)
    pulse = 0.88 + 0.12 * math.sin(TAU * 3 * ph)
    rot = -TAU / 3 * ph
    cr, sr = math.cos(rot), math.sin(rot)
    aa = 0.55 / R
    w_rim, w_dash, w_in, w_tri = max(0.03, aa), max(0.02, aa * 0.8), max(0.025, aa * 0.8), max(0.022, aa * 0.7)
    normals = [(math.cos(a), math.sin(a)) for a in (math.pi / 2, math.pi / 2 + TAU / 3, math.pi / 2 + 2 * TAU / 3)]
    for y in range(max(0, int(cy - R * 1.1)), min(f.h, int(cy + R * 1.1) + 2)):
        ny = (y - cy) / R
        for x in range(max(0, int(cx - R * AX * 1.1)), min(f.w, int(cx + R * AX * 1.1) + 2)):
            nx = (x - cx) / (R * AX)
            r = math.hypot(nx, ny)
            if r > 1.08:
                continue
            u = (math.atan2(ny, nx) / TAU) % 1.0
            I = hot = 0.0
            if u < a_rim:
                I += J.ring(r, 0.94, w_rim) * 0.85
                if 0.995 < r < 1.07 and J.frac(u * 48 + ph) < 0.3:
                    I += 0.42
            if a_dash and J.frac(u * 36 - ph) < 0.55:
                I += J.ring(r, 0.81, w_dash) * 0.7 * a_dash
            if 0.45 < r < 0.73 and a_coil > 0:
                sp = u * 10 + 2 * ph
                fp = J.frac(sp)
                if 0.1 < fp < 0.9 and int(sp) % 10 < a_coil:
                    band = J.sstep(0.45, 0.5, r) * (1 - J.sstep(0.68, 0.73, r))
                    I += band * (0.78 if fp < 0.2 or fp > 0.8 else 0.5)
            if 0.4 < r < 0.92:
                I += math.exp(-J.frac(ph - u) * 7) * 0.32 * min(a_coil, 1)
            if a_inner:
                I += J.ring(r, 0.38, w_in) * 0.95 * a_inner
            if a_tri and r < 0.4:
                px, py = nx * cr - ny * sr, nx * sr + ny * cr
                m = max(px * a + py * c for a, c in normals)
                e = math.exp(-((m - 0.15) / w_tri) ** 2) * a_tri
                I += e + (0.14 * a_tri if m < 0.15 else 0)
                hot += e
            if a_core and r < 0.6:
                c = math.exp(-(r / 0.13) ** 2) * a_core * pulse
                I += c + math.exp(-(r / 0.55) ** 2) * 0.13 * a_core
                hot += c
            if I < 0.08:
                f.ch[y][x] = " "
                continue
            Ic = min(I, 1.0)
            col = J.mix(J.DIM, J.CY, Ic / 0.55) if Ic < 0.55 else J.mix(J.CY, J.ICE, (Ic - 0.55) / 0.45)
            if hot > 0.45:
                col = J.mix(col, J.WHITE, hot - 0.45)
            f.ch[y][x] = J.RAMP[1 + int(Ic * (len(J.RAMP) - 2))]
            f.co[y][x] = col


HSCALE = 1.5
# vertical nearest-neighbour: 13 rows -> 19
HLINES = []
for yy in range(int(len(L.HELMET) * HSCALE)):
    src = L.HELMET[int(yy / HSCALE)]
    HLINES.append("".join(src[int(x / HSCALE)] for x in range(int(len(src) * HSCALE))))


def helmet(f, ox, oy, ph, reveal):
    edge = reveal * (len(HLINES) + 2)
    pulse = 0.75 + 0.25 * math.sin(TAU * 4 * ph)
    for y, line in enumerate(HLINES):
        a, b = len(line) - len(line.lstrip()), len(line.rstrip())
        f.put(ox + a, oy + y, " " * (b - a), J.BG)
        d = edge - y
        if d <= 0:
            continue
        for x, c in enumerate(line):
            if c == " ":
                continue
            wave = 0.5 + 0.5 * math.sin(x * 0.4 - y * 0.7 + TAU * 2 * ph)
            col = {"e": J.mix(J.CY, J.WHITE, pulse), "n": J.AM, "o": J.mix(J.CY, J.ICE, wave * 0.5),
                   "x": J.mix(J.DIM, J.CY, 0.35 + 0.4 * wave)}.get(c, J.mix(J.DEEP, J.DIM, wave))
            g = L.GLYPH[c]
            if d < 1.3:
                g, col = "▓", J.WHITE
            f.put(ox + x, oy + y, g, col)


# --------------------------------------------------------------- ambience
def background(f, ph, a=1.0):
    for y in range(f.h):
        for x in range(2 if y % 2 else 0, f.w, 4):
            wv = 0.5 + 0.5 * math.sin(x * 0.07 + y * 0.21 - TAU * ph)
            f.ch[y][x] = "·"
            f.co[y][x] = J.mix(J.BG, J.DEEP, (0.35 + 0.65 * wv * wv) * a)


def ruler(f, y, ph, a, up):
    """Scrolling tick ruler across the full width; 40 cells per loop."""
    shift = int(round(40 * ph))
    for x in range(f.w):
        k = (x + shift) % 40
        if k == 0:
            ch, col = "┃", J.mix(J.BG, J.CY, a * 0.8)
        elif k % 8 == 0:
            ch, col = ("╵" if up else "╷"), J.mix(J.BG, J.DIM, a)
        else:
            ch, col = "·", J.mix(J.BG, J.DEEP, a * 0.9)
        f.put(x, y, ch, col)


def shimmer_cells(f, cells, x0, y0, ph, base, hi, width=7.0):
    """Block title with a white band sweeping across once per loop."""
    pos = ph * (TITLE_W + 30) - 15
    for x, y in cells:
        g = math.exp(-((x - pos - y * 2) / width) ** 2)
        col = J.mix(J.mix(base, J.CY, y / 6), hi, g)
        f.put(x0 + x, y0 + y, "█", col)


def shimmer(f, y, text, ph, base, hi):
    x0 = (f.w - len(text)) // 2
    pos = ph * (len(text) + 16) - 8
    for i, c in enumerate(text):
        if c != " ":
            f.put(x0 + i, y, c, J.mix(base, hi, math.exp(-((i - pos) / 3.0) ** 2)))


def ellipse(f, cx, cy, R, rr, n, chars, col, phase=0.0):
    for i in range(n):
        a = i / n * TAU + phase
        x, y = int(round(cx + math.cos(a) * R * AX * rr)), int(round(cy + math.sin(a) * R * rr))
        if 0 <= x < f.w and 0 <= y < f.h:
            f.put(x, y, chars(i) if callable(chars) else chars, col(i) if callable(col) else col)


def bezel(f, cx, cy, R, ph, a):
    spin = TAU * ph / 36
    ellipse(f, cx, cy, R, 1.16, 420, lambda i: "┃" if i % 10 == 0 else ("╵" if i % 5 == 0 else "·"),
            lambda i: J.mix(J.BG, J.CY if i % 10 == 0 else J.DIM, a), spin)


def orbit(f, cx, cy, R, ph, a):
    for k, (rr, turns, n) in enumerate(((1.18, 1, 14), (1.23, -2, 12), (1.28, 3, 10))):
        for j in range(n):
            ang = TAU * (ph * turns + k * 0.31) - j * 0.07 * (1 if turns > 0 else -1)
            x, y = int(round(cx + math.cos(ang) * R * AX * rr)), int(round(cy + math.sin(ang) * R * rr))
            if 0 <= x < f.w and 0 <= y < f.h:
                f.put(x, y, "◆" if j == 0 else "·", J.mix(J.BG, J.ICE if j == 0 else J.CY, a * (1 - j / n)))


def sweep(f, cx, cy, R, ph, a):
    head = TAU * ph
    for i in range(46):
        ang = head - i * 0.04
        for rr in (1.1, 1.13):
            x, y = int(round(cx + math.cos(ang) * R * AX * rr)), int(round(cy + math.sin(ang) * R * rr))
            if 0 <= x < f.w and 0 <= y < f.h:
                v = (1 - i / 46) ** 1.5 * a
                f.put(x, y, "◉" if i == 0 else "•" if i < 5 else "·", J.mix(J.BG, J.WHITE if i == 0 else J.CY, v))


def brackets(f, ph, a):
    w, h = f.w, f.h
    col = J.mix(J.BG, J.AM, a * (0.6 + 0.4 * math.sin(TAU * ph * 2)))
    for k in range(14):
        for sx, d in ((2, 1), (w - 3, -1)):
            f.put(sx + d * k, 4, "━", col)
            f.put(sx + d * k, h - 5, "━", col)
    for k in range(1, 6):
        for sx in (2, w - 3):
            f.put(sx, 4 + k, "┃", col)
            f.put(sx, h - 5 - k, "┃", col)
    for sx, sy, c in ((2, 4, "┏"), (w - 3, 4, "┓"), (2, h - 5, "┗"), (w - 3, h - 5, "┛")):
        f.put(sx, sy, c, col)


# ---------------------------------------------------------------- HUD
PANEL_W, PANEL_H, PANEL_Y = 34, 25, 6


def panel_frame(f, x0, y0, w, h, title, a, rev):
    """Boxed panel revealed top-to-bottom; returns the last visible row."""
    last = y0 + int(rev * h)
    col = J.mix(J.BG, J.DIM, a)
    for y in range(y0, min(last, y0 + h)):
        edge = y == y0 or y == y0 + h - 1
        f.put(x0, y, "┌" if y == y0 else "└" if y == y0 + h - 1 else "│", col)
        f.put(x0 + w - 1, y, "┐" if y == y0 else "┘" if y == y0 + h - 1 else "│", col)
        if edge:
            f.put(x0 + 1, y, "─" * (w - 2), col)
    if rev > 0.15:
        f.put(x0 + 2, y0, "[ " + title + " ]", J.mix(J.BG, J.AM, a))
    if rev < 1:
        f.put(x0, last, "▓" * w, J.mix(J.BG, J.WHITE, a * 0.8))
    return last


def panel_levels(f, x0, y0, w, h, ph, a, last):
    """Animated level bars: whole cycles per loop."""
    n = (w - 4) // 2
    base = y0 + h - 3
    steps = h - 7
    for i in range(n):
        k = 1 + i % 3
        v = 0.5 + 0.4 * math.sin(TAU * (k * ph + i * 0.173)) + 0.1 * math.sin(TAU * (2 * ph + i * 0.41))
        hh = max(1, int(v * steps))
        for j in range(hh):
            y = base - j
            if y >= last:
                continue
            t = j / steps
            col = J.mix(J.DIM, J.CY, t / 0.6) if t < 0.6 else J.mix(J.CY, J.AM if t > 0.88 else J.ICE, (t - 0.6) / 0.4)
            f.put(x0 + 2 + i * 2, y, "█▌", J.mix(J.BG, col, a))
    for x in range(w - 4):
        if base + 1 < last:
            f.put(x0 + 2 + x, base + 1, "▔", J.mix(J.BG, J.DIM, a))
    if base + 2 < last:
        f.put(x0 + 2, base + 2, "· · · · · · · · · · · · · · · ·"[: w - 4], J.mix(J.BG, J.DEEP, a))


def panel_scope(f, x0, y0, w, h, ph, a, last):
    """Oscilloscope trace with fading afterglow."""
    midy = y0 + h // 2
    for gy in (midy - 6, midy, midy + 6):
        for x in range(w - 4):
            if gy < last:
                f.put(x0 + 2 + x, gy, "┄" if gy != midy else "─", J.mix(J.BG, J.DEEP, a))
    def trace(p):
        yv = 5.5 * math.sin(TAU * (p / 17.0 - ph * 2)) * (0.65 + 0.35 * math.sin(TAU * (p / 40.0 + ph)))
        return int(round(midy + yv + 1.6 * math.sin(TAU * (p / 6.0 + ph * 3))))

    for tail in range(7, -1, -1):
        prev = None
        for x in range(w - 4):
            y = trace(x - tail * 0.7)
            v = (1 - tail / 8.0) * a
            for yy in range(min(y, prev if prev is not None else y), max(y, prev if prev is not None else y) + 1):
                if yy < last and y0 < yy < y0 + h - 1:
                    f.put(x0 + 2 + x, yy, "◉" if tail == 0 and yy == y and x % 9 == 0 else "•" if tail == 0 else "·",
                          J.mix(J.BG, J.ICE if tail == 0 else J.CY, v))
            prev = y
    if y0 + h - 3 < last:
        f.put(x0 + 2, y0 + h - 3, "T-" + "·" * (w - 8) + "+T", J.mix(J.BG, J.DIM, a))


# --------------------------------------------------------------- opening
def ignite(f, cx, cy, p):
    if p < 0.2:
        f.put(cx, cy, "·" if p < 0.08 else "●", J.mix(J.DIM, J.WHITE, p / 0.2))
        return
    q = (p - 0.2) / 0.8
    n = int(f.w * 0.5 * ease_out(q * 1.3))
    fade = 1 - q * 0.6
    f.put(cx - n, cy, "─" * (2 * n + 1), J.mix(J.BG, J.ICE, fade))
    f.put(cx - n // 2, cy, "━" * (n + 1), J.mix(J.BG, J.WHITE, fade))
    for dy, ch in ((-1, "▁"), (1, "▔")):  # anamorphic bloom
        w2 = int(n * 0.55)
        f.put(cx - w2, cy + dy, ch * (2 * w2 + 1), J.mix(J.BG, J.CY, fade * 0.45))
    span = int(f.h * 0.45 * ease_out(q))
    for dy in range(-span, span + 1):
        f.put(cx, cy + dy, "┃" if abs(dy) < 2 else "│", J.mix(J.BG, J.CY, fade * (1 - abs(dy) / (f.h * 0.5))))
    Rr = q * f.h * 0.46
    for i in range(int(Rr * 9) + 12):
        a = i / (Rr * 9 + 12) * TAU
        f.put(int(cx + math.cos(a) * Rr * AX), int(cy + math.sin(a) * Rr), "◦•"[i & 1], J.mix(J.CY, J.BG, q * 0.6))
    f.put(cx - 1, cy, "◉◉◉", J.WHITE)


def stars(f, cx, cy, s):
    """Warp tunnel of glyph streaks, s 0..1."""
    speed = 0.3 + 3.2 * s * s
    reach = max(f.w / AX, f.h) * 0.62
    for i in range(380):
        a = hrand(i, 1) * TAU
        sp = 0.5 + hrand(i, 2)
        z = (hrand(i, 3) + s * speed * sp * 0.6 + 2.5 * s * s * sp) % 1.0
        r0 = z * z * reach
        trail = (0.4 + 7.0 * s * s) * z * sp
        steps = max(1, int(trail * 1.4))
        ca, sa = math.cos(a), math.sin(a)
        ang = abs(sa)
        ch = "│" if ang > 0.9 else "─" if ang < 0.3 else ("╲" if ca * sa > 0 else "╱")
        tint = hrand(i, 4)
        for k in range(steps + 1):
            r = r0 - trail * k / steps
            if r < 0:
                break
            x, y = int(cx + ca * r * AX), int(cy + sa * r)
            if 0 <= x < f.w and 0 <= y < f.h:
                lum = J.clamp(z * 1.5 - k / (steps + 2) * 0.8) * (0.45 + 0.55 * s)
                col = J.mix(J.DEEP, J.ICE if tint > 0.8 else (J.AM if tint < 0.08 else J.CY), lum)
                f.put(x, y, ch if k or s > 0.5 else "·", J.mix(col, J.WHITE, 0.6) if k == 0 and s > 0.2 else col)


def shockwave(f, cx, cy, k):
    """Expanding glyph rings from the drop, k = seconds since drop."""
    for n in range(4):
        q = (k - n * 0.14) / 1.6
        if not 0 < q < 1:
            continue
        rr = ease_out(q) * 58
        fade = (1 - q) ** 1.4
        cnt = int(rr * 11) + 16
        for i in range(cnt):
            a = i / cnt * TAU
            x, y = int(round(cx + math.cos(a) * rr * AX)), int(round(cy + math.sin(a) * rr))
            if 0 <= x < f.w and 0 <= y < f.h:
                f.put(x, y, "◦•○"[(i + n) % 3], J.mix(J.BG, J.WHITE if n == 0 else J.CY, fade * (0.9 - n * 0.15)))


def title_particles(f, x0, y0, t, t0, ph):
    """Block wordmark assembling out of scattered glyphs, then shimmering."""
    done = []
    for i, (x, y) in enumerate(TITLE):
        a = hrand(i, 11) * TAU
        d = (0.4 + hrand(i, 12)) * f.w * 0.55
        lag = hrand(i, 13) * 0.55 + x / TITLE_W * 0.35
        q = (t - t0 - lag) / 0.7
        if q <= 0:
            continue
        e = ease_out(q)
        if q < 1:
            fx = int(x0 + x + math.cos(a) * d * (1 - e))
            fy = int(y0 + y + math.sin(a) * d * 0.35 * (1 - e))
            f.put(fx, fy, "█" if e > 0.85 else "▓" if e > 0.6 else "▒" if e > 0.3 else "░", J.mix(J.DIM, J.ICE, e))
        else:
            done.append((x, y))
            lock = J.clamp(1 - (t - t0 - lag - 0.7) / 0.35)
            col = J.mix(J.mix(J.ICE, J.CY, y / 6), J.WHITE, lock)
            f.put(x0 + x, y0 + y, "█", col)
    if len(done) == len(TITLE):
        # settled: hand over to the loop-safe shimmer
        w = J.clamp((t - t0 - 1.7) / 0.5)
        if w >= 1:
            shimmer_cells(f, TITLE, x0, y0, ph, J.ICE, J.WHITE)


def glitch(f, seed, amount):
    """Horizontal row tears, like a signal locking in."""
    for y in range(f.h):
        if hrand(seed, y) < amount:
            s = int((hrand(seed, y, 9) - 0.5) * 14)
            if s:
                f.ch[y] = f.ch[y][-s:] + f.ch[y][:-s]
                f.co[y] = f.co[y][-s:] + f.co[y][:-s]


# ----------------------------------------------------------------- frame
def frame(i):
    f = J.Frame(COLS, ROWS)
    t = i / FPS
    ph = (i % LOOP) / LOOP
    cx, cy = CX, CY_
    sub = "L O C K E   //   S Y S T E M   B O O T"
    if t < T_SPARK:
        return f
    if t < T_WARP:
        ignite(f, cx, cy, (t - T_SPARK) / (T_WARP - T_SPARK))
        return f
    if t < T_DROP:
        s = (t - T_WARP) / (T_DROP - T_WARP)
        background(f, 0, J.sstep(0, 0.3, s))
        stars(f, cx, cy, s)
        f.put(cx - 1, cy, "◉", J.WHITE)
        if s > 0.86:  # pre-drop bloom
            k = (s - 0.86) / 0.14
            f.put(cx - int(k * 40), cy, "━" * int(k * 80 + 1), J.mix(J.CY, J.WHITE, k))
        return f
    k = t - T_DROP  # seconds since the drop
    a = J.sstep(0.4, 1.4, k)
    background(f, ph)
    b = k * 1.2
    reactor(f, cx, cy, R, ph, b)
    shockwave(f, cx, cy, k)
    bezel(f, cx, cy, R, ph, a)
    orbit(f, cx, cy, R, ph, a)
    sweep(f, cx, cy, R, ph, a * 0.8)
    brackets(f, ph, a)
    ruler(f, 0, ph, a, False)
    ruler(f, ROWS - 1, ph, a, True)
    lx, rx = 8, COLS - 8 - PANEL_W
    rev = ease_io((k - 0.9) / 1.1)
    if rev > 0:
        la = 0.9 * J.sstep(0.9, 1.4, k)
        last = panel_frame(f, lx, PANEL_Y, PANEL_W, PANEL_H, "ARC", la, rev)
        panel_levels(f, lx, PANEL_Y, PANEL_W, PANEL_H, ph, la, last)
        last = panel_frame(f, rx, PANEL_Y, PANEL_W, PANEL_H, "FLUX", la, rev)
        panel_scope(f, rx, PANEL_Y, PANEL_W, PANEL_H, ph, la, last)
    hx = cx - len(HLINES[0]) // 2
    helmet(f, hx, cy - len(HLINES) // 2 - 1, ph, J.sstep(1.8, 3.0, k))
    if k > 2.5:
        title_particles(f, TITLE_X, TITLE_ROW, k, 2.5, ph)
    if k > 3.7:
        if k < 4.3:
            f.cput(SUB_ROW, J.scramble(sub, (k - 3.7) / 0.6, 5), J.mix(J.AM, J.BG, 0.35))
        else:
            shimmer(f, SUB_ROW, sub, J.frac(ph + 0.5), J.mix(J.AM, J.BG, 0.35), J.AM)
    if k < 0.3:  # white-out fading to scene
        for y in range(f.h):
            f.co[y] = [J.mix(c, J.WHITE, 0.95 * (1 - k / 0.3)) for c in f.co[y]]
        glitch(f, i, 0.45 * (1 - k / 0.3))
    elif 2.5 < k < 2.5 + 2.0 and abs(k - 4.55) < 0.07:
        glitch(f, i, 0.12)
    return f


def progress_bar(n):
    f = J.Frame(BAR + 2, 1)
    f.put(0, 0, "▕" + "█" * n + "░" * (BAR - n) + "▏", J.CY)
    for x in range(1, n + 1):
        f.co[0][x] = J.mix(J.DIM, J.CY, x / max(n, 1))
    if 0 < n < BAR:
        f.put(n, 0, "▓", J.WHITE)
    return f


_RAS = None


def render(args):
    global _RAS
    i, out = args
    if _RAS is None:
        _RAS = RR.Raster(PX, lh=18)
    _RAS.paint(frame(i)).quantize(40, dither=0).save(os.path.join(out, f"frame-{i}.png"), optimize=True)
    return i


def overlay(out):
    """Vignette + scanlines + sparse grain, oversized so the script can jitter it."""
    import numpy as np
    from PIL import Image
    w, h = 1920 + 16, 1080 + 16
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2))
    alpha = np.clip((d - 0.55) / 0.75, 0, 1) ** 2 * 0.82
    alpha = np.maximum(alpha, ((yy.astype(int) % 3) == 0) * 0.10)
    rng = np.random.default_rng(7)
    grain = rng.random((h, w)) < 0.035
    rgb = np.zeros((h, w, 3), np.uint8)
    rgb[..., :] = (0, 3, 6)
    rgb[grain] = (150, 200, 220)
    alpha = np.where(grain, 0.10, alpha)
    img = np.dstack([rgb, (alpha * 255).astype(np.uint8)])
    Image.fromarray(img, "RGBA").save(os.path.join(out, "overlay.png"), optimize=True)


def solids(out):
    from PIL import Image
    for name, col in (("black", (0, 0, 0)), ("white", (255, 255, 255)), ("cyan", J.CY)):
        Image.new("RGB", (2, 2), col).save(os.path.join(out, f"px-{name}.png"))


def script(out):
    src = open(os.path.join(HERE, "jarvis-ascii.script.in")).read()
    for k, v in {"@FPS@": FPS, "@INTRO@": INTRO, "@LOOP@": LOOP, "@BAR@": BAR, "@SCENE_W@": SCENE_W,
                 "@SCENE_H@": SCENE_H, "@DROP@": int(T_DROP * FPS), "@PX@": 9}.items():
        src = src.replace(k, str(v))
    open(os.path.join(out, "jarvis-ascii.script"), "w").write(src)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "build"
    os.makedirs(out, exist_ok=True)
    with mp.Pool(max(1, (os.cpu_count() or 2) - 1)) as pool:
        for n, _ in enumerate(pool.imap_unordered(render, [(i, out) for i in range(INTRO + LOOP)])):
            if n % 40 == 0:
                print(f"  {n}/{INTRO + LOOP}", flush=True)
    ras = RR.Raster(PX, lh=18)
    for n in range(BAR + 1):
        ras.paint(progress_bar(n)).save(os.path.join(out, f"bar-{n}.png"), optimize=True)
    overlay(out)
    solids(out)
    script(out)
    print(f"{INTRO + LOOP} frames {SCENE_W}x{SCENE_H} @ {FPS}fps, {BAR + 1} bars -> {out}")


if __name__ == "__main__":
    main()
