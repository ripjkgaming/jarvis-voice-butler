"""Headless playback regression checks; no microphone or speech model needed."""

import asyncio
import threading
import time

import numpy as np
import pytest

from games.voice.tts import MemorySink, PiperSpeaker
from playback_gate import PlaybackGate


def test_cancel_before_synthesis_is_durable_at_playback():
    gate = PlaybackGate()
    gate.cancel("old")
    session = gate.session("old")
    writes = []
    try:
        assert not session.write(b"\x01\x00" * 200, 1000, writes.append)
        assert not writes
    finally:
        session.close()


def test_cancel_fades_and_drops_remainder_under_200ms():
    gate = PlaybackGate()
    session = gate.session("active")
    writes = []
    first = threading.Event()

    def write(data):
        writes.append(data)
        first.set()
        time.sleep(len(data) / 2 / 1000)

    pcm = np.full(1000, 1000, dtype=np.int16).tobytes()
    worker = threading.Thread(target=lambda: session.write(pcm, 1000, write))
    worker.start()
    assert first.wait(1)
    started = time.monotonic()
    gate.cancel("active")
    worker.join(1)
    session.close()
    assert not worker.is_alive()
    assert time.monotonic() - started < 0.2
    samples = np.frombuffer(b"".join(writes), dtype=np.int16)
    assert samples[-1] == 0
    assert len(samples) < 200
    assert np.all(np.diff(samples.astype(int)) <= 0)


def test_two_owners_serialize_and_cancelled_waiter_never_writes():
    gate = PlaybackGate()
    first, second = gate.session("first"), gate.session("second")
    assert first.acquire()
    written = []
    thread = threading.Thread(
        target=lambda: second.write(b"\x01\x00", 1000, written.append)
    )
    thread.start()
    gate.cancel("second")
    thread.join(1)
    first.close()
    second.close()
    assert not thread.is_alive()
    assert not written


@pytest.mark.asyncio
async def test_concurrent_piper_replies_do_not_overlap_and_queued_stop_persists():
    speaker = PiperSpeaker(voice=object(), sink=MemorySink(realtime=True, rate=1000))
    speaker.rate = 1000
    speaker.render_stream = lambda text: iter(
        [np.full(500, 2000, dtype=np.int16).tobytes()]
    )
    first = asyncio.create_task(speaker.speak("First."))
    second = asyncio.create_task(speaker.speak("Second."))
    await asyncio.sleep(0.04)
    speaker.stop()
    assert await first is False
    assert await second is False
    assert speaker._sink.seconds < 0.25
    assert speaker._sink.aborted == 0
    assert await speaker.speak("New request.") is True


@pytest.mark.asyncio
async def test_piper_cancellation_waits_for_native_writer_before_next_reply():
    sink = MemorySink(realtime=True, rate=1000)
    speaker = PiperSpeaker(voice=object(), sink=sink)
    speaker.rate = 1000
    speaker.render_stream = lambda text: iter(
        [np.full(500, 2000, dtype=np.int16).tobytes()]
    )
    task = asyncio.create_task(speaker.speak("Old request."))
    await asyncio.sleep(0.04)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    count = len(sink.chunks)
    await asyncio.sleep(0.03)
    assert len(sink.chunks) == count
    assert await speaker.speak("New request.") is True


def test_wake_speaker_lease_uses_bridge_port_and_token_file(monkeypatch, tmp_path):
    import io
    import urllib.request

    from wake_client import WakeClient

    (tmp_path / "bridge_token").write_text("test-secret\n")
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_BRIDGE_PORT", raising=False)
    monkeypatch.delenv("JARVIS_BRIDGE_TOKEN", raising=False)
    seen = []

    def open_request(request, timeout):
        seen.append(request)
        assert timeout == 1
        return io.BytesIO(b'{"ok":true}')

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    assert WakeClient._speaker_lease("claim")
    assert seen[0].full_url == "http://127.0.0.1:4317/speaker"
    assert seen[0].get_header("Authorization") == "Bearer test-secret"
    assert b'"action": "claim"' in seen[0].data


def test_wake_remote_cancel_rejects_late_frames_and_ids_are_room_scoped():
    import json

    from playback_gate import get_playback_gate
    from wake_client import WakeClient

    client = WakeClient.__new__(WakeClient)
    client._playback_scope = "test-room"
    client._agent_turn_id = 9
    client._agent_output = None
    client._handle_turn_data(
        json.dumps({"type": "jarvis_turn", "event": "cancel", "turn_id": 9}).encode()
    )
    session = get_playback_gate().session(("test-room", 9))
    other = get_playback_gate().session(("another-room", 9))
    try:
        assert session.cancelled.is_set()
        assert not other.cancelled.is_set()
    finally:
        session.close()
        other.close()
    client._handle_turn_data(
        json.dumps(
            {"type": "jarvis_turn", "event": "state", "state": "speaking", "turn_id": 8}
        ).encode()
    )
    assert client._agent_turn_id == 9
    client._handle_turn_data(
        json.dumps(
            {
                "type": "jarvis_turn",
                "event": "state",
                "state": "speaking",
                "turn_id": 10,
            }
        ).encode()
    )
    assert client._agent_turn_id == 10


@pytest.mark.asyncio
async def test_wake_mic_opens_before_model_load_and_buffers_startup(monkeypatch):
    import sys
    from types import SimpleNamespace

    from wake_client import BLOCKSIZE, WakeClient

    client = WakeClient.__new__(WakeClient)
    client._mic_lock = threading.Lock()
    client._muted = False
    client._mute_epoch = 0
    client._listen_queue_live = True
    order = []

    class FinishedError(Exception):
        pass

    class Input:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def __enter__(self):
            order.append("mic")
            self.callback(b"\x01\x00" * BLOCKSIZE, BLOCKSIZE, None, None)
            return self

        def __exit__(self, *args):
            order.append("closed")

    def load():
        order.append("model")
        raise FinishedError

    monkeypatch.setitem(
        sys.modules, "sounddevice", SimpleNamespace(RawInputStream=Input)
    )
    monkeypatch.setattr(client, "_load_wake_model", load)
    with pytest.raises(FinishedError):
        await client._listen_loop(None)
    assert order == ["mic", "model", "closed"]
    assert await client._capture_queue.get() == b"\x01\x00" * BLOCKSIZE
