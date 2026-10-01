"""DND rule: only past 22:30 AND after 30+ minutes without input."""

from __future__ import annotations

import datetime

import dnd
import input_idle


def _ts(hour: int, minute: int = 0) -> float:
    return datetime.datetime(2026, 9, 29, hour, minute).timestamp()


def test_active_only_when_late_and_idle_long_enough() -> None:
    idle_45 = lambda now=None: 45 * 60.0  # noqa: E731
    assert dnd.active(_ts(23, 0), idle_fn=idle_45) is True


def test_not_active_before_2230_even_if_idle() -> None:
    idle_45 = lambda now=None: 45 * 60.0  # noqa: E731
    assert dnd.active(_ts(22, 15), idle_fn=idle_45) is False
    assert dnd.active(_ts(12, 0), idle_fn=idle_45) is False


def test_not_active_while_still_typing_late_at_night() -> None:
    assert dnd.active(_ts(23, 30), idle_fn=lambda now=None: 0.0) is False
    assert dnd.active(_ts(23, 30), idle_fn=lambda now=None: 29 * 60.0) is False


def test_unknown_idle_is_not_dnd() -> None:
    assert dnd.active(_ts(23, 30), idle_fn=lambda now=None: None) is False


def test_idle_threshold_env(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_QUIET_IDLE_MINUTES", "10")
    assert dnd.active(_ts(23, 0), idle_fn=lambda now=None: 11 * 60.0) is True
    monkeypatch.setenv("JARVIS_QUIET_IDLE_MINUTES", "junk")
    assert dnd.idle_minutes_required() == 30


def test_quiet_window_env(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_QUIET_HOURS", "23:30-06:00")
    idle_45 = lambda now=None: 45 * 60.0  # noqa: E731
    assert dnd.active(_ts(23, 0), idle_fn=idle_45) is False
    assert dnd.active(_ts(23, 45), idle_fn=idle_45) is True


def test_idle_seconds_from_state_file(tmp_path, monkeypatch) -> None:
    import json

    monkeypatch.delenv("JARVIS_IDLE_MINUTES", raising=False)
    path = tmp_path / "input_idle.json"
    now = 10_000.0

    def write(**kw):
        base = {
            "idle": True,
            "changed": now - 25 * 60,
            "updated": now,
            "timeout_s": 300,
        }
        path.write_text(json.dumps({**base, **kw}))

    write()
    # idle fired 25 min ago after 5 min of quiet -> 30 min of no input
    assert input_idle.idle_seconds(path, now) == 30 * 60
    write(idle=False)
    assert input_idle.idle_seconds(path, now) == 0.0
    write(updated=now - 600)
    assert input_idle.idle_seconds(path, now) is None
    write(timeout_s=60)
    assert input_idle.idle_seconds(path, now) is None
    path.write_text("nope")
    assert input_idle.idle_seconds(path, now) is None
    assert input_idle.idle_seconds(tmp_path / "missing.json", now) is None


def test_idle_gate_holds_policy_quiet_until_away() -> None:
    from proactive.policy import ProactivePolicy

    late = _ts(23, 30)
    away = ProactivePolicy(
        window=(22 * 60 + 30, 7 * 60), now_fn=lambda: late, idle_gate=lambda: True
    )
    typing = ProactivePolicy(
        window=(22 * 60 + 30, 7 * 60), now_fn=lambda: late, idle_gate=lambda: False
    )
    legacy = ProactivePolicy(window=(22 * 60 + 30, 7 * 60), now_fn=lambda: late)
    assert away.in_quiet() is True
    assert typing.in_quiet() is False
    assert legacy.in_quiet() is True
