"""Pure-render tests for the opus sleep screensaver (no Tk, no display)."""

from __future__ import annotations

import datetime
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import sleep_screensaver_opus as S  # noqa: E402

J = S.J
NOW = datetime.datetime(2026, 11, 12, 3, 7, 42)


def _info():
    i = S.empty_info()
    i.update(
        host="testhost",
        kernel="7.1.4",
        uptime=3725.0,
        load=(0.5, 0.75, 1.0),
        mem=(4 * 1048576, 16 * 1048576),
        swap=(0, 8 * 1048576),
        temp=55.0,
        temps=[("coretemp Package id 0", 55.0), ("nvme", 40.0)],
        cores=[0.1] * 12,
        rx=2048.0,
        tx=512.0,
        procs=321,
        since=NOW.timestamp() - 3600,
        agent=True,
        disks=[("/", 50e9, 100e9)],
        top=[("python3", 3.5)],
        log=["03:00:00 STANDBY ENGAGED"],
    )
    for k, v in (
        ("load", [0.5, 0.7]),
        ("temp", [50, 55]),
        ("rx", [10, 2048]),
        ("tx", [5, 512]),
        ("cpu", [0.1, 0.2]),
    ):
        i["hist"][k] = v
    return i


@pytest.mark.parametrize("cols,rows", [(192, 54), (160, 42), (100, 30)])
def test_render_shape_and_content(cols, rows):
    lines = S.render(5.0, cols, rows, NOW, _info())
    assert len(lines) == rows
    assert all(len(line) == cols for line in lines)
    text = "\n".join(lines)
    assert S.STANDBY in text
    assert "J.A.R.V.I.S" in text
    assert "██" in text  # big clock blocks


def test_render_is_pure_and_deterministic():
    a = S.render(7.25, 160, 42, NOW, _info())
    b = S.render(7.25, 160, 42, NOW, _info())
    assert a == b


def test_real_data_appears_and_missing_data_is_dashes():
    text = "\n".join(S.render(1.0, 192, 54, NOW, _info()))
    assert "testhost" in text and "DOUBLE CLAP" in text and "● ONLINE" in text
    empty = "\n".join(S.render(1.0, 192, 54, NOW, None))
    assert "--" in empty and "testhost" not in empty


def test_reactor_present_in_centre():
    lines = S.render(3.0, 192, 54, NOW, _info())
    L = S.layout(192, 54, "main", (0, 0))
    core = lines[L["cy"]][L["cx"] - 3 : L["cx"] + 4]
    assert any(c in J.RAMP[6:] for c in core), core


def test_drift_moves_content_against_burn_in():
    def standby_pos(t):
        for y, line in enumerate(S.render(t, 192, 54, NOW, _info())):
            x = line.find(S.STANDBY)
            if x >= 0:
                return x, y

    positions = {standby_pos(k * S.DRIFT_S + 1) for k in range(len(S.DRIFTS))}
    assert len(positions) >= 5
    for x, y in positions:  # never clipped off-screen
        assert x > 0 and 0 < y < 53


def test_reactor_port_matches_engine():
    R, cx, cy = 12, 40, 16
    fld = S.ReactorField(R)
    for t in (0.0, 1.3, 7.77, 42.0):
        f = J.Frame(81, 33)
        J.reactor(f, cx, cy, R, t, 10.0)
        idx = fld.levels(t)
        same = total = 0
        for j in range(idx.shape[0]):
            for i in range(idx.shape[1]):
                k = int(idx[j, i])
                if k < 0:
                    continue
                y, x = cy - fld.hy + j, cx - fld.hx + i
                if not (0 <= y < f.h and 0 <= x < f.w):
                    continue
                total += 1
                same += S.ReactorField.tile_spec(k)[0] == f.ch[y][x]
        assert total > 500
        assert same / total > 0.98, (t, same, total)


def test_side_roles_render():
    for role in ("side-l", "side-r"):
        text = "\n".join(S.render(2.0, 192, 54, NOW, _info(), role=role))
        assert "TELEMETRY" in text and S.STANDBY in text


def test_wake_animation_collapses_then_flashes():
    cols, rows = 160, 42
    L = S.layout(cols, rows, "main", (0, 0))

    def lit(lines, far=True):
        n = 0
        for y, line in enumerate(lines):
            for x, c in enumerate(line):
                if c != " " and (not far or abs(x - L["cx"]) > 40):
                    n += 1
        return n

    start = S.render_wake(0.0, cols, rows, 1.0, NOW, _info())
    mid = S.render_wake(0.5, cols, rows, 1.0, NOW, _info())
    flash = S.render_wake(0.65, cols, rows, 1.0, NOW, _info())
    end = S.render_wake(0.999, cols, rows, 1.0, NOW, _info())
    assert lit(mid) < lit(start) * 0.5  # HUD imploded towards the reactor
    assert flash[L["cy"]][L["cx"]] in "█▓"  # flash at the core
    assert lit(end, far=False) < lit(start, far=False)


def test_big_text_dimensions():
    rows = S.big_text("12:34")
    assert len(rows) == 5 and len({len(r) for r in rows}) == 1


def test_read_state(tmp_path):
    p = tmp_path / "sleep_state.json"
    assert S.read_state(str(p)) == {"state": "awake", "since": None}
    p.write_text(json.dumps({"state": "asleep", "since": 123.5}))
    assert S.read_state(str(p)) == {"state": "asleep", "since": 123.5}
    p.write_text("{bad")
    assert S.read_state(str(p))["state"] == "awake"


def test_state_path_honours_jarvis_home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert S.state_path() == os.path.join(str(tmp_path), "sleep_state.json")


def test_exit_reason():
    assert S.exit_reason("asleep", True, True, False, None, 99) is None
    assert S.exit_reason("awake", True, False, False, None, 0) == "awake"
    assert S.exit_reason("awake", False, True, True, 20, 0) == "awake"
    # stale awake file: fullscreen gives up after the grace, previews keep running
    assert S.exit_reason("awake", False, False, False, None, 1) is None
    assert (
        S.exit_reason("awake", False, False, False, None, S.STALE_GRACE + 1)
        == "stale-awake"
    )
    assert S.exit_reason("awake", False, False, True, None, 999) is None
    assert S.exit_reason("awake", False, False, False, 20, 999) is None


def test_every_glyph_exists_in_the_raster_font():
    pytest.importorskip("PIL")
    import jarvis_raster as RR

    try:
        ras = RR.Raster(16)
    except FileNotFoundError:
        pytest.skip("no monospace font")
    tofu = ras._mask("￿").tobytes()
    chars = set()
    for role in ("main", "side-l", "side-r"):
        chars |= set("".join(S.render(3.0, 192, 54, NOW, _info(), role=role)))
    for p in (0.3, 0.7):
        chars |= set("".join(S.render_wake(p, 160, 42, 1.0, NOW, _info())))
    missing = [c for c in chars if c != " " and ras._mask(c).tobytes() == tofu]
    assert not missing, missing
