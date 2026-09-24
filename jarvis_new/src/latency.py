"""Per-turn latency attribution: what exactly made Jarvis slow. Pure + tiny.

Every function tool is wrapped (see agent._wrap_tools_with_timing) so each
call records its duration. Turn boundaries come from the voice session:
user-final-transcript opens a turn, the assistant message closes it.

Surfaces, cheapest first:
- actions.log always gets `latency <tool> <ms>ms` + a `latency turn …` line,
- the HUD captions get the breakdown (overlay reads captions.log),
- the `latency_report` voice tool speaks the last turn on demand
  ("why was that slow?"),
- turns slower than SPEAK_THRESHOLD_S with at least one tool call are
  announced proactively — the user asked to always be told the cause.

Zero inference cost. Never raises (wrappers swallow registry errors so
telemetry can never break a voice session).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

#: Proactive announcement only for real stalls with an attributable cause.
SPEAK_THRESHOLD_S = 15.0

#: Keep the last N tool calls per turn for the breakdown.
MAX_CALLS_PER_TURN = 12


@dataclass
class TurnSummary:
    total_s: float = 0.0
    calls: list[tuple[str, float]] = field(default_factory=list)

    @property
    def tool_s(self) -> float:
        return sum(ms for _, ms in self.calls) / 1000.0

    @property
    def culprit(self) -> tuple[str, float] | None:
        if not self.calls:
            return None
        name, ms = max(self.calls, key=lambda c: c[1])
        return (name, ms / 1000.0)


class LatencyTracker:
    """In-memory turn registry. One global instance (see TRACKER)."""

    def __init__(self) -> None:
        self._turn_start: float | None = None
        self._calls: list[tuple[str, float]] = []
        self.last: TurnSummary = TurnSummary()

    def turn_begin(self, now: float | None = None) -> None:
        self._turn_start = now if now is not None else time.monotonic()
        self._calls = []

    def record_tool_call(self, tool: str, elapsed_ms: float) -> TurnSummary | None:
        """Record one finished tool call. Returns the turn summary iff this
        call closes nothing — always None; summaries come from turn_end."""
        try:
            self._calls.append((str(tool), float(elapsed_ms)))
            del self._calls[: max(0, len(self._calls) - MAX_CALLS_PER_TURN)]
        except Exception:
            pass
        return None

    def turn_end(self, now: float | None = None) -> TurnSummary:
        # No reset here: an assistant message mid tool-chain must not wipe
        # the attribution. Calls accumulate until the next turn_begin, so
        # the final summary of a turn names every tool in it.
        end = now if now is not None else time.monotonic()
        total = max(
            0.0, end - (self._turn_start if self._turn_start is not None else end)
        )
        self.last = TurnSummary(total_s=total, calls=list(self._calls))
        return self.last


TRACKER = LatencyTracker()


def should_announce(summary: TurnSummary) -> bool:
    """Only real stalls with an attributable tool cause get spoken."""
    return summary.total_s >= SPEAK_THRESHOLD_S and bool(summary.calls)


def format_breakdown(summary: TurnSummary) -> str:
    """One-line log/caption form. Pure."""
    if not summary.calls:
        return f"turn {summary.total_s:.1f}s, no tools (all model)"
    bits = ", ".join(f"{name} {ms / 1000.0:.1f}s" for name, ms in summary.calls)
    return f"turn {summary.total_s:.1f}s: {bits}"


def format_spoken(summary: TurnSummary) -> str:
    """Butler-voice explanation naming the exact cause. Pure."""
    if not summary.calls:
        return (
            f"That took {summary.total_s:.0f} seconds, Sir, and no tools were "
            "involved — the delay was the model thinking, not anything I did."
        )
    top = summary.culprit
    assert top is not None
    name, secs = top
    others = len(summary.calls) - 1
    tail = f", plus {others} smaller step{'s' if others != 1 else ''}" if others else ""
    return (
        f"That took {summary.total_s:.0f} seconds, Sir. The culprit was "
        f"{name}, at {secs:.0f} seconds{tail}."
    )
