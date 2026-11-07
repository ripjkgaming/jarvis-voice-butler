"""Headless playback regression checks; no microphone or speech model needed."""

import asyncio
import threading
import time

import numpy as np

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
