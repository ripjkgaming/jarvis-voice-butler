"""PCM energy protects idle deadlines without controlling provider turns."""

import asyncio
import contextlib
from array import array
from types import SimpleNamespace

import pytest

import agent
import latency
import school
from jarvis_rt import AgentSession, room_io


def frame(level):
    """One 800-sample PCM16 block (50 ms @ 16 kHz) as the mic delivers it."""
    return array("h", [level, -level] * 400).tobytes()


def test_activity_meter_passes_identical_audio_and_stores_no_frames():
    now = [14.0]
    meter = agent._InputActivity(clock=lambda: now[0])
    audio = frame(1200)
    original = bytes(audio)
    assert meter.process(audio) is audio
    assert audio == original
    assert meter.holding(now[0])
    assert meter.started_at == 14
    assert not any(
        isinstance(value, (bytes, bytearray, memoryview))
        for value in vars(meter).values()
    )


def test_silence_does_not_hold_and_continuous_noise_has_hard_cap():
    now = [0.0]
    meter = agent._InputActivity(clock=lambda: now[0])
    assert meter.process(frame(0)) is not None
    assert not meter.holding(0)
    meter.process(frame(1200))
    now[0] = 59.9
    # Continuous frames (no silence) keep the same start time.
    for at in range(1, 121):
        now[0] = at / 2
        meter.process(frame(1200))
    assert not meter.holding(60)
    for at in (60.5, 61, 61.5, 62):
        now[0] = at
        meter.process(frame(0))
    now[0] = 62.1
    meter.process(frame(1200))
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
        meter.process(frame(2400))
    assert not meter.active(now[0], grace=0.1)
    now[0] += 0.05
    meter.process(frame(9000))  # ~11.5 dB over the room
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
            meter.process(frame(1200))
            await settle()
            assert not session.closed
            assert session.user_state == "listening", (
                "energy must not fake SDK speech state"
            )
        now[0] = 18
        meter.process(frame(0))
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
    meter.process(frame(1200))
    task = asyncio.create_task(
        agent._school_follow_up(
            session, clock=lambda: now[0], tick=0.001, first=15, window=15
        )
    )
    try:
        await settle()
        for at in range(1, 121):
            now[0] = at / 2
            meter.process(frame(1200))
            await settle()
        await asyncio.wait_for(task, 1)
        assert session.closed
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class _Recorder:
    """Backend stand-in: keeps every PCM block the session forwards."""

    def __init__(self):
        self.pushed = []

    def push_audio(self, pcm):
        self.pushed.append(pcm)


class _Mic:
    """Room stand-in: yields its blocks, then goes quiet."""

    def __init__(self, blocks):
        self._blocks = blocks
        self.speaker = None
        self.video = None

    async def mic_frames(self):
        for block in self._blocks:
            yield block
        await asyncio.Event().wait()

    def on_chat(self, callback):
        pass

    def on(self, event, callback):
        pass

    def send_text(self, topic, payload):
        pass

    def send_audio(self, pcm):
        pass

    def flush_playback(self):
        pass

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_room_mic_path_invokes_activity_observer_without_changing_audio():
    """Real RoomIO mic path: the observer sees each block, the session gets
    the same bytes, and the meter keeps only timestamps."""
    observed = []

    class ObservedActivity(agent._InputActivity):
        def process(self, audio):
            original = bytes(audio)
            result = super().process(audio)
            observed.append((original, result))
            return result

    meter = ObservedActivity()
    backend = _Recorder()
    session = AgentSession(backend=backend)
    audio = frame(1200)
    room = _Mic([audio])
    io = room_io.RoomIO(
        session,
        room,
        room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(noise_cancellation=meter),
            close_on_disconnect=False,
        ),
    )
    await io.start()
    try:
        for _ in range(100):
            if backend.pushed:
                break
            await settle()
        assert len(observed) == 1
        original, result = observed[0]
        assert result is not None
        assert backend.pushed == [original]
        assert backend.pushed[0] == audio
        assert meter.last_active_at is not None
        assert meter.holding(meter._clock())
    finally:
        await io.aclose()
    assert meter.enabled


@pytest.mark.asyncio
async def test_native_input_returns_to_normal_follow_up_window(
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
        now[0] = 10
        meter.process(frame(1200))
        await settle()
        now[0] = 11
        meter.process(frame(0))
        await settle()
        now[0] = 27
        await settle()
        await asyncio.wait_for(task, 1)
        assert session.closed, "native input uses the normal follow-up window"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
