"""Perspective 3D particle decoration for the existing watch canvas.

Owns no windows, timers, inputs, capture, threads or security state. The watch
owner calls update. Geometry, camera projection and morphing are live math;
there are no baked frames, runtime assets or third-party dependencies.
"""

from __future__ import annotations

import colorsys
import contextlib
import math
import os
import time
from functools import lru_cache

MAX_PARTICLES = 720
CYCLE_SECONDS = 36.0
FORM_NAMES = (
    "Rift Emitter",
    "Strange Loop",
    "Parallax Engine",
    "Null Relic",
    "Silent Stack",
    "Discontinuity Field",
)
TAU = math.tau


def _fract(x):
    return x % 1.0


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _turn(p, yaw=0.0, pitch=0.0):
    x, y, z = p
    x, z = x * math.cos(yaw) + z * math.sin(yaw), -x * math.sin(yaw) + z * math.cos(yaw)
    return (
        x,
        y * math.cos(pitch) - z * math.sin(pitch),
        y * math.sin(pitch) + z * math.cos(pitch),
    )


def _orbit(i, u, v):
    ring = i % 3
    # Interrupted 3D rings, with an explicit rear arc and displaced axes.
    a = (0.08 + u * 0.82) * TAU
    r = (1.45, 1.23, 1.05)[ring] + (v - 0.5) * 0.055
    p = (r * math.cos(a), r * math.sin(a), 0)
    x, y, z = _turn(p, (0.20, 1.15, -0.53)[ring], (0.83, -0.30, -0.51)[ring])
    return x, y - 0.24, z


def _cube_edge(i, u, size):
    axis = i % 3
    values = [size[k] * (0.5 if (i // (3 * 2**k)) % 2 else -0.5) for k in range(3)]
    values[axis] = size[axis] * (u - 0.5)
    return tuple(values)


@lru_cache(maxsize=1)
def target_clouds():
    """Six deterministic 3D point arrangements, reused by every monitor."""
    clouds = [[] for _ in FORM_NAMES]
    for i in range(MAX_PARTICLES):
        u = _fract(i * 0.61803398875 + 0.13)
        v = _fract(i * 0.754877666 + 0.37)
        w = _fract(i * 0.569840291 + 0.19)
        # Rift: broken triangular outline, sparse volume, orbit dust, upward jet.
        if i % 10 < 4:
            verts = ((0, 0.86, 0.14), (-0.79, -0.98, 0.14), (0.79, -0.98, 0.14))
            k = i % 3
            p = _mix(verts[k], verts[(k + 1) % 3], u * 0.90 + 0.04)
            if i % 4 == 0:
                p = _mix(p, (0, -0.40, -0.18), v * 0.75)
            p = (p[0] + (v - 0.5) * 0.045, p[1], p[2] + (w - 0.5) * 0.24)
        elif i % 10 < 9:
            p = _orbit(i, u, v)
        else:
            y = 0.89 + u * 1.27
            p = ((v - 0.5) * 0.60 * u, y, 0.12 + (w - 0.5) * 0.45 * u)
        clouds[0].append(p)
        # Strange Loop: a genuine 3D torus knot with a narrow particle ribbon.
        a = u * TAU
        r = 1 + 0.34 * math.cos(3 * a)
        p = (r * math.cos(2 * a), r * math.sin(2 * a) * 0.9, 0.56 * math.sin(3 * a))
        side = (
            math.cos(2 * a) * math.cos(1.5 * a),
            math.sin(2 * a) * math.cos(1.5 * a),
            math.sin(1.5 * a),
        )
        clouds[1].append(tuple(p[k] + side[k] * (v - 0.5) * 0.19 for k in range(3)))
        # Parallax: gimbals and a faceted, floating wire core.
        if i % 10 < 7:
            p = _orbit(i, u, v)
        else:
            equator = (
                (0.57, -0.20, 0),
                (0, -0.20, 0.52),
                (-0.57, -0.20, 0),
                (0, -0.20, -0.52),
            )
            p = _mix((0, 0.65 if i % 2 else -0.98, 0), equator[i % 4], u)
        clouds[2].append(p)
        # Null Relic: twelve independently located tetrahedral shard skeletons.
        shard = i % 12
        a = shard * 2.399
        c = (math.cos(a) * 0.46, (shard / 11 - 0.5) * 2.2, math.sin(a) * 0.46)
        verts = (
            (0, 0.43, 0),
            (-0.26, -0.23, 0.18),
            (0.28, -0.19, 0.17),
            (0, -0.25, -0.28),
        )
        edges = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
        e = edges[(i // 12) % 6]
        p = _turn(_mix(verts[e[0]], verts[e[1]], u), a, 0.12)
        clouds[3].append(tuple(p[k] + c[k] for k in range(3)))
        # Silent Stack: open slab edges around a deep longitudinal void.
        slab = i % 22
        level = slab // 2
        sign = -1 if slab % 2 else 1
        p = _cube_edge(i // 22, u, (0.39, 0.17, 0.76))
        p = (p[0] + sign * 0.31, p[1] + (level - 5) * 0.27, p[2])
        clouds[4].append(_turn(p, (level - 5) * 0.06))
        # Discontinuity: a discontinuous spatial lattice, not a flat grid.
        cell = i % 25
        c = (
            (cell % 5 - 2) * 0.48,
            (cell // 5 - 2) * 0.40,
            (((cell * 7) % 3) - 1) * 0.45,
        )
        p = _cube_edge(i // 25, u, (0.22, 0.22, 0.22))
        p = tuple(p[k] + c[k] for k in range(3))
        clouds[5].append(_turn(p, 0.25, 0.12))
    # Stable angular ordering reduces violent cross-volume paths during morphs.
    return tuple(
        tuple(sorted(c, key=lambda p: (math.atan2(p[1], p[0]), p[2]))) for c in clouds
    )


def _jet(point, t):
    x, y, z = point
    original = (y - 0.89) / 1.27
    if (
        0 < original < 1
        and abs(x) <= 0.301 * original
        and abs(z - 0.12) <= 0.226 * original
    ):
        flow = (original + t / 6) % 1
        ratio = flow / max(0.02, original)
        return x * ratio, 0.89 + flow * 1.27, 0.12 + (z - 0.12) * ratio
    return point


def scene_points(t, stride=1):
    """36 s loop: hold each form 3 s, then ease into the next for 3 s."""
    t = t % CYCLE_SECONDS
    index = int(t // 6)
    phase = t % 6
    u = max(0.0, (phase - 3) / 3)
    blend = u * u * (3 - 2 * u)
    source = target_clouds()[index]
    dest = target_clouds()[(index + 1) % 6]
    drift = math.sin(math.pi * blend) * 0.085
    yaw = math.sin(t * TAU / CYCLE_SECONDS) * 0.15
    lift = math.sin(t * TAU / 6) * 0.025
    points = []
    for i in range(0, MAX_PARTICLES, stride):
        a, b = source[i], dest[i]
        if index == 0:
            a = _jet(a, t)
        if index == 5:
            b = _jet(b, t)
        x, y, z = _mix(a, b, blend)
        # Restrained, continuous dust drift; exact period closes the loop seam.
        wave = t * TAU / 12 + i * 2.399
        x += math.cos(wave) * (0.014 + drift)
        z += math.sin(wave) * (0.014 + drift)
        points.append(_turn((x, y + lift, z), yaw, 0.035 * math.sin(t * TAU / 18)))
    return tuple(points)


def palette(t):
    hue = (
        0.56 + 0.44 * (0.5 - 0.5 * math.cos((t % CYCLE_SECONDS) * TAU / CYCLE_SECONDS))
    ) % 1
    rgb = colorsys.hsv_to_rgb(hue, 0.55, 1)
    return tuple(
        "#" + "".join(f"{int(v * level):02x}" for v in rgb)
        for level in (80, 110, 150, 185, 215, 245)
    )


def project(point, width, height, yaw=0.0, pitch=0.0):
    """Perspective camera: depth affects both projected position and size."""
    x, y, z = _turn(point, -0.36 + yaw, 0.075 + pitch)
    factor = 6 / (6 - z)
    scale = min(width * 0.24, height * 0.15)
    return width * 0.5 + x * scale * factor, height * 0.39 - y * scale * factor, z


@lru_cache(maxsize=1)
def eye_paths():
    """Exactly one code iris. A foreground 3D glyph plane never enters morphs."""
    paths = []
    z = 1.85
    for r in (0.115, 0.185, 0.255):
        for sector in range(6):
            paths.append(
                tuple(
                    (r * math.cos(a), r * math.sin(a), z)
                    for a in ((sector + j / 8) * TAU / 6 for j in range(7))
                )
            )
    for j in range(16):
        a = j * TAU / 16
        x = 0.315 * math.cos(a)
        y = 0.315 * math.sin(a)
        glyph = (
            ((-0.011, 0.018), (0, 0.024), (0, -0.024))
            if j % 2
            else (
                (-0.014, -0.02),
                (0.014, -0.02),
                (0.014, 0.02),
                (-0.014, 0.02),
                (-0.014, -0.02),
            )
        )
        paths.append(tuple((x + u, y + v, z) for u, v in glyph))
    paths.append(
        ((-0.026, 0.065, z), (0.025, 0.015, z), (0.013, -0.06, z), (-0.026, 0.065, z))
    )
    return tuple(paths)


def _target(target):
    try:
        if target is None or len(target) != 2:
            return 0.0, 0.0
        x, y = map(float, target)
        if not math.isfinite(x) or not math.isfinite(y):
            return 0.0, 0.0
        return max(-1, min(1, x * 2 - 1)), max(-1, min(1, y * 2 - 1))
    except (TypeError, ValueError, OverflowError):
        return 0.0, 0.0


class ParticleWatchRenderer:
    """Fixed canvas resources. No interaction ownership or independent timers.

    12 fps idle / 20 fps active; sustained >18 ms work degrades to 360 points
    at 8 fps. JARVIS_WATCH_LOW_POWER=1 selects that fallback immediately.
    JARVIS_REDUCED_MOTION=1 draws a still blue Rift with a single code eye.
    """

    def __init__(self, canvas, width, height, *, reduced_motion=None):
        self.c = canvas
        self.w = width
        self.h = height
        self.tag = "watch_particles"
        self.reduced = (
            os.getenv("JARVIS_REDUCED_MOTION", "0") == "1"
            if reduced_motion is None
            else bool(reduced_motion)
        )
        self.low_detail = os.getenv("JARVIS_WATCH_LOW_POWER", "0") == "1"
        self.dead = False
        self.last = None
        self.elapsed = 0.0
        self.x = self.y = 0.0
        self.slow_frames = 0
        self._hidden = False
        self._drawn = False
        self._colors = [None] * MAX_PARTICLES
        self._eye_color = None
        self._hidden_points = False
        target_clouds()
        self.points = []
        self.eye_items = []
        try:
            for _ in range(MAX_PARTICLES):
                self.points.append(
                    canvas.create_oval(
                        -5, -5, -4, -4, fill="#235377", outline="", tags=self.tag
                    )
                )
            for _ in eye_paths():
                self.eye_items.append(
                    canvas.create_line(
                        -5, -5, -4, -4, fill="#84cbea", width=1, tags=self.tag
                    )
                )
        except Exception:
            with contextlib.suppress(Exception):
                canvas.delete(self.tag)
            raise

    def update(self, now=None, target=None, active=False):
        if self.dead:
            return
        now = time.monotonic() if now is None else now
        if not math.isfinite(now):
            return
        try:
            if not self.c.winfo_ismapped():
                self._hidden = True
                self.last = None
                return
            if self.reduced and self._drawn:
                return
            gap = 1 / 8 if self.low_detail else (1 / 20 if active else 1 / 12)
            if self.last is not None and now - self.last < gap:
                return
            start = time.perf_counter()
            dt = 0 if self.last is None else max(0, min(0.2, now - self.last))
            self.last = now
            self._hidden = False
            if not self.reduced:
                self.elapsed = (self.elapsed + dt) % CYCLE_SECONDS
            tx, ty = (0.0, 0.0) if self.reduced else _target(target)
            ease = 1 - math.exp(-max(dt, 0.05) * 4)
            self.x += (tx - self.x) * ease
            self.y += (ty - self.y) * ease
            yaw = self.x * 0.10
            pitch = -self.y * 0.055
            cloud = scene_points(self.elapsed, 2 if self.low_detail else 1)
            # Reuse canvas slots from far to near; foreground eye stays above all dust.
            projected = sorted(
                (project(p, self.w, self.h, yaw, pitch) for p in cloud),
                key=lambda p: p[2],
            )
            colors = palette(self.elapsed)
            scale = min(self.w * 0.24, self.h * 0.15)
            for i, (x, y, z) in enumerate(projected):
                r = max(0.65, min(2.3, scale / 95 * (0.95 + max(0, z) * 0.23)))
                item = self.points[i]
                self.c.coords(item, x - r, y - r, x + r, y + r)
                color = colors[max(0, min(5, int((z + 1.5) * 1.7)))]
                if color != self._colors[i]:
                    self.c.itemconfigure(item, fill=color)
                    self._colors[i] = color
            if self.low_detail and not self._hidden_points:
                for item in self.points[MAX_PARTICLES // 2 :]:
                    self.c.itemconfigure(item, state="hidden")
                self._hidden_points = True
            # Eye position/scale are coherent through every morph; glyph plane tilts
            # gently in 3D with gaze but is centered independently of camera yaw.
            for item, path in zip(self.eye_items, eye_paths()):
                coords = []
                for x, y, z in path:
                    x, y, z = _turn((x, y, z - 1.85), yaw, pitch)
                    factor = 6 / (6 - (z + 1.85))
                    coords.extend(
                        (
                            self.w * 0.5 + x * scale * factor,
                            self.h * 0.39 - y * scale * factor,
                        )
                    )
                self.c.coords(item, *coords)
            eye_color = colors[-1]
            if eye_color != self._eye_color:
                for item in self.eye_items:
                    self.c.itemconfigure(item, fill=eye_color)
                self._eye_color = eye_color
            self._drawn = True
            cost = time.perf_counter() - start
            self.slow_frames = (
                self.slow_frames + 1 if cost > 0.018 else max(0, self.slow_frames - 1)
            )
            if self.slow_frames >= 5:
                self.low_detail = True
        except Exception:
            # Decoration failure must never affect capture, polling or lock behavior.
            self.dead = True

    def destroy(self):
        self.dead = True
        with contextlib.suppress(Exception):
            self.c.delete(self.tag)
        self.points.clear()
        self.eye_items.clear()
