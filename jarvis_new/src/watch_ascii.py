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


def decode(g: Grid, x0: int, y: int, text: str, t: float, t0: float, level: int, dur=0.6, seed=0.0):
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


# ---------------------------------------------------------------- gaze


class Gaze:
    """Idle saccades + blinks; follows the real motion target when given."""

    def __init__(self) -> None:
        self.x = self.y = 0.0
        self.tx = self.ty = 0.0
        self.seg = -1
        self.blink_at = 3.2

    def update(self, t: float, dt: float, target=None) -> float:
        if target is not None:
            self.tx, self.ty = target
        else:
            seg = int(t / 2.3)
            if seg != self.seg:
                self.seg = seg
                self.tx = (hash01(seg, 1.0) - 0.5) * 0.8
                self.ty = (hash01(seg, 2.0) - 0.5) * 0.45
        k = 1 - math.exp(-max(0.0, dt) * (14 if target is not None else 8))
        self.x += (self.tx - self.x) * k
        self.y += (self.ty - self.y) * k
        if self.blink_at < t < self.blink_at + 0.24:
            return abs((t - self.blink_at) / 0.12 - 1)
        if t >= self.blink_at + 0.24:
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


def _lid(nx: float, openness: float) -> float:
    return max(0.0, 1 - nx * nx) ** 0.8 * 0.62 * openness


def eye(g: Grid, cx: int, cy: int, R: float, ax: float, t: float, *,
        openness=1.0, gaze=(0.0, 0.0), alert=0.0, pupil=0.18):
    """Almond eye: slope-traced lids, radial-stroke iris, glowing pupil.

    R is the eye half-height scale in rows; ax is cell height / cell width,
    so one unit across is R * ax columns and the eye stays round on screen.
    """
    W = R * ax * 2.0  # half width in columns
    x0, x1 = max(0, int(cx - W) - 1), min(g.cols - 1, int(cx + W) + 1)
    gx, gy = gaze
    if openness <= 0.02:
        for x in range(x0 + 1, x1):
            nx = (x - cx) / W
            c = "=" if abs(nx) < 0.82 else "-"
            g.set(x, cy, c, ICE if abs(nx) < 0.5 else BRIGHT)
            if abs(nx) < 0.9 and x % 3 == 0:
                g.set(x, cy + 1, "'" if abs(nx) < 0.6 else ".", DIM)
        return
    # Interior: sclera, iris and pupil, clipped by the lids.
    icx = cx + gx * W * 0.42
    icy = cy + gy * R * 0.36
    ir_max = 0.74
    scan = frac(t * 0.3) * 2.6 - 1.3
    spin = t * 0.35
    for y in range(max(0, int(cy - R * 0.7) - 1), min(g.rows, int(cy + R * 0.7) + 2)):
        for x in range(x0, x1 + 1):
            nx = (x - cx) / W
            if abs(nx) >= 1.0:
                continue
            ny = (y - cy) / (R * 1.6)
            if abs(ny) >= _lid(nx, openness) - 0.02:
                continue
            ix = (x - icx) / (R * ax)
            iy = (y - icy) / R
            ir = math.hypot(ix, iy)
            if ir < pupil:
                # Solid dark pupil; glows red while something moves.
                if pupil < 0.3:
                    g.set(x, y, "@", ACCENT if alert > 0.3 else FAINT)
                else:
                    g.clear(x, y)
                continue
            if ir < pupil + 0.06:
                g.set(x, y, "o", ICE)
                continue
            if ir < ir_max:
                th = math.atan2(iy, ix)
                fib = 0.5 + 0.5 * math.sin(th * 22 + spin * 4 + ir * 14)
                fib *= 0.6 + 0.4 * math.sin(th * 7 - spin * 2)
                if ir > ir_max - 0.07:
                    g.set(x, y, "#" if (int(th * 9 + t * 3) % 6) else "%", BRIGHT)
                elif fib > 0.32:
                    lvl = BRIGHT if fib > 0.8 else MID if fib > 0.55 else DIM
                    g.set(x, y, _stroke(th), lvl)
                elif ir < pupil + 0.16 and frac(th / TAU * 16 - t * 0.5) < 0.5:
                    g.set(x, y, "*", MID)
                else:
                    g.set(x, y, ".", FAINT)
                continue
            # Sclera: shaded toward the lids for volume, plus a scan beam.
            depth = (1 - abs(ny) / max(_lid(nx, openness), 1e-3)) * (1 - nx * nx)
            if abs(nx - scan) < 0.025:
                g.set(x, y, ":", MID)
            elif depth > 0.15 and hash01(x, y) < 0.08 + depth * 0.3:
                g.set(x, y, ".:"[depth > 0.6], FAINT if depth < 0.6 else DIM)
            else:
                g.clear(x, y)
    # Catch-light on the iris.
    hx, hy = int(round(icx - R * ax * 0.16)), int(round(icy - R * 0.17))
    if abs(hy - cy) / (R * 1.6) < _lid((hx - cx) / W, openness):
        g.set(hx, hy, "*", HOT)
    # Lids traced as lines whose glyph follows the curve's slope.
    prev = {}
    for x in range(x0, x1 + 1):
        nx = (x - cx) / W
        if abs(nx) > 1.0:
            continue
        lid = _lid(nx, openness) * R * 1.6
        d = 1 / W
        slope = (_lid(min(1, nx + d), openness) - _lid(max(-1, nx - d), openness)) * R * 1.6 / 2
        for sign in (-1, 1):
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
        if openness > 0.35 and abs(nx) < 0.55 and x % 4 == 0:
            ly = int(round(cy - lid)) - 1
            g.set(x, ly, "\\" if nx < -0.2 else "/" if nx > 0.2 else "|", MID)
        if openness > 0.35 and abs(nx) < 0.6 and x % 5 == 2:
            g.set(x, int(round(cy + lid)) + 1, ".", DIM)
    # Corner points.
    g.set(int(round(cx - W)), cy, "<", ICE)
    g.set(int(round(cx + W)), cy, ">", ICE)


def reticle(g: Grid, cx: int, cy: int, R: float, ax: float, t: float, boot=9.0, alert=0.0):
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
                    if 0 <= syp < g.rows and 0 <= sxp < g.cols and g.lv[syp][sxp] <= FAINT:
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
            g.set(x, y, "=" if y in (0, g.rows - 1) else "#", ACCENT if k < 12 else BRIGHT)
            x, y = pts[(head - k + L // 2) % L]
            g.set(x, y, "=" if y in (0, g.rows - 1) else "#", ACCENT if k < 12 else BRIGHT)


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


def compose(cols: int, rows: int, t: float, *, ax=2.0, gaze=(0.0, 0.0),
            openness=1.0, alert=0.0, pupil=0.18, zoom=1.0, boot=9.0,
            title="WORK ONGOING", title_t0=0.8, status="", clock="",
            header="JARVIS :: WATCH", rec_on=False, recording=False,
            live=0.0, tracking=None, busy=False, eye_y=0.38, trk=True) -> Grid:
    """Lay out one full frame. Pure: same inputs, same grid."""
    g = Grid(max(1, cols), max(1, rows))
    if cols < 20 or rows < 8:
        g.put(1, rows // 2, ascii_only(title)[: cols - 2], BRIGHT)
        return g
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
    if zoom <= 1.05:
        reticle(g, cx, cy, R, ax, t, boot, alert)
    eye(g, cx, cy, R, ax, t, openness=openness, gaze=gaze, alert=alert, pupil=pupil)
    if zoom > 1.05:
        return g
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
    ry = ty0 + used + 1
    if boot > title_t0 + 0.3:
        rw = min(cols - 8, block_width(title, 1) + 10)
        b = 0.5 - 0.5 * math.cos(t * TAU / (1.6 if busy else 2.8))
        core = f"[ {spinner(t) if busy else 'o' if b < 0.5 else 'O'} ]"
        line = "-" * ((rw - len(core)) // 2)
        g.put(cx - rw // 2, ry, line, DIM)
        g.put(cx - rw // 2 + len(line), ry, core, BRIGHT if b > 0.5 or busy else MID)
        g.put(cx - rw // 2 + len(line) + len(core), ry, line, DIM)
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
    return g


# ---------------------------------------------------------------- Tk card


class AsciiCard:
    """Draws compose() frames onto a Tk canvas as layered monospace rows.

    One text item per (colour level, row): a level's row holds only that
    level's glyphs, spaces elsewhere, so the monospace layers line up.
    12 fps idle / 20 fps on motion; sustained slow frames drop to 8 fps.
    JARVIS_REDUCED_MOTION=1 freezes the animation clock.
    """

    def __init__(self, canvas, width: int, height: int, family: str = "DejaVu Sans Mono") -> None:
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
                            ox, oy + r * self.lh, anchor="nw", text="",
                            font=self.font, fill=level_hex(lvl),
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
        self.state = dict(g=0.0, clock="", status="", rec_on=False, recording=False,
                          live=0.0, title="WORK ONGOING")

    def paint(self, g, pulse, clock, status, rec_on, recording, live) -> None:
        self.state.update(g=float(g), clock=clock, status=status, rec_on=bool(rec_on),
                          recording=bool(recording), live=float(live))

    def saving(self, title: str, status: str) -> None:
        if self.saving_at is None and self.last is not None:
            self.saving_at = self.last
        self.state.update(title=title, status=status, g=0.0, rec_on=False, recording=False)
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
        open_ = sstep(0.4, 1.5, t) * (1.0 if self.reduced else blink)
        if busy:
            open_ *= 1 - sstep(0.0, 0.45, now - self.saving_at)
        alert = s["g"]
        grid = compose(
            self.cols, self.rows, t, ax=self.ax, gaze=(self.gaze.x, self.gaze.y),
            openness=open_, alert=alert, pupil=0.18 - 0.06 * alert + 0.012 * math.sin(t * 1.3),
            boot=t, title=s["title"], status=s["status"], clock=s["clock"],
            rec_on=s["rec_on"], recording=s["recording"], live=s["live"],
            tracking=tgt, busy=busy and s["title"].upper().startswith("SAVING"),
        )
        self._push(grid, alert)
        cost = time.perf_counter() - start
        self.slow_frames = self.slow_frames + 1 if cost > 0.03 else max(0, self.slow_frames - 1)
        if self.slow_frames >= 5:
            self.slow = True

    def _push(self, grid: Grid, alert: float) -> None:
        if abs(alert - self.alert_drawn) > 0.02 or (alert in (0.0, 1.0) and alert != self.alert_drawn):
            for lvl in range(1, LEVELS):
                self.c.itemconfigure(f"{self.tag}{lvl}", fill=level_hex(lvl, alert))
            self.alert_drawn = alert
        for r in range(self.rows):
            ch, lv = grid.ch[r], grid.lv[r]
            present = set(lv)
            for lvl in range(1, LEVELS):
                if lvl in present:
                    text = "".join(c if l == lvl else " " for c, l in zip(ch, lv)).rstrip()
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
