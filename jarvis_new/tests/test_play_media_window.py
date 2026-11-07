"""play_media must reveal, never minimise, the player window (mocked KWin)."""

from __future__ import annotations

import subprocess

from system import kwin_windows, window_ctl, window_layout

INFO = "caption: Song - YouTube - Brave\nminimized: {m}\nuuid: {{new}}\n"


def _setup(monkeypatch, states, fronts):
    """states: minimized flag per getWindowInfo read; fronts: active titles."""
    ids = iter([{"old"}, {"old", "new"}])
    monkeypatch.setattr(window_ctl, "brave_window_ids", lambda **k: next(ids, {"old", "new"}))
    scripts: list[str] = []
    monkeypatch.setattr(
        window_layout, "run_script", lambda js, name="", run=None: scripts.append(js) or True
    )
    acts: list[tuple] = []
    monkeypatch.setattr(kwin_windows, "act_on", lambda a, u, **k: acts.append((a, u)))
    st, fr = iter(states), iter(fronts)
    last = {"s": states[-1], "f": fronts[-1]}

    def run(argv, **k):
        v = next(st, last["s"])
        return subprocess.CompletedProcess(argv, 0, INFO.format(m=v), "")

    return scripts, acts, run, lambda: next(fr, last["f"])


def test_minimised_window_is_unminimised_and_activated(monkeypatch):
    scripts, acts, run, active = _setup(monkeypatch, ["true", "false"], ["Konsole", "Song - YouTube - Brave"])
    r = window_ctl.reveal_new_brave({"old"}, run=run, sleep=lambda s: None, active=active)
    assert r == {"found": True, "visible": True, "front": True, "title": "Song - YouTube - Brave"}
    assert len(scripts) == 2 and all("w.minimized = false" in s for s in scripts)
    assert acts == []  # never minimised anything


def test_still_minimised_is_reported_honestly(monkeypatch):
    scripts, acts, run, active = _setup(monkeypatch, ["true"], ["Konsole"])
    r = window_ctl.reveal_new_brave({"old"}, run=run, sleep=lambda s: None, active=active)
    assert r["found"] and not r["visible"] and not r["front"]
    assert len(scripts) == 3  # retried 3 times
    assert acts == []


def test_no_new_window_found(monkeypatch):
    monkeypatch.setattr(window_ctl, "brave_window_ids", lambda **k: {"old"})
    r = window_ctl.reveal_new_brave({"old"}, sleep=lambda s: None, wait_s=1)
    assert not r["found"]


def test_play_media_never_calls_minimize_or_refocus_previous():
    import inspect

    from system import core

    src = inspect.getsource(core.SystemTools.play_media)
    full = inspect.getsource(core)
    assert "background_new_brave" not in full
    assert '"minimize"' not in src
    assert "_return_to(" not in src


def test_visibility_line_wording():
    from system.core import _visibility_line

    assert "on screen and in front" in _visibility_line({"found": True, "visible": True, "front": True})
    assert "still minimised" in _visibility_line({"found": True, "visible": False, "front": False})
    assert "cannot confirm" in _visibility_line({"found": False})
