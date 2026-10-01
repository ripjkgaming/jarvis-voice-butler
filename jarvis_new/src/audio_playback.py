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
