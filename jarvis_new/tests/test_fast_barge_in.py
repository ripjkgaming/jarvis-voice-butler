"""Offline regression tests for microphone-first barge-in.

All PCM fidelity tests operate on exact captured samples, not STT estimates.
No microphones, speakers, network, Gemini account, or running Jarvis touched.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from audio_playback import FastAudioPlayback
from fast_barge_in import MicBargeIn
from talk_gate import TalkOverGate

RATE = 48000
BLOCK = 1536  # 32 ms


def pcm(rms, seed):
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(BLOCK)
    return np.clip(x / np.std(x) * rms, -30000, 30000).astype(np.int16).tobytes()


def primed_gate():
    gate = TalkOverGate()
    for i in range(250):  # 8 seconds of school/canteen ambience
        gate.process(np.frombuffer(pcm(2400, i), dtype=np.int16), i * .032)
    return gate


def test_onset_preserved_exactly_including_initial_weak_frame():
    gate = primed_gate()
    seen = []
    engine = MicBargeIn(gate, on_interrupt=lambda at: seen.append(at), pre_roll=3)
    t = 9.0
    input_blocks = [pcm(2600, 1000), pcm(11000, 1001), pcm(11000, 1002)]
    outputs = []
    for frame in input_blocks:
        gate.note_playback(8000, t)
        outputs += engine.feed(frame, t)
        t += .032
    assert len(seen) == 1
    assert (seen[0] - 9.0) <= .065  # first loud block at 9.032, stop at 9.064
    # ALL 3 source frames including the quiet syllable before detection, byte-for-byte.
    assert b"".join(outputs) == b"".join(input_blocks)
    next_block = pcm(4500, 1003)
    outputs += engine.feed(next_block, t)
    assert outputs[-1] == next_block


def test_noise_and_one_loud_click_do_not_interrupt():
    gate = primed_gate()
    fired = []
    engine = MicBargeIn(gate, on_interrupt=fired.append)
    for i in range(100):
        t = 9.0 + i * .032
        gate.note_playback(8000, t)
        frame = pcm(12000 if i == 20 else 2600, i + 2000)
        engine.feed(frame, t)
    assert fired == []
    assert engine.interrupted_at is None


def test_mic_continues_during_backend_interruption_and_resumes_only_when_quiet():
    gate = primed_gate()
    engine = MicBargeIn(gate)
    for i in range(2):
        gate.note_playback(8000, 9.0 + i * .032)
        engine.feed(pcm(12000, 3000 + i), 9.0 + i * .032)
    assert engine.interrupted_at is not None
    for i in range(30):
        frame = pcm(12000, 4000+i)
        assert engine.feed(frame, 9.1+i*.032) == [frame]
    # Only a separate Gemini-native new-reply event can authorize unmuting.
    # Mic capture remains byte-exact throughout this entire interruption.
    engine.resume()
    assert engine.interrupted_at is None


def test_pass_through_without_gate_preserves_all_audio():
    e = MicBargeIn(None)
    frames = [pcm(3000, i) for i in range(20)]
    output = []
    for i, frame in enumerate(frames):
        output.extend(e.feed(frame, i * .032))
    assert b"".join(output) == b"".join(frames)


@pytest.mark.asyncio
async def test_fast_audio_callback_silences_next_10ms_frame_and_discards_queue(monkeypatch):
    streams = []

    class Output:
        def __init__(self, **kwargs):
            self.samplerate = kwargs["samplerate"]
            self.callback = kwargs["callback"]
            self.blocksize = kwargs["blocksize"]
            self.closed = False
            streams.append(self)

        def start(self): pass
        def stop(self): pass
        def close(self): self.closed = True

        def pull(self):
            out = np.empty((self.blocksize, 1), dtype=np.int16)
            self.callback(out, self.blocksize, None, None)
            return out[:, 0].copy()

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=Output))
    p = FastAudioPlayback()
    try:
        await p.write(np.full(4800, 3200, dtype=np.int16), 48000)
        assert streams[0].pull().tolist() == [3200] * 480
        p.mute()
        assert np.all(streams[0].pull() == 0)
        assert p._queued_samples == 0
        await p.write(np.full(480, 1111, dtype=np.int16), 48000)
        assert np.all(streams[0].pull() == 0)
        p.resume()
        await p.write(np.full(480, 2200, dtype=np.int16), 48000)
        assert streams[0].pull().tolist() == [2200] * 480
    finally:
        await p.aclose()
        assert streams[0].closed


@pytest.mark.asyncio
async def test_fast_callback_reassembles_250ms_pcm_exactly_without_seams(monkeypatch):
    streams = []

    class Output:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.blocksize = kwargs["blocksize"]
            streams.append(self)
        def start(self): pass
        def stop(self): pass
        def close(self): pass
        def pull(self):
            out = np.empty((self.blocksize, 1), dtype=np.int16)
            self.callback(out, self.blocksize, None, None)
            return out[:, 0].copy()

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=Output))
    p = FastAudioPlayback()
    try:
        source = np.arange(12000, dtype=np.int16)
        # Five 50 ms producer messages combine into one 250 ms audio span.
        for part in np.split(source, 5):
            await p.write(part, 48000)
        received = np.concatenate([streams[0].pull() for _ in range(25)])
        assert np.array_equal(received, source)
        assert p._queued_samples == 0
    finally:
        await p.aclose()
