"""Bounded, blue holographic eyes for the existing Tk watch canvas.

No capture, identity, input, windows or timers: the watch owner supplies a
normalized motion centroid and calls update. All canvas objects are reused.
"""

from __future__ import annotations

import contextlib
import math
import os
import random
import time

TAU = math.tau
MAX_PARTICLES = 480


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def blink_open(now: float) -> float:
    """Fast closure, softer reopening, with an occasional double blink."""
    phase = now % 6.7
    for start in (0.0, 0.36) if int(now / 6.7) % 3 == 2 else (0.0,):
        t = phase - start
        if 0 <= t < 0.11:
            return 1 - math.sin(t / 0.11 * math.pi / 2)
        if 0.11 <= t < 0.30:
            return math.sin((t - 0.11) / 0.19 * math.pi / 2)
    return 1.0


class HolographicEyes:
    """A fixed-budget canvas renderer; target is (x,y) in camera coordinates.

    The caller must mirror x if its camera convention requires it. Pass None
    for stale/unavailable motion. Hidden canvases do no work. Slow canvases
    automatically degrade to 10 fps. reduced_motion gives a still portrait.
    """

    def __init__(self, canvas, width: int, height: int, *, reduced_motion=None):
        self.c = canvas
        self.w, self.h = width, height
        self.tag = (
            "watch_eyes"  # One portrait per canvas; also supports failed-init cleanup.
        )
        self.reduced = (
            os.getenv("JARVIS_REDUCED_MOTION", "0") == "1"
            if reduced_motion is None
            else reduced_motion
        )
        self.x = self.y = self.tilt = 0.0
        self.last = None
        self.low_detail = False
        self.dead = False
        self.slow_frames = 0
        self.epoch = None
        self.scale = min(width / 1000, height / 650)
        self.lines = []
        self.points = []
        # Segmented iris filaments: no filled cartoon eyeball or heavy bloom.
        for eye in (-1, 1):
            for ring in range(5):
                for segment in range(8):
                    item = canvas.create_line(
                        0,
                        0,
                        1,
                        1,
                        fill=("#164574", "#226bb0", "#398ed4", "#57b7ff", "#2469a4")[
                            ring
                        ],
                        width=1,
                        tags=self.tag,
                    )
                    self.lines.append((item, eye, ring, segment))
        rng = random.Random(817)
        for i in range(MAX_PARTICLES):
            eye = -1 if i < MAX_PARTICLES // 2 else 1
            angle = rng.random() * TAU
            radius = math.sqrt(rng.random())
            color = rng.choice(("#1b4979", "#276cad", "#3b94da", "#76c9ff"))
            item = canvas.create_oval(0, 0, 1, 1, fill=color, outline="", tags=self.tag)
            self.points.append((item, eye, angle, radius, rng.random()))
        self.lids = []
        for eye in (-1, 1):
            for side in (-1, 1):
                for layer in range(3):
                    item = canvas.create_line(
                        0,
                        0,
                        1,
                        1,
                        fill=("#102a4a", "#1d578f", "#5cbaff")[layer],
                        width=(5, 2, 1)[layer],
                        tags=self.tag,
                    )
                    self.lids.append((item, eye, side, layer))
        self.rails = []
        for side in (-1, 1):
            item = canvas.create_line(
                0, 0, 1, 1, fill="#173857", width=1, tags=self.tag
            )
            self.rails.append((item, side))

    def _project(self, x, y):
        ct, st = math.cos(self.tilt), math.sin(self.tilt)
        return (
            self.w / 2 + (x * ct - y * st + self.x * 15) * self.scale,
            self.h * 0.47 + (x * st + y * ct + self.y * 10) * self.scale,
        )

    def _curve(self, eye, side, aperture):
        coords = []
        for i in range(33):
            u = i / 32
            x = (u * 2 - 1) * 140
            # Sharply tapered corners and a gently asymmetric upper eyelid.
            y = side * math.sin(u * math.pi) ** 0.85 * 67 * aperture
            y += (u - 0.5) * eye * 13
            coords.extend(self._project(eye * 205 + x, y))
        return coords

    def update(self, now=None, target=None, active=False):
        if self.dead:
            return
        now = time.monotonic() if now is None else now
        gap = 0.1 if self.low_detail else (1 / 24 if active else 1 / 15)
        if self.last is not None and (self.reduced or now - self.last < gap):
            return
        try:
            if not self.c.winfo_ismapped():
                return
            start = time.perf_counter()
            dt = min(0.15, max(0, now - self.last)) if self.last is not None else 1 / 24
            self.last = now
            if self.epoch is None:
                self.epoch = now - 1.0  # Start with eyes open.
            t = now - self.epoch
            valid = (
                target is not None
                and len(target) == 2
                and all(math.isfinite(float(v)) for v in target)
            )
            tx, ty = (
                (
                    (clamp(float(target[0]), 0, 1) - 0.5) * 2,
                    (clamp(float(target[1]), 0, 1) - 0.5) * 2,
                )
                if valid
                else (0.0, 0.0)
            )
            if self.reduced:
                tx = ty = 0.0
            ease = 1 - math.exp(-dt * 6)
            self.x += (tx - self.x) * ease
            self.y += (ty - self.y) * ease
            self.tilt += ((self.x * 0.055 + self.y * 0.018) - self.tilt) * ease * 0.65
            aperture = 1.0 if self.reduced else blink_open(t)
            for item, eye, side, _layer in self.lids:
                self.c.coords(item, *self._curve(eye, side, max(0.025, aperture)))
            for item, eye, ring, segment in self.lines:
                coords = []
                radius = 24 + ring * 9
                for j in range(7):
                    a = (segment + j / 8) * TAU / 8 + (
                        0 if self.reduced else t * (0.028 if ring % 2 else -0.022)
                    )
                    x = math.cos(a) * radius + self.x * 42
                    y = math.sin(a) * radius + self.y * 22
                    bound = (
                        max(0, math.sin((x / 140 + 1) * math.pi / 2)) ** 0.85
                        * 66
                        * aperture
                    )
                    y = clamp(y, -bound, bound)
                    coords.extend(self._project(eye * 205 + x, y))
                self.c.coords(item, *coords)
            for index, (item, eye, angle, radius, phase) in enumerate(self.points):
                if self.low_detail and index % 2:
                    self.c.itemconfigure(item, state="hidden")
                    continue
                a = angle + (0 if self.reduced else t * 0.012 * (1 if eye == 1 else -1))
                # Annular iris dust, an outer scleral cloud, and central pupil.
                r = 27 + radius * 45 if phase < 0.72 else 74 + radius * 59
                x = math.cos(a) * r + self.x * 42
                y = math.sin(a) * r * (1 if phase < 0.72 else 0.44) + self.y * 22
                bound = (
                    max(0, math.sin(clamp((x / 140 + 1) / 2, 0, 1) * math.pi)) ** 0.85
                    * 65
                    * aperture
                )
                visible = abs(x) < 138 and abs(y) < bound and aperture > 0.06
                if not visible:
                    self.c.coords(item, -5, -5, -4, -4)
                    continue
                px, py = self._project(eye * 205 + x, y)
                size = max(0.65, min(1.8, self.scale * (0.7 + phase)))
                self.c.coords(item, px - size, py - size, px + size, py + size)
            for item, side in self.rails:
                self.c.coords(
                    item,
                    *self._project(side * 374, -16),
                    *self._project(side * 388, -16),
                    *self._project(side * 388, 16),
                    *self._project(side * 374, 16),
                )
            if time.perf_counter() - start > 0.018:
                self.slow_frames += 1
                if self.slow_frames >= 5:
                    self.low_detail = True
        except Exception:
            # Keep the last complete portrait. A lost/destroyed canvas must
            # never stop the watch loop, stop polling, or affect capture.
            self.dead = True

    def destroy(self):
        self.dead = True
        with contextlib.suppress(Exception):
            self.c.delete(self.tag)
        self.points.clear()
        self.lines.clear()
        self.lids.clear()
        self.rails.clear()
