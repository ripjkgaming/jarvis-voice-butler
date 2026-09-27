"""Tests for latency attribution (src/latency.py + tool wrapping)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from latency import (
    SPEAK_THRESHOLD_S,
    LatencyTracker,
    format_breakdown,
    format_spoken,
    should_announce,
)


def test_turn_records_tool_breakdown() -> None:
    t = LatencyTracker()
    t.turn_begin(now=100.0)
    t.record_tool_call("open_url", 2500.0)
    t.record_tool_call("read_page", 800.0)
    s = t.turn_end(now=104.0)
    assert s.total_s == pytest.approx(4.0)
    assert s.culprit == ("open_url", pytest.approx(2.5))
    assert "open_url 2.5s" in format_breakdown(s)


def test_no_tools_blames_model() -> None:
    t = LatencyTracker()
    t.turn_begin(now=0.0)
    s = t.turn_end(now=6.0)
    assert s.calls == []
    assert "no tools" in format_breakdown(s)
    assert "model thinking" in format_spoken(s)


def test_spoken_names_culprit_and_seconds() -> None:
    t = LatencyTracker()
    t.turn_begin(now=0.0)
    t.record_tool_call("nmap_scan", 12000.0)
    t.record_tool_call("read_page", 1000.0)
    s = t.turn_end(now=16.0)
    line = format_spoken(s)
    assert "nmap_scan" in line and "12 seconds" in line and "16 seconds" in line


def test_announce_only_slow_tool_turns() -> None:
    t = LatencyTracker()
    t.turn_begin(now=0.0)
    t.record_tool_call("x", 100.0)
    assert not should_announce(t.turn_end(now=3.0))  # fast: silent
    t.turn_begin(now=0.0)
    assert not should_announce(t.turn_end(now=SPEAK_THRESHOLD_S + 5))  # slow, no tools
    t.turn_begin(now=0.0)
    t.record_tool_call("open_url", 9000.0)
    assert should_announce(t.turn_end(now=SPEAK_THRESHOLD_S + 1))


def test_tracker_resets_between_turns() -> None:
    t = LatencyTracker()
    t.turn_begin(now=0.0)
    t.record_tool_call("a", 500.0)
    t.turn_end(now=1.0)
    t.turn_begin(now=10.0)
    s = t.turn_end(now=11.0)
    assert s.calls == [] and s.total_s == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_tool_wrap_records_duration() -> None:

    from agent import _wrap_tools_with_timing
    from latency import TRACKER

    async def fake_tool(context, x: str = "hi"):
        import asyncio

        await asyncio.sleep(0.05)
        return {"say": "done"}

    # SimpleNamespace, not MagicMock: MagicMock auto-creates any
    # attribute, which trips the wrap-once marker guard.
    tool = SimpleNamespace(id="fake_probe", _func=fake_tool)
    [_wrapped] = _wrap_tools_with_timing([tool])
    TRACKER.turn_begin()
    out = await tool._func(MagicMock(), "hi")
    assert out == {"say": "done"}
    summary = TRACKER.turn_end()
    assert len(summary.calls) == 1
    assert summary.calls[0][0] == "fake_probe"
    assert summary.calls[0][1] >= 40.0  # ms, actually slept 50ms


def test_phase_marks_measure_think_and_speak() -> None:
    t = LatencyTracker()
    t.turn_begin(now=0.0)
    t.mark("thinking", now=1.2)
    t.mark("speaking", now=2.0)
    s = t.turn_end(now=3.0)
    assert s.phase_gap("heard", "thinking") == pytest.approx(1.2)
    assert s.phase_gap("thinking", "speaking") == pytest.approx(0.8)
    assert "think 1.2s" in format_breakdown(s)
    assert "speak 0.8s" in format_breakdown(s)


def test_phase_gap_missing_is_none() -> None:
    t = LatencyTracker()
    t.turn_begin(now=0.0)
    s = t.turn_end(now=1.0)
    assert s.phase_gap("heard", "thinking") is None


def test_mark_outside_turn_ignored() -> None:
    t = LatencyTracker()
    t.mark("thinking")
    assert t.turn_end().phases == []


def test_is_quota_error_matrix() -> None:
    from latency import is_quota_error

    assert is_quota_error(Exception("429 Too Many Requests"))
    assert is_quota_error(Exception("quota exceeded for metric X"))
    assert is_quota_error(Exception("RESOURCE_EXHAUSTED"))
    chained = Exception("all LLMs failed")
    chained.__cause__ = Exception("429")
    assert is_quota_error(chained)
    assert not is_quota_error(Exception("400 minimum deadline 10s"))
    assert not is_quota_error(Exception("invalid API key"))
    assert not is_quota_error(ValueError("no such file"))


def test_vad_kwargs_defaults_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import vad_kwargs

    monkeypatch.delenv("JARVIS_VAD_MIN_SILENCE", raising=False)
    assert vad_kwargs()["min_silence_duration"] == pytest.approx(0.55)
    monkeypatch.setenv("JARVIS_VAD_MIN_SILENCE", "0.3")
    assert vad_kwargs()["min_silence_duration"] == pytest.approx(0.3)
    monkeypatch.setenv("JARVIS_VAD_THRESHOLD", "junk")
    assert vad_kwargs()["activation_threshold"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_slow_tool_emits_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import asyncio
    from types import SimpleNamespace

    import agent as agent_mod
    from agent import _wrap_tools_with_timing
    from latency import TRACKER

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(agent_mod, "SLOW_TOOL_S", 0.05)

    async def slow_tool(context):
        await asyncio.sleep(0.25)
        return {"say": "done"}

    tool = SimpleNamespace(id="slow_probe", _func=slow_tool)
    _wrap_tools_with_timing([tool])
    TRACKER.turn_begin()
    assert await tool._func(None) == {"say": "done"}
    await asyncio.sleep(0)  # let the cancelled nudge settle
    captions = (tmp_path / "captions.log").read_text()
    assert "(still on it: slow_probe" in captions


@pytest.mark.asyncio
async def test_pending_tracks_inflight_tools() -> None:
    import asyncio
    from types import SimpleNamespace

    from agent import _wrap_tools_with_timing
    from latency import TRACKER

    started = asyncio.Event()
    release = asyncio.Event()

    async def hanging_tool(context):
        started.set()
        await release.wait()
        return {"say": "done"}

    tool = SimpleNamespace(id="hang_probe", _func=hanging_tool)
    _wrap_tools_with_timing([tool])
    TRACKER.turn_begin()
    task = asyncio.create_task(tool._func(None))
    await started.wait()
    assert TRACKER.pending == 1
    release.set()
    assert await task == {"say": "done"}
    assert TRACKER.pending == 0


@pytest.mark.asyncio
async def test_watchdog_speaks_when_specialist_stalls(monkeypatch) -> None:
    import asyncio

    from agent import _specialist_watchdog
    from latency import TRACKER

    async def instant_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant_sleep)

    said: list[str] = []

    class FakeSession:
        async def generate_reply(self, **kwargs):
            raise RuntimeError("model refused")

        async def say(self, line: str):
            said.append(line)

    TRACKER.turn_begin()
    TRACKER.pending = 0
    TRACKER.last_tool_end = None
    await _specialist_watchdog(FakeSession(), entered_at=0.0)
    assert said and "Still on it" in said[0]


@pytest.mark.asyncio
async def test_watchdog_stays_quiet_while_working(monkeypatch) -> None:
    import asyncio
    import time

    from agent import _specialist_watchdog
    from latency import TRACKER

    async def instant_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant_sleep)

    said: list[str] = []

    class FakeSession:
        async def generate_reply(self, **kwargs):
            said.append("nudge")

        async def say(self, line: str):
            said.append(line)

    TRACKER.turn_begin()
    TRACKER.pending = 1  # a tool is running: hands off
    await _specialist_watchdog(FakeSession(), entered_at=0.0)
    assert said == []
    TRACKER.pending = 0
    TRACKER.last_tool_end = time.monotonic()  # just finished: hands off
    await _specialist_watchdog(FakeSession(), entered_at=0.0)
    assert said == []
    TRACKER.pending = 0
