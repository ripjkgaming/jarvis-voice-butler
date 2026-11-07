"""Synthetic synthesis/playback verifies bounded work and safe interruption."""

import asyncio
import threading

import pytest

from games.voice.tts import PiperSpeaker


async def wait_thread(event):
    assert await asyncio.to_thread(event.wait, 1)


async def test_cancelled_speech_stops_native_worker_and_resets_state(monkeypatch):
    speaker = PiperSpeaker()
    entered, released, finished = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )

    def render(text):
        try:
            for _ in range(100):
                yield b"\0\0" * 220
        finally:
            finished.set()

    def play(pcm):
        entered.set()
        assert released.wait(2)
        return not speaker._stop.is_set()

    monkeypatch.setattr(speaker, "render_stream", render)
    monkeypatch.setattr(speaker, "_play", play)
    task = asyncio.create_task(speaker.speak("Test."))
    try:
        await wait_thread(entered)
        task.cancel()
        released.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert speaker._stop.is_set()
        assert not speaker.speaking
        assert speaker.out_rms == 0
        assert finished.is_set()
    finally:
        released.set()


async def test_synthesis_does_not_run_an_entire_reply_ahead_of_playback(monkeypatch):
    speaker = PiperSpeaker()
    entered, released = threading.Event(), threading.Event()
    produced = []

    def render(text):
        for number in range(100):
            produced.append(number)
            yield b"\0\0" * 220

    def play(pcm):
        entered.set()
        assert released.wait(2)
        return not speaker._stop.is_set()

    monkeypatch.setattr(speaker, "render_stream", render)
    monkeypatch.setattr(speaker, "_play", play)
    task = asyncio.create_task(speaker.speak("Test."))
    try:
        await wait_thread(entered)
        await asyncio.sleep(0.05)
        assert len(produced) <= 4
    finally:
        speaker.stop()
        released.set()
        await task


async def test_playback_error_leaves_speaker_idle_and_stops_producer(monkeypatch):
    speaker = PiperSpeaker()
    monkeypatch.setattr(speaker, "render_stream", lambda text: iter([b"\0\0"] * 100))

    def fail(pcm):
        raise OSError("speaker disconnected")

    monkeypatch.setattr(speaker, "_play", fail)
    with pytest.raises(OSError, match="disconnected"):
        await speaker.speak("Test.")
    assert not speaker.speaking
    assert speaker.out_rms == 0
    assert speaker._stop.is_set()


async def test_cancellation_during_native_cleanup_is_propagated(monkeypatch):
    speaker = PiperSpeaker()
    closing, release = threading.Event(), threading.Event()

    def render(text):
        try:
            yield b"\0\0"
        finally:
            closing.set()
            assert release.wait(2)

    def stop_playback(pcm):
        speaker.stop()
        return False

    monkeypatch.setattr(speaker, "render_stream", render)
    monkeypatch.setattr(speaker, "_play", stop_playback)
    task = asyncio.create_task(speaker.speak("Test."))
    try:
        await wait_thread(closing)
        # Playback has stopped, but its native synthesis worker still owns
        # the speaker. Cancellation must survive waiting for that ownership.
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not speaker.speaking
        assert speaker.out_rms == 0
    finally:
        release.set()
