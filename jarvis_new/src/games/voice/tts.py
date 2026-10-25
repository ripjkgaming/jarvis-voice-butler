"""Local text-to-speech: Piper (en_GB-alan), sentence by sentence, interruptible.

A stop flag is checked between ≤100 ms chunks, so speech cuts almost at once.
Common lines are pre-synthesised and cached. Never touches Jarvis's session.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import OrderedDict
from typing import Protocol

import numpy as np

import audio_shared

CHUNK_S = 0.1

COMMON_LINES = [
    "Very good, Sir.",
    "Your move, Sir.",
    "Checkmate.",
    "Check.",
    "Game paused, Sir. Say 'resume the game' when ready.",
    "Resuming, Sir.",
    "Sorry, Sir, I didn't catch a move. Try something like 'knight to f three'.",
    "I'm afraid I can't discuss that offline, Sir. Moves and hints still work.",
    "Undone, Sir.",
    "A new game, Sir.",
    "Shall I review the game, Sir?",
]


class AudioSink(Protocol):
    def write(self, pcm: bytes) -> None: ...
    def abort(self) -> None: ...
    def close(self) -> None: ...


class SoundDeviceSink:
    """Blocking int16 mono output through sounddevice (PipeWire)."""

    def __init__(self, rate: int, device: str | int | None = None):
        import sounddevice as sd

        self._stream = sd.RawOutputStream(
            samplerate=rate, channels=1, dtype="int16", device=device
        )
        self._stream.start()

    def write(self, pcm: bytes) -> None:
        self._stream.write(pcm)

    def abort(self) -> None:
        try:
            self._stream.abort()
            self._stream.start()
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


class MemorySink:
    """Captures audio (tests, --mic-test dry runs). ``realtime`` paces writes."""

    def __init__(self, realtime: bool = False, rate: int = 22050):
        self.chunks: list[bytes] = []
        self.realtime = realtime
        self.rate = rate
        self.aborted = 0

    def write(self, pcm: bytes) -> None:
        self.chunks.append(pcm)
        if self.realtime:
            time.sleep(len(pcm) / 2 / self.rate)

    def abort(self) -> None:
        self.aborted += 1

    def close(self) -> None:
        pass

    @property
    def seconds(self) -> float:
        return sum(len(c) for c in self.chunks) / 2 / self.rate


class PiperSpeaker:
    def __init__(
        self,
        *,
        voice=None,
        sink: AudioSink | None = None,
        length_scale: float = 1.0,
        volume: float = 1.0,
        device: str | int | None = None,
    ):
        self._voice = voice
        self._sink = sink
        self._device = device
        self.length_scale = length_scale
        self.volume = volume
        self.rate = 22050
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._stop = threading.Event()
        self._gate = threading.Event()
        self._gate.set()
        self.speaking = False
        self.intervals: list[tuple[float, float]] = []
        self._current_start = 0.0
        self.out_rms = 0.0
        self.stops = 0

    # ------------------------------------------------------------- loading
    def load(self) -> None:
        if self._voice is None:
            self._voice = audio_shared.load_piper_voice(
                audio_shared.resolve_voice_path()
            )
        try:
            r = int(self._voice.config.sample_rate)
            if r > 0:
                self.rate = r
        except Exception:
            pass

    def _sink_or_open(self) -> AudioSink:
        if self._sink is None:
            self._sink = SoundDeviceSink(self.rate, self._device)
        return self._sink

    def render(self, sentence: str) -> bytes:
        key = f"{self.length_scale}|{sentence}"
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if self._voice is None:
            self.load()
        pcm = audio_shared.render_sentence_pcm(
            self._voice,
            sentence,
            length_scale=self.length_scale,
            cancelled=self._stop,
        )
        if pcm and not self._stop.is_set():
            self._cache[key] = pcm
            while len(self._cache) > 256:
                self._cache.popitem(last=False)
        return pcm

    def warm_cache(self, lines: list[str] | None = None) -> None:
        for line in lines or COMMON_LINES:
            for s in audio_shared.sentence_split(line):
                self.render(s)

    # ------------------------------------------------------------- control
    def stop(self) -> None:
        """Cut speech now: flag checked between ≤100 ms chunks, device flushed."""
        self.stops += 1
        self._stop.set()
        self._gate.set()
        if self._sink is not None:
            self._sink.abort()

    def hold(self, on: bool) -> None:
        """Pause output (barge-in ducking) until the listener decides."""
        if on:
            self._gate.clear()
        else:
            self._gate.set()

    def overlaps(self, t0: float, t1: float, margin: float = 0.12) -> bool:
        """Did we speak during [t0, t1] (or are we speaking now)?"""
        now = time.monotonic()
        spans = list(self.intervals)
        if self.speaking:
            spans.append((self._current_start, now))
        return any(a - margin <= t1 and b + margin >= t0 for a, b in spans)

    # ------------------------------------------------------------ speaking
    async def speak(self, text: str) -> bool:
        """Speak ``text``; returns False if interrupted by ``stop``."""
        sentences = audio_shared.sentence_split(text)
        if not sentences:
            return True
        self._stop.clear()
        loop = asyncio.get_running_loop()
        nxt = loop.run_in_executor(None, self.render, sentences[0])
        completed = True
        started = False
        for i in range(len(sentences)):
            pcm = await nxt
            if self._stop.is_set():
                completed = False
                break
            if i + 1 < len(sentences):
                nxt = loop.run_in_executor(None, self.render, sentences[i + 1])
            if not pcm:
                continue
            if not started:
                started = True
                self.speaking = True
                self._current_start = time.monotonic()
            ok = await loop.run_in_executor(None, self._play, pcm)
            if not ok:
                completed = False
                break
        if started:
            self.intervals.append((self._current_start, time.monotonic()))
            self.intervals = self.intervals[-20:]
        self.speaking = False
        self.out_rms = 0.0
        return completed

    def _play(self, pcm: bytes) -> bool:
        sink = self._sink_or_open()
        n = int(self.rate * CHUNK_S) * 2
        data = pcm
        if self.volume != 1.0:
            a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) * self.volume
            data = np.clip(a, -32768, 32767).astype(np.int16).tobytes()
        for i in range(0, len(data), n):
            if self._stop.is_set():
                return False
            while not self._gate.wait(0.02):
                if self._stop.is_set():
                    return False
            chunk = data[i : i + n]
            a = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            self.out_rms = float(np.sqrt(np.mean(a * a))) if a.size else 0.0
            sink.write(chunk)
        return not self._stop.is_set()

    def close(self) -> None:
        self.stop()
        if self._sink is not None:
            self._sink.close()
