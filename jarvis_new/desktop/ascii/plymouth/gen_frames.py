"""Render the jarvis-ascii Plymouth theme: ASCII boot frames + progress glyph bars.

Everything on screen during boot is a glyph cell from the deck ASCII engine,
rasterised here because Plymouth only draws images. Frames 0..INTRO-1 play
once (reactor assembles, helmet scans in); INTRO..INTRO+LOOP-1 loop forever.
Every motion in the loop completes whole cycles per LOOP frames, so the wrap
is seamless.

usage: python3 gen_frames.py OUT_DIR
"""
import math
import os
import random
import sys

DECK = os.path.expanduser("~/deck/lib")
sys.path.insert(0, DECK)
import jarvis_ascii as J  # noqa: E402
import jarvis_launch as L  # noqa: E402
import jarvis_raster as RR  # noqa: E402

COLS, ROWS, PX = 66, 28, 15
FPS = 15
INTRO, LOOP = 30, 60
BAR = 32  # progress cells
TAU = math.tau


def reactor(f, cx, cy, R, ph, b):
    """J.reactor with loop-safe rates: ph is loop phase 0..1, b assembly time."""
    ax = 2.1
    a_rim = J.sstep(0.0, 0.8, b)
    a_dash = J.sstep(0.4, 1.0, b)
    a_coil = J.clamp((b - 0.7) / 0.7) * 10
    a_inner = J.sstep(1.2, 1.5, b)
    a_tri = J.sstep(1.4, 1.9, b)
    a_core = J.sstep(1.7, 2.2, b)
    beam = ph
    pulse = 0.88 + 0.12 * math.sin(TAU * 3 * ph)
    rot = -TAU / 3 * ph  # triangle has 3-fold symmetry: one third turn per loop
    cr, sr = math.cos(rot), math.sin(rot)
    aa = 0.55 / R
    w_rim, w_dash, w_in, w_tri = max(0.03, aa), max(0.02, aa * 0.8), max(0.025, aa * 0.8), max(0.022, aa * 0.7)
    normals = [(math.cos(a), math.sin(a)) for a in (math.pi / 2, math.pi / 2 + TAU / 3, math.pi / 2 + 2 * TAU / 3)]
    for y in range(max(0, int(cy - R * 1.1)), min(f.h, int(cy + R * 1.1) + 2)):
        ny = (y - cy) / R
        for x in range(max(0, int(cx - R * ax * 1.1)), min(f.w, int(cx + R * ax * 1.1) + 2)):
            nx = (x - cx) / (R * ax)
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
                I += math.exp(-J.frac(beam - u) * 7) * 0.32 * min(a_coil, 1)
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


def helmet(f, ox, oy, ph, reveal):
    edge = reveal * (len(L.HELMET) + 2)
    pulse = 0.75 + 0.25 * math.sin(TAU * 4 * ph)
    for y, line in enumerate(L.HELMET):
        a, b = len(line) - len(line.lstrip()), len(line.rstrip())
        f.put(ox + a, oy + y, " " * (b - a), J.BG)
        d = edge - y
        if d <= 0:
            continue
        for x, c in enumerate(line):
            if c == " ":
                continue
            wave = 0.5 + 0.5 * math.sin(x * 0.5 - y * 0.8 + TAU * 2 * ph)
            col = {"e": J.mix(J.CY, J.WHITE, pulse), "n": J.AM, "o": J.mix(J.CY, J.ICE, wave * 0.5),
                   "x": J.mix(J.DIM, J.CY, 0.35 + 0.4 * wave)}.get(c, J.mix(J.DEEP, J.DIM, wave))
            g = L.GLYPH[c]
            if d < 1.3:
                g, col = "▓", J.WHITE
            f.put(ox + x, oy + y, g, col)


def background(f, ph):
    for y in range(f.h):
        for x in range(2 if y % 2 else 0, f.w, 4):
            wv = 0.5 + 0.5 * math.sin(x * 0.07 + y * 0.21 - TAU * ph)
            f.ch[y][x] = "·"
            f.co[y][x] = J.mix(J.BG, J.DEEP, 0.35 + 0.65 * wv * wv)


def shimmer(f, y, text, ph, base, hi):
    """Centered text with a bright band sweeping across once per loop."""
    x0 = (f.w - len(text)) // 2
    pos = ph * (len(text) + 16) - 8
    for i, c in enumerate(text):
        if c != " ":
            f.put(x0 + i, y, c, J.mix(base, hi, math.exp(-((i - pos) / 3.0) ** 2)))


def frame(i):
    f = J.Frame(COLS, ROWS)
    intro = i < INTRO
    t = i / FPS
    ph = 0.0 if intro else ((i - INTRO) % LOOP) / LOOP
    b = t * 1.1 if intro else 9.0
    background(f, ph)
    if intro and t < 0.5:
        rnd = random.Random(i)
        for _ in range(int(COLS * ROWS * 0.3 * (1 - t / 0.5))):
            f.put(rnd.randrange(COLS), rnd.randrange(ROWS), rnd.choice("░▒▓·:"), J.mix(J.DEEP, J.SLATE, rnd.random()))
    R, cy = 9.0, 10
    reactor(f, COLS // 2, cy, R, ph, b)
    hx = COLS // 2 - len(L.HELMET[0]) // 2
    helmet(f, hx, cy - len(L.HELMET) // 2, ph, J.sstep(0.6, 1.7, t) if intro else 1.0)
    title = "J . A . R . V . I . S"
    if intro:
        if t > 1.2:
            f.cput(cy + 11, J.scramble(title, (t - 1.2) / 0.6, 3), J.ICE)
    else:
        shimmer(f, cy + 11, title, ph, J.ICE, J.WHITE)
        sub = "L O C K E   //   S Y S T E M   B O O T"
        shimmer(f, cy + 13, sub, J.frac(ph + 0.5), J.mix(J.AM, J.BG, 0.35), J.AM)
    return f


def progress_bar(n):
    f = J.Frame(BAR + 2, 1)
    f.put(0, 0, "▕" + "█" * n + "░" * (BAR - n) + "▏", J.CY)
    for x in range(1, n + 1):
        f.co[0][x] = J.mix(J.DIM, J.CY, x / max(n, 1))
    return f


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "build"
    os.makedirs(out, exist_ok=True)
    ras = RR.Raster(PX, lh=18)
    for i in range(INTRO + LOOP):
        ras.paint(frame(i)).save(os.path.join(out, f"frame-{i}.png"), optimize=True)
    for n in range(BAR + 1):
        ras.paint(progress_bar(n)).save(os.path.join(out, f"bar-{n}.png"), optimize=True)
    print(f"{INTRO + LOOP} frames {COLS * ras.cw}x{ROWS * ras.lh}, {BAR + 1} bars -> {out}")


if __name__ == "__main__":
    main()
