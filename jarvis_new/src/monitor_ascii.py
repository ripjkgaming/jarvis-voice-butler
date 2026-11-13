"""ASCII Monitor Mode card: a bank-vault door that locks shut on every screen.

compose() is pure: one frame as a grid of 7-bit characters plus a colour level
per cell, shared by the fullscreen Tk card (VaultCard) and the terminal
`monitor` transition (~/deck/lib/jarvis_monitor.py). It reuses the Grid, palette
and helpers of watch_ascii so both modes look like one family, but the scene is
new: the door assembles ring by ring, the handwheel spins down, twelve locking
bolts slam out one at a time, a shockwave rolls off and the stamp LOCKED lands.
Idle, the combination dial ticks round, a radar sweep circles the rim and an
integrity scan crosses the door.

Nothing here reads files, opens devices or records anything.
"""

from __future__ import annotations

import contextlib
import math
import os
import time

import watch_ascii as wa
from watch_ascii import (
    ACCENT,
    BLANK,
    BRIGHT,
    DIM,
    FAINT,
    HOT,
    ICE,
    MID,
    TAU,
    Grid,
    ascii_only,
    clamp,
    decode,
    hash01,
    sstep,
)

BOLTS = 12
#: Scene clock (seconds) of each stage.
T_SCAN = 0.9  # CRT line wipes the frame in
T_BUILD = (0.5, 2.3)  # rings and face assemble
T_SPIN = (0.9, 3.9)  # handwheel spins down
T_BOLT0 = 2.3  # first bolt slams home
BOLT_GAP = 0.13
T_LOCKED = T_BOLT0 + BOLT_GAP * BOLTS + 0.3  # shockwave and stamp
T_SETTLED = T_LOCKED + 1.2
#: A bay starts this much later than the previous one, so doors shut in turn.
STAGGER = 0.5


#: Wallpaper transition: the desktop wall implodes into the vault.
WALL_PATH = os.path.expanduser("~/deck/assets/locke-wall-ascii.png")
WALL_HOLD = 0.4  # the real wallpaper holds, then converts to glyphs
WALL_T = 2.1  # collapse finishes
VAULT_AT = 1.0  # the door starts building under the last of the collapse
_WALLS: dict = {}


def load_wall(cols: int, rows: int, path: str | None = None):
    """Wallpaper as (glyph codes, colour levels) numpy arrays of rows x cols.

    None when the image, PIL or numpy is missing: the scene then runs without
    the wallpaper transition. Cached per size.
    """
    key = (cols, rows, path or WALL_PATH)
    if key in _WALLS:
        return _WALLS[key]
    out = None
    try:
        import numpy as np
        from PIL import Image

        im = Image.open(path or WALL_PATH).convert("L").resize((cols, rows), Image.BOX)
        v = np.asarray(im, dtype=np.float32)
        v = np.clip((v - 24.0) / 140.0, 0.0, 1.0)  # background (~19) -> 0
        ramp = np.frombuffer(wa.RAMP.encode(), dtype=np.uint8)
        idx = np.minimum(len(ramp) - 1, (v * len(ramp) * 1.6).astype(int))
        ch = np.where(v > 0.05, ramp[np.maximum(idx, 1)], 32).astype(np.uint8)
        lv = np.where(v > 0.05, np.minimum(ICE, 1 + (v * 5.5).astype(int)), 0).astype(
            np.uint8
        )
        out = (ch, lv)
    except Exception:
        out = None
    _WALLS[key] = out
    return out


def _wall_layer(
    g: Grid, wall, ts: float, ax: float, cx: int, cy: int, R: float
) -> None:
    """Desktop wallpaper, then a swirling implosion into the vault, then a dim ghost."""
    import numpy as np

    ch, lv = wall
    rows, cols = ch.shape
    yy, xx = np.mgrid[0:rows, 0:cols]
    dx = (xx - cx).astype(np.float32)
    dy = ((yy - cy) * ax).astype(np.float32)
    d = np.hypot(dx, dy)
    e = float(sstep(WALL_HOLD, WALL_T, ts))
    if e >= 1.0:
        # Ghost of the wallpaper behind the vault, outside the door.
        fade = float(sstep(WALL_T, WALL_T + 1.8, ts))
        keep = (d > R * 1.34) & (ch != 32)
        keep &= hash_grid(rows, cols) < fade * 0.8
        _blit(g, np.where(keep, ch, 32), np.where(keep, np.minimum(lv, FAINT + 1), 0))
        return
    sc = 1.0 - 0.965 * e**1.5
    dmax = float(math.hypot(cols / 2, rows / 2 * ax))
    ang = e * 3.4 * np.clip(1.0 - d / dmax, 0.0, 1.0) ** 1.4
    ca, sa = np.cos(ang), np.sin(ang)
    vx = (dx * ca + dy * sa) / sc
    vy = (-dx * sa + dy * ca) / sc
    sx = np.rint(cx + vx).astype(int)
    sy = np.rint(cy + vy / ax).astype(int)
    inside = (sx >= 0) & (sx < cols) & (sy >= 0) & (sy < rows)
    sxc, syc = np.clip(sx, 0, cols - 1), np.clip(sy, 0, rows - 1)
    och = np.where(inside, ch[syc, sxc], 32)
    olv = np.where(inside, lv[syc, sxc], 0)
    if e > 0:
        # Screen edge of the collapsing wallpaper: a hot frame.
        edge = (
            (np.abs(cx + vx - (cols - 1) / 2) > cols / 2 - 1.2)
            | (np.abs(cy + vy / ax - rows / 2) > rows / 2 - 0.9)
        ) & inside
        och = np.where(edge, ord("#"), och)
        olv = np.where(edge, HOT, olv)
        # Speed streaks tearing toward the centre.
        streak = (
            (~inside)
            & (np.hypot(dx, dy) < dmax * (1.1 - e))
            & (hash_grid(rows, cols, int(ts * 24)) < 0.012 * e)
        )
        och = np.where(streak, ord("-"), och)
        olv = np.where(streak, MID, olv)
        # Brighten as it compresses.
        olv = np.where(
            (olv > 0) & (olv < BRIGHT), np.minimum(BRIGHT, olv + int(e * 2.5)), olv
        )
    if ts < WALL_HOLD + 0.35:
        # Conversion glitch: a hot scan bar sweeps down as the wall turns to glyphs.
        bar = int(sstep(WALL_HOLD - 0.1, WALL_HOLD + 0.3, ts) * rows)
        if 0 <= bar < rows:
            och[bar, :] = ord("=")
            olv[bar, :] = HOT
    _blit(g, och, olv)


_HASH: dict = {}


def hash_grid(rows: int, cols: int, k: int = 0):
    import numpy as np

    key = (rows, cols, k)
    if key not in _HASH:
        if len(_HASH) > 400:
            _HASH.clear()
        yy, xx = np.mgrid[0:rows, 0:cols]
        h = np.sin(xx * 12.9898 + yy * 78.233 + k * 37.719) * 43758.5453
        _HASH[key] = (h - np.floor(h)).astype(np.float32)
    return _HASH[key]


def _blit(g: Grid, ch, lv) -> None:
    for y in range(g.rows):
        g.ch[y] = list(bytes(ch[y]).decode("ascii"))
        g.lv[y] = lv[y].tolist()


def dirch(vx: float, vy: float) -> str:
    """Line glyph for a pixel-space direction (y down). Pure."""
    a = math.atan2(vy, vx) % math.pi
    if a < math.pi / 8 or a > 7 * math.pi / 8:
        return "-"
    if a < 3 * math.pi / 8:
        return "\\"
    if a < 5 * math.pi / 8:
        return "|"
    return "/"


def _angdiff(a: float, b: float) -> float:
    """Signed shortest a-b in [-pi, pi]."""
    return (a - b + math.pi) % TAU - math.pi


class _Pen:
    """Polar drawing on a grid whose cells are ax times taller than wide."""

    def __init__(self, g: Grid, cx: int, cy: int, ax: float) -> None:
        self.g, self.cx, self.cy, self.ax = g, cx, cy, ax

    def xy(self, r: float, a: float) -> tuple[int, int]:
        return (
            int(round(self.cx + r * math.cos(a))),
            int(round(self.cy + r * math.sin(a) / self.ax)),
        )

    def spoke(self, r0: float, r1: float, a: float, ch: str | None, lv: int) -> None:
        n = max(2, int(abs(r1 - r0) * 1.6))
        for i in range(n + 1):
            r = r0 + (r1 - r0) * i / n
            x, y = self.xy(r, a)
            self.g.set(x, y, ch or dirch(math.cos(a), math.sin(a)), lv)

    def ring(
        self, r: float, lv: int, ch: str | None = None, sweep: float = 1.0
    ) -> None:
        """Circle outline; `sweep` < 1 draws only that share, clockwise from 12."""
        n = max(24, int(TAU * r * 1.3))
        for i in range(n):
            a = TAU * i / n
            if (a + math.pi / 2) % TAU / TAU > sweep:
                continue
            x, y = self.xy(r, a)
            self.g.set(x, y, ch or dirch(-math.sin(a), math.cos(a)), lv)


def vault_geometry(cols: int, rows: int, ax: float) -> tuple[int, int, float]:
    """(cx, cy, R) with R in width cells. Pure."""
    big = rows >= 26
    spare = 22 if big else 7
    r_rows = max(3.0, (rows - spare) / 2 / 1.17)
    R = min(r_rows * ax, cols * 0.27)
    cx = cols // 2
    cy = 3 + int(R / ax * 1.17) + (1 if big else 0)
    return cx, cy, R


def _fill_face(
    g: Grid, cx: int, cy: int, R: float, ax: float, s: float, build: float
) -> None:
    """Door face, seams, rivets, and the ring set, revealed outward by `build`."""
    reach = R * 1.05
    y0, y1 = int(cy - reach / ax) - 1, int(cy + reach / ax) + 2
    x0, x1 = int(cx - reach) - 1, int(cx + reach) + 2
    for y in range(max(1, y0), min(g.rows - 1, y1)):
        dy = (y - cy) * ax
        for x in range(max(1, x0), min(g.cols - 1, x1)):
            dx = x - cx
            r = math.hypot(dx, dy)
            if r > R * 0.985 or r > R * build:
                continue
            a = math.atan2(dy, dx)
            u = r / R
            if u > 0.93:
                g.set(x, y, "#", BRIGHT)  # outer rim
            elif u > 0.885:
                g.set(x, y, "=", MID)  # bevel
            elif u > 0.74:
                # Tick ring: a mark every 6 degrees, longer every 30.
                k = a / TAU * 60
                near = abs(k - round(k))
                if near < 0.16:
                    g.set(
                        x,
                        y,
                        "|" if round(k) % 5 == 0 else ":",
                        DIM if round(k) % 5 else MID,
                    )
                elif hash01(x, y) < 0.05:
                    g.set(x, y, ".", FAINT)
            elif u > 0.70:
                g.set(x, y, "-", DIM)
            elif u > 0.40:
                # Armoured plate: radial panel seams every 45 degrees, rivets.
                seam = abs(_angdiff(a, round(a / (TAU / 8)) * (TAU / 8))) * r
                if seam < 0.5:
                    g.set(x, y, dirch(math.cos(a), math.sin(a)), DIM)
                elif hash01(x, y, 3.0) < 0.012:
                    g.set(x, y, "o", MID)
                elif hash01(x, y, 1.0) < 0.20:
                    g.set(x, y, ".", FAINT)


def _bolts(pen: _Pen, R: float, s: float) -> None:
    for i in range(BOLTS):
        a = TAU * i / BOLTS - math.pi / 2
        t0 = T_BOLT0 + i * BOLT_GAP
        ext = sstep(t0, t0 + 0.22, s)
        # Housing on the rim, always there once the door exists.
        pen.spoke(R * 0.99, R * 1.05, a, "H", DIM)
        if ext <= 0:
            continue
        flash = 0.0 < s - (t0 + 0.22) < 0.18
        lv = HOT if flash else ICE
        pen.spoke(R * 1.03, R * (1.03 + 0.15 * ext), a, "#", lv)
        if ext >= 1:
            x, y = pen.xy(R * 1.19, a)
            pen.g.set(x, y, "@" if flash else "O", ACCENT if flash else BRIGHT)


def _wheel(pen: _Pen, R: float, s: float, ax: float) -> None:
    g = pen.g
    p = clamp((s - T_SPIN[0]) / (T_SPIN[1] - T_SPIN[0]))
    theta = 5 * TAU * (1 - (1 - p) ** 3) if s >= T_SPIN[0] else 0.0
    spinning = 0.02 < p < 0.97
    rw = R * 0.40
    pen.ring(rw, BRIGHT, "o" if not spinning else None)
    pen.ring(rw * 0.12 + 0.01, HOT, "@")
    for k in range(6):
        a = theta + k * TAU / 6
        pen.spoke(R * 0.07, rw, a, None, ICE)
        x, y = pen.xy(rw * 1.0, a)
        g.set(x, y, "O", ACCENT)
    g.set(pen.cx, pen.cy, "@", HOT)
    # Motion blur: faint ghost spokes while the wheel is fast.
    if spinning and p < 0.7:
        for k in range(6):
            a = theta - 0.35 + k * TAU / 6
            pen.spoke(R * 0.15, rw * 0.9, a, ".", FAINT)


def _dial(pen: _Pen, R: float, s: float) -> None:
    """Combination dial between wheel and plate; steps one notch every 1.7 s."""
    g = pen.g
    rd = R * 0.53
    if s < T_SPIN[1] - 0.6:
        return
    idle = max(0.0, s - T_LOCKED)
    steps = idle / 1.7
    n = math.floor(steps) + sstep(0.0, 0.25, steps - math.floor(steps))
    dir_ = -1 if int(math.floor(steps)) % 7 in (3, 4, 5) else 1
    a0 = -math.pi / 2 + dir_ * n * (TAU / 24)
    for i in range(24):
        a = -math.pi / 2 + i * TAU / 24
        x, y = pen.xy(rd, a)
        g.set(x, y, "+" if i % 6 == 0 else ".", MID if i % 6 == 0 else DIM)
    x, y = pen.xy(rd, a0)
    g.set(x, y, "V", ACCENT)
    x, y = pen.xy(rd - 1.2, a0)
    g.set(x, y, "|" if True else ".", ACCENT)
    # Fixed index mark on top.
    x, y = pen.xy(rd + 1.5, -math.pi / 2)
    g.set(x, y, "v", HOT)


def _radar(pen: _Pen, R: float, s: float) -> None:
    """Sweep with a fading tail around the tick ring, once the door is shut."""
    if s < T_LOCKED:
        return
    head = (s - T_LOCKED) * 1.15 % TAU
    ring = R * 0.80
    n = int(TAU * ring * 1.4)
    for i in range(n):
        a = TAU * i / n
        d = _angdiff(head, a) % TAU
        if d > 1.5:
            continue
        lv = HOT if d < 0.08 else BRIGHT if d < 0.35 else MID if d < 0.8 else DIM
        x, y = pen.xy(ring, a)
        pen.g.set(x, y, "*" if d < 0.08 else "+" if d < 0.35 else ":", lv)


def _scan(g: Grid, cx: int, cy: int, R: float, ax: float, s: float) -> None:
    """Integrity scan: a bright line crossing the door every 13 s."""
    if s < T_SETTLED:
        return
    k = ((s - T_SETTLED) % 13.0) / 1.4
    if k >= 1.0:
        return
    y = int(cy - R / ax + k * 2 * R / ax)
    dy = (y - cy) * ax
    half = math.sqrt(max(0.0, (R * 0.9) ** 2 - dy * dy))
    for x in range(int(cx - half), int(cx + half) + 1):
        g.set(x, y, "=", HOT if hash01(x, y, int(s * 20)) > 0.3 else ACCENT)
        if y + 1 < g.rows - 1 and g.lv[y + 1][x] > BLANK:
            g.lv[y + 1][x] = max(g.lv[y + 1][x], MID)


def _shock(pen: _Pen, R: float, s: float) -> None:
    k = (s - T_LOCKED) / 1.1
    if not 0.0 <= k < 1.0:
        return
    r = R * (1.05 + k * 0.9)
    pen.ring(
        r, HOT if k < 0.35 else BRIGHT if k < 0.7 else DIM, "=" if k < 0.7 else "."
    )


def compose(
    cols: int,
    rows: int,
    t: float,
    *,
    ax: float = 2.0,
    bay: int = 0,
    bays: int = 1,
    clock: str = "",
    left: tuple = (),
    right: tuple = (),
    status: str = "",
    stagger: float = STAGGER,
    boot: float | None = None,
    wall=None,
) -> Grid:
    """One frame of the vault scene. Pure. `wall` = load_wall() result."""
    g = Grid(cols, rows)
    ts = max(0.0, (t if boot is None else boot) - bay * stagger)
    s = ts - VAULT_AT if wall is not None else ts
    cx, cy, R = vault_geometry(cols, rows, ax)
    pen = _Pen(g, cx, cy, ax)
    big = rows >= 26

    if wall is not None:
        _wall_layer(g, wall, ts, ax, cx, cy, R)
        if ts < VAULT_AT - 0.3:
            msg = "[ LOCKDOWN INITIATED ]"
            decode(g, cx - len(msg) // 2, 2, msg, ts, WALL_HOLD, HOT, dur=0.6, seed=9)
            return g
    else:
        # Background: faint drifting grid so the black is never dead.
        for y in range(2, rows - 2, 3):
            for x in range(3 + (y // 3 % 2) * 4, cols - 2, 8):
                w = 0.5 + 0.5 * math.sin(x * 0.07 + y * 0.21 - t * 0.9)
                if w > 0.8:
                    g.set(x, y, ".", FAINT)

    # Frame.
    wa.border(g, t, boot=s + 0.2)

    build = sstep(T_BUILD[0], T_BUILD[1], s)
    if build > 0:
        _fill_face(g, cx, cy, R, ax, s, build)
        pen.ring(R * 0.985, BRIGHT, "#", sweep=min(1.0, build * 1.3))
        # Hinge barrels on the left edge of the door.
        for k in (-0.5, 0.5):
            x, y = pen.xy(R * 1.02, math.pi + k)
            for yy in range(y - 1, y + 2):
                g.set(x, yy, "[", MID)
                g.set(x - 1, yy, "]", DIM)
    if s >= T_BOLT0 - 0.4:
        _bolts(pen, R, s)
    if build > 0.5:
        _dial(pen, R, s)
        _wheel(pen, R, s, ax)
        _radar(pen, R, s)
    _shock(pen, R, s)
    if wall is not None:
        # Impact: the collapsed screen punches out a ring as the door takes over.
        k = (ts - (WALL_T - 0.25)) / 0.9
        if 0.0 <= k < 1.0:
            pen.ring(
                R * (0.25 + 1.5 * k),
                HOT if k < 0.4 else BRIGHT if k < 0.75 else MID,
                "*" if k < 0.4 else "=" if k < 0.75 else ".",
            )
    _scan(g, cx, cy, R, ax, s)

    # Header.
    hdr = f"JARVIS :: MONITOR :: VAULT {bay + 1:02d}/{bays:02d}"
    g.wipe(2, 1, len(hdr) + 2)
    decode(g, 3, 1, hdr, s, 0.2, MID, dur=0.5, seed=5)
    if clock:
        g.put(cols - len(clock) - 3, 1, ascii_only(clock), BRIGHT)

    # Real readouts, either side of the door.
    for lines, side in ((left, 0), (right, 1)):
        for i, line in enumerate(lines):
            line = ascii_only(str(line))
            x = 4 if side == 0 else cols - 4 - len(line)
            yy = 4 + i * 2
            if x < 2 or yy >= rows - 4:
                continue
            if x + len(line) > cx - R * 1.3 and side == 0 and cols < 100:
                continue
            if x < cx + R * 1.3 and side == 1 and cols < 100:
                continue
            decode(
                g, x, yy, line, s, 1.4 + i * 0.12, MID, dur=0.4, seed=10 + i + side * 7
            )

    # Door state caption, under the dial of the stage we are in.
    stage = (
        "POWER"
        if s < 0.9
        else "ALIGN"
        if s < T_BOLT0
        else "BOLTS %02d/%02d" % (min(BOLTS, int((s - T_BOLT0) / BOLT_GAP) + 1), BOLTS)
        if s < T_LOCKED
        else "LOCKED"
    )
    below = cy + int(R / ax * 1.22) + 1
    if s < 0.3:
        pass
    elif big and below < rows - 10:
        cap = f"[ {stage} ]"
        g.wipe(cx - len(cap) // 2 - 1, below, len(cap) + 2)
        g.put(cx - len(cap) // 2, below, cap, HOT if stage == "LOCKED" else BRIGHT)
        # Title block.
        ty = below + 2
        title = "MONITORING"
        used = wa.block(g, cx, ty, title, s, 2.6)
        if used:
            sub = "JARVIS IS MONITORING THIS DEVICE"
            ry = ty + used
            decode(g, cx - len(sub) // 2, ry, sub, s, 3.1, BRIGHT, dur=0.7, seed=2)
    else:
        sub = f"[ {stage} ] JARVIS IS MONITORING THIS DEVICE"
        sub = sub[: max(0, cols - 6)]
        yy = min(rows - 3, below)
        g.wipe(1, yy, cols - 2)
        g.put(max(2, cx - len(sub) // 2), yy, sub, BRIGHT)

    # Status, bottom.
    st = ascii_only(status)
    sy = rows - 2
    g.wipe(1, sy, cols - 2)
    if st and s > 1.0:
        decode(
            g,
            max(2, cx - len(st) // 2),
            sy,
            st[: cols - 4],
            s,
            1.0,
            MID,
            dur=0.4,
            seed=4,
        )

    # CRT turn-on: a bright line sweeps down and reveals the frame under it.
    if wall is None and s < T_SCAN:
        edge = int(sstep(0.0, T_SCAN, s) * rows)
        for y in range(edge + 1, rows):
            for x in range(cols):
                g.ch[y][x], g.lv[y][x] = " ", BLANK
        if edge < rows:
            for x in range(cols):
                g.set(x, edge, "=" if hash01(x, 1.0, int(s * 40)) < 0.8 else "#", HOT)
    return g


class VaultCard(wa.AsciiCard):
    """Tk canvas renderer for compose(); the base class owns the text items."""

    def __init__(
        self, canvas, width, height, family="DejaVu Sans Mono", bay=0, bays=1, info=None
    ) -> None:
        super().__init__(canvas, width, height, family)
        self.tag = "monitor_ascii"
        self.bay, self.bays = bay, bays
        self.info = info or (lambda: ((), (), ""))
        self.info_at = -9.0
        self.info_val = ((), (), "")
        # Terminal `monitor` already played the scan and build; pick up mid-scene.
        try:
            self.wall = load_wall(self.cols, self.rows)
        except Exception:
            self.wall = None
        hand = (VAULT_AT + 1.8) if self.wall is not None else 2.0
        self.skip = (
            hand if os.getenv("JARVIS_MONITOR_ORIGIN", "") == "terminal" else 0.0
        )
        self.png = None
        self._png_on(width, height)

    def _png_on(self, width: int, height: int) -> None:
        """Real wallpaper for the first beat, so the card starts as the desktop."""
        if self.wall is None or self.skip:
            return
        try:
            from PIL import Image, ImageTk

            im = Image.open(WALL_PATH).convert("RGB").resize((width, height))
            self._png_img = ImageTk.PhotoImage(im, master=self.c)
            self.png = self.c.create_image(
                0, 0, anchor="nw", image=self._png_img, tags=("monitor_png",)
            )
        except Exception:
            self.png = None

    def _png_off(self) -> None:
        if self.png is not None:
            with contextlib.suppress(Exception):
                self.c.delete("monitor_png")
            self.png = None

    def update(self, now=None, target=None, active=False) -> None:
        if self.dead:
            return
        now = time.monotonic() if now is None else now
        if not math.isfinite(now) or not self.c.winfo_ismapped():
            self.last = None
            return
        gap = 1 / 8 if self.slow else 1 / 15
        if self.reduced:
            gap = 0.5
        if self.last is not None and now - self.last < gap:
            return
        start = time.perf_counter()
        if self.t0 is None:
            self.t0 = now
        self.last = now
        t = 8.0 if self.reduced else now - self.t0 + self.skip
        if now - self.info_at > 1.0:
            self.info_at = now
            try:
                self.info_val = self.info()
            except Exception:
                self.info_val = ((), (), "")
        left, right, status = self.info_val
        if self.png is not None:
            # Desktop beat: show the real wallpaper, draw no glyphs yet.
            if t - self.bay * STAGGER < WALL_HOLD:
                return
            self._png_off()
        grid = compose(
            self.cols,
            self.rows,
            t,
            ax=self.ax,
            bay=self.bay,
            bays=self.bays,
            clock=time.strftime("%H:%M:%S"),
            left=left,
            right=right,
            status=status,
            wall=self.wall,
        )
        self._push(grid, 0.0)
        cost = time.perf_counter() - start
        self.slow_frames = (
            self.slow_frames + 1 if cost > 0.045 else max(0, self.slow_frames - 1)
        )
        if self.slow_frames >= 6:
            self.slow = True
