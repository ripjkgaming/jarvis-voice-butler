"""Probe regressions: fake RTC/audio/credentials only, never a live model call."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest


@pytest.fixture
def probe():
    path = Path(__file__).resolve().parents[1] / "scripts" / "voice_latency.py"
    spec = importlib.util.spec_from_file_location("voice_latency_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_clock(probe, monkeypatch):
    clock = SimpleNamespace(now=100.0)
    real_sleep = asyncio.sleep

    async def sleep(delay):
        clock.now += delay
        await real_sleep(0)

    monkeypatch.setattr(probe, "time", SimpleNamespace(perf_counter=lambda: clock.now))
    monkeypatch.setattr(
        probe, "asyncio", SimpleNamespace(**(vars(asyncio) | {"sleep": sleep}))
    )
    return clock


async def test_speak_timestamps_playout_end_and_preserves_pcm(probe, monkeypatch):
    clock = fake_clock(probe, monkeypatch)
    frames = []

    async def drain():
        clock.now += 0.8

    source = SimpleNamespace(
        capture_frame=AsyncMock(side_effect=lambda frame: frames.append(frame)),
        wait_for_playout=AsyncMock(side_effect=drain),
    )
    pcm = np.arange(15, dtype=np.int16)
    end = await probe.speak(source, pcm, 1000)
    assert end == pytest.approx(100.8)
    source.wait_for_playout.assert_awaited_once()
    assert (
        b"".join(bytes(frame.data) for frame in frames) == np.pad(pcm, (0, 5)).tobytes()
    )


async def test_reply_during_sender_drain_is_retained_as_signed_overlap(
    probe, monkeypatch
):
    clock = fake_clock(probe, monkeypatch)
    ear = probe.AgentEar()
    ear.wait_quiet = AsyncMock()
    started = asyncio.Event()

    async def speak(source, audio, sr):
        assert ear.armed
        ear.record_voice(100.2)
        clock.now = 101.0
        return clock.now

    async def silence(source, sr, stop):
        started.set()
        await stop.wait()

    monkeypatch.setattr(probe, "speak", speak)
    monkeypatch.setattr(probe, "silence_pump", silence)
    source = SimpleNamespace(wait_for_playout=AsyncMock())
    latency = await probe.measure_reply(source, np.zeros(10, np.int16), 1000, ear)
    assert latency == pytest.approx(-0.8)
    assert not ear.armed
    source.wait_for_playout.assert_awaited_once()
    ear.wait_quiet.assert_awaited_once()


async def test_quiet_requires_observation_and_agent_done_state(probe, monkeypatch):
    clock = fake_clock(probe, monkeypatch)
    ear = probe.AgentEar()
    ear.last_voiced = 90.0
    ear.agent_state = "speaking"
    quiet = asyncio.create_task(ear.wait_quiet(0.3, 2.0))
    await asyncio.sleep(0)
    assert not quiet.done()
    ear.agent_state = "listening"
    await quiet
    assert clock.now >= 100.3


async def test_quiet_timeout_does_not_start_next_question(probe, monkeypatch):
    fake_clock(probe, monkeypatch)
    ear = probe.AgentEar()
    ear.agent_state = "thinking"
    with pytest.raises(TimeoutError):
        await ear.wait_quiet(0.3, 0.5)


async def test_listen_closes_stream_even_when_cancelled(probe, monkeypatch):
    entered = asyncio.Event()
    closed = asyncio.Event()

    class Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            entered.set()
            await asyncio.Future()

        async def aclose(self):
            closed.set()

    monkeypatch.setattr(probe.rtc, "AudioStream", lambda track: Stream())
    ear = probe.AgentEar()
    task = asyncio.create_task(ear.listen(object()))
    await entered.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert closed.is_set()


async def test_dead_receive_stream_cannot_report_false_quiet(probe, monkeypatch):
    class Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise OSError("fake receive failure")

        aclose = AsyncMock()

    monkeypatch.setattr(probe.rtc, "AudioStream", lambda track: Stream())
    ear = probe.AgentEar()
    ear.arm()
    await asyncio.gather(ear.listen(object()), return_exceptions=True)
    with pytest.raises(OSError, match="fake receive failure"):
        await ear.wait_first(0.01)
    with pytest.raises(OSError, match="fake receive failure"):
        await ear.wait_quiet(0.0, 0.01)


async def test_dead_silence_sender_is_not_silently_discarded(probe):
    async def fail():
        raise OSError("fake send failure")

    task = asyncio.create_task(fail())
    await asyncio.sleep(0)
    with pytest.raises(OSError, match="fake send failure"):
        await probe.stop_pump(task)


async def test_main_requires_live_opt_in_before_credentials_or_render(
    probe, monkeypatch
):
    creds = Mock(side_effect=AssertionError("credentials touched"))
    render = Mock(side_effect=AssertionError("Piper loaded"))
    monkeypatch.setattr(probe, "load_livekit_env", creds)
    monkeypatch.setattr(probe, "render", render)
    assert await probe.main(1) == 2
    creds.assert_not_called()
    render.assert_not_called()


@pytest.fixture
def fake_live(probe, monkeypatch):
    handlers = {}
    room = SimpleNamespace(
        on=lambda event: lambda callback: handlers.setdefault(event, callback),
        connect=AsyncMock(),
        disconnect=AsyncMock(),
        local_participant=SimpleNamespace(publish_track=AsyncMock()),
    )
    source = SimpleNamespace(clear_queue=Mock(), aclose=AsyncMock())
    ear = SimpleNamespace(
        arm=Mock(),
        disarm=Mock(),
        subscribed=asyncio.Event(),
        wait_first=AsyncMock(return_value=100.0),
        wait_quiet=AsyncMock(),
    )
    ear.subscribed.set()
    pump_closed = asyncio.Event()

    async def silence(*args):
        try:
            await asyncio.Future()
        finally:
            pump_closed.set()

    monkeypatch.setattr(
        probe,
        "load_livekit_env",
        lambda: {
            "LIVEKIT_URL": "ws://fake.invalid",
            "LIVEKIT_API_KEY": "fake",
            "LIVEKIT_API_SECRET": "fake",
        },
    )
    monkeypatch.setattr(probe, "mint_summon_token", lambda **kwargs: "fake")
    monkeypatch.setattr(probe, "render", lambda text: (np.zeros(10, np.int16), 1000))
    monkeypatch.setattr(probe, "AgentEar", lambda: ear)
    monkeypatch.setattr(probe, "silence_pump", silence)
    monkeypatch.setattr(probe.rtc, "Room", lambda: room)
    monkeypatch.setattr(probe.rtc, "AudioSource", lambda *args: source)
    monkeypatch.setattr(
        probe.rtc.LocalAudioTrack, "create_audio_track", lambda *args: object()
    )
    return SimpleNamespace(room=room, source=source, ear=ear, pump_closed=pump_closed)


async def test_delayed_startup_greeting_is_observed_before_questions(
    probe, fake_live, monkeypatch
):
    async def question(*args):
        fake_live.ear.wait_first.assert_awaited_once()
        fake_live.ear.wait_quiet.assert_awaited_once()
        return 0.75

    monkeypatch.setattr(probe, "measure_reply", question)
    assert await probe.main(1, live=True) == 0
    fake_live.source.aclose.assert_awaited_once()
    fake_live.room.disconnect.assert_awaited_once()


async def test_missing_startup_greeting_stops_before_questions(
    probe, fake_live, monkeypatch
):
    fake_live.ear.wait_first.return_value = None
    question = AsyncMock()
    monkeypatch.setattr(probe, "measure_reply", question)
    with pytest.raises(TimeoutError):
        await probe.main(1, live=True)
    question.assert_not_called()
    fake_live.source.aclose.assert_awaited_once()
    fake_live.room.disconnect.assert_awaited_once()


async def test_no_reply_stops_without_misattributing_late_audio(
    probe, fake_live, monkeypatch, capsys
):
    question = AsyncMock(return_value=None)
    monkeypatch.setattr(probe, "measure_reply", question)
    assert await probe.main(2, live=True) == 1
    question.assert_awaited_once()
    assert "missing=1" in capsys.readouterr().out


@pytest.mark.parametrize("stage", ["connect", "publish", "greeting", "question"])
async def test_failures_cleanup_room_audio_and_pump(
    probe, fake_live, monkeypatch, stage
):
    failure = RuntimeError("synthetic failure")
    if stage == "connect":
        fake_live.room.connect.side_effect = failure
    elif stage == "publish":
        fake_live.room.local_participant.publish_track.side_effect = failure
    elif stage == "greeting":
        fake_live.ear.wait_first.side_effect = failure
    else:
        monkeypatch.setattr(probe, "measure_reply", AsyncMock(side_effect=failure))
    with pytest.raises(RuntimeError, match="synthetic failure"):
        await probe.main(1, live=True)
    fake_live.room.disconnect.assert_awaited_once()
    if stage != "connect":
        fake_live.source.aclose.assert_awaited_once()
    assert not [
        task for task in asyncio.all_tasks() if task.get_name() == "probe-silence"
    ]


def test_summary_keeps_first_exchange_distinct_and_does_not_hide_missing(probe, capsys):
    probe.print_summary([None, 1.0, 2.0, 3.0, 4.0])
    text = capsys.readouterr().out
    assert "first exchange" in text and "missing" in text
    assert "warm" in text and "p50=2.50s" in text and "p95=3.85s" in text
    assert "not independent cold runs" in text


def test_cli_requires_live_flag_without_invoking_main(probe, monkeypatch):
    main = AsyncMock()
    monkeypatch.setattr(probe, "main", main)
    with pytest.raises(SystemExit) as exc:
        probe.cli(["-n", "1"])
    assert exc.value.code == 2
    main.assert_not_called()


async def test_no_question_or_answer_content_printed(
    probe, fake_live, monkeypatch, capsys
):
    monkeypatch.setattr(probe, "QUESTIONS", ["private synthetic question"])
    monkeypatch.setattr(probe, "measure_reply", AsyncMock(return_value=-0.25))
    assert await probe.main(1, live=True) == 0
    output = capsys.readouterr().out
    assert "private" not in output
    assert "question 1" in output and "-0.25s" in output and "overlap" in output
