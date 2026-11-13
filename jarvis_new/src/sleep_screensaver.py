"""JARVIS//SLEEP - Jarvis-themed ASCII standby screensaver for the sleep state.

A fullscreen, borderless, always-on-top Tk window (title JARVIS-SLEEP) that
paints the jarvis_ascii frame buffer through jarvis_raster: a slowly pulsing
STARK arc reactor in the centre, scanning rings, drifting glyph rain, a live
clock and a dim standby line. Glyph set and layout drift every few seconds so
a static screen does not burn in.

Input is ignored. The window exits on SIGTERM/SIGINT or when
~/.jarvis/sleep_state.json reads state == "awake". SIGUSR1 (sent by the
controller on a wake) plays the wake clip in this same window and exits, as
does --wake-anim at launch: reactor collapse, white flash, a shockwave ring,
then the glyph layer dissolves while the window fades to transparent, so the
desktop and the restored windows show through. The controller restores the
windows at FADE_START_S.

usage: python -m src.sleep_screensaver [--wake-anim] [--state-file PATH] [--fps N]
Env: JARVIS_DECK_LIB overrides the engine directory (default ~/deck/lib).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import signal
import socket
import sys
import time

DECK_LIB = os.environ.get("JARVIS_DECK_LIB", os.path.expanduser("~/deck/lib"))
if DECK_LIB not in sys.path:
    sys.path.insert(0, DECK_LIB)
import jarvis_ascii as J  # noqa: E402

WINDOW_TITLE = "JARVIS-SLEEP"
STATE_PATH = os.path.expanduser("~/.jarvis/sleep_state.json")
STANDBY_TEXT = "STANDBY - DOUBLE CLAP TO WAKE"
BLACK = (0, 0, 0)
FPS = 15
PX = 14  # font pixel size, one cell = 8x17 px at this size
# Wake clip timeline, seconds from the wake. sleep_controller starts the window
# restore at FADE_START_S (its WAKE_WINDOWS_DELAY_S), so the fade reveals them.
COLLAPSE_END_S = 0.6  # reactor and glyph streams collapse into the core
FLASH_END_S = 0.8  # white bloom peaks; the fade starts here
FADE_END_S = 2.1  # glyph layer gone and window transparent; the window closes
WAKE_DUR = FADE_END_S
FADE_START_S = FLASH_END_S
SHIFT_S = 45.0  # burn-in guard: glyph set and layout drift every SHIFT_S seconds
STATE_POLL_S = 1.0
ALPHABETS = ("01<>/\\=+*#", "ABCDEF0123456789", "%&@$;:!?~^|", "X#x+*-/=")
REACTOR_FRAC = 0.30  # reactor radius as a share of the grid height

# 5-row clock font, '#' glyphs (colon is 1 column wide)
DIGITS = {
    "0": (" ### ", "#   #", "#   #", "#   #", " ### "),
    "1": ("  #  ", " ##  ", "  #  ", "  #  ", " ### "),
    "2": (" ### ", "#   #", "   # ", "  #  ", "#####"),
    "3": ("#### ", "    #", " ### ", "    #", "#### "),
    "4": ("#   #", "#   #", "#####", "    #", "    #"),
    "5": ("#####", "#    ", "#### ", "    #", "#### "),
    "6": (" ### ", "#    ", "#### ", "#   #", " ### "),
    "7": ("#####", "    #", "   # ", "  #  ", "  #  "),
    "8": (" ### ", "#   #", " ### ", "#   #", " ### "),
    "9": (" ### ", "#   #", " ####", "    #", " ### "),
}
COLON = (" ", "#", " ", "#", " ")
BLANK_COLON = (" ", " ", " ", " ", " ")
_ASCII_MAP = str.maketrans({"█": "@", "▓": "%", "▒": "#", "░": "+", "·": "."})


def _border(f: J.Frame, title: str) -> None:
    """ASCII frame around the whole screen with the window title on the top edge."""
    cols, rows = f.w, f.h
    col = J.DIM
    f.put(0, 0, "+" + "-" * (cols - 2) + "+", col)
    f.put(0, rows - 1, "+" + "-" * (cols - 2) + "+", col)
    for y in range(1, rows - 1):
        f.put(0, y, "|", col)
        f.put(cols - 1, y, "|", col)
    f.put(3, 0, "[ " + title + " ]", J.AM)


def clock_rows(text: str, colon_on: bool = True) -> list[str]:
    """Render HH:MM:SS as five text rows in the clock font."""
    rows = ["", "", "", "", ""]
    for ch in text:
        if ch == ":":
            glyph = COLON if colon_on else BLANK_COLON
        else:
            glyph = DIGITS[ch]
        for i in range(5):
            rows[i] += glyph[i] + " "
    return [r.rstrip() for r in rows]


def _in_text_zone(x: int, y: int, cols: int, rows: int, cx: int, cy: int) -> bool:
    """Cells the HUD text sits on: top clock band, bottom standby line, the two side panels."""
    if 1 <= y <= 9 and abs(x - cx) < 26:
        return True
    if rows - 8 <= y <= rows - 3 and abs(x - cx) < 26:
        return True
    return cy - 3 <= y <= cy + 10 and (x < 26 or x > cols - 26)


def _rain(
    f: J.Frame, t: float, alphabet: str, cx: int, cy: int, R: int, collapse: float
) -> None:
    """Drifting glyph streams; collapse > 0 pulls them into the reactor."""
    rx, ry = R * 2.1, R  # cell aspect matches the reactor's 2.1 horizontal stretch
    period = f.h + 16
    for x0 in range(1, f.w - 1, 3):
        speed = 4.0 + 7.0 * J.jit(x0, 11)
        head = (t * speed + J.jit(x0, 13) * period) % period
        trail = 6 + int(J.jit(x0, 17) * 12)
        for k in range(trail):
            y = int(head) - k
            if not 1 <= y < f.h - 1:
                continue
            x = x0
            if collapse > 0:
                x = int(round(x + (cx - x) * collapse))
                y = int(round(y + (cy - y) * collapse))
                if not (1 <= x < f.w - 1 and 1 <= y < f.h - 1):
                    continue
            if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 < 1.2**2:
                continue  # keep the reactor area clean
            if _in_text_zone(x, y, f.w, f.h, cx, cy):
                continue  # keep clock, standby line and side panels legible
            idx = int(J.jit(x0 * 131 + k, int(t * 1.5) + y) * len(alphabet))
            f.ch[y][x] = alphabet[idx]
            f.co[y][x] = J.ICE if k == 0 else J.mix(J.DIM, J.CY, 1 - k / trail)


def _scan(f: J.Frame, t: float, cx: int, cy: int, R: int, collapse: float) -> None:
    """Two expanding scan rings and a rotating amber sweep arm."""
    shrink = 1 - 0.8 * collapse
    for k in range(2):
        ph = (t / 5.0 + 0.5 * k) % 1.0
        rr = R * (1.25 + 0.6 * ph) * shrink
        if rr < 2:
            continue
        n = max(24, int(rr * 9))
        col = J.mix(BLACK, J.CY, 0.55 * (1 - ph))
        for i in range(n):
            a = J.TAU * i / n
            f.put(
                int(round(cx + rr * 2.1 * math.cos(a))),
                int(round(cy + rr * math.sin(a))),
                "." if i % 4 else "+",
                col,
            )
    if collapse < 1:
        a = (t * 0.7) % J.TAU
        col = J.mix(BLACK, J.AM, 0.6 * (1 - collapse))
        for s in range(int(R * 1.15), int(R * 1.8)):
            ch = "=" if s % 2 else "-"
            f.put(
                int(round(cx + s * 2.1 * math.cos(a))),
                int(round(cy + s * math.sin(a))),
                ch,
                col,
            )


def _ticks(f: J.Frame, t: float, cx: int, cy: int, R: int) -> None:
    """Dial ticks around the reactor, turning very slowly."""
    off = t * 0.05
    n = 72
    rr = R * 1.3
    for i in range(n):
        a = J.TAU * i / n + off
        ch = ":" if i % 6 == 0 else "."
        f.put(
            int(round(cx + rr * 2.1 * math.cos(a))),
            int(round(cy + rr * math.sin(a))),
            ch,
            J.DIM,
        )


def _hud(f, t, when, uptime, load, host, dx, dy, cx, cy, R) -> None:
    """Clock, date, the standby line and the two real-data side panels."""
    cols, rows = f.w, f.h
    clock = clock_rows(when.strftime("%H:%M:%S"), colon_on=when.microsecond < 500_000)
    cw = max(len(r) for r in clock)
    top = 3 + dy
    x0 = (cols - cw) // 2 + dx
    for i, line in enumerate(clock):
        f.put(x0, top + i, line, J.ICE)
    f.cput(1, when.strftime("STARK ARC REACTOR  //  %a %d %b %Y").upper(), J.DIM)

    breathe = 0.5 + 0.5 * math.sin(t * 0.9)
    f.cput(rows - 5 + dy, STANDBY_TEXT, J.mix(J.DEEP, J.DIM, 0.6 + 0.4 * breathe))

    px = 4 + dx
    py = cy - 2 + dy
    left = [
        ("UPTIME", _fmt_uptime(uptime) if uptime is not None else "n/a"),
        ("LOAD 1/5/15", " ".join("%.2f" % v for v in load) if load else "n/a"),
        ("HOST", host or "n/a"),
        ("STATE", "STANDBY"),
    ]
    for i, (k, v) in enumerate(left):
        f.put(px, py + 2 * i, k, J.AM)
        f.put(px, py + 2 * i + 1, v, J.SLATE)
    right = [
        ("DATE", when.strftime("%Y-%m-%d")),
        ("WEEK / DAY", "%s / %s" % (when.strftime("%V"), when.strftime("%j"))),
        ("CLOCK", when.strftime("%H:%M")),
        ("EPOCH", str(int(when.timestamp()))),
    ]
    rx = cols - 4 + dx
    for i, (k, v) in enumerate(right):
        f.put(rx - len(k), py + 2 * i, k, J.AM)
        f.put(rx - len(v), py + 2 * i + 1, v, J.SLATE)


def _fmt_uptime(secs: float) -> str:
    d, rem = divmod(int(secs), 86400)
    h, rem = divmod(rem, 3600)
    return "%dd %02d:%02d" % (d, h, rem // 60)


def wake_levels(wake: float) -> tuple[float, float, float, float]:
    """(collapse, flash, fade, ring), each 0..1, for wake progress 0..1. Pure."""
    e = wake * WAKE_DUR
    fade = J.sstep(FLASH_END_S, FADE_END_S, e)
    collapse = J.sstep(0.0, COLLAPSE_END_S, e)
    flash = J.sstep(COLLAPSE_END_S, FLASH_END_S, e) * (1.0 - fade)
    ring = J.sstep(COLLAPSE_END_S, FADE_END_S, e)
    return collapse, flash, fade, ring


def window_alpha(wake: float) -> float:
    """Window opacity during the wake clip: opaque through the flash, clear at the end."""
    return 1.0 - wake_levels(wake)[2]


def build(
    cols: int,
    rows: int,
    t: float,
    *,
    when=None,
    uptime=None,
    load=None,
    host="",
    wake=None,
) -> J.Frame:
    """Compose one frame. Pure: no Tk, no I/O. wake is None or progress 0..1 of the wake clip."""
    when = when or dt.datetime(2000, 1, 1, 0, 0, 0)
    f = J.Frame(cols, rows)
    cx, cy = cols // 2, rows // 2
    R = max(6, int(rows * REACTOR_FRAC))
    epoch = int(t // SHIFT_S)
    alphabet = ALPHABETS[epoch % len(ALPHABETS)]
    dx = int(round(2 * math.sin(epoch * 0.7)))
    dy = int(round(math.sin(epoch * 0.45)))

    _border(f, WINDOW_TITLE)

    collapse, flash, fade, ring = (
        wake_levels(wake) if wake is not None else (0.0, 0.0, 0.0, 0.0)
    )

    _ticks(f, t, cx, cy, R)
    _rain(f, t, alphabet, cx, cy, R, collapse)
    _scan(f, t, cx, cy, R, collapse)

    boost = 0.12 + 0.12 * math.sin(t * 0.9) + 3.0 * flash
    shock = None
    if wake is not None and collapse > 0:
        shock = (0.25 + ring, 1.0 - flash)
    J.reactor(
        f, cx, cy, R, t, 3.0, shock=shock, boost=boost, scale=1.0 - 0.15 * collapse
    )
    for y in range(
        f.h
    ):  # the engine's reactor uses block glyphs; keep the screen ASCII
        f.ch[y] = list("".join(f.ch[y]).translate(_ASCII_MAP))

    if wake is None:
        _hud(f, t, when, uptime, load, host, dx, dy, cx, cy, R)
    else:
        _shock_ring(f, cx, cy, R, ring)
        _dissolve(f, fade)
        _flash(f, cx, cy, R, flash)
    return f


def _shock_ring(f: J.Frame, cx: int, cy: int, R: int, p: float) -> None:
    """A shockwave ring that expands out of the reactor over the wake clip."""
    if p <= 0 or p >= 1:
        return
    rr = R * (1.1 + 4.2 * p)
    n = max(48, int(rr * 10))
    col = J.mix(BLACK, J.ICE, 0.9 * (1 - p))
    for i in range(n):
        a = J.TAU * i / n
        ch = "+" if i % 3 == 0 else "="
        f.put(
            int(round(cx + rr * 2.1 * math.cos(a))),
            int(round(cy + rr * math.sin(a))),
            ch,
            col,
        )


def _cell_rand(x: int, y: int) -> float:
    """Fixed per-cell value in [0, 1): the same cell dissolves at the same point each time."""
    h = (x * 374761393 + y * 668265263) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 65536.0


def _dissolve(f: J.Frame, fade: float) -> None:
    """Remove glyphs cell by cell as fade rises; at 1 the glyph layer is gone."""
    if fade <= 0:
        return
    for y in range(f.h):
        row = f.ch[y]
        for x in range(f.w):
            if row[x] != " " and _cell_rand(x, y) < fade:
                row[x] = " "


def _flash(f: J.Frame, cx: int, cy: int, R: int, flash: float) -> None:
    if flash <= 0:
        return
    rx = int(R * 2.1 * 1.4)
    ry = int(R * 1.4)
    for y in range(max(0, cy - ry), min(f.h, cy + ry + 1)):
        for x in range(max(0, cx - rx), min(f.w, cx + rx + 1)):
            if f.ch[y][x] != " ":
                f.co[y][x] = J.mix(f.co[y][x], J.WHITE, 0.85 * flash)


def compose(cols: int, rows: int, t: float, **kw) -> list[str]:
    """Frame as plain text rows, each exactly cols characters wide."""
    return ["".join(row) for row in build(cols, rows, t, **kw).ch]


def is_awake(path: str) -> bool:
    """True when the sleep state file reads state == "awake". Missing or bad file means keep sleeping."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return (
        isinstance(data, dict) and str(data.get("state", "")).strip().lower() == "awake"
    )


def _uptime() -> float | None:
    try:
        with open("/proc/uptime", encoding="utf-8") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _load():
    try:
        return os.getloadavg()
    except OSError:
        return None


WINDOWED_SIZE = (1280, 720)


def _set_alpha(root, alpha: float) -> None:
    """Window opacity. Without a compositor it does nothing and the dissolve still runs."""
    try:
        root.attributes("-alpha", alpha)
    except Exception:  # noqa: BLE001 - optional effect; never stop the clip for it
        pass


def run(
    fps: int = FPS,
    wake_anim: bool = False,
    state_path: str = STATE_PATH,
    windowed: bool = False,
    seconds: float | None = None,
) -> None:
    """Open the screensaver window and drive it until an exit condition fires.

    Default: fullscreen, borderless, always-on-top, input ignored.
    windowed: normal 1280x720 window the user can close; no topmost, input not ignored.
    seconds: exit on its own after this many seconds (preview use).
    """
    import tkinter as tk

    import jarvis_raster as RR

    ras = RR.Raster(PX)
    host = socket.gethostname()
    ctl = {
        "stop": False,
        "wake_req": False,
        "wake_t0": None,
        "next_poll": 0.0,
        "stats_t": -1.0,
        "stats": (None, None),
    }

    def _on_signal(_signum, _frame):
        ctl["stop"] = True  # acted on by the next tick

    def _on_wake(_signum, _frame):
        ctl["wake_req"] = True  # the controller's wake: play the clip now

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGUSR1, _on_wake)

    root = tk.Tk()
    root.title(WINDOW_TITLE)
    if windowed:
        sw, sh = WINDOWED_SIZE
        root.geometry(f"{sw}x{sh}+100+100")
        root.configure(bg="#000000")
    else:
        root.overrideredirect(True)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{sw}x{sh}+0+0")
        root.attributes("-topmost", True)
        root.configure(bg="#000000", cursor="none")
    cols, rows = ras.grid(sw, sh)
    label = tk.Label(
        root,
        bd=0,
        highlightthickness=0,
        bg="#000000",
        cursor="none" if not windowed else "",
    )
    label.place(x=0, y=0)
    photo = None
    if not windowed:
        for ev in (
            "<Key>",
            "<KeyRelease>",
            "<Button>",
            "<ButtonRelease>",
            "<Motion>",
            "<MouseWheel>",
        ):
            root.bind_all(ev, lambda _e: "break")

    t0 = time.monotonic()
    period_ms = max(1, int(1000 / max(1, fps)))

    def _finish():
        root.destroy()

    if windowed:
        root.protocol("WM_DELETE_WINDOW", _finish)

    def tick():
        nonlocal photo
        now = time.monotonic()
        if seconds is not None and not ctl["stop"] and now - t0 >= seconds:
            ctl["stop"] = True
        if ctl["wake_t0"] is None and not ctl["stop"] and now >= ctl["next_poll"]:
            ctl["next_poll"] = now + STATE_POLL_S
            if is_awake(state_path):
                ctl["stop"] = True
        if ctl["wake_t0"] is None and ctl["wake_req"]:
            ctl["wake_t0"] = now
        if ctl["wake_t0"] is None and ctl["stop"]:
            if not wake_anim:
                _finish()
                return
            ctl["wake_t0"] = now
        wake = None
        if ctl["wake_t0"] is not None:
            wake = (now - ctl["wake_t0"]) / WAKE_DUR
            if wake >= 1.0:
                _finish()
                return
            _set_alpha(root, window_alpha(wake))
        if now - ctl["stats_t"] >= 1.0:
            ctl["stats_t"] = now
            ctl["stats"] = (dt.datetime.now(), _uptime(), _load())
        when, uptime, load = ctl["stats"]
        frame = build(
            cols,
            rows,
            now - t0,
            when=when,
            uptime=uptime,
            load=load,
            host=host,
            wake=wake,
        )
        img = ras.paint(frame, bg=BLACK)
        data = b"P6 %d %d 255\n" % img.size + img.tobytes()
        if photo is None:
            photo = tk.PhotoImage(master=root, format="ppm", data=data)
            label.configure(image=photo)
            label.place(x=(sw - img.size[0]) // 2, y=(sh - img.size[1]) // 2)
        else:
            photo.configure(data=data)
        root.after(max(1, period_ms - int((time.monotonic() - now) * 1000)), tick)

    root.after(1, tick)
    root.mainloop()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="sleep_screensaver", description="JARVIS//SLEEP standby screensaver."
    )
    ap.add_argument(
        "--wake-anim",
        action="store_true",
        help="play the wake clip (collapse, flash, fade) before closing",
    )
    ap.add_argument(
        "--state-file",
        default=STATE_PATH,
        help="sleep state JSON (default: %(default)s)",
    )
    ap.add_argument(
        "--fps", type=int, default=FPS, help="frame rate, 5-30 (default: %(default)s)"
    )
    ap.add_argument(
        "--windowed", action="store_true", help="1280x720 closable window for previews"
    )
    ap.add_argument(
        "--seconds", type=float, default=None, help="exit automatically after N seconds"
    )
    a = ap.parse_args(argv)
    run(
        fps=max(5, min(30, a.fps)),
        wake_anim=a.wake_anim,
        state_path=a.state_file,
        windowed=a.windowed,
        seconds=a.seconds,
    )


if __name__ == "__main__":
    main()
