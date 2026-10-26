"""Drag particles must fly back and reassemble into the window on release."""

import importlib.util
import math
import random
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

_PATH = Path(__file__).resolve().parent.parent / "desktop" / "cinematic" / "dragfx" / "dragfx.py"


@pytest.fixture()
def dragfx(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    spec = importlib.util.spec_from_file_location("dragfx_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _drag(mod, steps=40):
    random.seed(7)
    f = mod.Field()
    f.emit((100, 100, 400, 300), (0, 0), 0.016)
    for i in range(steps):
        f.emit((100 + i * 18, 100 + i * 6, 400, 300), (0, 0), 0.016)
        f.step(0.016)
    return f


def test_release_sends_motes_home_and_empties(dragfx):
    f = _drag(dragfx)
    end = (100 + 40 * 18, 100 + 40 * 6, 400, 300)
    assert f.p, "drag should shed motes"
    f.release(end)
    assert f.home == end and f.last is None
    assert all(m.mode == 1 for m in f.p)
    x, y, w, h = end
    peak_flash = 0.0
    for _ in range(int(1.6 / 0.016)):
        f.step(0.016)
        peak_flash = max(peak_flash, f.flash)
        for m in f.p:
            assert math.isfinite(m.x) and math.isfinite(m.y)
            assert 0.0 <= m.alpha <= 1.0
    assert not f.p, "all motes must land or fade within the return window"
    assert peak_flash > 0.05, "rim shimmer should fire as motes arrive"


def test_returning_motes_converge_on_window(dragfx):
    f = _drag(dragfx)
    end = (100 + 40 * 18, 100 + 40 * 6, 400, 300)
    f.release(end)
    cx, cy = end[0] + 200, end[1] + 150

    def mean_dist():
        return sum(math.hypot(m.x - cx, m.y - cy) for m in f.p) / max(len(f.p), 1)

    before = mean_dist()
    for _ in range(25):
        f.step(0.016)
    assert mean_dist() < before


def test_new_drag_cancels_return_target(dragfx):
    f = _drag(dragfx, 5)
    f.release((0, 0, 400, 300))
    f.emit((50, 0, 400, 300), (0, 0), 0.016)
    f.emit((90, 0, 400, 300), (0, 0), 0.016)
    assert f.home is None


def test_reform_tracks_committed_frame_after_snap(dragfx):
    f = _drag(dragfx)
    old = (-1200, 80, 400, 300)
    f.release(old)
    targets = [(m.tx, m.ty) for m in f.p]
    snapped = (1920, 0, 960, 1040)
    f.retarget(snapped)
    assert f.home == snapped
    for m, (tx, ty) in zip(f.p, targets):
        assert m.tx == pytest.approx(1920 + (tx + 1200) * 960 / 400)
        assert m.ty == pytest.approx((ty - 80) * 1040 / 300)
    for _ in range(110):
        f.step(0.016)
    assert not f.p


def test_regrab_clears_outline_before_any_movement(dragfx):
    f = _drag(dragfx)
    f.release((0, 0, 400, 300))
    f.flash = 0.8
    f.begin((2000, 100, 400, 300))
    assert f.home is None
    assert f.flash == 0
    f.release((2200, 100, 400, 300))
    assert all(2198 <= m.tx <= 2602 and 98 <= m.ty <= 402 for m in f.p)


def test_reports_keep_reform_owned_by_dragged_window(dragfx):
    import json
    from types import SimpleNamespace

    app = SimpleNamespace(
        locked=False, field=dragfx.Field(), window_id=None, last_report=0,
        clock=SimpleNamespace(isValid=lambda: True, nsecsElapsed=lambda: 1000000000),
        timer=SimpleNamespace(isActive=lambda: True),
    )

    def report(kind, window_id, x):
        dragfx.App.Report(app, json.dumps(dict(s=kind, id=window_id, x=x, y=0,
                                               w=400, h=300, cx=0, cy=0)))

    report('start', 1, 100)
    report('step', 1, 150)
    report('end', 1, 150)
    report('end', 2, 3000)
    report('target', 2, 3000)
    assert app.field.home == (150, 0, 400, 300)
    report('target', 1, 1920)
    assert app.field.home == (1920, 0, 400, 300)
    report('step', 1, 150)
    assert app.field.home == (1920, 0, 400, 300)
    report('start', 2, 3000)
    report('target', 1, 100)
    assert app.field.home is None
