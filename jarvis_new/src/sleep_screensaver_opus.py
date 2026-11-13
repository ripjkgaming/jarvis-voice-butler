"""JARVIS//SLEEP (opus build) - ASCII standby screensaver shown while Jarvis sleeps.

Arc reactor in the centre of the primary screen, a dense telemetry HUD around
it, big ASCII clock, and a dim "STANDBY - DOUBLE CLAP TO WAKE" line. Secondary
monitors get telemetry walls. Every number shown is read live from /proc and
/sys; nothing is invented.

Rendering reuses the deck engine (~/deck/lib/jarvis_ascii.py palette, Frame,
box, reactor maths, crt_off; jarvis_raster.py glyph masks). The reactor is a
numpy port of jarvis_ascii.reactor (unit-tested for glyph parity) so a frame
costs well under a millisecond, and the Tk side only uploads changed pixels.

Burn-in: everything drifts one step every DRIFT_S seconds and the background
glyph lattice shifts phase with it; the palette is dimmed and the bg is black.

Behaviour
  fullscreen      borderless, always on top, one window per monitor, hidden
                  cursor, keyboard/mouse ignored, title JARVIS-SLEEP.
  exit            SIGTERM/SIGINT, or $JARVIS_HOME/sleep_state.json -> "awake".
  --wake-anim     on exit play a 0.6 s collapse-into-reactor + flash. When the
                  state is already "awake" at launch (the controller spawns it
                  that way) the animation plays straight away.
  --windowed      1280x720 normal, closable window, not on top (safe preview).
  --seconds N     auto-exit after N seconds.

Preview:  python3 -m src.sleep_screensaver_opus --windowed --seconds 20 --wake-anim
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import re
import signal
import socket
import subprocess
import sys
import time
from collections import deque

import numpy as np

DECK_LIB = os.environ.get("JARVIS_DECK_LIB") or os.path.expanduser("~/deck/lib")
if DECK_LIB not in sys.path:
    sys.path.insert(0, DECK_LIB)
import jarvis_ascii as J  # noqa: E402

TITLE = "JARVIS-SLEEP"
PX = 16  # font px -> 10x20 cells, 192x54 grid on 1920x1080
FPS = 15  # reactor rate
HUD_DT = 0.25  # HUD recompose interval (clock, sweep, log)
SAMPLE_DT = 2.0  # /proc sampling interval
PROC_DT = 10.0  # /proc/<pid> scan interval (process count, top, agent)
STATE_DT = 0.5  # sleep_state.json poll interval
DRIFT_S = 40.0  # burn-in drift step
STALE_GRACE = 5.0  # fullscreen + awake + never saw asleep -> leave
WAKE_DUR = 0.6
REACTOR_SLOW = 0.55  # calmer spin/pulse while asleep
GAIN = 0.86  # overall reactor brightness while asleep

BLACK = (0, 0, 0)
mix = J.mix


def _dim(c, k):
    return mix(BLACK, c, k)


C_DOT = _dim(J.DEEP, 0.55)
C_DEEP = _dim(J.DEEP, 0.95)
C_FRAME = _dim(J.DIM, 0.85)
C_TXT = _dim(J.SLATE, 0.62)
C_VAL = _dim(J.ICE, 0.66)
C_CY = _dim(J.CY, 0.72)
C_AM = _dim(J.AM, 0.72)
C_AMD = _dim(J.AM, 0.42)
C_CLOCK = _dim(J.CY, 0.46)
C_STBY = _dim(J.SLATE, 0.40)
C_RD = _dim(J.RD, 0.7)

STANDBY = "STANDBY - DOUBLE CLAP TO WAKE"
SPARK = " ▁▂▃▄▅▆▇█"
DRIFTS = [
    (0, 0),
    (1, 0),
    (2, 1),
    (1, 1),
    (0, 1),
    (-1, 1),
    (-2, 0),
    (-1, -1),
    (0, -1),
    (1, -1),
]

# ------------------------------------------------------------------ helpers


def put(f, x, y, s, col):
    f.put(int(x), int(y), s, col)


def clear(f, x, y, w, h):
    for yy in range(max(0, y), min(f.h, y + h)):
        put(f, x, yy, " " * w, BLACK)


def hms(sec):
    if sec is None or sec < 0:
        return "--:--:--"
    return J.hms(sec)


def meter(v, n):
    """Dense HUD meter: filled cells then dim ticks."""
    if v is None:
        return "·" * n
    k = int(round(J.clamp(v) * n))
    return "■" * k + "·" * (n - k)


def spark(vals, n, top=None):
    vals = list(vals)[-n:]
    if not vals:
        return " " * n
    top = top or max(max(vals), 1e-9)
    s = "".join(SPARK[min(8, int(J.clamp(v / top) * 8 + 0.5))] for v in vals)
    return s.rjust(n, " ")


def rate(b):
    if b is None:
        return "--"
    for u in ("B", "K", "M", "G"):
        if b < 1024 or u == "G":
            return f"{b:5.1f}{u}/s" if u != "B" else f"{b:5.0f}B/s"
        b /= 1024.0


def gib(kb):
    return "--" if kb is None else f"{kb / 1048576:.1f}G"


def drift_for(t):
    epoch = int(max(t, 0) // DRIFT_S)
    return epoch, DRIFTS[epoch % len(DRIFTS)]


def empty_info():
    return {
        "host": "--",
        "kernel": "--",
        "uptime": None,
        "load": None,
        "mem": None,
        "swap": None,
        "temp": None,
        "temps": [],
        "cores": [],
        "rx": None,
        "tx": None,
        "procs": None,
        "since": None,
        "agent": None,
        "disks": [],
        "top": [],
        "hist": {"load": [], "temp": [], "rx": [], "tx": [], "cpu": []},
        "log": [],
    }


# ---------------------------------------------------------- reactor (numpy)


class ReactorField:
    """Vectorised port of jarvis_ascii.reactor (fully assembled, tint=None).

    levels(t) gives one int per cell: -1 = outside (HUD shows through),
    0 = blank inside the reactor, k>0 = atlas tile (glyph + colour bin).
    """

    Q = 50  # intensity bins (multiple of 10 keeps RAMP boundaries exact)
    H = 9  # white-hot levels
    AX = 2.1
    RMAX = 1.08

    def __init__(self, R):
        self.R = R = max(3, int(R))
        self.hy = int(R * self.RMAX) + 1
        self.hx = int(R * self.AX * self.RMAX) + 1
        dy = np.arange(-self.hy, self.hy + 1, dtype=np.float64)[:, None]
        dx = np.arange(-self.hx, self.hx + 1, dtype=np.float64)[None, :]
        ny = np.broadcast_to(dy / R, (dy.size, dx.size))
        nx = np.broadcast_to(dx / (R * self.AX), (dy.size, dx.size))
        r = np.hypot(nx, ny)
        self.nx, self.ny, self.r = nx, ny, r
        self.u = (np.arctan2(ny, nx) / J.TAU) % 1.0
        self.inside = r <= self.RMAX
        aa = 0.55 / max(R, 1)
        w_rim, w_dash, w_in = max(0.03, aa), max(0.02, aa * 0.8), max(0.025, aa * 0.8)
        self.w_tri = max(0.022, aa * 0.7)

        def ring(c, w):
            d = (r - c) / w
            return np.where(np.abs(d) < 3, np.exp(-d * d), 0.0)

        self.rim = ring(0.94, w_rim) * 0.85
        self.tick = (r > 0.995) & (r < 1.07)
        self.dash = ring(0.81, w_dash) * 0.7
        coil = (r > 0.45) & (r < 0.73)
        s1 = np.clip((r - 0.45) / 0.05, 0, 1)
        s2 = np.clip((r - 0.68) / 0.05, 0, 1)
        self.coil = np.where(
            coil, (s1 * s1 * (3 - 2 * s1)) * (1 - s2 * s2 * (3 - 2 * s2)), 0.0
        )
        self.beam_m = (r > 0.4) & (r < 0.92)
        self.inner = ring(0.38, w_in) * 0.95
        self.tri_m = r < 0.4
        core = r < 0.6
        self.core_c = np.where(core, np.exp(-((r / 0.13) ** 2)), 0.0)
        self.core_h = np.where(core, np.exp(-((r / 0.55) ** 2)) * 0.13, 0.0)
        self.shape = r.shape

    def intensity(self, t, boost=0.0):
        u = self.u
        spin = t * 0.09
        beam = J.frac(t * 0.42)
        pulse = 0.88 + 0.12 * math.sin(t * 7.0)
        cr, sr = math.cos(-t * 0.6), math.sin(-t * 0.6)
        I = self.rim + np.where(self.tick & (((u * 48 + spin) % 1.0) < 0.3), 0.42, 0.0)
        I = I + np.where(((u * 36 - t * 0.22) % 1.0) < 0.55, self.dash, 0.0)
        fp = (u * 10 + t * 0.55) % 1.0
        I = I + self.coil * ((fp > 0.1) & (fp < 0.9)) * np.where(
            (fp < 0.2) | (fp > 0.8), 0.78, 0.5
        )
        I = I + np.where(self.beam_m, np.exp(-((beam - u) % 1.0) * 7) * 0.32, 0.0)
        I = I + self.inner
        px = self.nx * cr - self.ny * sr
        py = self.nx * sr + self.ny * cr
        m = np.maximum.reduce([px * a + py * c for a, c in J._NORMALS])
        e = np.where(self.tri_m, np.exp(-(((m - 0.15) / self.w_tri) ** 2)), 0.0)
        I = I + e + np.where(self.tri_m & (m < 0.15), 0.14, 0.0)
        c = self.core_c * pulse * (1 + boost)
        I = I + c + self.core_h * (1 + boost * 2)
        return I, e + c

    def levels(self, t, boost=0.0):
        I, hot = self.intensity(t, boost)
        Ic = np.minimum(I, 1.0)
        b = np.where(Ic >= 1.0, self.Q, np.floor(Ic * self.Q)).astype(np.int32)
        qh = np.where(
            hot > 0.45, np.rint(np.clip(hot - 0.45, 0, 1) * (self.H - 1)), 0
        ).astype(np.int32)
        idx = np.where(I >= 0.08, (b + 1) * self.H + qh, 0)
        return np.where(self.inside, idx, -1)

    @classmethod
    def ntiles(cls):
        return (cls.Q + 2) * cls.H

    @classmethod
    def tile_spec(cls, k, gain=GAIN):
        """atlas index -> (glyph, rgb)."""
        qi, qh = divmod(k, cls.H)
        if qi == 0:
            return " ", BLACK
        b = qi - 1
        if b >= cls.Q:
            g, Ic = J.RAMP[-1], 1.0
        else:
            g, Ic = J.RAMP[1 + b // (cls.Q // 10)], (b + 0.5) / cls.Q
        lo, mid, hi = J.DIM, J.CY, J.ICE
        col = mix(lo, mid, Ic / 0.55) if Ic < 0.55 else mix(mid, hi, (Ic - 0.55) / 0.45)
        if qh:
            col = mix(col, J.WHITE, qh / (cls.H - 1))
        return g, _dim(col, gain)


_FIELDS = {}


def field(R):
    R = max(3, int(R))
    if R not in _FIELDS:
        _FIELDS[R] = ReactorField(R)
    return _FIELDS[R]


def overlay_reactor(f, cx, cy, R, t, boost=0.0, gain=GAIN):
    """Write reactor glyphs into Frame f (pure path; the Tk path uses atlas pixels)."""
    fld = field(R)
    idx = fld.levels(t, boost)
    for j in range(idx.shape[0]):
        y = cy - fld.hy + j
        if not 0 <= y < f.h:
            continue
        row, crow = f.ch[y], f.co[y]
        for i in np.nonzero(idx[j] >= 0)[0]:
            x = cx - fld.hx + int(i)
            if 0 <= x < f.w:
                g, col = ReactorField.tile_spec(int(idx[j, i]), gain)
                row[x] = g
                crow[x] = col


# ------------------------------------------------------------------ big clock

_DIG = {
    "0": ["███", "█ █", "█ █", "█ █", "███"],
    "1": ["██ ", " █ ", " █ ", " █ ", "███"],
    "2": ["███", "  █", "███", "█  ", "███"],
    "3": ["███", "  █", "███", "  █", "███"],
    "4": ["█ █", "█ █", "███", "  █", "  █"],
    "5": ["███", "█  ", "███", "  █", "███"],
    "6": ["███", "█  ", "███", "█ █", "███"],
    "7": ["███", "  █", "  █", "  █", "  █"],
    "8": ["███", "█ █", "███", "█ █", "███"],
    "9": ["███", "█ █", "███", "  █", "███"],
    ":": [" ", "▪", " ", "▪", " "],
    " ": [" ", " ", " ", " ", " "],
}


def big_text(s):
    rows = [""] * 5
    for i, ch in enumerate(s):
        g = _DIG.get(ch, _DIG[" "])
        for k in range(5):
            rows[k] += "".join(c * 2 for c in g[k]) + ("  " if i < len(s) - 1 else "")
    return rows


# ------------------------------------------------------------------ layout


def layout(cols, rows, role="main", drift=(0, 0)):
    dx, dy = drift
    R = max(4, int(rows * 0.245))
    cy = int(rows * 0.40) + dy
    cx = cols // 2 + dx
    outer = int(R * ReactorField.AX * 1.36) + 1
    pw = min(46, (cols - 2 * outer) // 2 - 5)
    return {
        "cx": cx,
        "cy": cy,
        "R": R,
        "dx": dx,
        "dy": dy,
        "outer": outer,
        "pw": pw if pw >= 26 else 0,
        "top": 1 + dy,
        "bot": rows - 2 + dy,
    }


def _bars(f, t, now, info, L, title):
    w = f.w
    top, bot, dx = L["top"], L["bot"], L["dx"]
    since = info.get("since")
    slept = hms(now.timestamp() - since) if since else "--:--:--"
    tl = f" ╱ J.A.R.V.I.S  //  {title} "
    tr = f" L.O.C.K.E  ·  SLEEP T+{slept}  ·  {now:%H:%M:%S} ╲ "
    up = info.get("uptime")
    bl = f" ▲ TX {rate(info.get('tx'))}   ▼ RX {rate(info.get('rx'))}   ◆ {info.get('procs') or '--'} PROC "
    br = f" {info.get('host', '--')}  ·  {info.get('kernel', '--')}  ·  UP {hms(up) if up else '--'} "
    for y, l, r in ((top, tl, tr), (bot, bl, br)):
        x0, x1 = 1 + dx, w - 2 + dx
        put(f, x0, y, "─" * (x1 - x0 + 1), C_FRAME)
        if len(l) + len(r) + 6 > x1 - x0:
            r = ""
        put(f, x0 + 1, y, l, C_CY)
        if r:
            put(f, x1 - len(r), y, r, C_CY)
        for x in range(x0, x1 + 1):
            if 0 <= x < w and f.ch[y][x] in "╱╲▲▼◆":
                f.co[y][x] = C_AM
    # edge rulers
    for y in range(top + 1, bot):
        k = y - top
        g = "┼" if k % 10 == 0 else ("┤" if k % 2 == 0 else "│")
        put(f, 1 + dx, y, g if g != "┤" else "├", C_DEEP)
        put(f, w - 2 + dx, y, g, C_DEEP)
        if k % 10 == 0:
            put(f, 2 + dx, y, f"{k:02d}", C_DEEP)
            put(f, w - 4 + dx, y, f"{k:02d}", C_DEEP)


def _background(f, epoch, L):
    ph = epoch % 3
    for y in range(L["top"] + 1, L["bot"]):
        if (y + ph) % 2:
            continue
        for x in range(4 + (y + ph * 2) % 6, f.w - 4, 6):
            f.ch[y][x] = "·"
            f.co[y][x] = C_DOT
    for y in range(L["top"] + 4 + ph, L["bot"] - 1, 9):
        for x in range(12 + ph * 4, f.w - 8, 24):
            f.ch[y][x] = "+"
            f.co[y][x] = C_DEEP


def _reticle(f, t, L, info):
    cx, cy, R = L["cx"], L["cy"], L["R"]
    rx, ry = R * ReactorField.AX * 1.2, R * 1.2
    for k in range(72):
        a = k * J.TAU / 72
        x, y = cx + math.cos(a) * rx, cy + math.sin(a) * ry
        put(
            f,
            round(x),
            round(y),
            "+" if k % 6 == 0 else "·",
            C_FRAME if k % 6 == 0 else C_DEEP,
        )
    for deg, lab in ((270, "000"), (0, "090"), (90, "180"), (180, "270")):
        a = math.radians(deg)
        x = cx + math.cos(a) * rx * 1.09
        y = cy + math.sin(a) * ry * 1.12
        put(f, round(x) - 1, round(y), lab, C_TXT)
    # outer arcs in four quadrants
    rx2, ry2 = R * ReactorField.AX * 1.33, R * 1.33
    for q in range(4):
        for k in range(14):
            a = math.radians(q * 90 + 25 + k * 3)
            put(
                f,
                round(cx + math.cos(a) * rx2),
                round(cy + math.sin(a) * ry2),
                "╌",
                C_DEEP,
            )
    # sweep marker, one turn per 90 s, with a fading tail
    base = (t / 90.0) % 1.0
    for j in range(6):
        a = (base - j * 0.006) * J.TAU - math.pi / 2
        x, y = cx + math.cos(a) * rx, cy + math.sin(a) * ry
        put(
            f,
            round(x),
            round(y),
            "◆" if j == 0 else "•",
            C_AM if j == 0 else _dim(J.AM, 0.5 - j * 0.07),
        )
    # crosshair bus lines
    pw = L["pw"]
    xl0 = 1 + L["dx"] + (pw + 4 if pw else 4)
    xl1 = int(cx - rx2) - 2
    xr0 = int(cx + rx2) + 2
    xr1 = f.w - 2 + L["dx"] - (pw + 4 if pw else 4)
    if xl1 - xl0 > 6:
        put(f, xl0, cy, "┤" + "─ " * ((xl1 - xl0) // 2), C_DEEP)
        load = info.get("load")
        put(f, xl0 + 3, cy - 1, f"LOAD {load[0]:.2f}" if load else "LOAD --", C_TXT)
    if xr1 - xr0 > 6:
        put(f, xr0, cy, " ─" * ((xr1 - xr0) // 2) + "├", C_DEEP)
        m = info.get("mem")
        lab = f"MEM {m[0] / m[1] * 100:3.0f}%" if m else "MEM --"
        put(f, xr1 - len(lab) - 2, cy - 1, lab, C_TXT)


def _kv(f, x, y, k, v, w, vcol=C_VAL):
    put(f, x, y, k, C_TXT)
    v = str(v)[: max(0, w - len(k) - 1)]
    put(f, x + w - len(v), y, v, vcol)


def _panel(f, x, y, w, h, title, max_y):
    if y + h > max_y:
        h = max_y - y
    if h < 3:
        return 0
    J.box(f, x, y, w, h, title, col=C_FRAME, tcol=C_AM)
    put(f, x + w - 4, y + h - 1, "╱╱", C_FRAME)  # font lacks the engine's corner glyphs
    return h


def _left(f, t, now, info, L, x, y0, y1):
    w = L["pw"]
    iw = w - 4
    y = y0
    h = _panel(f, x, y, w, 9, "SYSTEM", y1)
    if h:
        load = info.get("load")
        mem, swap = info.get("mem"), info.get("swap")
        rowsv = [
            ("HOST", info.get("host", "--")),
            ("KERNEL", info.get("kernel", "--")),
            ("UPTIME", hms(info["uptime"]) if info.get("uptime") else "--"),
            ("LOAD 1/5/15", " ".join(f"{v:.2f}" for v in load) if load else "--"),
            (
                "MEM",
                f"{gib(mem[0])}/{gib(mem[1])} {meter(mem[0] / mem[1], 8)}"
                if mem
                else "--",
            ),
            ("SWAP", f"{gib(swap[0])}/{gib(swap[1])}" if swap and swap[1] else "--"),
            (
                "CPU TEMP",
                f"{info['temp']:.0f}°C" if info.get("temp") is not None else "--",
            ),
        ]
        for i, (k, v) in enumerate(rowsv[: h - 2]):
            _kv(f, x + 2, y + 1 + i, k, v, iw)
        y += h + 1
    h = _panel(f, x, y, w, 8, "SLEEP CYCLE", y1)
    if h:
        since = info.get("since")
        ag = info.get("agent")
        rowsv = [
            ("STATE", "ASLEEP" if since else "AWAKE · PREVIEW", C_AM),
            (
                "SINCE",
                datetime.datetime.fromtimestamp(since).strftime("%a %H:%M:%S")
                if since
                else "--",
                C_VAL,
            ),
            ("ELAPSED", hms(now.timestamp() - since) if since else "--", C_VAL),
            ("WAKE TRIGGER", "DOUBLE CLAP", C_CY),
            (
                "VOICE AGENT",
                "--" if ag is None else ("● ONLINE" if ag else "○ OFFLINE"),
                C_CY if ag else C_RD,
            ),
            ("STATE POLL", f"{STATE_DT:.1f}s", C_VAL),
        ]
        for i, (k, v, c) in enumerate(rowsv[: h - 2]):
            _kv(f, x + 2, y + 1 + i, k, v, iw, c)
        y += h + 1
    cores = info.get("cores") or []
    h = _panel(
        f,
        x,
        y,
        w,
        4 + (len(cores) + 3) // 4 if cores else 4,
        f"CORES ×{len(cores) or '--'}",
        y1,
    )
    if h and cores:
        cw = iw // 4
        for i, v in enumerate(cores):
            r, c = divmod(i, 4)
            if 1 + r >= h - 1:
                break
            s = f"{i:02d}" + SPARK[min(8, int(v * 8 + 0.5))] + f"{v * 100:3.0f}"
            put(f, x + 2 + c * cw, y + 1 + r, s[:cw], C_TXT)
            put(f, x + 4 + c * cw, y + 1 + r, s[2], C_CY)
        hist = info["hist"].get("cpu", [])
        if 2 + (len(cores) + 3) // 4 < h:
            put(f, x + 2, y + h - 2, spark(hist, iw, 1.0), C_CY)


def _right(f, t, now, info, L, x, y0, y1):
    w = L["pw"]
    iw = w - 4
    y = y0
    hist = info["hist"]
    h = _panel(f, x, y, w, 11, "TELEMETRY", y1)
    if h:
        sw = iw - 11
        lines = [
            (
                "LOAD",
                spark(hist["load"], sw),
                f"{hist['load'][-1]:.2f}" if hist["load"] else "--",
            ),
            (
                "TEMP",
                spark(hist["temp"], sw),
                f"{hist['temp'][-1]:.0f}°" if hist["temp"] else "--",
            ),
            (
                "RX",
                spark(hist["rx"], sw),
                rate(hist["rx"][-1]).strip() if hist["rx"] else "--",
            ),
            (
                "TX",
                spark(hist["tx"], sw),
                rate(hist["tx"][-1]).strip() if hist["tx"] else "--",
            ),
        ]
        for i, (k, s, v) in enumerate(lines):
            yy = y + 1 + i * 2
            if yy >= y + h - 1:
                break
            put(f, x + 2, yy, k, C_TXT)
            put(f, x + 2 + iw - len(v), yy, v, C_VAL)
            put(f, x + 7, yy + 1 if yy + 1 < y + h - 1 else yy, s[: iw - 5], C_CY)
        y += h + 1
    temps = info.get("temps") or []
    h = _panel(f, x, y, w, 3 + max(1, min(4, len(temps))), "THERMAL", y1)
    if h:
        for i, (k, v) in enumerate(temps[: h - 2]):
            _kv(
                f,
                x + 2,
                y + 1 + i,
                k.upper()[:14],
                f"{v:5.1f}°C {meter(v / 100, 6)}",
                iw,
                C_AM if v > 80 else C_VAL,
            )
        if not temps:
            _kv(f, x + 2, y + 1, "SENSORS", "--", iw)
        y += h + 1
    h = _panel(f, x, y, w, max(3, y1 - y), "EVENT LOG", y1)
    if h:
        log = list(info.get("log") or [])[-(h - 2) :]
        for i, line in enumerate(log):
            put(f, x + 2, y + 1 + i, line[:iw], C_TXT if i < len(log) - 1 else C_VAL)


def _clock(f, t, now, L, show_seconds=True):
    s = (
        now.strftime("%H:%M")
        if (int(now.timestamp()) % 2 == 0)
        else now.strftime("%H %M")
    )
    rows = big_text(s)
    w = len(rows[0])
    x = L["cx"] - w // 2
    y = L["cy"] + int(L["R"] * 1.2) + 2
    clear(f, x - 3, y - 1, w + 8, 10)
    for k, r in enumerate(rows):
        put(f, x, y + k, r.replace(" ", "\0"), C_CLOCK)
    if show_seconds:
        put(f, x + w + 2, y + 4, f"{now:%S}", C_AMD)
    return y + 5


def compose_hud(
    cols, rows, t, now=None, info=None, role="main", drift=None, title="STANDBY"
):
    """HUD layer only (no reactor glyphs) as a jarvis_ascii.Frame."""
    now = now or datetime.datetime.now()
    info = info or empty_info()
    epoch, d = drift_for(t)
    if drift is not None:
        d = drift
    f = J.Frame(cols, rows)
    f.co = [[BLACK] * cols for _ in range(rows)]
    if role != "main":
        return _compose_side(f, t, now, info, role, d, epoch)
    L = layout(cols, rows, role, d)
    _background(f, epoch, L)
    _bars(f, t, now, info, L, title)
    _reticle(f, t, L, info)
    if L["pw"]:
        _left(f, t, now, info, L, 4 + L["dx"], L["top"] + 2, L["bot"] - 1)
        _right(
            f, t, now, info, L, cols - 4 - L["pw"] + L["dx"], L["top"] + 2, L["bot"] - 1
        )
    y = _clock(f, t, now, L)
    date = "   ".join(" ".join(p) for p in now.strftime("%a %d %b %Y").upper().split())
    sy = min(y + 3, L["bot"] - 1)  # the standby line always fits, even on tiny grids
    if y + 1 < sy:
        put(f, L["cx"] - len(date) // 2, y + 1, date, C_TXT)
    line = f"───  ◆  {STANDBY}  ◆  ───"
    clear(f, L["cx"] - len(line) // 2 - 1, sy, len(line) + 2, 1)
    put(f, L["cx"] - len(line) // 2, sy, line, C_STBY)
    return f


def _compose_side(f, t, now, info, role, d, epoch):
    cols, rows = f.w, f.h
    L = {
        "dx": d[0],
        "dy": d[1],
        "top": 1 + d[1],
        "bot": rows - 2 + d[1],
        "cx": cols // 2 + d[0],
        "pw": 0,
    }
    _background(f, epoch, L)
    _bars(f, t, now, info, L, "TELEMETRY · " + ("WEST" if role == "side-l" else "EAST"))
    x0, x1 = 4 + d[0], cols - 5 + d[0]
    y, y1 = L["top"] + 2, L["bot"] - 1
    W = x1 - x0
    hist = info["hist"]
    # big spaced banner
    banner = "S  T  A  N  D  B  Y"
    if role == "side-l":
        cores = info.get("cores") or []
        h = _panel(
            f,
            x0,
            y,
            W,
            3 + max(1, len(cores)),
            f"PROCESSOR  ·  {len(cores) or '--'} THREADS",
            y1,
        )
        bw = W - 22
        for i, v in enumerate(cores[: h - 2]):
            put(f, x0 + 2, y + 1 + i, f"CPU{i:02d}", C_TXT)
            put(f, x0 + 9, y + 1 + i, meter(v, bw), C_CY if v < 0.85 else C_AM)
            put(f, x0 + 10 + bw, y + 1 + i, f"{v * 100:5.1f}%", C_VAL)
        y += h + 1
        h = _panel(f, x0, y, W, 5, "LOAD HISTORY", y1)
        if h:
            put(f, x0 + 2, y + 1, spark(hist["load"], W - 4), C_CY)
            put(f, x0 + 2, y + 2, spark(hist["cpu"], W - 4, 1.0), _dim(J.CY, 0.45))
            load = info.get("load")
            put(
                f,
                x0 + 2,
                y + 3,
                f"LOAD {' / '.join(f'{v:.2f}' for v in load) if load else '--'}",
                C_TXT,
            )
            y += h + 1
        top = info.get("top") or []
        h = _panel(f, x0, y, W, max(3, min(9, y1 - y - 7)), "TOP PROCESSES", y1)
        for i, (name, pct) in enumerate(top[: max(0, h - 2)]):
            _kv(
                f,
                x0 + 2,
                y + 1 + i,
                f"{name[:24]:<24}",
                f"{pct:5.1f}%  {meter(pct / 100, 10)}",
                W - 4,
            )
        y += h + 1
    else:
        h = _panel(f, x0, y, W, 8, "NETWORK", y1)
        if h:
            put(f, x0 + 2, y + 1, f"▲ TX {rate(info.get('tx'))}", C_TXT)
            put(f, x0 + 2, y + 2, spark(hist["tx"], W - 4), C_CY)
            put(f, x0 + 2, y + 4, f"▼ RX {rate(info.get('rx'))}", C_TXT)
            put(f, x0 + 2, y + 5, spark(hist["rx"], W - 4), C_CY)
            y += h + 1
        disks = info.get("disks") or []
        h = _panel(f, x0, y, W, 2 + max(1, len(disks)), "STORAGE", y1)
        for i, (mnt, used, tot) in enumerate(disks[: max(0, h - 2)]):
            bw = W - 40
            put(f, x0 + 2, y + 1 + i, f"{mnt[:14]:<14}", C_TXT)
            put(f, x0 + 17, y + 1 + i, meter(used / tot if tot else 0, bw), C_CY)
            put(
                f,
                x0 + 18 + bw,
                y + 1 + i,
                f"{used / 1e9:7.0f}/{tot / 1e9:.0f} GB",
                C_VAL,
            )
        y += h + 1
        temps = info.get("temps") or []
        h = _panel(f, x0, y, W, max(3, min(4 + len(temps), y1 - y - 7)), "THERMAL", y1)
        for i, (k, v) in enumerate(temps[: max(0, h - 3)]):
            _kv(
                f,
                x0 + 2,
                y + 1 + i,
                k.upper()[:20],
                f"{v:5.1f}°C {meter(v / 100, 16)}",
                W - 4,
                C_AM if v > 80 else C_VAL,
            )
        if h > 3:
            put(f, x0 + 2, y + h - 2, spark(hist["temp"], W - 4), C_AMD)
        y += h + 1
    if y + 2 < y1:
        by = min(y1 - 2, y + (y1 - y) // 2)
        put(f, L["cx"] - len(banner) // 2, by, banner, C_STBY)
        put(f, L["cx"] - len(STANDBY) // 2, by + 2, STANDBY, _dim(J.SLATE, 0.3))
    return f


def compose(cols, rows, t, now=None, info=None, role="main", drift=None):
    """Full scene (HUD + reactor) as a Frame."""
    f = compose_hud(cols, rows, t, now, info, role, drift)
    if role == "main":
        _, d = drift_for(t)
        L = layout(cols, rows, role, d if drift is None else drift)
        overlay_reactor(f, L["cx"], L["cy"], L["R"], t * REACTOR_SLOW)
    return f


def render(t, cols=192, rows=54, now=None, info=None, role="main", drift=None):
    """Pure render: scene at time t -> list of strings (one per row)."""
    f = compose(cols, rows, t, now, info, role, drift)
    return ["".join(r) for r in f.ch]


# ------------------------------------------------------------- wake animation


def _ease_in(x):
    x = J.clamp(x)
    return x * x


def wake_frame(base, p, cx, cy, R, t):
    """0.6 s exit: HUD implodes into the reactor (p<0.6), then shock + flash."""
    f = J.Frame(base.w, base.h)
    f.co = [[BLACK] * base.w for _ in range(base.h)]
    q = _ease_in(p / 0.6)
    ax = ReactorField.AX
    if p < 0.6:
        s = 1 - q
        for y in range(base.h):
            br, bc = base.ch[y], base.co[y]
            for x in range(base.w):
                g = br[x]
                if g == " ":
                    continue
                nx, ny = (x - cx) / (R * ax), (y - cy) / R
                if nx * nx + ny * ny < 1.17:
                    continue  # reactor area: redrawn below
                tx, ty = round(cx + (x - cx) * s), round(cy + (y - cy) * s)
                if 0 <= tx < f.w and 0 <= ty < f.h:
                    f.ch[ty][tx] = g
                    f.co[ty][tx] = mix(bc[x], J.ICE, q * 0.8)
        J.reactor(f, cx, cy, R, t, 10.0, boost=q * 1.6, scale=1.0 - 0.35 * q)
        return f
    k = (p - 0.6) / 0.4
    J.reactor(
        f,
        cx,
        cy,
        R,
        t,
        10.0,
        shock=(k * 2.8, 1.0 - k),
        boost=2.0 * (1 - k),
        scale=0.65 * (1 - k) + 0.05,
    )
    flash = 1.0 - k * 1.6
    if flash > 0:
        reach = 0.3 + k * 3.0
        for y in range(f.h):
            ny = (y - cy) / R
            for x in range(f.w):
                nx = (x - cx) / (R * ax)
                d = math.sqrt(nx * nx + ny * ny) / reach
                v = flash * (1 - d) if d < 1 else 0.0
                if v > 0.06 and v * 1.0 >= (J.jit(x, y) * 0.35):
                    f.ch[y][x] = "█▓▒░·"[min(4, int((1 - v) * 5))]
                    f.co[y][x] = mix(J.ICE, J.WHITE, v)
    return f


def render_wake(p, cols=192, rows=54, t=0.0, now=None, info=None, drift=(0, 0)):
    base = compose(cols, rows, t, now, info, "main", drift)
    L = layout(cols, rows, "main", drift)
    f = wake_frame(base, p, L["cx"], L["cy"], L["R"], t * REACTOR_SLOW)
    return ["".join(r) for r in f.ch]


# ------------------------------------------------------------------ sampling


def _read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return ""


def state_path():
    home = os.environ.get("JARVIS_HOME", "").strip()
    return os.path.join(home or os.path.expanduser("~/.jarvis"), "sleep_state.json")


def read_state(path=None):
    """{"state": "awake"|"asleep", "since": float|None}; missing/bad = awake."""
    try:
        with open(path or state_path()) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {"state": "awake", "since": None}
    if not isinstance(d, dict) or d.get("state") not in ("awake", "asleep"):
        return {"state": "awake", "since": None}
    s = d.get("since")
    return {
        "state": d["state"],
        "since": float(s) if isinstance(s, (int, float)) else None,
    }


def exit_reason(state, seen_asleep, file_changed, windowed, seconds, elapsed):
    """Why the saver should leave on this state poll, or None to keep running.

    "awake" counts once we saw "asleep" or the file was rewritten after launch.
    A fullscreen saver that only ever saw a stale "awake" (no --seconds) leaves
    after STALE_GRACE so it can never trap an awake desktop; previews
    (--windowed / --seconds) wait for a real transition.
    """
    if state != "awake":
        return None
    if seen_asleep or file_changed:
        return "awake"
    if not windowed and seconds is None and elapsed > STALE_GRACE:
        return "stale-awake"
    return None


class Sampler:
    """Cheap live facts from /proc and /sys. sample() every SAMPLE_DT."""

    def __init__(self):
        self.info = empty_info()
        self.info["host"] = socket.gethostname()
        self.info["kernel"] = (
            os.uname().release.split("-")[0] if hasattr(os, "uname") else "--"
        )
        self.hist = {k: deque(maxlen=160) for k in ("load", "temp", "rx", "tx", "cpu")}
        self.info["hist"] = self.hist
        self.log = deque(maxlen=24)
        self.info["log"] = self.log
        self._stat = self._cpu()
        self._net = self._netb()
        self._pt = {}
        self._tp = time.monotonic()
        self._proc_t = time.monotonic()
        self._hw = self._hwmons()

    def event(self, msg):
        self.log.append(f"{datetime.datetime.now():%H:%M:%S} {msg}")

    @staticmethod
    def _cpu():
        out = []
        for line in _read("/proc/stat").splitlines():
            if line.startswith("cpu"):
                v = [int(x) for x in line.split()[1:]]
                out.append((sum(v), v[3] + (v[4] if len(v) > 4 else 0)))
        return out

    @staticmethod
    def _netb():
        rx = tx = 0
        for line in _read("/proc/net/dev").splitlines()[2:]:
            name, _, rest = line.partition(":")
            if name.strip() == "lo" or not rest:
                continue
            v = rest.split()
            rx += int(v[0])
            tx += int(v[8])
        return rx, tx

    @staticmethod
    def _hwmons():
        out = []
        base = "/sys/class/hwmon"
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return out
        for h in names:
            p = os.path.join(base, h)
            name = _read(os.path.join(p, "name")).strip()
            f = os.path.join(p, "temp1_input")
            if os.path.exists(f):
                label = _read(os.path.join(p, "temp1_label")).strip()
                out.append(
                    (
                        f"{name} {label}".strip()
                        if label and name in ("coretemp", "k10temp")
                        else name,
                        f,
                    )
                )
        return out

    def _procs(self, dt):
        n, top, cur = 0, [], {}
        try:
            pids = [p for p in os.listdir("/proc") if p.isdigit()]
        except OSError:
            return None, []
        tck = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        agent = False
        for pid in pids:
            s = _read(f"/proc/{pid}/stat")
            if not s:
                continue
            n += 1
            lp, rp = s.find("("), s.rfind(")")
            name = s[lp + 1 : rp]
            f = s[rp + 2 :].split()
            try:
                ticks = int(f[11]) + int(f[12])
            except (IndexError, ValueError):
                continue
            cur[pid] = ticks
            if pid in self._pt and dt > 0:
                pct = (ticks - self._pt[pid]) / tck / dt * 100
                if pct > 0.05:
                    top.append((name, pct))
            if not agent and name.startswith("python"):
                if "agent.py" in _read(f"/proc/{pid}/cmdline"):
                    agent = True
        self._pt = cur
        self.info["agent"] = agent
        top.sort(key=lambda x: -x[1])
        return n, top[:8]

    def sample(self, since=None):
        i = self.info
        now = time.monotonic()
        dt = max(now - self._tp, 1e-3)
        self._tp = now
        i["since"] = since
        la = _read("/proc/loadavg").split()
        i["load"] = tuple(float(x) for x in la[:3]) if len(la) >= 3 else None
        mi = {}
        for line in _read("/proc/meminfo").splitlines():
            k, _, v = line.partition(":")
            if v:
                mi[k] = int(v.split()[0])
        if "MemTotal" in mi:
            i["mem"] = (mi["MemTotal"] - mi.get("MemAvailable", 0), mi["MemTotal"])
            i["swap"] = (
                mi.get("SwapTotal", 0) - mi.get("SwapFree", 0),
                mi.get("SwapTotal", 0),
            )
        up = _read("/proc/uptime").split()
        i["uptime"] = float(up[0]) if up else None
        st = self._cpu()
        if st and self._stat and len(st) == len(self._stat):
            fr = []
            for (a, ai), (b, bi) in zip(st, self._stat):
                d = a - b
                fr.append(1 - (ai - bi) / d if d > 0 else 0.0)
            self.hist["cpu"].append(fr[0])
            i["cores"] = fr[1:]
        self._stat = st
        rx, tx = self._netb()
        i["rx"], i["tx"] = (rx - self._net[0]) / dt, (tx - self._net[1]) / dt
        self._net = (rx, tx)
        temps = []
        for name, path in self._hw:
            v = _read(path).strip()
            if v.lstrip("-").isdigit():
                temps.append((name, int(v) / 1000.0))
        i["temps"] = temps
        cpu_t = [
            v for n, v in temps if n.startswith(("coretemp", "k10temp", "zenpower"))
        ] or [v for _, v in temps[:1]]
        i["temp"] = cpu_t[0] if cpu_t else None
        disks, seen = [], set()
        for m in ("/", "/home", "/mnt/data"):
            try:
                s = os.statvfs(m)
                dev = (s.f_blocks, s.f_bfree)  # btrfs subvolumes share one pool
            except OSError:
                continue
            if dev in seen:
                continue
            seen.add(dev)
            tot = s.f_blocks * s.f_frsize
            disks.append((m, tot - s.f_bavail * s.f_frsize, tot))
        i["disks"] = disks
        if now - self._proc_t >= PROC_DT or i["procs"] is None:
            i["procs"], i["top"] = self._procs(now - self._proc_t)
            self._proc_t = now
        if i["load"]:
            self.hist["load"].append(i["load"][0])
        if i["temp"] is not None:
            self.hist["temp"].append(i["temp"])
        self.hist["rx"].append(i["rx"])
        self.hist["tx"].append(i["tx"])
        return i


# ------------------------------------------------------------------ Tk side


def monitors():
    """[(name, x, y, w, h, primary)] via xrandr; [] when unavailable."""
    try:
        out = subprocess.run(
            ["xrandr", "--listactivemonitors"],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    mons = []
    for line in out.splitlines():
        m = re.search(r"(\+?\*?)(\S+)\s+(\d+)/\d+x(\d+)/\d+\+(-?\d+)\+(-?\d+)", line)
        if m and ":" in line.split()[0]:
            mons.append(
                (
                    m.group(2),
                    int(m.group(5)),
                    int(m.group(6)),
                    int(m.group(3)),
                    int(m.group(4)),
                    "*" in m.group(1),
                )
            )
    return mons


class Painter:
    """Diff-paints Frames into a PIL image with cached (glyph, colour) tiles."""

    def __init__(self, ras, cols, rows):
        from PIL import Image

        self.Image = Image
        self.ras, self.cols, self.rows = ras, cols, rows
        self.cw, self.lh = ras.cw, ras.lh
        self.img = Image.new("RGB", (cols * self.cw, rows * self.lh), BLACK)
        self.prev = None
        self.tiles = {}

    def _tile(self, g, col):
        key = (g, col)
        t = self.tiles.get(key)
        if t is None:
            if len(self.tiles) > 6000:
                self.tiles.clear()
            t = self.Image.new("RGB", (self.cw, self.lh), BLACK)
            if g != " ":
                t.paste(col, (0, 0, self.cw, self.lh), self.ras._mask(g))
            self.tiles[key] = t
        return t

    def update(self, f):
        """Paint changed cells; return dirty cell rects [(x0, y0, x1, y1)] (exclusive)."""
        cw, lh, img = self.cw, self.lh, self.img
        spans = []
        prev = self.prev
        for y in range(self.rows):
            ch, co = f.ch[y], f.co[y]
            if prev is not None and prev[0][y] == ch and prev[1][y] == co:
                continue
            pc, pk = (prev[0][y], prev[1][y]) if prev is not None else (None, None)
            lo = hi = -1
            for x in range(self.cols):
                g, c = ch[x], co[x]
                if g == " ":
                    c = BLACK
                if pc is not None:
                    og, oc = pc[x], pk[x]
                    if og == " ":
                        oc = BLACK
                    if og == g and oc == c:
                        continue
                if pc is None and g == " ":
                    continue
                img.paste(self._tile(g, c), (x * cw, y * lh))
                if lo < 0:
                    lo = x
                hi = x
            if lo >= 0:
                spans.append((y, lo, hi + 1))
        self.prev = ([list(r) for r in f.ch], [list(r) for r in f.co])
        rects = []
        for y, a, b in spans:
            if (
                rects
                and rects[-1][3] == y
                and not (b < rects[-1][0] - 8 or a > rects[-1][2] + 8)
            ):
                r = rects[-1]
                rects[-1] = (min(r[0], a), r[1], max(r[2], b), y + 1)
            else:
                rects.append((a, y, b, y + 1))
        if prev is None:
            return [(0, 0, self.cols, self.rows)]
        return rects


def _ppm(arr):
    h, w = arr.shape[:2]
    return b"P6 %d %d 255\n" % (w, h) + arr.tobytes()


class Screen:
    """One window (monitor) with its canvas photo and painters."""

    def __init__(self, app, root, geom, role, windowed):
        import tkinter as tk

        self.app, self.role = app, role
        x, y, w, h = geom
        self.win = root if root is not None else tk.Toplevel(app.root)
        win = self.win
        win.title(TITLE)
        win.configure(bg="#000000")
        if windowed:
            win.geometry(f"{w}x{h}")
            win.resizable(False, False)
            win.protocol("WM_DELETE_WINDOW", app.request_exit)
        else:
            win.overrideredirect(True)
            win.geometry(f"{w}x{h}+{x}+{y}")
            win.attributes("-topmost", True)
            win.configure(cursor="none")
            win.protocol("WM_DELETE_WINDOW", lambda: None)
        ras = app.ras
        self.cols, self.rows = ras.grid(w, h)
        self.ox = (w - self.cols * ras.cw) // 2
        self.oy = (h - self.rows * ras.lh) // 2
        self.canvas = tk.Canvas(
            win,
            width=w,
            height=h,
            bg="#000000",
            highlightthickness=0,
            bd=0,
            cursor="" if windowed else "none",
        )
        self.canvas.pack(fill="both", expand=True)
        self.photo = tk.PhotoImage(width=self.cols * ras.cw, height=self.rows * ras.lh)
        self.canvas.create_image(self.ox, self.oy, image=self.photo, anchor="nw")
        self.painter = Painter(ras, self.cols, self.rows)
        self.L = None
        self.last_hud = None
        self.hud_px = None
        self.prev_idx = None

    def upload(self, arr, px, py):
        self.photo.tk.call(
            self.photo.name,
            "put",
            _ppm(np.ascontiguousarray(arr)),
            "-format",
            "ppm",
            "-to",
            px,
            py,
        )

    def blit_rects(self, rects):
        cw, lh = self.painter.cw, self.painter.lh
        img = self.painter.img
        for x0, y0, x1, y1 in rects:
            box = (x0 * cw, y0 * lh, x1 * cw, y1 * lh)
            self.upload(np.asarray(img.crop(box)), box[0], box[1])
            z = self.hud_px[0] if self.hud_px else None
            if z and x0 < z[0] + z[2] and z[0] < x1 and y0 < z[1] + z[3] and z[1] < y1:
                self.hud_px = None

    def hud(self, t, now, info, drift, full=False):
        f = compose_hud(self.cols, self.rows, t, now, info, self.role, drift)
        self.last_hud = f
        rects = self.painter.update(f)
        if full:  # drift moved the reactor: old reactor pixels must be overwritten too
            rects = [(0, 0, self.cols, self.rows)]
            self.hud_px = None
        if rects:
            self.blit_rects(rects)
        if self.role == "main":
            self.L = layout(self.cols, self.rows, "main", drift)

    def reactor(self, t):
        if self.role != "main" or self.L is None:
            return
        L, app = self.L, self.app
        fld = field(L["R"])
        idx = fld.levels(t * REACTOR_SLOW)
        cw, lh = self.painter.cw, self.painter.lh
        x0, y0 = L["cx"] - fld.hx, L["cy"] - fld.hy
        # clip to grid
        i0, j0 = max(0, -x0), max(0, -y0)
        i1 = min(idx.shape[1], self.cols - x0)
        j1 = min(idx.shape[0], self.rows - y0)
        if i1 <= i0 or j1 <= j0:
            return
        idx = idx[j0:j1, i0:i1]
        gx, gy = x0 + i0, y0 + j0
        h, w = idx.shape
        tiles = app.atlas[np.maximum(idx, 0)]  # h, w, lh, cw, 3
        key = (gx, gy, w, h)
        if self.hud_px is None or self.hud_px[0] != key:
            box = (gx * cw, gy * lh, (gx + w) * cw, (gy + h) * lh)
            self.hud_px = (
                key,
                np.asarray(self.painter.img.crop(box)).reshape(h, lh, w, cw, 3),
            )
            self.prev_idx = None  # HUD under the reactor changed: upload everything
        hud = self.hud_px[1]
        out = np.where(
            (idx >= 0)[:, None, :, None, None], tiles.transpose(0, 2, 1, 3, 4), hud
        )
        out = out.reshape(h * lh, w * cw, 3)
        prev, self.prev_idx = self.prev_idx, idx
        if prev is None or prev.shape != idx.shape:
            self.upload(out, gx * cw, gy * lh)
            return
        # only the changed part: bands of up to BAND rows, union of their changed spans
        ch = idx != prev
        rows = np.nonzero(ch.any(axis=1))[0]
        BAND = 4
        k = 0
        while k < len(rows):
            j0 = int(rows[k])
            j1 = j0
            while k < len(rows) and rows[k] < j0 + BAND:
                j1 = int(rows[k])
                k += 1
            cols_ = np.nonzero(ch[j0 : j1 + 1].any(axis=0))[0]
            i0, i1 = int(cols_[0]), int(cols_[-1]) + 1
            self.upload(
                out[j0 * lh : (j1 + 1) * lh, i0 * cw : i1 * cw],
                (gx + i0) * cw,
                (gy + j0) * lh,
            )

    def full(self, f):
        """Paint an arbitrary Frame (wake animation)."""
        rects = self.painter.update(f)
        if rects:
            self.blit_rects(rects)

    def sync_full(self, t):
        """Make the painter's image match the screen (HUD + reactor) before animating."""
        if self.last_hud is None:
            return
        if self.role == "main" and self.L:
            f = compose_hud(
                self.cols,
                self.rows,
                t,
                None,
                self.app.sampler.info,
                "main",
                (self.L["dx"], self.L["dy"]),
            )
            overlay_reactor(
                f, self.L["cx"], self.L["cy"], self.L["R"], t * REACTOR_SLOW
            )
            self.base = f
            self.painter.update(f)
            self.blit_rects([(0, 0, self.cols, self.rows)])
        else:
            self.base = self.last_hud


class App:
    def __init__(self, args):
        import tkinter as tk
        import jarvis_raster as RR

        self.args = args
        self.ras = RR.Raster(args.px)
        self.atlas = self._build_atlas()
        self.sampler = Sampler()
        self.exit_req = None
        self.animating = False
        self.t0 = time.monotonic()
        self.state_file = state_path()
        st = read_state(self.state_file)
        self.since = st["since"] if st["state"] == "asleep" else None
        self.seen_asleep = st["state"] == "asleep"
        self.start_mtime = self._mtime()
        self.next_state = 0.0
        self.next_hud = 0.0
        self.next_sample = 0.0
        self.epoch = -1
        self.frames = 0
        self.root = tk.Tk(className=TITLE)
        self.root.title(TITLE)
        self.screens = []
        if args.windowed:
            self.screens.append(
                Screen(self, self.root, (0, 0, 1280, 720), "main", True)
            )
        else:
            mons = [] if args.primary_only else monitors()
            if not mons:
                sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
                mons = [("screen", 0, 0, sw, sh, True)]
            prim = next((m for m in mons if m[5]), mons[0])
            px_ = prim[1]
            self.screens.append(Screen(self, self.root, prim[1:5], "main", False))
            others = sorted(
                (m for m in mons if m is not prim), key=lambda m: abs(m[1] - px_)
            )
            for k, m in enumerate(others):
                # both telemetry walls get shown even when the side screens share a side
                role = (
                    ("side-l", "side-r")[k % 2]
                    if len({o[1] < px_ for o in others}) == 1
                    else ("side-l" if m[1] < px_ else "side-r")
                )
                self.screens.append(Screen(self, None, m[1:5], role, False))
        for s in signal.SIGTERM, signal.SIGINT:
            signal.signal(s, self._on_signal)
        self.sampler.event("STANDBY ENGAGED")
        self.sampler.event(
            f"DISPLAYS {len(self.screens)} · {self.screens[0].cols}x{self.screens[0].rows} CELLS"
        )
        if (
            args.wake_anim
            and st["state"] == "awake"
            and not args.windowed
            and args.seconds is None
        ):
            self.exit_req = "awake"  # controller spawned us for the wake flash only
        self.sampler.event("WAKE TRIGGER ARMED · DOUBLE CLAP")

    def _build_atlas(self):
        n = ReactorField.ntiles()
        cw, lh = self.ras.cw, self.ras.lh
        atlas = np.zeros((n, lh, cw, 3), np.uint8)
        masks = {}
        for k in range(n):
            g, col = ReactorField.tile_spec(k)
            if g == " ":
                continue
            m = masks.get(g)
            if m is None:
                m = masks[g] = (
                    np.asarray(self.ras._mask(g), np.float32)[..., None] / 255.0
                )
            atlas[k] = (m * np.array(col, np.float32)).astype(np.uint8)
        return atlas

    def _mtime(self):
        try:
            return os.stat(self.state_file).st_mtime_ns
        except OSError:
            return None

    def _on_signal(self, signum, _frm):
        if self.animating and signum == signal.SIGINT:
            os._exit(0)
        self.exit_req = self.exit_req or signal.Signals(signum).name

    def request_exit(self):
        self.exit_req = self.exit_req or "closed"

    def _poll_state(self, el):
        mt = self._mtime()
        st = read_state(self.state_file)
        if st["state"] == "asleep":
            if not self.seen_asleep:
                self.sampler.event("STATE ASLEEP CONFIRMED")
            self.seen_asleep = True
            self.since = st["since"]
            return
        why = exit_reason(
            "awake",
            self.seen_asleep,
            mt != self.start_mtime,
            self.args.windowed,
            self.args.seconds,
            el,
        )
        if why:
            self.exit_req = self.exit_req or why

    def tick(self):
        if self.animating:
            return
        start = time.monotonic()
        el = start - self.t0
        if self.args.seconds is not None and el >= self.args.seconds:
            self.exit_req = self.exit_req or "timeout"
        if el >= self.next_state:
            self.next_state = el + STATE_DT
            self._poll_state(el)
        if self.exit_req:
            self.finish(el)
            return
        if el >= self.next_sample:
            self.next_sample = el + SAMPLE_DT
            self.sampler.sample(self.since)
        epoch, drift = drift_for(el)
        if epoch != self.epoch:
            if self.epoch >= 0:
                self.sampler.event(f"BURN-IN DRIFT {drift[0]:+d},{drift[1]:+d}")
            self.epoch = epoch
            self.next_hud = 0.0
            full = True
        else:
            full = False
        if el >= self.next_hud:
            self.next_hud = el + HUD_DT
            now = datetime.datetime.now()
            for s in self.screens:
                s.hud(el, now, self.sampler.info, drift, full)
        self.screens[0].reactor(el)
        self.frames += 1
        spent = time.monotonic() - start
        self.root.after(max(5, int((1.0 / self.args.fps - spent) * 1000)), self.tick)

    def finish(self, el):
        self.animating = True
        if not self.args.wake_anim:
            self.close()
            return
        if self.screens[0].last_hud is None:
            now = datetime.datetime.now()
            self.sampler.sample(self.since)
            for s in self.screens:
                s.hud(el, now, self.sampler.info, drift_for(el)[1])
        for s in self.screens:
            s.sync_full(el)
        self.root.update_idletasks()
        a0 = time.monotonic()

        def step():
            p = (time.monotonic() - a0) / WAKE_DUR
            if p >= 1.0:
                for s in self.screens:
                    s.full(J.Frame(s.cols, s.rows))
                self.root.update_idletasks()
                self.root.after(40, self.close)
                return
            for s in self.screens:
                if s.role == "main":
                    L = s.L
                    f = wake_frame(
                        s.base,
                        p,
                        L["cx"],
                        L["cy"],
                        L["R"],
                        (el + p * WAKE_DUR) * REACTOR_SLOW,
                    )
                else:
                    f = J.crt_off(s.base, p)
                s.full(f)
            self.root.after(15, step)

        step()

    def close(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.root.after(1, self.tick)
        self.root.mainloop()
        return self.exit_req


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Jarvis ASCII sleep screensaver (opus build)"
    )
    ap.add_argument(
        "--wake-anim",
        action="store_true",
        help="play the 0.6s collapse + flash on exit",
    )
    ap.add_argument(
        "--windowed", action="store_true", help="1280x720 closable preview window"
    )
    ap.add_argument(
        "--seconds", type=float, default=None, help="auto-exit after N seconds"
    )
    ap.add_argument("--fps", type=float, default=FPS)
    ap.add_argument("--px", type=int, default=PX, help="glyph font size")
    ap.add_argument(
        "--primary-only",
        action="store_true",
        help="fullscreen on the primary monitor only",
    )
    args = ap.parse_args(argv)
    args.fps = max(1.0, min(30.0, args.fps))
    if args.windowed and args.px == PX:
        args.px = 14
    app = App(args)
    reason = app.run()
    print(f"jarvis-sleep: exit ({reason})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
