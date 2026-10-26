"""Bounded 3D watch decoration: loop, resources, lifecycle and failure isolation."""

import math

import pytest

import watch_particles as wp


class Canvas:
    def __init__(self):
        self.items = {}
        self.mapped = True
        self.calls = 0

    def _create(self, coords, style):
        item = len(self.items) + 1
        self.items[item] = [coords, style]
        return item

    def create_oval(self, *coords, **style):
        return self._create(coords, style)

    def create_line(self, *coords, **style):
        return self._create(coords, style)

    def coords(self, item, *coords):
        self.calls += 1
        self.items[item][0] = coords

    def itemconfigure(self, item, **style):
        self.calls += 1
        self.items[item][1].update(style)

    def winfo_ismapped(self):
        return self.mapped

    def delete(self, tag):
        self.items = {k: v for k, v in self.items.items() if v[1]["tags"] != tag}


def test_targets_are_distinct_bounded_volumes_and_loop_is_seamless():
    targets = wp.target_clouds()
    assert len(targets) == 6
    assert all(len(cloud) == wp.MAX_PARTICLES for cloud in targets)
    for cloud in targets:
        assert all(math.isfinite(v) and abs(v) < 3 for p in cloud for v in p)
        assert max(p[2] for p in cloud) - min(p[2] for p in cloud) > 0.5
    assert len(set(targets)) == 6
    assert wp.scene_points(0) == wp.scene_points(wp.CYCLE_SECONDS)
    assert wp.palette(0) == wp.palette(wp.CYCLE_SECONDS)
    assert wp.scene_points(13.5, 2) == wp.scene_points(13.5)[::2]
    # A tiny step across the cycle seam must not create a jump.
    a, b = wp.scene_points(35.999), wp.scene_points(0.001)
    assert max(math.dist(p, q) for p, q in zip(a, b)) < 0.01
    for t in (2.999, 5.999, 8.999, 11.999, 17.999, 23.999, 29.999):
        assert (
            max(
                math.dist(p, q)
                for p, q in zip(wp.scene_points(t), wp.scene_points(t + 0.002))
            )
            < 0.02
        )


def test_color_reaches_red_and_returns_blue_with_small_per_frame_changes():
    def rgb(s):
        return tuple(int(s[i : i + 2], 16) for i in (1, 3, 5))

    blue, red = rgb(wp.palette(0)[-1]), rgb(wp.palette(18)[-1])
    assert blue[2] > blue[0] and red[0] > red[2]
    for n in range(720):
        a, b = rgb(wp.palette(n / 20)[-1]), rgb(wp.palette((n + 1) / 20)[-1])
        assert max(abs(x - y) for x, y in zip(a, b)) <= 5


def test_projection_has_perspective_and_scene_stays_above_status_text():
    far = wp.project((1, 0, -1), 1000, 650, yaw=0.36, pitch=-0.075)
    near = wp.project((1, 0, 1), 1000, 650, yaw=0.36, pitch=-0.075)
    assert near[0] - 500 > far[0] - 500
    for w, h in [(640, 480), (1920, 1080), (3440, 1440)]:
        for t in range(36):
            for p in wp.scene_points(t):
                x, y, _z = wp.project(p, w, h)
                assert 0 < x < w and 0 < y < h * 0.70


def test_fixed_budget_single_eye_and_cleanup():
    c = Canvas()
    e = wp.ParticleWatchRenderer(c, 1000, 650)
    count = len(c.items)
    assert count == wp.MAX_PARTICLES + len(wp.eye_paths())
    assert len(e.eye_items) == len(wp.eye_paths())
    for i in range(80):
        e.update(100 + i * 0.08, (10, -10), True)
    assert len(c.items) == count and not e.dead
    assert -1 <= e.x <= 1 and -1 <= e.y <= 1
    assert all(math.isfinite(v) for coords, _ in c.items.values() for v in coords)
    e.destroy()
    assert not c.items
    e.update(200)
    assert not c.items


def test_hidden_pauses_time_and_reduced_motion_is_static():
    c = Canvas()
    e = wp.ParticleWatchRenderer(c, 1000, 650)
    e.update(10)
    e.update(10.1)
    elapsed = e.elapsed
    calls = c.calls
    c.mapped = False
    e.update(100)
    assert c.calls == calls and e.elapsed == elapsed
    c.mapped = True
    e.update(100.1)
    assert e.elapsed == elapsed
    e.update(100.2)
    assert e.elapsed < elapsed + 0.11
    r = wp.ParticleWatchRenderer(Canvas(), 1000, 650, reduced_motion=True)
    r.update(0)
    calls = r.c.calls
    r.update(10, (1, 1), True)
    assert r.c.calls == calls and r.elapsed == 0 and r.x == 0


@pytest.mark.parametrize(
    "target", [None, (float("nan"), 0), ("bad", 0), 12, [], (float("inf"), 1)]
)
def test_invalid_motion_is_neutral_and_does_not_disable_renderer(target):
    e = wp.ParticleWatchRenderer(Canvas(), 1000, 650)
    e.update(0, target, True)
    assert not e.dead and e.x == 0 and e.y == 0


def test_low_power_and_sustained_slow_frames_reduce_work(monkeypatch):
    monkeypatch.setenv("JARVIS_WATCH_LOW_POWER", "1")
    c = Canvas()
    e = wp.ParticleWatchRenderer(c, 1000, 650)
    e.update(10)
    assert (
        e.low_detail
        and sum(v[1].get("state") == "hidden" for v in c.items.values())
        == wp.MAX_PARTICLES // 2
    )
    calls = c.calls
    e.update(10.02)
    assert c.calls == calls
    monkeypatch.delenv("JARVIS_WATCH_LOW_POWER")
    e = wp.ParticleWatchRenderer(Canvas(), 1000, 650)
    ticks = iter(i * 0.025 for i in range(60))
    monkeypatch.setattr(wp.time, "perf_counter", lambda: next(ticks))
    for i in range(8):
        e.update(i * 0.2)
    assert e.low_detail


def test_lost_canvas_is_contained():
    c = Canvas()
    e = wp.ParticleWatchRenderer(c, 1000, 650)
    c.coords = lambda *args: (_ for _ in ()).throw(RuntimeError("destroyed"))
    e.update(0)
    assert e.dead
    e.destroy()
