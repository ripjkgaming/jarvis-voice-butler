"""Hermetic tests for window layouts + undo (src/system/window_layout.py)."""

from __future__ import annotations

import json
import subprocess

import pytest

import active_window
from system import window_layout as wl


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    return tmp_path


def test_layout_fracs() -> None:
    assert wl.layout_fracs("left", 1) == [(0.0, 0.0, 0.5, 1.0)]
    assert wl.layout_fracs("right", 1) == [(0.5, 0.0, 0.5, 1.0)]
    assert wl.layout_fracs("split", 2) == [(0.0, 0.0, 0.5, 1.0), (0.5, 0.0, 0.5, 1.0)]
    assert len(wl.layout_fracs("thirds", 5)) == 3
    grid = wl.layout_fracs("grid", 3)
    assert grid[0] == (0.0, 0.0, 0.5, 0.5) and grid[2] == (0.0, 0.5, 1.0, 0.5)
    for n in range(1, 10):
        cells = wl.layout_fracs("grid", n)
        assert len(cells) == n
        assert abs(sum(w * h for _, _, w, h in cells) - 1.0) < 1e-9  # tiles the screen
    with pytest.raises(ValueError):
        wl.layout_fracs("spiral", 2)


def test_arrange_script_pushes_snapshot_first() -> None:
    js = wl.arrange_script(
        "split",
        ["Firefox", "Code"],
        ("org.jarvis.Focus", "/org/jarvis/Focus", "org.jarvis.Focus"),
    )
    assert '["firefox", "code"]' in js
    assert js.index('"Layout"') < js.index("w.frameGeometry = {x: Math.round")


def test_restore_script_embeds_snapshot() -> None:
    js = wl.restore_script({"windows": [{"id": "{a}", "x": 1, "y": 2, "w": 3, "h": 4}]})
    assert '"id": "{a}"' in js and "frameGeometry" in js


def test_snapshot_store_and_undo(home) -> None:
    snap = {
        "layout": "left",
        "windows": [{"id": "{a}", "x": 10, "y": 20, "w": 800, "h": 600}],
    }
    assert wl.note_snapshot(json.dumps(snap), now=1.0)
    assert not wl.note_snapshot("garbage")
    assert not wl.note_snapshot(json.dumps({"windows": []}))
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "7", "")

    assert wl.undo(run=run) == snap
    assert any("loadScript" in " ".join(c) for c in calls)
    assert wl.latest_snapshot() is None  # consumed
    assert wl.undo(run=run) is None


def test_snapshot_pruning(home) -> None:
    for i in range(wl.UNDO_KEEP + 5):
        wl.note_snapshot(json.dumps({"windows": [{"id": str(i)}]}), now=float(i + 1))
    assert len(list(wl.undo_dir().glob("*.json"))) == wl.UNDO_KEEP


def test_dbus_layout_member_routes_to_store(home) -> None:
    raw = json.dumps({"windows": [{"id": "{b}", "x": 0, "y": 0, "w": 1, "h": 1}]})
    assert active_window.handle_dbus_message("Layout", [raw]) == "layout"
    assert wl.latest_snapshot()[1]["windows"][0]["id"] == "{b}"
