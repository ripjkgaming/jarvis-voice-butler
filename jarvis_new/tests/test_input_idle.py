"""Hermetic tests for src/input_idle.py (no Wayland, no helper spawn)."""

from __future__ import annotations

import json
import time

import pytest

import draft_notify
import input_idle

NOW = 1_759_000_000.0


def _write(path, *, idle: bool = False, updated: float = NOW, timeout_s: int = 300):
    path.write_text(
        json.dumps({"updated": updated, "timeout_s": timeout_s, "idle": idle})
    )
    return path


def test_read_state_active(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_IDLE_MINUTES", raising=False)
    p = _write(tmp_path / "state.json", idle=False)
    assert input_idle.read_state(p, now=NOW) == "active"


def test_read_state_idle(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_IDLE_MINUTES", raising=False)
    p = _write(tmp_path / "state.json", idle=True)
    assert input_idle.read_state(p, now=NOW) == "idle"


def test_read_state_missing_file(tmp_path) -> None:
    assert input_idle.read_state(tmp_path / "nope.json", now=NOW) == "unknown"


def test_read_state_malformed_json(tmp_path) -> None:
    p = tmp_path / "state.json"
    p.write_text("{not json")
    assert input_idle.read_state(p, now=NOW) == "unknown"


def test_read_state_non_dict_json(tmp_path) -> None:
    p = tmp_path / "state.json"
    p.write_text(json.dumps(["active"]))
    assert input_idle.read_state(p, now=NOW) == "unknown"


def test_read_state_stale_heartbeat(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_IDLE_MINUTES", raising=False)
    p = _write(tmp_path / "state.json", idle=False, updated=NOW - 61.0)
    assert input_idle.read_state(p, now=NOW) == "unknown"


def test_read_state_timeout_mismatch(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_IDLE_MINUTES", raising=False)
    p = _write(tmp_path / "state.json", idle=False, timeout_s=60)
    assert input_idle.read_state(p, now=NOW) == "unknown"


def test_read_state_timeout_follows_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_IDLE_MINUTES", "10")
    p = _write(tmp_path / "state.json", idle=True, timeout_s=600)
    assert input_idle.read_state(p, now=NOW) == "idle"


def test_idle_minutes_default(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_IDLE_MINUTES", raising=False)
    assert input_idle.idle_minutes() == 5


def test_idle_minutes_env_override(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_IDLE_MINUTES", "10")
    assert input_idle.idle_minutes() == 10


def test_idle_minutes_junk_env_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_IDLE_MINUTES", "junk")
    assert input_idle.idle_minutes() == 5


def test_ensure_helper_fresh_state_no_spawn(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_IDLE_MINUTES", raising=False)
    # Freshness is judged against the real clock inside ensure_helper.
    p = _write(tmp_path / "state.json", idle=False, updated=time.time())

    def no_spawn(*a, **k):
        raise AssertionError("must not spawn for fresh state")

    assert input_idle.ensure_helper(spawn=no_spawn, path=p) is True


def test_ensure_helper_spawns_once_when_unknown(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_IDLE_MINUTES", raising=False)
    monkeypatch.setattr(input_idle, "_proc", None)
    monkeypatch.setattr(input_idle, "_system_python", lambda: "/usr/bin/python3")
    p = tmp_path / "missing.json"
    calls: list = []

    class _Proc:
        def poll(self):
            return 0

    def fake_spawn(argv, **kw):
        calls.append((argv, kw))
        return _Proc()

    assert input_idle.ensure_helper(spawn=fake_spawn, path=p) is True
    assert len(calls) == 1
    argv, kw = calls[0]
    assert argv == [
        "/usr/bin/python3",
        str(input_idle.HELPER),
        str(p),
        str(5 * 60),
    ]
    assert kw.get("start_new_session") is True


def test_ensure_helper_no_respawn_while_alive(tmp_path, monkeypatch) -> None:
    class _Alive:
        def poll(self):
            return None

    monkeypatch.setattr(input_idle, "_proc", _Alive())
    p = tmp_path / "missing.json"  # unknown state, but proc is alive

    def no_spawn(*a, **k):
        raise AssertionError("must not spawn while proc is alive")

    assert input_idle.ensure_helper(spawn=no_spawn, path=p) is True
    # monkeypatch resets input_idle._proc after the test.


def test_ensure_helper_spawn_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(input_idle, "_proc", None)
    monkeypatch.setattr(input_idle, "_system_python", lambda: "/usr/bin/python3")
    p = tmp_path / "missing.json"

    def bad_spawn(*a, **k):
        raise RuntimeError("no fork today")

    assert input_idle.ensure_helper(spawn=bad_spawn, path=p) is False


@pytest.mark.parametrize(
    ("state", "want"),
    [("active", "present"), ("idle", "absent"), ("unknown", "unknown")],
)
def test_status_maps_state(monkeypatch, state, want) -> None:
    monkeypatch.setattr(input_idle, "ensure_helper", lambda *a, **k: True)
    monkeypatch.setattr(input_idle, "read_state", lambda *a, **k: state)
    assert input_idle.status() == {"status": want}


def test_default_presence_returns_input_idle_status(monkeypatch) -> None:
    sentinel = {"status": "present"}
    monkeypatch.setattr(input_idle, "status", lambda: sentinel)
    assert draft_notify._default_presence() is sentinel


# --- read_recent ---


def test_read_recent_true(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_IDLE_MINUTES", raising=False)
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"updated": NOW, "idle": False, "recent": True}))
    assert input_idle.read_recent(p, now=NOW) is True


def test_read_recent_false_no_recent(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_IDLE_MINUTES", raising=False)
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"updated": NOW, "idle": False, "recent": False}))
    assert input_idle.read_recent(p, now=NOW) is False


def test_read_recent_false_stale(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_IDLE_MINUTES", raising=False)
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"updated": NOW - 61.0, "idle": False, "recent": True}))
    assert input_idle.read_recent(p, now=NOW) is False


def test_read_recent_false_idle(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_IDLE_MINUTES", raising=False)
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"updated": NOW, "idle": True, "recent": True}))
    assert input_idle.read_recent(p, now=NOW) is False


def test_read_recent_false_malformed(tmp_path) -> None:
    p = tmp_path / "state.json"
    p.write_text("{not json")
    assert input_idle.read_recent(p, now=NOW) is False
