"""Ordered speaker I/O without blocking mic forwarding or call control.

PortAudio's blocking write paces playback. Keep that backpressure (one
awaited frame at a time), but perform open/start/write/close on one dedicated
thread. No resampling, extra audio queue, or changes to device latency.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor


class AudioPlayback:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jarvis-speaker"
        )
        self._output = None
        self._closed = False
        self._close_task: asyncio.Task | None = None

    def _write(self, samples, sample_rate: int) -> None:
        import sounddevice as sd

        if self._output is None or self._output.samplerate != sample_rate:
            self._close_output()
            self._output = sd.OutputStream(
                samplerate=sample_rate, channels=1, dtype="int16"
            )
            self._output.start()
        self._output.write(samples)

    def _close_output(self) -> None:
        output, self._output = self._output, None
        if output is not None:
            output.close()

    async def write(self, samples, sample_rate: int) -> None:
        """Await each frame before submitting another; samples remain unchanged."""
        if self._closed:
            raise RuntimeError("speaker is closed")
        await asyncio.get_running_loop().run_in_executor(
            self._executor, self._write, samples, sample_rate
        )

    async def _close(self) -> None:
        try:
            # Queued on the SAME worker: a cancelled write may still be inside
            # PortAudio. Never close its stream concurrently with that write.
            await asyncio.get_running_loop().run_in_executor(
                self._executor, self._close_output
            )
        finally:
            self._executor.shutdown(wait=False)

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close())
        # Even repeated cancellation must leave the worker able to close.
        await asyncio.shield(self._close_task)


class FastAudioPlayback:
    """Continuous callback-based PCM output, locally muteable without a model RTT.

    The output callback always fills its block; cancellation atomically drops
    the pending PCM and returns silence on the next hardware callback. A
    bounded 250 ms queue avoids adding an unbounded audible interruption tail.
    The legacy blocking AudioPlayback above remains available as fallback.
    """

    QUEUE_SECONDS = 0.25
    CALLBACK_SECONDS = 0.01

    def __init__(self):
        import threading
        from collections import deque

        self._guard = threading.Lock()
        self._queue = deque()
        self._queued_samples = 0
        self._muted = False
        self._fade_left = 0
        self._fade_total = 1
        self._closed = False
        self._stream = None
        self._rate = None
        self._open_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jarvis-fast-speaker"
        )

    def fade_out(self, ms: float = 120.0) -> None:
        """Ramp the speaker to silence over ``ms``, then stay muted like mute().

        The ramp runs inside the hardware callback, so it is click-free and
        independent of the event loop. Resume works exactly as after mute().
        """
        with self._guard:
            if self._muted or self._fade_left:
                return
            total = max(1, int((self._rate or 24000) * max(5.0, ms) / 1000.0))
            self._fade_total = self._fade_left = total

    def mute(self) -> None:
        """Synchronous, thread-safe and independent of slow model calls."""
        with self._guard:
            self._muted = True
            self._fade_left = 0
            self._queue.clear()
            self._queued_samples = 0

    def resume(self) -> None:
        with self._guard:
            self._queue.clear()
            self._queued_samples = 0
            self._fade_left = 0
            self._muted = False

    def _callback(self, outdata, frames, _time_info, _status):
        # Never block on the network, STT, TTS, or asyncio event loop here.
        outdata.fill(0)
        with self._guard:
            if self._muted:
                return
            pos = 0
            while pos < frames and self._queue:
                data = self._queue[0]
                take = min(frames - pos, len(data))
                outdata[pos : pos + take, 0] = data[:take]
                pos += take
                self._queued_samples -= take
                if take == len(data):
                    self._queue.popleft()
                else:
                    self._queue[0] = data[take:]
            if self._fade_left:
                import numpy as np

                n = min(self._fade_left, frames)
                gain = (self._fade_left - np.arange(n)) / self._fade_total
                outdata[:n, 0] = (outdata[:n, 0] * gain).astype(np.int16)
                outdata[n:] = 0
                self._fade_left -= n
                if not self._fade_left:
                    self._muted = True
                    self._queue.clear()
                    self._queued_samples = 0

    def _open(self, rate):
        import sounddevice as sd

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
        self._rate = rate
        self._stream = sd.OutputStream(
            samplerate=rate,
            channels=1,
            dtype="int16",
            blocksize=max(1, int(rate * self.CALLBACK_SECONDS)),
            latency="low",
            callback=self._callback,
        )
        self._stream.start()

    async def write(self, samples, sample_rate: int) -> None:
        import numpy as np

        if self._closed:
            raise RuntimeError("speaker is closed")
        if not len(samples):
            return
        rate = int(sample_rate)
        if rate <= 0:
            raise ValueError("invalid sample rate")
        async with self._open_lock:
            if self._stream is None or self._rate != rate:
                await asyncio.get_running_loop().run_in_executor(
                    self._executor, self._open, rate
                )
        pending = np.asarray(samples, dtype=np.int16).reshape(-1).copy()
        cursor = 0
        limit = max(1, int(rate * self.QUEUE_SECONDS))
        while cursor < len(pending):
            if self._closed:
                raise RuntimeError("speaker is closed")
            with self._guard:
                if self._muted:
                    return
                capacity = max(0, limit - self._queued_samples)
                if capacity:
                    count = min(capacity, len(pending) - cursor)
                    self._queue.append(pending[cursor : cursor + count])
                    self._queued_samples += count
                    cursor += count
            if cursor < len(pending):
                await asyncio.sleep(self.CALLBACK_SECONDS / 2)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.mute()
        async with self._open_lock:

            def close():
                stream, self._stream = self._stream, None
                if stream is not None:
                    stream.stop()
                    stream.close()

            try:
                await asyncio.get_running_loop().run_in_executor(self._executor, close)
            finally:
                self._executor.shutdown(wait=False, cancel_futures=True)
