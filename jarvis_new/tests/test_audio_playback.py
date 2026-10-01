"""Headless speaker tests: byte/order parity and cancellation while writing."""

import asyncio
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from audio_playback import AudioPlayback


@pytest.fixture
def speaker(monkeypatch):
    import sys

    events = []
    entered = threading.Event()
    release = threading.Event()
    release.set()

    class Output:
        def __init__(self, **kwargs):
            self.samplerate = kwargs["samplerate"]
            events.append(("open", threading.get_ident(), kwargs))

        def start(self):
            events.append(("start", threading.get_ident(), None))

        def write(self, samples):
            events.append(("write", threading.get_ident(), samples.copy()))
            entered.set()
            assert release.wait(2), "test did not release speaker"
            events.append(("written", threading.get_ident(), None))

        def close(self):
            events.append(("close", threading.get_ident(), None))

    monkeypatch.setitem(
        sys.modules, "sounddevice", SimpleNamespace(OutputStream=Output)
    )
    return SimpleNamespace(
        events=events, entered=entered, release=release, Output=Output
    )


async def test_playback_preserves_pcm_order_and_rate(speaker):
    playback = AudioPlayback()
    blocks = [
        np.array([-32768, 0, 32767], dtype=np.int16),
        np.arange(24, dtype=np.int16),
    ]
    try:
        for block in blocks:
            await playback.write(block, 24000)
        await playback.write(blocks[0], 48000)
    finally:
        await playback.aclose()
    writes = [event[2] for event in speaker.events if event[0] == "write"]
    assert all(
        np.array_equal(a, b) for a, b in zip(writes, [*blocks, blocks[0]], strict=True)
    )
    opens = [event[2] for event in speaker.events if event[0] == "open"]
    assert opens == [
        {"samplerate": rate, "channels": 1, "dtype": "int16"} for rate in (24000, 48000)
    ]
    assert [event[0] for event in speaker.events].count("close") == 2
    threads = {event[1] for event in speaker.events}
    assert len(threads) == 1 and threading.get_ident() not in threads


async def test_blocked_speaker_keeps_event_loop_responsive_and_closes_after_write(
    speaker,
):
    speaker.release.clear()
    playback = AudioPlayback()
    task = asyncio.create_task(playback.write(np.zeros(240, dtype=np.int16), 24000))
    try:
        assert await asyncio.to_thread(speaker.entered.wait, 1)
        # This callback must run even though the native audio write is blocked.
        await asyncio.wait_for(asyncio.sleep(0), 0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        close = asyncio.create_task(playback.aclose())
        await asyncio.sleep(0.02)
        assert not close.done()
        assert not any(event[0] == "close" for event in speaker.events)
        speaker.release.set()
        await asyncio.wait_for(close, 1)
        assert [event[0] for event in speaker.events][-2:] == ["written", "close"]
    finally:
        speaker.release.set()
        await playback.aclose()


async def test_empty_playback_never_opens_speaker(speaker):
    playback = AudioPlayback()
    await playback.aclose()
    await playback.aclose()
    assert speaker.events == []
    with pytest.raises(RuntimeError):
        await playback.write(np.zeros(1, dtype=np.int16), 24000)


async def test_wake_playback_preserves_school_gain_and_closes_input(
    speaker, monkeypatch
):
    from livekit import rtc

    from wake_client import WakeClient

    original = np.array([-3000, 1000, 8000], dtype=np.int16)
    closed = []

    class Stream:
        def __init__(self, track):
            pass

        async def __aiter__(self):
            yield SimpleNamespace(frame=rtc.AudioFrame(original.tobytes(), 24000, 1, 3))

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(rtc, "AudioStream", Stream)
    monkeypatch.setattr("wake_client._school_quiet", lambda: True)
    monkeypatch.setattr("wake_client._school_gain", lambda: 0.2)
    client = WakeClient.__new__(WakeClient)
    await client._play_agent(None)
    writes = [event[2] for event in speaker.events if event[0] == "write"]
    assert len(writes) == 1
    assert np.array_equal(writes[0], (original.astype("float32") * 0.2).astype("int16"))
    assert closed == [True]
    assert client._last_agent_voice > 0


@pytest.mark.parametrize("failure_stage", ["start", "write"])
async def test_wake_closes_speaker_and_input_on_device_error(
    speaker, monkeypatch, failure_stage
):
    from livekit import rtc

    from wake_client import WakeClient

    closed = []

    class Stream:
        def __init__(self, track):
            pass

        async def __aiter__(self):
            yield SimpleNamespace(frame=rtc.AudioFrame(b"\0\0" * 240, 24000, 1, 240))

        async def aclose(self):
            closed.append(True)

    def fail(*args):
        raise OSError("fake speaker unavailable")

    monkeypatch.setattr(speaker.Output, failure_stage, fail)
    monkeypatch.setattr(rtc, "AudioStream", Stream)
    monkeypatch.setattr("wake_client._school_quiet", lambda: False)
    with pytest.raises(OSError, match="fake speaker unavailable"):
        await WakeClient.__new__(WakeClient)._play_agent(None)
    assert closed == [True]
    assert [event[0] for event in speaker.events][-1] == "close"


async def test_repeated_cancel_still_closes_rtc_stream(speaker, monkeypatch):
    from livekit import rtc

    from wake_client import WakeClient

    speaker.release.clear()
    closed = []

    class Stream:
        def __init__(self, track):
            pass

        async def __aiter__(self):
            yield SimpleNamespace(frame=rtc.AudioFrame(b"\0\0" * 240, 24000, 1, 240))

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(rtc, "AudioStream", Stream)
    monkeypatch.setattr("wake_client._school_quiet", lambda: False)
    task = asyncio.create_task(WakeClient.__new__(WakeClient)._play_agent(None))
    try:
        assert await asyncio.to_thread(speaker.entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0.02)  # now awaiting ordered speaker close
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == [True]
    finally:
        speaker.release.set()
        await asyncio.sleep(0.03)  # protected worker close finishes
