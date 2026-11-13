"""Async bridge between the call null-sinks and the Jarvis voice pipeline.

ears:  parec -d <sink_in>.monitor 16 kHz mono s16le -> on_audio(frame)
mouth: play(pcm24k) -> resample -> pacat -> <sink_out>

Playback is paced in <=100 ms chunks with a small lead so flush() (barge-in)
silences output within roughly one chunk. Process spawning and the clock are
injectable for tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np

log = logging.getLogger("jarvis.calls.bridge")

EARS_RATE = 16_000
TTS_RATE = 24_000
FRAME_BYTES = 1024  # 32 ms @ 16 kHz mono s16le
MAX_CHUNK_MS = 100
LEAD_MS = 60  # how far ahead of real time we write into pacat

Spawn = Callable[..., Awaitable[Any]]
AudioCb = Callable[[bytes], "Awaitable[None] | None"]


async def _default_spawn(*argv: str, stdin: bool = False):
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL if stdin else asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )


def resample_s16(pcm: bytes, src: int, dst: int, tail: float | None = None):
    """Linear resample mono s16le. Returns (pcm, last_sample) for chaining.

    Chunk boundaries are continuous when ``tail`` (previous chunk's last
    input sample) is passed back in.
    """
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32)
    if x.size == 0:
        return b"", tail
    last = float(x[-1])
    if src == dst:
        return x.astype("<i2").tobytes(), last
    if tail is not None:
        x = np.concatenate(([tail], x))
        off = 1
    else:
        off = 0
    n_out = round((x.size - off) * dst / src)
    # output sample k sits at input position k*src/dst (+off for the tail)
    pos = np.arange(n_out, dtype=np.float64) * (src / dst) + off
    y = np.interp(pos, np.arange(x.size), x)
    return np.clip(np.rint(y), -32768, 32767).astype("<i2").tobytes(), last


def level(pcm: bytes) -> float:
    """RMS level 0..1."""
    if len(pcm) < 2:
        return 0.0
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32)
    return float(min(1.0, np.sqrt(np.mean(x * x)) / 32768.0 * 4.0))


class CallBridge:
    def __init__(
        self,
        sink_in: str = "jarvis_call_in",
        sink_out: str = "jarvis_call_out",
        out_rate: int = 48_000,
        spawn: Spawn = _default_spawn,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_level: Callable[[str, float], Any] | None = None,
    ) -> None:
        self.sink_in, self.sink_out = sink_in, sink_out
        self.out_rate = out_rate
        self._spawn, self._sleep = spawn, sleep
        self._on_level = on_level
        self._on_audio: AudioCb | None = None
        self._rec = None
        self._play_proc = None
        self._tasks: list[asyncio.Task] = []
        self._q: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()
        self._gen = 0
        self._tail: float | None = None
        self.in_level = 0.0
        self.out_level = 0.0
        self.running = False

    async def start(self, on_audio: AudioCb) -> None:
        if self.running:
            return
        self._on_audio = on_audio
        self._rec = await self._spawn(
            "parec",
            "-d",
            f"{self.sink_in}.monitor",
            "--rate",
            str(EARS_RATE),
            "--channels",
            "1",
            "--format",
            "s16le",
            "--latency-msec",
            "32",
        )
        self._play_proc = await self._spawn(
            "pacat",
            "-d",
            self.sink_out,
            "--rate",
            str(self.out_rate),
            "--channels",
            "1",
            "--format",
            "s16le",
            "--latency-msec",
            "50",
            stdin=True,
        )
        self.running = True
        self._tasks = [
            asyncio.create_task(self._read_loop()),
            asyncio.create_task(self._write_loop()),
        ]

    async def _read_loop(self) -> None:
        try:
            while True:
                frame = await self._rec.stdout.readexactly(FRAME_BYTES)
                self.in_level = level(frame)
                self._emit("in", self.in_level)
                r = self._on_audio(frame) if self._on_audio else None
                if inspect.isawaitable(r):
                    await r
        except (asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        except Exception:
            log.exception("ears loop failed")

    async def _write_loop(self) -> None:
        try:
            while True:
                gen, chunk = await self._q.get()
                if gen != self._gen:
                    continue
                dur = len(chunk) / 2 / self.out_rate
                self.out_level = level(chunk)
                self._emit("out", self.out_level)
                self._play_proc.stdin.write(chunk)
                await self._play_proc.stdin.drain()
                # stay only LEAD_MS ahead of real time
                await self._sleep(max(0.0, dur - LEAD_MS / 1000))
                if self._q.empty():
                    self.out_level = 0.0
        except (asyncio.CancelledError, BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception("mouth loop failed")

    def _emit(self, which: str, v: float) -> None:
        if self._on_level:
            with contextlib.suppress(Exception):
                self._on_level(which, v)

    async def play(self, pcm24k: bytes) -> None:
        """Queue 24 kHz mono s16le audio; returns once queued."""
        pcm, self._tail = resample_s16(pcm24k, TTS_RATE, self.out_rate, self._tail)
        step = self.out_rate * MAX_CHUNK_MS // 1000 * 2
        gen = self._gen
        for i in range(0, len(pcm), step):
            self._q.put_nowait((gen, pcm[i : i + step]))

    async def flush(self) -> None:
        """Drop everything queued (barge-in). Output stops within ~1 chunk."""
        self._gen += 1
        self._tail = None
        while not self._q.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._q.get_nowait()
        self.out_level = 0.0

    @property
    def pending_ms(self) -> int:
        return self._q.qsize() * MAX_CHUNK_MS

    async def stop(self) -> None:
        if not self.running and not self._tasks:
            return
        self.running = False
        await self.flush()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks = []
        for proc in (self._rec, self._play_proc):
            if proc is None:
                continue
            with contextlib.suppress(Exception):
                if proc.stdin is not None:
                    proc.stdin.close()
            with contextlib.suppress(Exception):
                proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 2)
        self._rec = self._play_proc = None
