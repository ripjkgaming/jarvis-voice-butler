"""Renderer safety: fixed resources, bounded gaze, no lifetime ownership."""

import math

from watch_eyes import MAX_PARTICLES, HolographicEyes, blink_open


class Canvas:
    def __init__(self):
        self.items = {}
        self.mapped = True
        self.calls = 0

    def create_line(self, *coords, **style):
        return self._create("line", coords, style)

    def create_oval(self, *coords, **style):
        return self._create("oval", coords, style)

    def _create(self, kind, coords, style):
        n = len(self.items) + 1
        self.items[n] = [kind, coords, style]
        return n

    def coords(self, item, *coords):
        self.items[item][1] = coords
        self.calls += 1

    def itemconfigure(self, item, **style):
        self.items[item][2].update(style)

    def winfo_ismapped(self):
        return self.mapped

    def delete(self, tag):
        self.items = {k: v for k, v in self.items.items() if v[2].get("tags") != tag}


def test_fixed_budget_bounded_finite_and_smooth_gaze():
    canvas = Canvas()
    eyes = HolographicEyes(canvas, 1920, 1080)
    count = len(canvas.items)
    assert count == MAX_PARTICLES + 94
    eyes.update(100, (100, -100), True)
    assert 0 < eyes.x < 0.4
    for i in range(300):
        eyes.update(100 + i / 24, (100, -100), True)
    assert len(canvas.items) == count
    assert 0 < eyes.x <= 1 and -1 <= eyes.y < 0
    assert abs(eyes.tilt) < 0.08
    for _, coords, _ in canvas.items.values():
        assert all(math.isfinite(n) and -10 <= n <= 1920 for n in coords)
    eyes.destroy()
    assert not canvas.items
    eyes.update(200)
    assert not canvas.items


def test_hidden_idle_reduced_and_stale_target():
    c = Canvas()
    e = HolographicEyes(c, 1000, 650)
    c.mapped = False
    e.update(100, (1, 1), True)
    assert not c.calls
    c.mapped = True
    e.update(100, (1, 1), True)
    calls = c.calls
    e.update(100.001, (1, 1), True)
    assert c.calls == calls
    for i in range(60):
        e.update(101 + i / 15, None)
    assert abs(e.x) < 0.001
    reduced = HolographicEyes(Canvas(), 1000, 650, reduced_motion=True)
    reduced.update(10)
    calls = reduced.c.calls
    reduced.update(20, (1, 1), True)
    assert reduced.c.calls == calls and reduced.x == 0


def test_blink_and_lost_canvas_do_not_escape():
    assert blink_open(0.11) == 0
    assert blink_open(0.31) == 1
    assert all(0 <= blink_open(i / 100) <= 1 for i in range(2000))
    c = Canvas()
    e = HolographicEyes(c, 1000, 650)
    c.coords = lambda *a: (_ for _ in ()).throw(RuntimeError("lost canvas"))
    e.update(100)
    assert e.dead
    e.destroy()


def test_slow_canvas_degrades_without_allocating(monkeypatch):
    import watch_eyes

    ticks = iter(i * 0.02 for i in range(40))
    monkeypatch.setattr(watch_eyes.time, "perf_counter", lambda: next(ticks))
    c = Canvas()
    e = HolographicEyes(c, 1000, 650)
    count = len(c.items)
    for i in range(8):
        e.update(100 + i * 0.2, (0.8, 0.3), True)
    assert e.low_detail
    assert len(c.items) == count
    assert (
        sum(v[2].get("state") == "hidden" for v in c.items.values())
        == MAX_PARTICLES // 2
    )
    calls = c.calls
    e.update(101.45, (0.8, 0.3), True)
    assert c.calls == calls  # Degraded updates are limited to 10 fps.
