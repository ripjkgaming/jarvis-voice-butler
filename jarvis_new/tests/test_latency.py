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


def test_no_tools_leaves_unmeasured_stages_unattributed() -> None:
    t = LatencyTracker()
    t.turn_begin(now=0.0)
    s = t.turn_end(now=6.0)
    assert s.calls == []
    assert "no measured tool calls" in format_breakdown(s)
    assert "unattributed" in format_breakdown(s)
    assert "origin=final_transcript" in format_breakdown(s)
    assert "no measured tool calls" in format_spoken(s)
    assert "model thinking" not in format_spoken(s)
    assert "all model" not in format_breakdown(s)


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


def test_long_answer_that_started_fast_is_not_a_stall():
    t = LatencyTracker()
    t.turn_begin(now=0.0)
    t.record_tool_call("quote_action", 5.0)
    t.mark("speaking", now=4.5)
    # Committed at 16 s because the answer itself was long: not a stall.
    assert not should_announce(t.turn_end(now=16.0))
    t.turn_begin(now=0.0)
    t.record_tool_call("research", 9000.0)
    t.mark("speaking", now=SPEAK_THRESHOLD_S + 2)
    assert should_announce(t.turn_end(now=SPEAK_THRESHOLD_S + 8))


def test_sdk_eou_zero_is_explicitly_ambiguous() -> None:
    from latency import format_sdk_metrics

    metric = SimpleNamespace(
        type="eou_metrics",
        end_of_utterance_delay=0.0,
        transcription_delay=0.0,
        on_user_turn_completed_delay=0.012,
        transcript="private content must not be logged",
    )
    assert format_sdk_metrics(metric) == (
        "sdk_eou end_of_utterance_s=unavailable_or_zero "
        "transcription_s=unavailable_or_zero user_turn_callback_s=0.012000"
    )


def test_sdk_realtime_labels_provider_scope_and_preserves_small_values() -> None:
    from latency import format_sdk_metrics

    assert format_sdk_metrics(
        SimpleNamespace(type="realtime_model_metrics", ttft=0.000001)
    ) == (
        "sdk_realtime provider_generation_to_first_audio_s=0.000001 "
        "scope=provider_generation_not_user_e2e"
    )
    assert (
        format_sdk_metrics(SimpleNamespace(type="realtime_model_metrics", ttft=-1))
        is None
    )


@pytest.mark.parametrize(
    "invalid", [None, True, "0.5", -0.1, float("nan"), float("inf")]
)
def test_sdk_metrics_skip_missing_or_invalid_values(invalid) -> None:
    from latency import format_sdk_metrics

    assert (
        format_sdk_metrics(SimpleNamespace(type="realtime_model_metrics", ttft=invalid))
        is None
    )


def test_sdk_metrics_never_serialize_unknown_fields() -> None:
    from latency import format_sdk_metrics

    class Metric:
        type = "llm_metrics"
        ttft = 0.75
        request_id = "private request id"
        transcript = "private content"

        def __str__(self):
            raise AssertionError("Do not serialize the metric")

    assert format_sdk_metrics(Metric()) == "sdk_llm request_to_first_token_s=0.750000"
    assert format_sdk_metrics(SimpleNamespace(type="private unknown type")) is None


def test_sdk_stt_streamed_zero_duration_is_not_a_recognition_measurement() -> None:
    from latency import format_sdk_metrics

    assert (
        format_sdk_metrics(
            SimpleNamespace(
                type="stt_metrics", streamed=True, duration=0.0, audio_duration=2.0
            )
        )
        == "sdk_stt input_audio_s=2.000000"
    )
    assert (
        format_sdk_metrics(
            SimpleNamespace(
                type="stt_metrics", streamed=False, duration=0.3, audio_duration=2.0
            )
        )
        == "sdk_stt recognize_s=0.300000 input_audio_s=2.000000"
    )
    assert (
        format_sdk_metrics(SimpleNamespace(type="tts_metrics", ttfb=0.12))
        == "sdk_tts request_to_first_audio_s=0.120000"
    )


def test_message_metrics_preserve_missing_versus_real_zero_and_ignore_content() -> None:
    from latency import format_message_metrics

    assert format_message_metrics("assistant", {}) is None
    assert (
        format_message_metrics(
            "assistant",
            {
                "playback_latency": 0.0,
                "content": "private",
                "started_speaking_at": 100.0,
            },
        )
        == "sdk_turn role=assistant output_reported_playback_s=0.000000"
    )
    assert (
        format_message_metrics(
            "user",
            {"end_of_turn_delay": 0.4, "transcription_delay": None, "e2e_latency": 8.0},
        )
        == "sdk_turn role=user end_of_turn_s=0.400000"
    )
    assert format_message_metrics("private role", {"e2e_latency": 8.0}) is None


def test_voice_observer_records_only_first_response_from_detected_end() -> None:
    from latency import VoiceLatencyObserver

    observer = VoiceLatencyObserver()
    assert observer.user_state_changed("speaking", now=10.0) is None
    assert observer.user_state_changed("speaking", now=11.0) is None
    assert observer.user_state_changed("listening", now=12.0) is None
    assert observer.user_state_changed("listening", now=13.0) is None
    assert observer.agent_state_changed("thinking", now=13.5) is None
    assert observer.agent_state_changed("speaking", now=14.0) == (
        "voice_observed user_speaking_state_s=2.000000 "
        "vad_end_to_agent_speaking_s=2.000000 scope=state_events_not_acoustic_e2e"
    )
    assert observer.agent_state_changed("speaking", now=15.0) is None
    observer.agent_state_changed("listening", now=16.0)
    assert observer.agent_state_changed("speaking", now=17.0) is None


def test_voice_observer_ignores_greeting_and_missing_endpoint() -> None:
    from latency import VoiceLatencyObserver

    observer = VoiceLatencyObserver()
    assert observer.agent_state_changed("speaking", now=1.0) is None
    assert observer.user_state_changed("listening", now=2.0) is None
    assert observer.agent_state_changed("speaking", now=3.0) is None


def test_voice_observer_flags_early_reply_without_fabricating_zero_latency() -> None:
    from latency import VoiceLatencyObserver

    observer = VoiceLatencyObserver()
    observer.user_state_changed("speaking", now=10.0)
    assert observer.agent_state_changed("speaking", now=11.0) is None
    assert observer.user_state_changed("listening", now=12.0) == (
        "voice_observed user_speaking_state_s=2.000000 "
        "agent_speaking_before_vad_end=true scope=state_events_not_acoustic_e2e"
    )
    assert observer.agent_state_changed("speaking", now=15.0) is None


def test_voice_observer_new_user_speech_discards_previous_wait() -> None:
    from latency import VoiceLatencyObserver

    observer = VoiceLatencyObserver()
    observer.user_state_changed("speaking", now=10.0)
    observer.user_state_changed("listening", now=12.0)
    observer.user_state_changed("speaking", now=15.0)
    observer.user_state_changed("listening", now=16.0)
    line = observer.agent_state_changed("speaking", now=17.0)
    assert "user_speaking_state_s=1.000000" in line
    assert "vad_end_to_agent_speaking_s=1.000000" in line


def test_voice_observer_away_discards_pending_response() -> None:
    from latency import VoiceLatencyObserver

    observer = VoiceLatencyObserver()
    observer.user_state_changed("speaking", now=10.0)
    observer.user_state_changed("listening", now=12.0)
    observer.user_state_changed("away", now=13.0)
    assert observer.agent_state_changed("speaking", now=14.0) is None
    observer.agent_state_changed("listening", now=14.5)
    observer.user_state_changed("speaking", now=15.0)
    observer.user_state_changed("listening", now=16.0)
    assert observer.agent_state_changed("speaking", now=17.0) is not None


def test_voice_observer_does_not_label_continuing_old_audio_as_a_new_reply() -> None:
    from latency import VoiceLatencyObserver

    observer = VoiceLatencyObserver()
    observer.agent_state_changed("speaking", now=1.0)
    observer.user_state_changed("speaking", now=2.0)
    observer.user_state_changed("listening", now=3.0)
    assert observer.agent_state_changed("speaking", now=4.0) is None
    observer.agent_state_changed("listening", now=5.0)
    assert "vad_end_to_agent_speaking_s=3.000000" in observer.agent_state_changed(
        "speaking", now=6.0
    )


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1.0, True, "private"])
def test_voice_observer_invalid_or_out_of_order_marks_do_not_invent_latency(
    invalid,
) -> None:
    from latency import VoiceLatencyObserver

    observer = VoiceLatencyObserver()
    observer.user_state_changed("speaking", now=10.0)
    assert observer.user_state_changed("listening", now=invalid) is None
    assert observer.user_state_changed("listening", now=9.0) is None
    assert observer.agent_state_changed("speaking", now=invalid) is None
    assert observer.agent_state_changed("speaking", now=9.0) is None
    observer.user_state_changed("listening", now=12.0)
    assert "vad_end_to_agent_speaking_s=2.000000" in observer.agent_state_changed(
        "speaking", now=14.0
    )


class TimingSession:
    def __init__(self):
        self.handlers = {}

    def on(self, event, callback):
        self.handlers[event] = callback

    def emit(self, event, payload):
        if handler := self.handlers.get(event):
            handler(payload)


def test_install_session_timing_logs_numeric_events_without_reading_content(
    monkeypatch,
):
    import latency

    clock = iter([10.0, 12.0, 14.0])
    monkeypatch.setattr(latency.time, "monotonic", lambda: next(clock))
    logs = []
    session = TimingSession()
    latency.install_session_timing(
        session, lambda kind, line: logs.append((kind, line))
    )
    assert set(session.handlers) == {
        "metrics_collected",
        "conversation_item_added",
        "user_state_changed",
        "agent_state_changed",
    }

    class PrivateItem:
        role = "assistant"

        def __init__(self):
            self.metrics = {"playback_latency": 0.01}

        @property
        def text_content(self):
            raise AssertionError("Telemetry must not read conversation content")

    session.emit(
        "metrics_collected",
        SimpleNamespace(
            metrics=SimpleNamespace(
                type="realtime_model_metrics",
                ttft=0.001,
            )
        ),
    )
    session.emit("user_state_changed", SimpleNamespace(new_state="speaking"))
    session.emit("user_state_changed", SimpleNamespace(new_state="listening"))
    session.emit("agent_state_changed", SimpleNamespace(new_state="speaking"))
    # A provider's late final transcript cannot reset the observation or
    # cause the observer to inspect/store its transcript content.
    session.emit("user_input_transcribed", SimpleNamespace(is_final=True))
    session.emit("conversation_item_added", SimpleNamespace(item=PrivateItem()))
    assert logs == [
        (
            "latency",
            "sdk_realtime provider_generation_to_first_audio_s=0.001000 "
            "scope=provider_generation_not_user_e2e",
        ),
        (
            "latency",
            "voice_observed user_speaking_state_s=2.000000 "
            "vad_end_to_agent_speaking_s=2.000000 scope=state_events_not_acoustic_e2e",
        ),
        ("latency", "sdk_turn role=assistant output_reported_playback_s=0.010000"),
    ]


def test_install_session_timing_isolates_sessions_and_swallows_telemetry_errors(
    monkeypatch,
):
    import latency

    now = [10.0]
    monkeypatch.setattr(latency.time, "monotonic", lambda: now[0])
    first, second = TimingSession(), TimingSession()
    logs = []
    latency.install_session_timing(first, lambda kind, line: logs.append(line))
    latency.install_session_timing(second, lambda kind, line: logs.append(line))
    first.emit("user_state_changed", SimpleNamespace(new_state="speaking"))
    now[0] = 12.0
    first.emit("user_state_changed", SimpleNamespace(new_state="listening"))
    now[0] = 13.0
    second.emit("agent_state_changed", SimpleNamespace(new_state="speaking"))
    assert not logs
    first.emit("agent_state_changed", SimpleNamespace(new_state="speaking"))
    assert len(logs) == 1

    def failing_log(_kind, _line):
        raise RuntimeError("log unavailable")

    failing = TimingSession()
    latency.install_session_timing(failing, failing_log)
    failing.emit(
        "metrics_collected",
        SimpleNamespace(
            metrics=SimpleNamespace(
                type="realtime_model_metrics",
                ttft=0.1,
            )
        ),
    )
    for event in failing.handlers:
        failing.emit(event, None)

    class BrokenMetric:
        @property
        def type(self):
            raise RuntimeError("bad telemetry")

    failing.emit("metrics_collected", SimpleNamespace(metrics=BrokenMetric()))
