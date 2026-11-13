#!/usr/bin/env python3
"""JARVIS-SLEEP screensaver (sonnet build): ASCII arc-reactor standby screen.

Pure render (render_frame / frame_lines) is separate from the Tk shell.
Exits on SIGTERM/SIGINT or when ~/.jarvis/sleep_state.json state == 'awake'.
Input is ignored.  Flags: --wake-anim  --windowed  --seconds N
"""

import argparse
import datetime
import json
import math
import os
import signal
import sys
import time

DECK_LIB = os.path.expanduser("~/deck/lib")
if DECK_LIB not in sys.path:
    sys.path.insert(0, DECK_LIB)
import jarvis_ascii as J  # noqa: E402

STATE_PATH = os.path.expanduser("~/.jarvis/sleep_state.json")
TITLE = "JARVIS-SLEEP"
WAKE_DUR = 0.6
FPS = 15
TAGLINE = "STANDBY - DOUBLE CLAP TO WAKE"

DIGITS = {
    "0": ("███", "█ █", "█ █", "█ █", "███"),
    "1": (" █ ", "██ ", " █ ", " █ ", "███"),
    "2": ("███", "  █", "███", "█  ", "███"),
    "3": ("███", "  █", "███", "  █", "███"),
    "4": ("█ █", "█ █", "███", "  █", "  █"),
    "5": ("███", "█  ", "███", "  █", "███"),
    "6": ("███", "█  ", "███", "█ █", "███"),
    "7": ("███", "  █", "  █", "  █", "  █"),
    "8": ("███", "█ █", "███", "█ █", "███"),
    "9": ("███", "█ █", "███", "  █", "███"),
    ":": ("   ", " ▪ ", "   ", " ▪ ", "   "),
}


def read_state(path=STATE_PATH):
    try:
        with open(path) as fh:
            return str(json.load(fh).get("state", "")).lower()
    except Exception:
        return ""


def is_awake(path=STATE_PATH):
    return read_state(path) == "awake"


def sys_info():
    """Real, cheap system readings (call every few seconds, not per frame)."""
    up = load = mem = None
    try:
        up = float(open("/proc/uptime").read().split()[0])
        load = open("/proc/loadavg").read().split()[0]
        m = {}
        for ln in open("/proc/meminfo"):
            k, v = ln.split(":", 1)
            m[k] = int(v.split()[0])
            if len(m) >= 3 and "MemAvailable" in m:
                break
        mem = 100 * (1 - m["MemAvailable"] / m["MemTotal"])
    except Exception:
        pass
    return {"uptime": up, "load": load, "mem": mem}


def big_clock(text):
    rows = ["", "", "", "", ""]
    for ch in text:
        g = DIGITS.get(ch, ("   ",) * 5)
        for i in range(5):
            rows[i] += g[i] + " "
    return rows


def drift(t):
    """Slow whole-scene wander against burn-in (cells)."""
    return int(round(4 * math.sin(t / 53.0))), int(round(1.6 * math.sin(t / 37.0 + 1)))


def _hud(f, cx, cy, R, t, info, now):
    w, h = f.w, f.h
    ox, oy = drift(t)
    # drifting dim dot grid
    J.background(f, int(t * 2) / 2 * 0.35, 1, h - 1)
    # frame corners
    for x, y, sx, sy in (
        (2, 1, 1, 1),
        (w - 3, 1, -1, 1),
        (2, h - 2, 1, -1),
        (w - 3, h - 2, -1, -1),
    ):
        f.put(
            x,
            y,
            "┌"
            if sx > 0 and sy > 0
            else "┐"
            if sx < 0 and sy > 0
            else "└"
            if sx > 0
            else "┘",
            J.DIM,
        )
    # header / footer (drift lightly)
    f.put(5 + ox, 1, "JARVIS//SLEEP", J.CY)
    f.put(19 + ox, 1, "▸ LOCKE·DECK · LOW-POWER HUD", J.DIM)
    ds = now.strftime("%a %d %b %Y").upper()
    f.put(w - 6 - len(ds) + ox, 1, ds, J.AM)
    # orbiting reticle ticks around reactor
    ticks = 72
    for i in range(ticks):
        a = i / ticks * J.TAU + t * 0.05
        x = int(round(cx + math.cos(a) * R * 2.1 * 1.22))
        y = int(round(cy + math.sin(a) * R * 1.22))
        if 0 <= y < h and 0 <= x < w and f.ch[y][x] == " ":
            major = i % 6 == 0
            f.ch[y][x] = "+" if major else "·"
            f.co[y][x] = J.CY if major else J.DIM
    # side telemetry (real values)
    up = info.get("uptime")
    lines = [
        ("UPTIME", J.hms(up) if up is not None else "--"),
        ("LOAD", info.get("load") or "--"),
        ("MEM", "%d%%" % info["mem"] if info.get("mem") is not None else "--"),
        ("MODE", "STANDBY"),
    ]
    lx = 6 + ox
    ly = cy - 4 + oy
    f.put(lx, ly - 1, "┌─ SYSTEM " + "─" * 12, J.DIM)
    for i, (k, v) in enumerate(lines):
        f.put(lx, ly + i, "│ %-7s" % k, J.SLATE)
        f.put(lx + 10, ly + i, str(v), J.ICE)
    f.put(lx, ly + len(lines), "└" + "─" * 21, J.DIM)
    rx = w - 28 + ox
    f.put(rx, ly - 1, "┌─ CHANNELS " + "─" * 9, J.DIM)
    chans = [
        ("VOICE", "LISTEN"),
        ("CLAP", "ARMED"),
        ("DISPLAY", "SLEEP"),
        ("AGENT", "IDLE"),
    ]
    for i, (k, v) in enumerate(chans):
        f.put(rx, ly + i, "│ %-8s" % k, J.SLATE)
        f.put(rx + 11, ly + i, v, J.AM if v == "ARMED" else J.CY)
    f.put(rx, ly + len(chans), "└" + "─" * 21, J.DIM)
    # waveform "listening" strip (clap armed)
    wy = h - 4
    n = min(60, w - 20)
    x0 = (w - n) // 2
    bars = " ▁▂▃▄▅▆▇"
    for i in range(n):
        v = 0.5 + 0.5 * math.sin(i * 0.5 - t * 1.7) * math.sin(i * 0.13 + t * 0.4)
        v *= 0.25 + 0.15 * math.sin(t * 0.8)
        f.put(x0 + i, wy, bars[int(v * 7.99)], J.DIM)


def render_frame(w, h, t, now=None, info=None, wake=None):
    """Build the screen as a jarvis_ascii.Frame.

    t: seconds since start.  now: datetime for the clock.  wake: None or 0..1
    progress of the wake collapse animation.
    """
    now = now or datetime.datetime.now()
    info = info or {}
    f = J.Frame(w, h)
    ox, oy = drift(t)
    R = max(5.0, min(h * 0.235, w / 2.1 / 2.6))
    cx, cy = w // 2 + ox, int(h * 0.36) + oy
    _hud(f, cx, cy, R, t, info, now)
    # clock + tagline under the reactor
    txt = now.strftime("%H:%M")
    rows = big_clock(txt)
    ty = min(h - 11, int(cy + R * 1.3 + 2))
    for i, r in enumerate(rows):
        f.put((w - len(r)) // 2 + ox, ty + i, r, J.ICE if i % 2 == 0 else J.CY)
    f.put((w - 3) // 2 + ox, ty + 6, ":%02d" % now.second, J.DIM)
    blink = 0.45 + 0.4 * (0.5 + 0.5 * math.sin(t * 1.6))
    f.put((w - len(TAGLINE)) // 2 + ox, h - 2, TAGLINE, J.mix(J.BG, J.CY, blink * 0.6))
    boost = 0.0
    shock = None
    if wake is not None:
        p = max(0.0, min(1.0, wake))
        e = p * p * (3 - 2 * p)
        g = J.Frame(w, h)
        for y in range(h):
            for x in range(w):
                c = f.ch[y][x]
                if c == " ":
                    continue
                nx = int(round(x + (cx - x) * e))
                ny = int(round(y + (cy - y) * e))
                if 0 <= nx < w and 0 <= ny < h:
                    g.ch[ny][nx] = c
                    g.co[ny][nx] = J.mix(f.co[y][x], J.ICE, e * 0.6)
        f = g
        boost = 2.0 * e
        if p > 0.6:
            shock = ((p - 0.6) / 0.4 * 1.9, 1.0 - (p - 0.6) / 0.4)
    J.reactor(f, cx, cy, R, t + 3, 9.0, shock=shock, boost=boost)
    if wake is not None and wake > 0.85:
        k = (wake - 0.85) / 0.15
        for y in range(h):
            for x in range(w):
                d = abs(x - cx) / 2.1 / R + abs(y - cy) / R
                if d < 3 * (1 - k) + 0.3 and (x + y + int(k * 9)) % 2 == 0:
                    f.ch[y][x] = "█"
                    f.co[y][x] = J.mix(J.WHITE, J.ICE, k)
    return f


def frame_lines(f):
    """Plain strings (no colour) of a Frame."""
    return ["".join(r) for r in f.ch]


# ------------------------------------------------------------------ painting
def dirty_rects(prev, f):
    """Cell rects (x0, x1, y0, y1 exclusive) that differ from prev; whole frame if prev is None."""
    if prev is None or (prev.w, prev.h) != (f.w, f.h):
        return [(0, f.w, 0, f.h)]
    rects = []
    for y in range(f.h):
        if prev.ch[y] == f.ch[y] and prev.co[y] == f.co[y]:
            continue
        a, b, pc, fc = prev.ch[y], f.ch[y], prev.co[y], f.co[y]
        xs = [x for x in range(f.w) if a[x] != b[x] or pc[x] != fc[x]]
        x0, x1 = xs[0], xs[-1] + 1
        if (
            rects
            and rects[-1][3] == y
            and abs(rects[-1][0] - x0) + abs(rects[-1][1] - x1) < 24
        ):
            r = rects[-1]
            rects[-1] = (min(r[0], x0), max(r[1], x1), r[2], y + 1)
        else:
            rects.append((x0, x1, y, y + 1))
    return rects


def paint_region(ras, f, x0, x1, y0, y1):
    from PIL import Image

    img = Image.new("RGB", ((x1 - x0) * ras.cw, (y1 - y0) * ras.lh), (0, 0, 0))
    for y in range(y0, y1):
        row, crow = f.ch[y], f.co[y]
        for x in range(x0, x1):
            c = row[x]
            if c != " ":
                px, py = (x - x0) * ras.cw, (y - y0) * ras.lh
                img.paste(crow[x], (px, py, px + ras.cw, py + ras.lh), ras._mask(c))
    return img


# ------------------------------------------------------------------ Tk shell
def run(args):
    import tkinter as tk
    import jarvis_raster as RR

    stop = {"flag": False}

    def on_sig(*_):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, on_sig)
    signal.signal(signal.SIGINT, on_sig)

    root = tk.Tk()
    root.title(TITLE)
    root.configure(bg="black")
    if args.windowed:
        W, H = 1280, 720
        root.geometry(f"{W}x{H}+100+60")
        root.protocol("WM_DELETE_WINDOW", lambda: stop.__setitem__("flag", True))
    else:
        W, H = root.winfo_screenwidth(), root.winfo_screenheight()
        root.overrideredirect(True)
        root.geometry(f"{W}x{H}+0+0")
        root.attributes("-topmost", True)
        for cur in ("none", "@/dev/null"):
            try:
                root.configure(cursor=cur)
                break
            except tk.TclError:
                pass
    px = 15 if W >= 1600 else 14
    ras = RR.Raster(px)
    cols, rows = W // ras.cw, H // ras.lh
    photo = tk.PhotoImage(width=W, height=H)
    label = tk.Label(root, image=photo, bd=0, highlightthickness=0, bg="black")
    label.place(x=0, y=0, width=W, height=H)
    try:
        label.configure(cursor=root.cget("cursor"))
    except tk.TclError:
        pass

    t0 = time.time()
    st = {"wake_t": None, "info": {}, "info_t": -99, "state_t": -99}
    dt = 1.0 / FPS

    def begin_exit():
        if st["wake_t"] is not None:
            return
        if args.wake_anim:
            st["wake_t"] = time.time()
        else:
            root.destroy()
            st["done"] = True

    def tick():
        t = time.time() - t0
        if st.get("done"):
            return
        if st["wake_t"] is None:
            if stop["flag"] or (args.seconds and t >= args.seconds):
                begin_exit()
            elif t - st["state_t"] > 0.5:
                st["state_t"] = t
                if is_awake():
                    begin_exit()
        if st.get("done"):
            return
        wake = None
        if st["wake_t"] is not None:
            wake = (time.time() - st["wake_t"]) / WAKE_DUR
            if wake >= 1.0:
                root.destroy()
                return
        if t - st["info_t"] > 5:
            st["info"], st["info_t"] = sys_info(), t
        f = render_frame(cols, rows, t, datetime.datetime.now(), st["info"], wake)
        prev = st.get("prev")
        for x0, x1, y0, y1 in dirty_rects(prev, f):
            img = paint_region(ras, f, x0, x1, y0, y1)
            ox, oy = (W - cols * ras.cw) // 2, (H - rows * ras.lh) // 2
            root.tk.call(
                photo,
                "put",
                b"P6 %d %d 255\n" % img.size + img.tobytes(),
                "-format",
                "ppm",
                "-to",
                ox + x0 * ras.cw,
                oy + y0 * ras.lh,
            )
        st["prev"] = f
        el = time.time() - t0 - t
        root.after(max(1, int((dt - el) * 1000)), tick)

    tick()
    root.mainloop()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--wake-anim", action="store_true", help="0.6s collapse+flash on exit"
    )
    ap.add_argument(
        "--windowed", action="store_true", help="safe 1280x720 closable preview window"
    )
    ap.add_argument(
        "--seconds", type=float, default=0, help="auto-exit after N seconds"
    )
    run(ap.parse_args(argv))


if __name__ == "__main__":
    main()
