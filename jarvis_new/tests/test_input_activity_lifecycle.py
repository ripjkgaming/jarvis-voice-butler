"""PCM energy protects idle deadlines without controlling provider turns."""

import asyncio
import contextlib
from array import array
from types import SimpleNamespace

import pytest
from livekit import rtc

import agent
import latency
import school


def frame(level):
    return rtc.AudioFrame(
        data=array("h", [level, -level] * 400).tobytes(),
        sample_rate=16000,
        num_channels=1,
        samples_per_channel=800,
    )


def test_activity_meter_passes_identical_audio_and_stores_no_frames():
    now = [14.0]
    meter = agent._InputActivity(clock=lambda: now[0])
    audio = frame(1200)
    original = bytes(audio.data)
    assert meter._process(audio) is audio
    assert bytes(audio.data) == original
    assert meter.holding(now[0])
    assert meter.started_at == 14
    assert not any(
        isinstance(value, (bytes, memoryview, rtc.AudioFrame))
        for value in vars(meter).values()
    )


def test_silence_does_not_hold_and_continuous_noise_has_hard_cap():
    now = [0.0]
    meter = agent._InputActivity(clock=lambda: now[0])
    assert meter._process(frame(0)) is not None
    assert not meter.holding(0)
    meter._process(frame(1200))
    now[0] = 59.9
    # Continuous frames (no silence) keep the same start time.
    for at in range(1, 121):
        now[0] = at / 2
        meter._process(frame(1200))
    assert not meter.holding(60)
    for at in (60.5, 61, 61.5, 62):
        now[0] = at
        meter._process(frame(0))
    now[0] = 62.1
    meter._process(frame(1200))
    assert meter.holding(62.1), "a real quiet gap permits a new bounded hold"
    meter._close()
    assert not meter.holding(62.1)


def test_steady_loud_room_stops_reading_as_speech_but_voice_on_top_does():
    """Canteen babble at a constant level is noise within seconds; a
    voice clearly above it still counts as activity."""
    now = [0.0]
    meter = agent._InputActivity(clock=lambda: now[0])
    for step in range(1, 201):  # 10 s of steady 2400 RMS room noise
        now[0] = step / 20
        meter._process(frame(2400))
    assert not meter.active(now[0], grace=0.1)
    now[0] += 0.05
    meter._process(frame(9000))  # ~11.5 dB over the room
    assert meter.active(now[0], grace=0.1)


class Session:
    user_state = "listening"
    agent_state = "listening"

    def __init__(self, meter):
        self._jarvis_input_activity = meter
        self.closed = False
        self.handlers = {}

    def on(self, name, callback):
        self.handlers.setdefault(name, []).append(callback)

    def off(self, name, callback):
        self.handlers[name].remove(callback)

    async def aclose(self):
        self.closed = True


@pytest.fixture
def quiet_services(monkeypatch):
    monkeypatch.setattr(latency, "TRACKER", SimpleNamespace(pending=0))
    monkeypatch.setattr(school, "awaiting_answer", lambda: False)
    import hud_events
    import system

    monkeypatch.setattr(hud_events, "caption", lambda *args: None)
    monkeypatch.setattr(system, "log_action", lambda *args: None)


async def settle():
    await asyncio.sleep(0.006)


@pytest.mark.asyncio
async def test_speech_at_fourteen_seconds_holds_native_call_then_resets_window(
    quiet_services,
):
    now = [0.0]
    meter = agent._InputActivity(clock=lambda: now[0])
    session = Session(meter)
    task = asyncio.create_task(
        agent._school_follow_up(
            session, clock=lambda: now[0], tick=0.001, first=15, window=15
        )
    )
    try:
        await settle()
        for at in (14, 14.5, 15, 15.5, 16, 16.5, 17):
            now[0] = at
            meter._process(frame(1200))
            await settle()
            assert not session.closed
            assert session.user_state == "listening", (
                "energy must not fake SDK speech state"
            )
        now[0] = 18
        meter._process(frame(0))
        await settle()
        now[0] = 32.9
        await settle()
        assert not session.closed
        now[0] = 33
        await asyncio.wait_for(task, 1)
        assert session.closed
        assert all(not callbacks for callbacks in session.handlers.values())
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_energy_without_provider_events_cannot_hold_call_forever(quiet_services):
    now = [0.0]
    meter = agent._InputActivity(clock=lambda: now[0])
    session = Session(meter)
    meter._process(frame(1200))
    task = asyncio.create_task(
        agent._school_follow_up(
            session, clock=lambda: now[0], tick=0.001, first=15, window=15
        )
    )
    try:
        await settle()
        for at in range(1, 121):
            now[0] = at / 2
            meter._process(frame(1200))
            await settle()
        await asyncio.wait_for(task, 1)
        assert session.closed
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_actual_rtc_stream_invokes_activity_observer_without_changing_audio():
    """Exercise the native processor hook without rooms, providers, or devices."""
    observed = []

    class ObservedActivity(agent._InputActivity):
        def _process(self, audio):
            original = bytes(audio.data)
            result = super()._process(audio)
            observed.append((audio, original, result is audio))
            return result

    meter = ObservedActivity()
    source = rtc.AudioSource(16000, 1)
    track = rtc.LocalAudioTrack.create_audio_track("offline-activity-check", source)
    stream = rtc.AudioStream.from_track(
        track=track,
        sample_rate=16000,
        num_channels=1,
        frame_size_ms=50,
        noise_cancellation=meter,
        auto_close_noise_cancellation=True,
    )
    try:
        receive = asyncio.create_task(stream.__anext__())
        audio = frame(1200)
        await source.capture_frame(audio)
        event = await asyncio.wait_for(receive, 2.0)
        assert event.frame.sample_rate == audio.sample_rate
        # The native stream can prepend startup silence while packetizing.
        # Verify byte identity across our observer, independently of that.
        delivered = next(entry for entry in observed if entry[0] is event.frame)
        assert delivered[2]
        assert bytes(event.frame.data) == delivered[1]
        assert meter.last_active_at is not None
        assert meter.holding(meter._clock())
    finally:
        await stream.aclose()
        await source.aclose()
    assert not meter.enabled
