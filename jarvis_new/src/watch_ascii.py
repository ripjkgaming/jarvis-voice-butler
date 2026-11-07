"""ASCII Watch Mode card: one big glyph eye, frame and block type.

compose() is pure. It lays out one frame as a grid of characters plus a
colour level per cell, so the fullscreen Tk card (AsciiCard) and the
terminal `watch` transition (~/deck/lib/jarvis_watch.py) draw the same eye.
Every glyph is 7-bit ASCII.

AsciiCard owns canvas items only: no windows, timers, input, capture,
threads or security state. The watch owner calls paint/update, and a
renderer failure must only ever remove the decoration (see _Panel).
"""

from __future__ import annotations

import contextlib
import math
import os
import time

TAU = math.tau
RAMP = " .,:;-~=+*o#%@"
NOISE = "01<>/\\|=+*#%@$&?"

#: Colour levels per cell. 0 is never drawn.
BLANK, FAINT, DIM, MID, BRIGHT, ICE, HOT, ACCENT = range(8)
LEVELS = 8

#: Idle palette (cyan, amber accent) and motion palette (amber, red accent).
IDLE = (
    (0, 0, 0),
    (14, 38, 50),
    (24, 88, 112),
    (52, 154, 190),
    (95, 227, 255),
    (206, 246, 255),
    (255, 255, 255),
    (255, 179, 71),
)
ALERT = (
    (0, 0, 0),
    (48, 24, 8),
    (122, 64, 20),
    (198, 114, 38),
    (255, 179, 71),
    (255, 226, 186),
    (255, 255, 255),
    (255, 77, 94),
)

# ---------------------------------------------------------------- math


def clamp(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


def sstep(a, b, x):
    k = clamp((x - a) / (b - a)) if b != a else float(x >= b)
    return k * k * (3 - 2 * k)


def frac(x):
    return x - math.floor(x)


def hash01(x, y, k=0.0):
    """Deterministic 0..1 noise, so a frame depends only on its inputs."""
    return frac(math.sin(x * 12.9898 + y * 78.233 + k * 37.719) * 43758.5453)


def level_rgb(level: int, alert: float = 0.0) -> tuple[int, int, int]:
    a, b = IDLE[level], ALERT[level]
    k = clamp(alert)
    return tuple(round(x + (y - x) * k) for x, y in zip(a, b))


def mix_rgb(a, b, k: float) -> tuple[int, int, int]:
    k = clamp(k)
    return tuple(round(x + (y - x) * k) for x, y in zip(a, b))


def level_hex(level: int, alert: float = 0.0) -> str:
    return "#%02x%02x%02x" % level_rgb(level, alert)


def ascii_only(text: str) -> str:
    text = (text or "").replace("·", ":").replace("•", ":")
    return text.encode("ascii", "replace").decode("ascii")


# ---------------------------------------------------------------- grid


class Grid:
    def __init__(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        self.ch = [[" "] * cols for _ in range(rows)]
        self.lv = [[BLANK] * cols for _ in range(rows)]

    def set(self, x: int, y: int, c: str, level: int) -> None:
        if 0 <= x < self.cols and 0 <= y < self.rows:
            self.ch[y][x] = c
            self.lv[y][x] = level

    def put(self, x: int, y: int, text: str, level: int) -> None:
        for i, c in enumerate(text):
            if c != " ":
                self.set(x + i, y, c, level)

    def clear(self, x: int, y: int) -> None:
        self.set(x, y, " ", BLANK)

    def wipe(self, x: int, y: int, w: int, h: int = 1) -> None:
        """Blank a box so text sits on clean black, not on the dot field."""
        for yy in range(max(1, y), min(self.rows - 1, y + h)):
            for xx in range(max(1, x), min(self.cols - 1, x + w)):
                self.ch[yy][xx] = " "
                self.lv[yy][xx] = BLANK

    def text(self) -> str:
        return "\n".join("".join(r) for r in self.ch)


def decode(
    g: Grid,
    x0: int,
    y: int,
    text: str,
    t: float,
    t0: float,
    level: int,
    dur=0.6,
    seed=0.0,
):
    """Text that resolves out of noise glyphs, left to right."""
    n = max(1, len(text))
    for i, c in enumerate(text):
        if c == " ":
            continue
        rt = t0 + i / n * dur + hash01(i, seed) * 0.15
        if t < rt - 0.3:
            continue
        if t < rt:
            k = int(hash01(i, seed, int(t * 30)) * len(NOISE))
            g.set(x0 + i, y, NOISE[k], DIM)
        else:
            g.set(x0 + i, y, c, HOT if t < rt + 0.08 else level)


# ---------------------------------------------------------------- choreography


def _keys(t: float, frames):
    """Smooth piecewise value through (time, value) keyframes."""
    if t <= frames[0][0]:
        return frames[0][1]
    for (t0, v0), (t1, v1) in zip(frames, frames[1:]):
        if t < t1:
            return v0 + (v1 - v0) * sstep(t0, t1, t)
    return frames[-1][1]


#: Startup: slit, two weak flutters, then the lids lift and settle.
WAKE = ((1.0, 0.0), (1.35, 0.28), (1.6, 0.04), (1.95, 0.55), (2.15, 0.18), (2.9, 1.0))
#: Iris focus: the pupil starts blown wide, then irises down to rest.
FOCUS = ((1.6, 0.52), (2.4, 0.34), (3.2, 0.18))


def wake_open(t: float) -> float:
    return _keys(t, WAKE)


def wake_pupil(t: float) -> float:
    return _keys(t, FOCUS)


def saving_pose(sv: float, done: bool = False) -> tuple[float, float]:
    """(openness, pupil) while saving. sv = seconds since saving began.

    The pupil pinches first, the lid sags in two stages, and a finished
    save lets the lid settle completely shut.
    """
    pupil = 0.18 - 0.11 * sstep(0.0, 0.5, sv)
    lid = 1 - 0.45 * sstep(0.15, 0.6, sv) - 0.55 * sstep(0.8, 1.5, sv)
    if done:
        lid = 0.0
    return max(0.0, lid), pupil


# ---------------------------------------------------------------- gaze


class Gaze:
    """Idle saccades + blinks; follows the real motion target when given.

    Saccades overshoot slightly and settle, with a small micro-tremor, and
    every few blinks is a slow double blink.
    """

    def __init__(self) -> None:
        self.x = self.y = 0.0
        self.tx = self.ty = 0.0
        self.vx = self.vy = 0.0
        self.seg = -1
        self.blink_at = 3.2
        self.blinks = 0

    def update(self, t: float, dt: float, target=None) -> float:
        dt = max(0.0, dt)
        if target is not None:
            self.tx, self.ty = target
        else:
            seg = int(t / 2.3)
            if seg != self.seg:
                self.seg = seg
                self.tx = (hash01(seg, 1.0) - 0.5) * 0.8
                self.ty = (hash01(seg, 2.0) - 0.5) * 0.45
        # Critically damped spring, sub-stepped so a slow frame can't blow up.
        stiff = 70.0 if target is not None else 30.0
        damp = 2 * math.sqrt(stiff) * 0.95
        n = max(1, int(dt / 0.008) + 1)
        h = dt / n
        for _ in range(n):
            self.vx += ((self.tx - self.x) * stiff - self.vx * damp) * h
            self.vy += ((self.ty - self.y) * stiff - self.vy * damp) * h
            self.x += self.vx * h
            self.y += self.vy * h
        self.x = clamp(self.x, -1.2, 1.2)
        self.y = clamp(self.y, -1.2, 1.2)
        dur = 0.34 if self.blinks % 4 == 3 else 0.24
        if self.blink_at < t < self.blink_at + dur:
            k = (t - self.blink_at) / dur
            # Fast down, slower up; upper lid leads.
            return abs(1 - sstep(0.0, 0.4, k)) if k < 0.4 else sstep(0.4, 1.0, k)
        if t >= self.blink_at + dur:
            self.blinks += 1
            self.blink_at = t + 3.5 + hash01(int(t), 7.0) * 4.5
        return 1.0


def norm_target(target):
    """Camera motion centroid (0..1, 0..1) -> gaze (-1..1). Bad input -> None."""
    try:
        if target is None or len(target) != 2:
            return None
        x, y = (float(v) for v in target)
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        # Mirror x: the webcam faces the room, the eye looks back at it.
        return clamp(1 - x * 2, -1, 1) * 0.75, clamp(y * 2 - 1, -1, 1) * 0.6
    except (TypeError, ValueError, OverflowError):
        return None


# ---------------------------------------------------------------- the eye


def _stroke(th: float) -> str:
    """ASCII line glyph pointing along screen angle th (y grows down)."""
    a = th % math.pi
    if a < math.pi / 8 or a >= 7 * math.pi / 8:
        return "-"
    if a < 3 * math.pi / 8:
        return "\\"
    if a < 5 * math.pi / 8:
        return "|"
    return "/"


def _lids(nx: float, openness: float) -> tuple[float, float]:
    """(upper, lower) lid height at nx, in the same units as _lid before.

    The upper lid does most of the travel, so a blink meets below centre.
    """
    base = max(0.0, 1 - nx * nx) ** 0.8 * 0.62
    up = base * openness * (1.0 + 0.12 * (1 - abs(nx)) * (1 - openness))
    lo = base * (0.28 + 0.72 * openness) * 0.92
    if openness <= 0.0:
        lo = 0.0
    return up, lo


def _lid(nx: float, openness: float) -> float:
    return _lids(nx, openness)[0]


def _closed(g: Grid, cx: int, cy: int, W: float, x0: int, x1: int, t: float, lash=1.0):
    """Shut lid: seam, drooping lashes, crease and a soft under-glow."""
    for x in range(x0 + 1, x1):
        nx = (x - cx) / W
        if abs(nx) >= 1.0:
            continue
        c = "=" if abs(nx) < 0.82 else "-"
        g.set(x, cy, c, ICE if abs(nx) < 0.5 else BRIGHT)
        sag = 1 + int(round((1 - nx * nx) * 0.6))
        if abs(nx) < 0.9 and x % 3 == 0:
            g.set(x, cy + sag, "'" if abs(nx) < 0.6 else ".", DIM)
        # Lashes droop down and out from the seam.
        if lash and x % 2 == 0 and abs(nx) > 0.12:
            tip = "\\" if nx > 0 else "/"
            g.set(x, cy + 1 + (abs(nx) < 0.5), tip if abs(nx) > 0.35 else "|", MID)
        if abs(nx) < 0.55 and x % 4 == 1:
            g.set(x, cy - 2, "_", FAINT)
    g.set(int(round(cx - W)), cy, "<", ICE)
    g.set(int(round(cx + W)), cy, ">", ICE)


def eye(
    g: Grid,
    cx: int,
    cy: int,
    R: float,
    ax: float,
    t: float,
    *,
    openness=1.0,
    gaze=(0.0, 0.0),
    alert=0.0,
    pupil=0.18,
):
    """Almond eye: slope-traced lids, lashes, crease, layered iris.

    R is the eye half-height scale in rows; ax is cell height / cell width,
    so one unit across is R * ax columns and the eye stays round on screen.
    """
    W = R * ax * 2.0  # half width in columns
    x0, x1 = max(0, int(cx - W) - 1), min(g.cols - 1, int(cx + W) + 1)
    gx, gy = gaze
    if openness <= 0.02:
        _closed(g, cx, cy, W, x0, x1, t)
        return
    # Interior: sclera, iris and pupil, clipped by the lids.
    icx = cx + gx * W * 0.42
    icy = cy + gy * R * 0.36
    ir_max = 0.74
    scan = frac(t * 0.3) * 2.6 - 1.3
    spin = t * 0.35
    breathe = 0.5 + 0.5 * math.sin(t * 2.1)
    for y in range(max(0, int(cy - R * 0.7) - 1), min(g.rows, int(cy + R * 0.7) + 2)):
        for x in range(x0, x1 + 1):
            nx = (x - cx) / W
            if abs(nx) >= 1.0:
                continue
            up, lo = _lids(nx, openness)
            ny = (y - cy) / (R * 1.6)
            lim = up if ny < 0 else lo
            if abs(ny) >= lim - 0.02:
                continue
            # Shade toward the lid edge so the ball reads as round.
            edge = clamp((abs(ny) / max(lim, 1e-3) - 0.62) / 0.38)
            ix = (x - icx) / (R * ax)
            iy = (y - icy) / R
            ir = math.hypot(ix, iy)
            if ir < pupil:
                # Dark pupil with a faint inner ring; glows when moved on.
                if alert > 0.3:
                    g.set(x, y, "@" if ir > pupil * 0.45 else "O", ACCENT)
                elif pupil < 0.3:
                    g.set(x, y, "@" if ir > pupil * 0.55 else ".", FAINT)
                else:
                    g.clear(x, y)
                continue
            if ir < pupil + 0.05:
                g.set(x, y, "o", ICE if edge < 0.6 else BRIGHT)
                continue
            if ir < ir_max:
                th = math.atan2(iy, ix)
                u = (ir - pupil) / max(1e-3, ir_max - pupil)  # 0 collar .. 1 limbus
                fib = 0.5 + 0.5 * math.sin(th * 22 + ir * 14)
                fib *= 0.6 + 0.4 * math.sin(th * 7)
                if ir > ir_max - 0.07:
                    # Limbal ring: bold, with notches.
                    lvl = BRIGHT if edge < 0.5 else MID
                    g.set(x, y, "#" if (int(th * 9) % 6) else "%", lvl)
                elif ir > ir_max - 0.13:
                    g.set(
                        x,
                        y,
                        "%" if hash01(x, y) < 0.5 else "*",
                        MID if edge < 0.5 else DIM,
                    )
                elif u < 0.26:
                    # Collarette: zig-zag crown round the pupil.
                    zig = abs(frac(th / TAU * 18) - 0.5) * 2
                    g.set(
                        x,
                        y,
                        "^"
                        if zig > 0.55 and abs(th) < 2.0
                        else "*"
                        if zig > 0.4
                        else ":",
                        BRIGHT if edge < 0.5 else MID,
                    )
                else:
                    crypt = hash01(int(th * 6), int(ir * 9)) < 0.1
                    if crypt:
                        g.set(x, y, " ", BLANK)  # dark crypts in the weave
                    elif fib > 0.32:
                        lvl = BRIGHT if fib > 0.8 else MID if fib > 0.55 else DIM
                        if edge > 0.5:
                            lvl = max(DIM, lvl - 1)
                        g.set(x, y, _stroke(th), lvl)
                    else:
                        g.set(x, y, ".", FAINT)
                continue
            # Sclera: shaded toward the lids for volume, plus a scan beam.
            depth = (1 - abs(ny) / max(lim, 1e-3)) * (1 - nx * nx)
            if abs(nx - scan) < 0.025:
                g.set(x, y, ":", MID)
            elif edge > 0.45 and hash01(x, y) < 0.35 + edge * 0.4:
                g.set(x, y, "." if edge < 0.8 else ":", FAINT if edge < 0.8 else DIM)
            elif depth > 0.15 and hash01(x, y) < 0.08 + depth * 0.3:
                g.set(x, y, ".:"[depth > 0.6], FAINT if depth < 0.6 else DIM)
            else:
                g.clear(x, y)
    # Catch-lights: window reflection (boxed) plus a small pinpoint opposite.
    hx, hy = int(round(icx - R * ax * 0.2)), int(round(icy - R * 0.2))
    if abs(hy - cy) / (R * 1.6) < _lid((hx - cx) / W, openness) - 0.08:
        g.set(hx, hy, "*", HOT)
        g.set(hx + 1, hy, "'", ICE)
        g.set(hx, hy + 1, "'", ICE)
    kx, ky = int(round(icx + R * ax * 0.18)), int(round(icy + R * 0.2))
    if abs(ky - cy) / (R * 1.6) < _lids((kx - cx) / W, openness)[1] - 0.08:
        g.set(kx, ky, "+", ICE)
    # Lids traced as lines whose glyph follows the curve's slope.
    prev = {}
    for x in range(x0, x1 + 1):
        nx = (x - cx) / W
        if abs(nx) > 1.0:
            continue
        d = 1 / W
        for sign in (-1, 1):
            f = lambda n: _lids(max(-1, min(1, n)), openness)[0 if sign < 0 else 1]
            lid = f(nx) * R * 1.6
            slope = (f(nx + d) - f(nx - d)) * R * 1.6 / 2
            y = int(round(cy + sign * lid))
            s = slope * sign  # rows per column, screen space (y down)
            if abs(s) < 0.45:
                c = "_" if sign < 0 and abs(nx) < 0.75 else "-"
            elif abs(s) < 1.6:
                c = "/" if s < 0 else "\\"
            else:
                c = "|"
            lvl = ICE if abs(nx) < 0.85 else BRIGHT
            g.set(x, y, c, lvl)
            # Close vertical gaps where the curve is steep.
            py = prev.get(sign)
            if py is not None and abs(y - py) > 1:
                step = 1 if y > py else -1
                for yy in range(py + step, y, step):
                    g.set(x if abs(yy - y) < abs(yy - py) else x - 1, yy, "|", lvl)
            prev[sign] = y
        up = _lids(nx, openness)[0] * R * 1.6
        lo = _lids(nx, openness)[1] * R * 1.6
        # Upper lashes: fan outward, longer mid-lid, curling flat as the lid drops.
        if x % 2 == 0 and abs(nx) < 0.92:
            ly = int(round(cy - up)) - 1
            lean = nx * 1.4 * (0.4 + 0.6 * openness)
            glyph = "\\" if lean < -0.3 else "/" if lean > 0.3 else "|"
            if openness < 0.5:
                glyph = "-" if abs(nx) > 0.5 else glyph
            g.set(x, ly, glyph, MID if abs(nx) > 0.25 else BRIGHT)
            if abs(nx) < 0.6 and hash01(x, 3.0) < 0.45 * openness:
                g.set(x, ly - 1, glyph, DIM)
        # Crease above the lid and the soft shadow below the lower lid.
        if openness > 0.5 and abs(nx) < 0.68:
            cr = int(round(cy - up)) - 2 - int(R * 0.1)
            if x % 2 == 1:
                g.set(
                    x,
                    cr,
                    "-" if abs(nx) < 0.4 else ".",
                    FAINT if abs(nx) > 0.4 else DIM,
                )
        if abs(nx) < 0.6 and x % 5 == 2:
            g.set(x, int(round(cy + lo)) + 1, ".", DIM)
        if abs(nx) < 0.34 and x % 3 == 1 and openness > 0.4:
            g.set(x, int(round(cy + lo)) + 2, ",", FAINT)
        # Lower lashes: short ticks on the outer thirds only.
        if abs(nx) > 0.55 and x % 3 == 0 and openness > 0.4:
            g.set(x, int(round(cy + lo)) + 1, "/" if nx < 0 else "\\", DIM)
    # Corner points: inner corner has a tear duct, outer corner a sharp wing.
    g.set(int(round(cx - W)), cy, "<", ICE)
    g.set(int(round(cx + W)), cy, ">", ICE)
    if openness > 0.4:
        g.put(int(round(cx - W)) + 1, cy, "(", MID)
        g.set(int(round(cx + W)) + 1, cy - 1, "-", DIM)


def brow(g: Grid, cx: int, cy: int, R: float, ax: float, t: float, lift=0.0, alert=0.0):
    """Brow arch above the eye. It lifts as the eye opens or notices motion."""
    W = R * ax * 2.0
    by = cy - R * (1.46 + 0.1 * lift + 0.14 * alert)
    for x in range(int(cx - W * 1.15), int(cx + W * 1.15) + 1):
        nx = (x - cx) / (W * 1.15)
        if abs(nx) > 1.0:
            continue
        y = int(round(by + (nx * nx) * R * 0.34 - 0.0 * nx))
        thick = 1 - abs(nx) ** 3
        hair = "~" if hash01(x, 5.0) < 0.5 else "="
        g.set(x, y, hair if thick > 0.35 else "-", MID if thick > 0.6 else DIM)
        if thick > 0.5 and hash01(x, 9.0) < 0.5:
            g.set(x, y - 1, "'" if nx < 0 else "`", FAINT)


def sonar(g: Grid, cx: int, cy: int, rx: float, ry: float, t: float, alert=0.0):
    """Rings that ping outward from the eye; faster and amber on motion."""
    period = 3.2 - 1.6 * clamp(alert)
    for k in range(2):
        u = frac(t / period + k * 0.5)
        r = 1.05 + u * 0.75
        n = int(60 * r)
        for i in range(n):
            a = i / n * TAU
            x = int(round(cx + math.cos(a) * rx * r))
            y = int(round(cy + math.sin(a) * ry * r))
            if not (1 <= x < g.cols - 1 and 1 <= y < g.rows - 1) or g.lv[y][x] > BLANK:
                continue
            if (i + int(t * 6)) % 3 == 0 or u < 0.2:
                g.set(
                    x,
                    y,
                    "." if u > 0.45 else ":" if u > 0.2 else "o",
                    FAINT if u > 0.5 else DIM if u > 0.25 else MID,
                )


def reticle(
    g: Grid, cx: int, cy: int, R: float, ax: float, t: float, boot=9.0, alert=0.0
):
    rx, ry = R * ax * 2.0 * 1.22, R * 1.24
    for y in range(int(cy - ry), int(cy + ry) + 1):
        dy = (y - cy) / ry
        if abs(dy) >= 1:
            continue
        hw = int(rx * math.sqrt(1 - dy * dy))
        g.wipe(cx - hw, y, 2 * hw + 1)
    n = max(24, int(rx * 1.1))
    p = sstep(0.1, 0.9, boot)
    head = frac(t * 0.08)
    for k in range(int(n * p)):
        u = k / n
        a = u * TAU
        x = int(round(cx + math.cos(a) * rx))
        y = int(round(cy + math.sin(a) * ry))
        if not (0 <= y < g.rows and 0 <= x < g.cols) or g.lv[y][x] > FAINT:
            continue
        near = frac(u - head)
        if near < 0.08:
            g.set(x, y, "o", ACCENT if alert > 0.5 else BRIGHT)
        elif k % 6 == 0:
            g.set(x, y, "+", MID)
        else:
            g.set(x, y, ".", DIM)
    # Radar tick ring with a sweeping wedge that fades behind its head.
    if p > 0.6:
        sweep = frac(t * (0.18 + 0.5 * alert))
        nt = 72
        for k in range(nt):
            u = k / nt
            a = u * TAU
            x = int(round(cx + math.cos(a) * rx * 1.1))
            y = int(round(cy + math.sin(a) * ry * 1.1))
            if not (1 <= y < g.rows - 1 and 1 <= x < g.cols - 1) or g.lv[y][x] > FAINT:
                continue
            behind = frac(sweep - u)
            major = k % 6 == 0
            if behind < 0.025:
                g.set(x, y, "#", ACCENT if alert > 0.5 else ICE)
            elif behind < 0.3:
                g.set(
                    x,
                    y,
                    _stroke(a + math.pi / 2) if major else ".",
                    BRIGHT if behind < 0.1 else MID if behind < 0.2 else DIM,
                )
            elif major:
                g.set(x, y, "+", FAINT)
    # Crosshair arms out to the sides, with a tick ruler.
    arm = int(min(rx * 0.55, (g.cols - 2 * rx) / 4))
    if arm > 4 and p > 0.5:
        for side in (-1, 1):
            xs = int(cx + side * (rx + 2))
            for i in range(arm):
                x = xs + side * i
                g.set(x, cy, "+" if i % 5 == 4 else "-", MID if i < 2 else DIM)
            g.set(xs, cy, "<" if side > 0 else ">", BRIGHT)
    # Corner brackets.
    bx, by = int(rx) + 3, int(ry) + 1
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = cx + sx * bx, cy + sy * by
            if p > 0.7:
                g.set(x, y, "+", BRIGHT)
                for i in range(1, 4):
                    g.set(x - sx * i, y, "-", MID)
                g.set(x, y - sy, "|", MID)


# ---------------------------------------------------------------- block type

FONT = {
    "A": (".###.", "#...#", "#####", "#...#", "#...#"),
    "C": (".####", "#....", "#....", "#....", ".####"),
    "D": ("####.", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "####.", "#....", "#####"),
    "G": (".####", "#....", "#..##", "#...#", ".###."),
    "H": ("#...#", "#...#", "#####", "#...#", "#...#"),
    "I": ("#####", "..#..", "..#..", "..#..", "#####"),
    "K": ("#...#", "#..#.", "###..", "#..#.", "#...#"),
    "M": ("#...#", "##.##", "#.#.#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", ".###."),
    "R": ("####.", "#...#", "####.", "#..#.", "#...#"),
    "S": (".####", "#....", ".###.", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#.."),
    "V": ("#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#.#.#", "##.##", "#...#"),
    " ": (".....",) * 5,
}
FONT_H = 5


def block_width(text: str, sx: int) -> int:
    return sum((5 * sx + sx) if c != " " else 3 * sx for c in text) - sx


def block(g: Grid, cx: int, y0: int, text: str, t: float, t0: float, alert=0.0) -> int:
    """Big ASCII block title centred on cx. Returns rows used (0 = plain)."""
    text = text.upper()
    if any(c not in FONT for c in text):
        return 0
    sx = 2 if block_width(text, 2) <= g.cols - 8 else 1
    w = block_width(text, sx)
    if w > g.cols - 4:
        return 0
    x0 = cx - w // 2
    band = frac(t * 0.22) * (w + 40) - 20
    x = x0
    for gi, c in enumerate(text):
        if c == " ":
            x += 3 * sx
            continue
        rt = t0 + gi * 0.06
        for r, line in enumerate(FONT[c]):
            for k, px in enumerate(line):
                if px != "#":
                    continue
                for j in range(sx):
                    cx_ = x + k * sx + j
                    yy = y0 + r
                    if t < rt:
                        continue
                    if t < rt + 0.35:
                        n = int(hash01(cx_, yy, int(t * 30)) * len(NOISE))
                        g.set(cx_, yy, NOISE[n], DIM)
                        continue
                    lit = abs((cx_ - x0) - band - r * 1.5) < 3
                    g.set(cx_, yy, "@" if lit else "#", HOT if lit else BRIGHT)
                    # Drop shadow one cell down-right.
                    sxp, syp = cx_ + 1, yy + 1
                    if (
                        0 <= syp < g.rows
                        and 0 <= sxp < g.cols
                        and g.lv[syp][sxp] <= FAINT
                    ):
                        g.set(sxp, syp, ".", DIM)
        x += 5 * sx + sx
    return FONT_H + 1


# ---------------------------------------------------------------- frame


def _perimeter(cols: int, rows: int):
    pts = [(x, 0) for x in range(cols)]
    pts += [(cols - 1, y) for y in range(1, rows)]
    pts += [(x, rows - 1) for x in range(cols - 2, -1, -1)]
    pts += [(0, y) for y in range(rows - 2, 0, -1)]
    return pts


def border(g: Grid, t: float, boot=9.0, alert=0.0):
    pts = _perimeter(g.cols, g.rows)
    L = len(pts)
    p = sstep(0.0, 0.7, boot)
    half = int(L / 2 * p)
    corners = {(0, 0), (g.cols - 1, 0), (0, g.rows - 1), (g.cols - 1, g.rows - 1)}
    # Draws in from the top-left corner both ways round.
    for i in list(range(half)) + list(range(L - half, L)):
        x, y = pts[i]
        if (x, y) in corners:
            c = "+"
        elif y in (0, g.rows - 1):
            c = "+" if x % 16 == 0 else "-"
        else:
            c = "+" if y % 8 == 0 else "|"
        g.set(x, y, c, MID if c == "+" else DIM)
    if alert > 0.05 and p >= 1:
        head = int(t * 140) % L
        for k in range(24):
            x, y = pts[(head - k) % L]
            g.set(
                x, y, "=" if y in (0, g.rows - 1) else "#", ACCENT if k < 12 else BRIGHT
            )
            x, y = pts[(head - k + L // 2) % L]
            g.set(
                x, y, "=" if y in (0, g.rows - 1) else "#", ACCENT if k < 12 else BRIGHT
            )


def texture(g: Grid, t: float, alert=0.0):
    """Faint drifting dot field and a rolling scan line."""
    sy = int(t * 5) % max(1, g.rows)
    for y in range(2, g.rows - 2, 2):
        off = 3 if y % 4 else 0
        for x in range(2 + off, g.cols - 2, 6):
            w = 0.5 + 0.5 * math.sin(x * 0.05 + y * 0.17 - t * 1.4)
            if w > 0.72:
                g.set(x, y, "." if w < 0.95 else "+", FAINT)
    if 1 < sy < g.rows - 2:
        for x in range(2, g.cols - 2, 2):
            g.set(x, sy, "-", FAINT)


def spinner(t: float) -> str:
    return "|/-\\"[int(t * 10) % 4]


def _assemble(g: Grid, src: Grid, cx: int, cy: int, R: float, ax: float, boot: float):
    """Merge src into g as if built from noise, spreading out from the pupil."""
    p = sstep(0.25, 1.9, boot) * 1.15
    for y in range(src.rows):
        row, lrow = src.ch[y], src.lv[y]
        for x in range(src.cols):
            if lrow[x] == BLANK:
                continue
            d = math.hypot((x - cx) / (R * ax * 2.0), (y - cy) / (R * 1.1))
            r = d * 0.62 + hash01(x, y) * 0.22
            if r < p - 0.1:
                g.set(x, y, row[x], lrow[x])
            elif r < p + 0.06:
                k = int(hash01(x, y, int(boot * 24)) * len(NOISE))
                g.set(x, y, NOISE[k], HOT if r < p - 0.04 else DIM)


def _streams(g: Grid, cx: int, cy: int, rx: float, ry: float, sv: float, n: int = 90):
    """Glyphs pour in from the edges into the shut eye: footage being stitched."""
    for i in range(n):
        h = hash01(i, 4.0)
        a = hash01(i, 1.0) * TAU
        far = 0.55 + 0.45 * hash01(i, 2.0)
        u = frac(sv * (0.55 + 0.4 * hash01(i, 3.0)) + h)
        e = u * u * (3 - 2 * u)
        r = far * (1 - e)
        x = int(round(cx + math.cos(a) * g.cols * 0.48 * r))
        y = int(round(cy + math.sin(a) * g.rows * 0.5 * r))
        if not (1 <= x < g.cols - 1 and 1 <= y < g.rows - 1) or g.lv[y][x] > FAINT:
            continue
        # Skip the eye's own box so the shut seam stays clean.
        if abs(x - cx) < rx * 0.9 and abs(y - cy) < ry * 0.45:
            continue
        glyph = ".:+*#"[min(4, int(e * 5))]
        g.set(
            x,
            y,
            glyph,
            FAINT if e < 0.3 else DIM if e < 0.6 else MID if e < 0.85 else BRIGHT,
        )


def compose(
    cols: int,
    rows: int,
    t: float,
    *,
    ax=2.0,
    gaze=(0.0, 0.0),
    openness=1.0,
    alert=0.0,
    pupil=0.18,
    zoom=1.0,
    boot=9.0,
    title="WORK ONGOING",
    title_t0=0.8,
    status="",
    clock="",
    header="JARVIS :: WATCH",
    rec_on=False,
    recording=False,
    live=0.0,
    tracking=None,
    busy=False,
    eye_y=0.38,
    trk=True,
    sv=None,
    done=False,
    trace=None,
    door=None,
) -> Grid:
    """Lay out one full frame. Pure: same inputs, same grid.

    sv: seconds since saving began (None when not saving). done: save
    finished. trace: recent motion levels (0..1), oldest first.
    """
    g = Grid(max(1, cols), max(1, rows))
    if cols < 20 or rows < 8:
        g.put(1, rows // 2, ascii_only(title)[: cols - 2], BRIGHT)
        return g
    saving = sv is not None
    texture(g, t, alert)
    border(g, t, boot, alert)
    # Header: name left, real clock right.
    if boot > 0.3:
        g.wipe(1, 1, g.cols - 2)
        decode(g, 3, 1, ascii_only(header), boot, 0.3, MID, dur=0.4, seed=1)
        if clock:
            ck = ascii_only(clock)
            decode(g, cols - 3 - len(ck), 1, ck, boot, 0.45, BRIGHT, dur=0.3, seed=2)
    R = max(3.0, min(rows * 0.2, cols / (ax * 2.0 * 1.6 * 2))) * zoom
    cx, cy = cols // 2, int(rows * eye_y)
    rx, ry = R * ax * 2.0 * 1.22, R * 1.24
    if zoom <= 1.05:
        reticle(g, cx, cy, R, ax, t, boot, alert)
        if boot > 3.0 and not saving:
            sonar(g, cx, cy, rx, ry, t, alert)
    if zoom <= 1.05 and boot < 3.2 and door is None:
        # Startup: eye assembles out of noise, then the lids lift.
        tmp = Grid(cols, rows)
        brow(tmp, cx, cy, R, ax, t, openness, alert)
        eye(
            tmp,
            cx,
            cy,
            R,
            ax,
            t,
            openness=openness,
            gaze=gaze,
            alert=alert,
            pupil=pupil,
        )
        _assemble(g, tmp, cx, cy, R, ax, boot)
    else:
        if zoom <= 1.05:
            brow(g, cx, cy, R, ax, t, openness, alert)
        eye(g, cx, cy, R, ax, t, openness=openness, gaze=gaze, alert=alert, pupil=pupil)
    if saving and zoom <= 1.05:
        if not done:
            _streams(g, cx, cy, rx, ry, sv)
        elif sv is not None:
            for i in range(4):
                u = frac(sv * 0.5 + i / 4)
                g.set(
                    int(cx + rx * 0.9 + u * 6),
                    int(cy - ry * 0.4 - u * 6),
                    "zZz"[i % 3] if u < 0.85 else ".",
                    MID if u < 0.6 else DIM,
                )
    if zoom > 1.05:
        return g
    # Lock-on brackets close around the iris when something moves.
    if alert > 0.3 and openness > 0.5 and not saving:
        icx = cx + gaze[0] * R * ax * 2.0 * 0.42
        icy = cy + gaze[1] * R * 0.36
        k = 1.0 + 0.7 * (1 - sstep(0.3, 0.8, alert)) + 0.08 * math.sin(t * 12)
        bw, bh = int(R * ax * 0.95 * k), int(R * 0.85 * k)
        for sx in (-1, 1):
            for sy in (-1, 1):
                x, y = int(icx + sx * bw), int(icy + sy * bh)
                g.set(x, y, "+", ACCENT)
                for i in range(1, 4):
                    g.set(x - sx * i, y, "-", ACCENT)
                g.set(x, y - sy, "|", ACCENT)
    # Side readouts, real values only: gaze on the left, motion on the right.
    if cols >= 110 and boot > 2.8 and not saving:
        _side_meters(g, cx, cy, R, ax, gaze, alert, trace, t)
    # Startup caption above the eye: the real stages of coming up.
    if boot < 3.7:
        stage = (
            "POWER"
            if boot < 1.0
            else "OPTICS"
            if boot < 1.9
            else "FOCUS"
            if boot < 2.9
            else "LOCKED"
        )
        cap = f"[ {stage} ]"
        g.wipe(cx - len(cap) // 2 - 1, 3, len(cap) + 2)
        g.put(cx - len(cap) // 2, 3, cap, BRIGHT if stage == "LOCKED" else MID)
    below = cy + int(R * 1.24) + 2
    g.wipe(cx - 10, below, 21)
    if not trk:
        pass
    elif tracking is not None and boot > 1.0:
        tx, ty = tracking
        g.put(cx - 9, below, f"TRK {tx:+.2f} {ty:+.2f}", ACCENT)
    elif boot > 1.0:
        g.put(cx - 9, below, "TRK  ---   --- ", DIM)
    ty0 = below + 2
    title = ascii_only(title).upper()
    tw = block_width(title, 2 if block_width(title, 2) <= cols - 8 else 1)
    g.wipe(cx - tw // 2 - 3, ty0 - 1, tw + 6, FONT_H + 4)
    used = block(g, cx, ty0, title, boot, title_t0, alert)
    if not used:
        sp = " ".join(title)
        decode(g, cx - len(sp) // 2, ty0, sp, boot, title_t0, BRIGHT, seed=3)
        used = 1
    # Rule under the title, with a breathing core.
    ry_ = ty0 + used + 1
    if boot > title_t0 + 0.3:
        rw = min(cols - 8, block_width(title, 1) + 10)
        b = 0.5 - 0.5 * math.cos(t * TAU / (1.6 if busy else 2.8))
        core = f"[ {spinner(t) if busy else 'o' if b < 0.5 else 'O'} ]"
        line = "-" * ((rw - len(core)) // 2)
        g.put(cx - rw // 2, ry_, line, DIM)
        g.put(cx - rw // 2 + len(line), ry_, core, BRIGHT if b > 0.5 or busy else MID)
        g.put(cx - rw // 2 + len(line) + len(core), ry_, line, DIM)
        if saving:
            _progress(g, cx, ry_ + 2, min(rw, 44), sv, done)
    # Status line, live dot and REC at the bottom.
    sy = rows - 3
    st = ascii_only(status)
    g.wipe(1, sy, g.cols - 2)
    if st and boot > title_t0 + 0.4:
        decode(g, cx - len(st) // 2, sy, st, boot, title_t0 + 0.4, MID, dur=0.4, seed=4)
        dot = "(*)" if live > 0.5 else "(.)"
        g.put(cx - len(st) // 2 - 6, sy, dot, BRIGHT if live > 0.5 else DIM)
    if recording:
        g.put(3, sy, "(@) R E C" if rec_on else "( ) R E C", ACCENT if rec_on else DIM)
    # CRT turn-on: a bright line sweeps down and reveals the frame under it.
    if boot < 1.0 and door is None:
        edge = int(sstep(0.0, 0.9, boot) * rows)
        for y in range(edge + 1, rows):
            for x in range(cols):
                g.ch[y][x], g.lv[y][x] = " ", BLANK
        if edge < rows:
            for x in range(cols):
                g.set(
                    x, edge, "=" if hash01(x, 1.0, int(boot * 40)) < 0.8 else "#", HOT
                )
    if door is not None:
        blast_door(g, t, door)
    return g


#: Voice activation: blast doors hold shut over a voice trace, then part.
DOOR_T = 1.1  # scene clock starts here
DOOR_OPEN = (1.0, 2.5)


def door_amount(t: float) -> float | None:
    """0 shut .. 1 open for the voice-activation intro; None once finished."""
    k = sstep(DOOR_OPEN[0], DOOR_OPEN[1], t)
    return None if k >= 1.0 else k


def blast_door(g: Grid, t: float, k: float) -> None:
    """Two armoured plates that part from the middle, hazard-striped at the lip.

    Shut, the seam carries a voice waveform and the command caption.
    """
    rows, cols = g.rows, g.cols
    mid = rows // 2
    gap = int(k * (rows / 2 + 2))
    for y in range(rows):
        d = abs(y - mid) - (0 if k <= 0 else gap)
        if k <= 0:
            d = abs(y - mid)
        if d < 0 or (k > 0 and abs(y - mid) < gap):
            continue
        lip = abs(y - mid) - gap if k > 0 else abs(y - mid)
        for x in range(cols):
            if lip <= 1:
                c = "/" if (x + y + int(t * 8)) % 6 < 3 else " "
                g.set(x, y, c, ACCENT if c == "/" else BLANK)
                if lip == 0:
                    g.set(x, y, "=", HOT)
            elif lip <= 2:
                g.set(x, y, "#", BRIGHT)
            elif (y - mid) % 6 == 0:
                g.set(x, y, "-", DIM)
            elif x % 28 == 0:
                g.set(
                    x,
                    y,
                    "o" if (y - mid) % 6 == 3 else "|",
                    MID if (y - mid) % 6 == 3 else DIM,
                )
            elif hash01(x, y) < 0.03:
                g.set(x, y, ".", FAINT)
            else:
                g.clear(x, y)
    if k <= 0.02:
        # Voice trace on the seam, loud then settling.
        amp = 2.4 * (1 - sstep(0.2, 1.0, t)) + 0.4
        for x in range(2, cols - 2):
            u = x / cols
            w = (
                math.sin(u * 40 + t * 18)
                * math.sin(u * 7 + t * 3)
                * math.exp(-(((u - 0.5) * 3.2) ** 2))
            )
            y = mid + int(round(w * amp * 2))
            g.set(
                x,
                y,
                "~" if abs(w) < 0.3 else "^" if w > 0 else "v",
                BRIGHT if abs(w) > 0.3 else MID,
            )
        cap = "VOICE COMMAND ACCEPTED"
        decode(
            g, cols // 2 - len(cap) // 2, mid - 4, cap, t, 0.1, BRIGHT, dur=0.5, seed=7
        )
        sub = "J A R V I S   ::   W A T C H   M O D E   E N G A G I N G"
        decode(
            g, cols // 2 - len(sub) // 2, mid + 4, sub, t, 0.35, MID, dur=0.5, seed=8
        )
        bar = int(sstep(0.0, DOOR_OPEN[0], t) * 30)
        g.put(cols // 2 - 16, mid + 6, "[" + "#" * bar + "." * (30 - bar) + "]", DIM)


def _side_meters(g, cx, cy, R, ax, gaze, alert, trace, t):
    H = max(6, int(R * 2))
    top = cy - H // 2
    # Left: gaze ruler with a live marker.
    lx = 5
    g.put(lx - 1, top - 2, "GAZE", MID)
    for i in range(H):
        g.set(lx, top + i, "+" if i % 4 == 0 else "|", DIM)
    my = top + int(round((gaze[1] * 0.5 + 0.5) * (H - 1)))
    g.put(lx + 1, my, f"<{gaze[1]:+.2f}", BRIGHT)
    g.put(lx - 1, top + H + 1, f"X {gaze[0]:+.2f}", MID)
    mx = lx + int(round((gaze[0] * 0.5 + 0.5) * 8))
    g.put(lx, top + H + 2, "[--------]", FAINT)
    g.set(mx, top + H + 2, "#", BRIGHT)
    # Right: motion level bar and a short history trace.
    rx_ = g.cols - 9
    g.put(rx_ - 2, top - 2, "MOTION", MID if alert < 0.3 else ACCENT)
    fill = int(round(clamp(alert) * H))
    for i in range(H):
        on = i >= H - fill
        g.set(
            rx_,
            top + i,
            "#" if on else "|",
            (ACCENT if alert > 0.3 else BRIGHT) if on else DIM,
        )
    g.put(rx_ + 1, top, "100", FAINT)
    g.put(rx_ + 1, top + H - 1, "  0", FAINT)
    if trace:
        w = 12
        vals = list(trace)[-w:]
        row = "".join(" .:-=+*#@"[int(clamp(v) * 8)] for v in vals)
        g.put(rx_ - 3, top + H + 1, "trace", FAINT)
        g.put(rx_ - 3, top + H + 2, row.rjust(w), ACCENT if alert > 0.3 else MID)


def _progress(g, cx, y, w, sv, done):
    """Indeterminate bar while stitching; full and steady once finished."""
    inner = max(8, w - 2)
    if done:
        bar = "#" * inner
        lvl = BRIGHT
    else:
        pos = (math.sin(sv * 2.4) * 0.5 + 0.5) * (inner - 8)
        bar = "".join(
            "#" if pos <= i < pos + 8 else "=" if pos - 4 <= i < pos + 12 else "."
            for i in range(inner)
        )
        lvl = MID
    g.put(cx - (inner + 2) // 2, y, "[" + bar + "]", lvl)
    g.put(cx - 4, y + 1, "SAVED  OK" if done else "STITCHING", BRIGHT if done else DIM)


# ---------------------------------------------------------------- Tk card


class AsciiCard:
    """Draws compose() frames onto a Tk canvas as layered monospace rows.

    One text item per (colour level, row): a level's row holds only that
    level's glyphs, spaces elsewhere, so the monospace layers line up.
    12 fps idle / 20 fps on motion; sustained slow frames drop to 8 fps.
    JARVIS_REDUCED_MOTION=1 freezes the animation clock.
    """

    def __init__(
        self, canvas, width: int, height: int, family: str = "DejaVu Sans Mono"
    ) -> None:
        import tkinter.font as tkfont

        self.c = canvas
        self.w, self.h = width, height
        self.tag = "watch_ascii"
        self.reduced = os.getenv("JARVIS_REDUCED_MOTION", "0") == "1"
        self.slow = os.getenv("JARVIS_WATCH_LOW_POWER", "0") == "1"
        px = max(9, round(height / 60))
        self.font = tkfont.Font(root=canvas, family=family, size=-px)
        self.cw = max(1, self.font.measure("0"))
        self.lh = max(1, self.font.metrics("linespace"))
        self.cols = max(20, width // self.cw)
        self.rows = max(8, height // self.lh)
        self.ax = self.lh / self.cw
        ox = (width - self.cols * self.cw) // 2
        oy = (height - self.rows * self.lh) // 2
        self.items = []
        self.texts = []
        try:
            for lvl in range(LEVELS):
                row_items = []
                for r in range(self.rows):
                    row_items.append(
                        canvas.create_text(
                            ox,
                            oy + r * self.lh,
                            anchor="nw",
                            text="",
                            font=self.font,
                            fill=level_hex(lvl),
                            tags=(self.tag, f"{self.tag}{lvl}"),
                        )
                    )
                self.items.append(row_items)
                self.texts.append([""] * self.rows)
        except Exception:
            with contextlib.suppress(Exception):
                canvas.delete(self.tag)
            raise
        self.gaze = Gaze()
        self.t0 = None
        self.last = None
        self.alert_drawn = -1.0
        self.dead = False
        self.slow_frames = 0
        self.saving_at = None
        self.force = False
        self.state = dict(
            g=0.0,
            clock="",
            status="",
            rec_on=False,
            recording=False,
            live=0.0,
            title="WORK ONGOING",
        )
        self.trace = []
        self.trace_at = 0.0
        # Terminal `watch` plays its own intro; voice activation gets the doors.
        self.doors = (
            os.getenv("JARVIS_WATCH_ORIGIN", "") != "terminal" and not self.reduced
        )

    def paint(self, g, pulse, clock, status, rec_on, recording, live) -> None:
        self.state.update(
            g=float(g),
            clock=clock,
            status=status,
            rec_on=bool(rec_on),
            recording=bool(recording),
            live=float(live),
        )

    def saving(self, title: str, status: str) -> None:
        if self.saving_at is None and self.last is not None:
            self.saving_at = self.last
        self.state.update(
            title=title, status=status, g=0.0, rec_on=False, recording=False
        )
        self.force = True

    def update(self, now=None, target=None, active=False) -> None:
        if self.dead:
            return
        now = time.monotonic() if now is None else now
        if not math.isfinite(now):
            return
        if not self.c.winfo_ismapped():
            self.last = None
            return
        busy = self.saving_at is not None
        gap = 1 / 8 if self.slow else 1 / 20 if active or busy else 1 / 12
        if self.reduced:
            gap = 0.5
        if self.last is not None and now - self.last < gap and not self.force:
            return
        self.force = False
        start = time.perf_counter()
        if self.t0 is None:
            self.t0 = now
        dt = 0.0 if self.last is None else min(0.25, now - self.last)
        self.last = now
        t = 3.0 if self.reduced else now - self.t0
        s = self.state
        tgt = norm_target(target) if active else None
        blink = self.gaze.update(t, dt, tgt)
        alert = s["g"]
        if now - self.trace_at > 0.25:
            self.trace_at = now
            self.trace = (self.trace + [alert])[-24:]
        ts, door = t, None
        if self.doors:
            door = door_amount(t)
            ts = max(0.0, t - DOOR_T)
        open_ = wake_open(ts) * (1.0 if self.reduced else blink)
        pupil = wake_pupil(ts) - 0.06 * alert + 0.012 * math.sin(t * 1.3)
        sv, done = None, False
        if busy:
            sv = now - self.saving_at
            done = s["title"].upper().startswith("SAVED")
            open_, pupil = saving_pose(sv, done)
        grid = compose(
            self.cols,
            self.rows,
            t,
            ax=self.ax,
            gaze=(self.gaze.x, self.gaze.y),
            openness=open_,
            alert=alert,
            pupil=pupil,
            boot=ts if not busy else t,
            title=s["title"],
            status=s["status"],
            clock=s["clock"],
            rec_on=s["rec_on"],
            recording=s["recording"],
            live=s["live"],
            tracking=tgt,
            busy=busy and not done,
            sv=sv,
            done=done,
            trace=self.trace,
            door=door,
        )
        self._push(grid, alert)
        cost = time.perf_counter() - start
        self.slow_frames = (
            self.slow_frames + 1 if cost > 0.03 else max(0, self.slow_frames - 1)
        )
        if self.slow_frames >= 5:
            self.slow = True

    def _push(self, grid: Grid, alert: float) -> None:
        if abs(alert - self.alert_drawn) > 0.02 or (
            alert in (0.0, 1.0) and alert != self.alert_drawn
        ):
            for lvl in range(1, LEVELS):
                self.c.itemconfigure(f"{self.tag}{lvl}", fill=level_hex(lvl, alert))
            self.alert_drawn = alert
        for r in range(self.rows):
            ch, lv = grid.ch[r], grid.lv[r]
            present = set(lv)
            for lvl in range(1, LEVELS):
                if lvl in present:
                    text = "".join(
                        c if l == lvl else " " for c, l in zip(ch, lv)
                    ).rstrip()
                else:
                    text = ""
                if text != self.texts[lvl][r]:
                    self.c.itemconfigure(self.items[lvl][r], text=text)
                    self.texts[lvl][r] = text

    def destroy(self) -> None:
        self.dead = True
        with contextlib.suppress(Exception):
            self.c.delete(self.tag)
        self.items.clear()
