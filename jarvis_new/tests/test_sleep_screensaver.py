"""Pure render helpers and the sleep-state reader for src.sleep_screensaver."""

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

sleep_screensaver = pytest.importorskip("src.sleep_screensaver")
S = sleep_screensaver

WHEN = dt.datetime(2026, 11, 12, 21, 4, 33, 200_000)
KW = dict(when=WHEN, uptime=3 * 86400 + 4000, load=(0.42, 0.38, 0.31), host="box")


def test_window_title_and_standby_text_are_exact():
    assert S.WINDOW_TITLE == "JARVIS-SLEEP"
    assert S.STANDBY_TEXT == "STANDBY - DOUBLE CLAP TO WAKE"


def test_compose_returns_exact_grid_of_strings():
    rows = S.compose(120, 40, 2.0, **KW)
    assert len(rows) == 40
    assert all(len(r) == 120 for r in rows)
    assert all(isinstance(r, str) for r in rows)


def test_compose_shows_standby_clock_and_panels():
    text = "\n".join(S.compose(160, 50, 2.0, **KW))
    assert S.STANDBY_TEXT in text
    assert "UPTIME" in text and "3d 01:06" in text
    assert "2026-11-12" in text
    assert "JARVIS-SLEEP" in text


def test_frame_is_ascii_only():
    text = "".join(S.compose(160, 50, 3.0, **KW))
    assert all(ord(c) < 128 for c in text)


def test_reactor_sits_in_centre():
    rows = S.compose(160, 50, 3.0, **KW)
    centre = rows[25][70:90]
    assert any(c not in " " for c in centre)


def test_same_time_gives_same_frame():
    assert S.compose(120, 40, 5.5, **KW) == S.compose(120, 40, 5.5, **KW)


def test_glyph_set_and_layout_shift_across_burn_in_epochs():
    a = S.compose(120, 40, 1.0, **KW)
    b = S.compose(120, 40, 1.0 + S.SHIFT_S * 2, **KW)
    assert a != b


def test_clock_rows_render_digits_and_colon():
    rows = S.clock_rows("21:04:33", colon_on=True)
    assert len(rows) == 5
    assert any("#" in r for r in rows)
    blank = S.clock_rows("21:04:33", colon_on=False)
    assert rows != blank


def test_wake_flash_is_brighter_than_idle():
    idle = S.build(120, 40, 2.0, **KW)
    flash = S.build(120, 40, 2.0, wake=S.FLASH_END_S / S.WAKE_DUR, **KW)

    def near_white(f):
        return sum(
            1
            for row, crow in zip(f.ch, f.co)
            for ch, c in zip(row, crow)
            if ch != " " and min(c) > 200
        )

    assert near_white(flash) > near_white(idle)


def test_wake_frame_omits_hud_text():
    text = "\n".join(S.compose(160, 50, 2.0, wake=0.3, **KW))
    assert S.STANDBY_TEXT not in text


def test_is_awake_reads_state_file(tmp_path):
    p = tmp_path / "sleep_state.json"
    assert S.is_awake(str(p)) is False  # missing file keeps sleeping
    p.write_text("{not json", encoding="utf-8")
    assert S.is_awake(str(p)) is False
    p.write_text(json.dumps({"state": "sleep"}), encoding="utf-8")
    assert S.is_awake(str(p)) is False
    p.write_text(json.dumps({"state": " Awake "}), encoding="utf-8")
    assert S.is_awake(str(p)) is True


def test_cli_parses_wake_anim_windowed_and_seconds(monkeypatch):
    seen = {}

    def fake_run(**kw):
        seen.update(kw)

    monkeypatch.setattr(S, "run", fake_run)
    S.main(["--wake-anim", "--windowed", "--seconds", "5", "--state-file", "/x/s.json"])
    assert seen["wake_anim"] is True
    assert seen["windowed"] is True
    assert seen["seconds"] == 5.0
    assert seen["state_path"] == "/x/s.json"


def test_wake_timeline_collapse_flash_then_fade():
    a = S.wake_levels(0.0)
    assert a[0] == 0.0 and a[2] == 0.0  # starts uncollapsed and unfaded
    peak = S.wake_levels(S.FLASH_END_S / S.WAKE_DUR)
    assert peak[1] > 0.99 and peak[2] == 0.0  # flash peaks, fade not started
    end = S.wake_levels(1.0)
    assert end[2] == 1.0 and end[1] == 0.0  # glyph layer fully faded, flash gone
    assert S.FADE_END_S - S.FLASH_END_S >= 1.0  # the fade runs 1.0 s or longer


def test_window_alpha_falls_monotonically_to_clear():
    steps = [S.window_alpha(i / 50) for i in range(51)]
    assert steps[0] == 1.0 and steps[-1] == 0.0
    assert all(b <= a for a, b in zip(steps, steps[1:]))


def test_glyph_layer_dissolves_during_the_fade():
    def glyphs(wake):
        return sum(
            1
            for row in S.build(120, 40, 2.0, wake=wake, **KW).ch
            for ch in row
            if ch != " "
        )

    early, mid, late = glyphs(S.FLASH_END_S / S.WAKE_DUR), glyphs(0.75), glyphs(0.9)
    assert early > mid > late >= 0
    assert glyphs(1.0) == 0


def test_fade_start_matches_controller_window_restore():
    controller = pytest.importorskip("sleep_controller")
    assert controller.WAKE_WINDOWS_DELAY_S == S.FADE_START_S
    assert controller.DEFAULT_SCREENSAVER_MODULE == "src.sleep_screensaver"
