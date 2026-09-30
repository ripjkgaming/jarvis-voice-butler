"""Hermetic tests for src/draft_notify.py (no camera, no network)."""

from __future__ import annotations

import datetime

import pytest

import draft_engine
from draft_notify import DraftWatcher, announce_line
from proactive.policy import ProactivePolicy

DAY = datetime.datetime(2026, 9, 29, 14, 0).timestamp()
NIGHT = datetime.datetime(2026, 9, 29, 23, 30).timestamp()
MORNING = datetime.datetime(2026, 9, 30, 8, 0).timestamp()


def _record(did: str = "m1", **kw) -> dict:
    rec = {
        "id": did,
        "thread_id": "t9",
        "to": "t@school.edu",
        "subject": "Re: Maths homework",
        "body": "Done, Sir.",
        "summary": "Teacher asks for the homework.",
        "sender": "Teacher",
        "created": DAY,
        "status": "pending",
        "priority": "normal",
    }
    rec.update(kw)
    return rec


def _harness(tmp_path, script, *, now: float = DAY):
    """Fake clock box, scripted presence, recording sleep/speak, real policy."""
    box = {"t": now}
    clock = lambda: box["t"]  # noqa: E731
    calls = {"presence": 0}
    seq = list(script)

    def presence_fn():
        calls["presence"] += 1
        if not seq:
            return {"status": "unknown"}
        nxt = seq.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    delays: list[float] = []

    async def sleep_fn(d: float) -> None:
        delays.append(d)

    spoken: list[str] = []

    async def speak_fn(line: str) -> None:
        spoken.append(line)

    policy = ProactivePolicy(
        window=(22 * 60, 7 * 60), cap=3, now_fn=clock, log_fn=lambda c, d: None
    )
    watcher = DraftWatcher(
        speak_fn,
        drafts_base=tmp_path,
        presence_fn=presence_fn,
        sleep_fn=sleep_fn,
        now_fn=clock,
        policy=policy,
        log_fn=lambda c, d: None,
    )
    return box, calls, delays, spoken, policy, watcher


@pytest.mark.asyncio
async def test_present_twice_announces_once(tmp_path) -> None:
    box, calls, delays, spoken, _, watcher = _harness(
        tmp_path, [{"status": "present"}, {"status": "present"}]
    )
    draft_engine.write_draft(_record(), tmp_path)
    await watcher._tick(box["t"])
    assert len(spoken) == 1
    assert spoken[0].startswith("Sir, I've drafted a reply to Teacher")
    assert "Maths homework" in spoken[0]
    assert "Re:" not in spoken[0]
    assert draft_engine.read_draft("m1", tmp_path)["status"] == "announced"
    assert calls["presence"] == 2
    assert delays == [2.0]


@pytest.mark.asyncio
async def test_no_pending_touches_no_camera(tmp_path) -> None:
    box, calls, delays, spoken, _, watcher = _harness(
        tmp_path, [{"status": "present"}, {"status": "present"}]
    )
    await watcher._tick(box["t"])
    assert spoken == []
    assert calls["presence"] == 0
    assert delays == []


@pytest.mark.asyncio
async def test_absent_then_present(tmp_path) -> None:
    box, calls, _delays, spoken, _, watcher = _harness(
        tmp_path,
        [{"status": "absent"}, {"status": "present"}, {"status": "present"}],
    )
    draft_engine.write_draft(_record(), tmp_path)
    await watcher._tick(box["t"])
    assert spoken == []
    assert draft_engine.read_draft("m1", tmp_path)["status"] == "pending"
    assert watcher._next_at - box["t"] == pytest.approx(20.0)
    box["t"] = watcher._next_at + 1
    await watcher._tick(box["t"])
    assert len(spoken) == 1
    assert draft_engine.read_draft("m1", tmp_path)["status"] == "announced"
    assert calls["presence"] == 3


@pytest.mark.asyncio
async def test_unknown_retries_with_growing_backoff(tmp_path) -> None:
    box, calls, delays, spoken, _, watcher = _harness(
        tmp_path, [{"status": "weird"}] * 20
    )
    draft_engine.write_draft(_record(), tmp_path)
    for expected in (20.0, 40.0, 80.0, 160.0, 300.0, 300.0):
        tick_now = box["t"]
        await watcher._tick(tick_now)
        assert spoken == []  # unknown never announces
        assert draft_engine.read_draft("m1", tmp_path)["status"] == "pending"
        assert watcher._next_at - tick_now == pytest.approx(expected)
        box["t"] = watcher._next_at + 1
    assert delays == []  # no confirm sleep without a first "present"
    assert calls["presence"] == 6


@pytest.mark.asyncio
async def test_presence_raise_retries_without_announcing(tmp_path) -> None:
    box, calls, _delays, spoken, _, watcher = _harness(
        tmp_path, [RuntimeError("camera dark")]
    )
    draft_engine.write_draft(_record(), tmp_path)
    await watcher._tick(box["t"])
    assert spoken == []
    assert draft_engine.read_draft("m1", tmp_path)["status"] == "pending"
    assert watcher._next_at - box["t"] == pytest.approx(20.0)
    assert calls["presence"] == 1


@pytest.mark.asyncio
async def test_present_then_absent_no_announcement(tmp_path) -> None:
    box, calls, delays, spoken, _, watcher = _harness(
        tmp_path, [{"status": "present"}, {"status": "absent"}]
    )
    draft_engine.write_draft(_record(), tmp_path)
    await watcher._tick(box["t"])
    assert spoken == []
    assert draft_engine.read_draft("m1", tmp_path)["status"] == "pending"
    assert calls["presence"] == 2
    assert delays == [2.0]


@pytest.mark.asyncio
async def test_repeat_draft_deduped(tmp_path) -> None:
    box, calls, _, spoken, _, watcher = _harness(tmp_path, [{"status": "present"}] * 4)
    draft_engine.write_draft(_record(), tmp_path)
    await watcher._tick(box["t"])
    assert len(spoken) == 1
    draft_engine.set_status("m1", "pending", tmp_path)
    await watcher._tick(box["t"])
    assert len(spoken) == 1  # never spoken twice
    assert draft_engine.read_draft("m1", tmp_path)["status"] == "announced"
    assert calls["presence"] == 4


@pytest.mark.asyncio
async def test_quiet_hours_hold_then_morning_announce(tmp_path) -> None:
    box, calls, _, spoken, _, watcher = _harness(
        tmp_path,
        [{"status": "present"}, {"status": "present"}],
        now=NIGHT,
    )
    draft_engine.write_draft(_record(), tmp_path)
    await watcher._tick(box["t"])
    assert spoken == []
    assert calls["presence"] == 0  # no camera at night
    assert draft_engine.read_draft("m1", tmp_path)["status"] == "pending"
    box["t"] = MORNING
    await watcher._tick(box["t"])
    assert len(spoken) == 1
    assert draft_engine.read_draft("m1", tmp_path)["status"] == "announced"


@pytest.mark.asyncio
async def test_urgency_high_vs_normal(tmp_path, monkeypatch) -> None:
    box, _, _, spoken, policy, watcher = _harness(tmp_path, [{"status": "present"}] * 4)
    draft_engine.write_draft(_record(priority="high"), tmp_path)
    seen: dict = {}
    orig = policy.consider

    def spy(text, **kw):
        seen.update(kw)
        return orig(text, **kw)

    monkeypatch.setattr(policy, "consider", spy)
    await watcher._tick(box["t"])
    assert seen.get("urgency") == "urgent"
    assert len(spoken) == 1

    draft_engine.write_draft(_record("m2", priority="normal"), tmp_path)
    await watcher._tick(box["t"])
    assert seen.get("urgency") == "info"


@pytest.mark.asyncio
async def test_announce_env_kill_switch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_DRAFT_ANNOUNCE", "0")
    box, calls, _, spoken, _, watcher = _harness(
        tmp_path, [{"status": "present"}, {"status": "present"}]
    )
    draft_engine.write_draft(_record(), tmp_path)
    await watcher._tick(box["t"])
    assert spoken == []
    assert calls["presence"] == 0
    assert draft_engine.read_draft("m1", tmp_path)["status"] == "pending"


@pytest.mark.asyncio
async def test_speak_error_keeps_pending(tmp_path, monkeypatch) -> None:
    box, _calls, _, spoken, _, watcher = _harness(
        tmp_path, [{"status": "present"}, {"status": "present"}]
    )
    draft_engine.write_draft(_record(), tmp_path)

    async def bad_speak(line: str) -> None:
        raise RuntimeError("speaker dead")

    monkeypatch.setattr(watcher, "_speak_fn", bad_speak)
    await watcher._tick(box["t"])  # must not raise
    assert spoken == []
    assert draft_engine.read_draft("m1", tmp_path)["status"] == "pending"


def test_announce_line_strips_untrusted_sender() -> None:
    line = announce_line(
        {
            "sender": "Evil <script>alert(1)</script>\x00\x07Hi",
            "subject": "Re: Re: Homework",
        }
    )
    assert line.startswith("Sir, I've drafted a reply to ")
    assert "Evil" in line and "Homework" in line
    assert "<" not in line and ">" not in line
    assert "\x00" not in line and "\x07" not in line
    assert "Re:" not in line


def test_announce_line_asks_what_to_change_or_send_as_is() -> None:
    from draft_notify import announce_line

    line = announce_line({"sender": "Arsh <a@b.c>", "subject": "Re: Skates"})
    assert "I've drafted a reply to" in line
    assert "What would you like me to change" in line
    assert "send it as is" in line
