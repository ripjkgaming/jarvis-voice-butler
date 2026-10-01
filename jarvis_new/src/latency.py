"""Content-free latency observations and measured tool durations.

Every function tool is wrapped (see agent._wrap_tools_with_timing) so each
call records its duration. Turn boundaries come from the voice session:
user-final-transcript opens a turn, the assistant message closes it. This
legacy interval is not end-to-end voice latency: realtime providers may
deliver final transcripts after their response has begun. The separate,
session-local VoiceLatencyObserver measures state events without transcripts.

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

import contextlib
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

#: Proactive announcement only for real stalls with an attributable cause.
SPEAK_THRESHOLD_S = 15.0

#: Keep the last N tool calls per turn for the breakdown.
MAX_CALLS_PER_TURN = 12


def _finite_nonnegative(value: object) -> float | None:
    """Accept numeric measurements only; never stringify content or missing data."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _duration_field(
    name: str, value: object, *, ambiguous_zero: bool = False
) -> str | None:
    number = _finite_nonnegative(value)
    if number is None:
        return None
    if ambiguous_zero and number == 0:
        return f"{name}=unavailable_or_zero"
    return f"{name}={number:.6f}"


def format_sdk_metrics(metric: object) -> str | None:
    """Allowlisted scalar timing fields from an SDK component metric.

    LiveKit 1.8.2's legacy EOU event replaces missing anchors with zero.
    Gemini's realtime ttft starts at provider generation creation (which
    can be the first received server message), not at the user's speech
    end or request submission. Keep those boundaries explicit. Missing,
    negative sentinel, and non-finite durations never become zero.
    """
    kind = getattr(metric, "type", None)
    suffix = ""
    ambiguous: tuple[str, ...] = ()
    if kind == "eou_metrics":
        label = "sdk_eou"
        fields = (
            ("end_of_utterance_s", "end_of_utterance_delay"),
            ("transcription_s", "transcription_delay"),
            ("user_turn_callback_s", "on_user_turn_completed_delay"),
        )
        ambiguous = ("end_of_utterance_s", "transcription_s")
    elif kind == "realtime_model_metrics":
        label = "sdk_realtime"
        fields = (("provider_generation_to_first_audio_s", "ttft"),)
        suffix = " scope=provider_generation_not_user_e2e"
    elif kind == "llm_metrics":
        label = "sdk_llm"
        fields = (("request_to_first_token_s", "ttft"),)
    elif kind == "tts_metrics":
        label = "sdk_tts"
        fields = (("request_to_first_audio_s", "ttfb"),)
    elif kind == "stt_metrics":
        label = "sdk_stt"
        fields = (("input_audio_s", "audio_duration"),)
        # Streaming STT reports a zero duration placeholder, not a fast
        # recognition call. Only a known batch call has a duration here.
        if getattr(metric, "streamed", None) is False:
            fields = (("recognize_s", "duration"), *fields)
    else:
        return None
    parts = [
        part
        for name, source in fields
        if (
            part := _duration_field(
                name, getattr(metric, source, None), ambiguous_zero=name in ambiguous
            )
        )
        is not None
    ]
    return f"{label} {' '.join(parts)}{suffix}" if parts else None


def format_message_metrics(
    role: str, metrics: Mapping[str, object] | None
) -> str | None:
    """Format available per-message timings, excluding content and identifiers.

    Unlike legacy EOU events, ChatMessage.metrics preserves absent values.
    The installed realtime pipeline omits several fields (including E2E);
    do not derive substitutes from provider generation timestamps. Output
    playback is what the SDK reports, not a physical-speaker measurement.
    """
    if not isinstance(metrics, Mapping):
        return None
    if role == "user":
        fields = (
            ("end_of_turn_s", "end_of_turn_delay"),
            ("transcription_s", "transcription_delay"),
            ("user_turn_callback_s", "on_user_turn_completed_delay"),
        )
    elif role == "assistant":
        fields = (
            ("llm_node_first_token_s", "llm_node_ttft"),
            ("tts_node_first_audio_s", "tts_node_ttfb"),
            ("output_reported_playback_s", "playback_latency"),
            ("sdk_user_end_to_response_s", "e2e_latency"),
        )
    else:
        return None
    parts = [
        part
        for name, source in fields
        if (part := _duration_field(name, metrics.get(source))) is not None
    ]
    return f"sdk_turn role={role} {' '.join(parts)}" if parts else None


class VoiceLatencyObserver:
    """One session's latest detected utterance, using a monotonic clock.

    Feed user and agent state events; log each non-None return. User
    speaking->listening is VAD *detection*, already after its silence
    wait. Its duration is an observed state interval, not acoustic speech
    duration or endpointing latency. Agent speaking is the SDK's output
    boundary, not the listener's physical speaker. These intervals omit
    wake detection and do not separate model, transport, or playback.

    Final transcripts never enter this observer, so delayed Gemini
    transcripts cannot erase first-response timing. New speech/away
    invalidates the old pending observation. Space is constant; no
    transcripts, per-turn history, or content-bearing objects are stored.
    """

    def __init__(self) -> None:
        self._user_state: str | None = None
        self._agent_state: str | None = None
        self._last_event_at: float | None = None
        self._speech_started_at: float | None = None
        self._speech_ended_at: float | None = None
        self._agent_spoke_at: float | None = None
        self._reported = False

    def _event_time(self, now: float | None) -> float | None:
        value = _finite_nonnegative(time.monotonic() if now is None else now)
        if value is None or (
            self._last_event_at is not None and value < self._last_event_at
        ):
            return None
        self._last_event_at = value
        return value

    def user_state_changed(self, state: str, now: float | None = None) -> str | None:
        if state not in ("speaking", "listening", "away"):
            return None
        at = self._event_time(now)
        if at is None or state == self._user_state:
            return None
        previous = self._user_state
        self._user_state = state
        if state in ("speaking", "away"):
            self._speech_started_at = at if state == "speaking" else None
            self._speech_ended_at = None
            self._agent_spoke_at = None
            self._reported = False
        elif previous == "speaking":
            self._speech_ended_at = at
        return self._report()

    def agent_state_changed(self, state: str, now: float | None = None) -> str | None:
        if state not in ("initializing", "idle", "listening", "thinking", "speaking"):
            return None
        at = self._event_time(now)
        if at is None or state == self._agent_state:
            return None
        self._agent_state = state
        if (
            state == "speaking"
            and self._speech_started_at is not None
            and self._agent_spoke_at is None
        ):
            self._agent_spoke_at = at
        return self._report()

    def _report(self) -> str | None:
        start, end, reply = (
            self._speech_started_at,
            self._speech_ended_at,
            self._agent_spoke_at,
        )
        if self._reported or start is None or end is None or reply is None:
            return None
        self._reported = True
        gap = (
            f"vad_end_to_agent_speaking_s={reply - end:.6f}"
            if reply >= end
            else "agent_speaking_before_vad_end=true"
        )
        return (
            f"voice_observed user_speaking_state_s={end - start:.6f} {gap} "
            "scope=state_events_not_acoustic_e2e"
        )


def install_session_timing(
    session, log: Callable[[str, str], object]
) -> VoiceLatencyObserver:
    """Install once per session; log(action, message) receives safe timing lines.

    These passive listeners do not change the session, the legacy global
    TRACKER, or its transcript boundary. SDK event/field drift and logging
    errors are contained so telemetry cannot break a spoken exchange.
    """
    observer = VoiceLatencyObserver()

    def emit(line: str | None) -> None:
        if line is not None:
            log("latency", line)

    def on_metrics(event) -> None:
        with contextlib.suppress(Exception):
            emit(format_sdk_metrics(getattr(event, "metrics", None)))

    def on_item(event) -> None:
        with contextlib.suppress(Exception):
            item = getattr(event, "item", None)
            emit(
                format_message_metrics(
                    getattr(item, "role", ""), getattr(item, "metrics", None)
                )
            )

    def on_user_state(event) -> None:
        with contextlib.suppress(Exception):
            emit(observer.user_state_changed(getattr(event, "new_state", "")))

    def on_agent_state(event) -> None:
        with contextlib.suppress(Exception):
            emit(observer.agent_state_changed(getattr(event, "new_state", "")))

    for name, callback in (
        ("metrics_collected", on_metrics),
        ("conversation_item_added", on_item),
        ("user_state_changed", on_user_state),
        ("agent_state_changed", on_agent_state),
    ):
        with contextlib.suppress(Exception):
            session.on(name, callback)
    return observer


@dataclass
class TurnSummary:
    total_s: float = 0.0
    calls: list[tuple[str, float]] = field(default_factory=list)
    phases: list[tuple[str, float]] = field(default_factory=list)

    @property
    def tool_s(self) -> float:
        return sum(ms for _, ms in self.calls) / 1000.0

    @property
    def culprit(self) -> tuple[str, float] | None:
        if not self.calls:
            return None
        name, ms = max(self.calls, key=lambda c: c[1])
        return (name, ms / 1000.0)

    def phase_gap(self, first: str, second: str) -> float | None:
        """Seconds between two phase marks (e.g. think time). Pure."""
        t0 = next((t for p, t in self.phases if p == first), None)
        t1 = next((t for p, t in self.phases if p == second), None)
        if t0 is None or t1 is None:
            return None
        return max(0.0, t1 - t0)


class LatencyTracker:
    """In-memory turn registry. One global instance (see TRACKER)."""

    def __init__(self) -> None:
        self._turn_start: float | None = None
        self._calls: list[tuple[str, float]] = []
        self._phases: list[tuple[str, float]] = []
        self.pending: int = 0
        self.last_tool_end: float | None = None
        self.last: TurnSummary = TurnSummary()

    def call_started(self) -> None:
        with contextlib.suppress(Exception):
            self.pending += 1

    def call_finished(self, tool: str, elapsed_ms: float) -> None:
        with contextlib.suppress(Exception):
            self.pending = max(0, self.pending - 1)
            self.last_tool_end = time.monotonic()
            self.record_tool_call(tool, elapsed_ms)

    def turn_begin(self, now: float | None = None) -> None:
        self._turn_start = now if now is not None else time.monotonic()
        self._calls = []
        self._phases = [("heard", self._turn_start)]

    def mark(self, phase: str, now: float | None = None) -> None:
        """Phase stamp inside a turn: thinking (agent state), speaking
        (first audio), tools_done. Ignored outside a turn."""
        if self._turn_start is None:
            return
        with contextlib.suppress(Exception):
            self._phases.append(
                (str(phase), now if now is not None else time.monotonic())
            )

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
        base = self._turn_start
        phases = [(p, t - base) for p, t in self._phases] if base is not None else []
        self.last = TurnSummary(total_s=total, calls=list(self._calls), phases=phases)
        return self.last


TRACKER = LatencyTracker()


def wait_s(summary: TurnSummary) -> float:
    """What Sir actually waited: until first audio when known. Pure.

    total_s runs until the reply is committed, which on Gemini Live is
    after the whole answer has played, so a long answer that started in
    4 s looked like a 15 s stall and got a spoken timing report.
    """
    return next((t for p, t in summary.phases if p == "speaking"), summary.total_s)


def should_announce(summary: TurnSummary) -> bool:
    """Only real stalls with an attributable tool cause get spoken."""
    return wait_s(summary) >= SPEAK_THRESHOLD_S and bool(summary.calls)


def format_breakdown(summary: TurnSummary) -> str:
    """One-line log/caption form. Pure."""
    think = summary.phase_gap("heard", "thinking")
    voice = summary.phase_gap("thinking", "speaking")
    staged = ""
    if think is not None or voice is not None:
        staged = (f" [think {think:.1f}s" if think is not None else " [think ?") + (
            f" speak {voice:.1f}s]" if voice is not None else "]"
        )
    prefix = f"turn {summary.total_s:.1f}s{staged} origin=final_transcript"
    if not summary.calls:
        return f"{prefix}, no measured tool calls (remaining stages unattributed)"
    bits = ", ".join(f"{name} {ms / 1000.0:.1f}s" for name, ms in summary.calls)
    return f"{prefix}: {bits}"


def is_quota_error(exc: BaseException) -> bool:
    """True when the failure chain shows rate limiting / exhausted quota.

    Shared by tests and the live graceful-degrade hook. Only quota
    evidence matches — deterministic errors raise through immediately.
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        text = f"{type(exc).__name__} {exc}".lower()
        if (
            "429" in text
            or "quota exceeded" in text
            or "resource_exhausted" in text
            or "rate limited" in text
            or "rate_limit" in text
        ):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def format_spoken(summary: TurnSummary) -> str:
    """Butler-voice explanation naming the exact cause. Pure."""
    if not summary.calls:
        return (
            f"The recorded turn took {summary.total_s:.0f} seconds, Sir, with "
            "no measured tool calls. These timings do not identify which "
            "other stage caused the delay."
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
