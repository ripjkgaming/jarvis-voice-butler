import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import sleep_screensaver_sonnet as S  # noqa: E402

NOW = datetime.datetime(2026, 1, 2, 13, 45, 7)


def test_frame_size_and_text():
    f = S.render_frame(140, 40, 5.0, NOW, {"load": "0.5"})
    lines = S.frame_lines(f)
    assert len(lines) == 40 and all(len(x) == 140 for x in lines)
    blob = "\n".join(lines)
    assert S.TAGLINE in blob
    assert "JARVIS//SLEEP" in blob


def test_clock_digits_present():
    f = S.render_frame(140, 40, 1.0, NOW)
    assert any("█" in x for x in S.frame_lines(f))


def test_wake_progress_renders():
    for p in (0.0, 0.3, 0.7, 0.95, 1.0):
        f = S.render_frame(120, 36, 2.0, NOW, wake=p)
        assert len(S.frame_lines(f)) == 36


def test_small_grid_no_crash():
    S.render_frame(60, 20, 0.0, NOW)


def test_drift_bounded_and_slow():
    for t in range(0, 600, 7):
        dx, dy = S.drift(t)
        assert abs(dx) <= 4 and abs(dy) <= 2
    assert S.drift(0) != S.drift(60)


def test_state_file(tmp_path):
    p = tmp_path / "s.json"
    assert not S.is_awake(str(p))
    p.write_text(json.dumps({"state": "sleeping"}))
    assert not S.is_awake(str(p))
    p.write_text(json.dumps({"state": "awake"}))
    assert S.is_awake(str(p))
    p.write_text("garbage")
    assert not S.is_awake(str(p))
