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
