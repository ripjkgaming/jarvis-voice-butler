"""Ordered speaker I/O without blocking mic forwarding or call control.

PortAudio's blocking write paces playback. Keep that backpressure (one
awaited frame at a time), but perform open/start/write/close on one dedicated
thread. No resampling, extra audio queue, or changes to device latency.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor


class AudioPlayback:
    def __init__(self, turn_id=None) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jarvis-speaker"
        )
        from playback_gate import get_playback_gate

        self._gate = get_playback_gate()
        self._session = self._gate.session(turn_id)
        self._output = None
        self._closed = False
        self._close_task: asyncio.Task | None = None

    def _write(self, samples, sample_rate: int) -> None:
        import sounddevice as sd

        if self._session.cancelled.is_set():
            if self._output is not None:
                self._session.fade(sample_rate, self._write_pcm)
            return
        if not self._session._acquired and not self._session.acquire():
            return
        if self._output is None or self._output.samplerate != sample_rate:
            self._close_output()
            self._output = sd.OutputStream(
                samplerate=sample_rate, channels=1, dtype="int16"
            )
            self._output.start()
        self._session.write(samples.tobytes(), sample_rate, self._write_pcm)

    def _write_pcm(self, pcm):
        import numpy as np

        self._output.write(np.frombuffer(pcm, dtype=np.int16))

    def cancel(self, turn_id=None):
        self._gate.cancel(self._session.turn_id if turn_id is None else turn_id)

    def set_turn(self, turn_id):
        # Session changes are ordered on the device worker by _switch_turn.
        self._pending_turn = turn_id

    def _switch_turn(self, turn_id):
        if turn_id == self._session.turn_id:
            return
        if self._output is not None and self._session.cancelled.is_set():
            self._session.fade(self._output.samplerate, self._write_pcm)
        self._session.close()
        self._session = self._gate.session(turn_id)

    def _close_output(self) -> None:
        output, self._output = self._output, None
        if output is not None:
            if self._session.cancelled.is_set():
                self._session.fade(
                    output.samplerate,
                    lambda pcm: output.write(
                        __import__("numpy").frombuffer(pcm, dtype="int16")
                    ),
                )
            output.close()

    async def write(self, samples, sample_rate: int, turn_id=None) -> None:
        """Await each frame before submitting another; samples remain unchanged."""
        if self._closed:
            raise RuntimeError("speaker is closed")
        loop = asyncio.get_running_loop()
        turn_id = (
            turn_id if turn_id is not None else getattr(self, "_pending_turn", None)
        )
        if turn_id is not None:
            await loop.run_in_executor(self._executor, self._switch_turn, turn_id)
        try:
            await loop.run_in_executor(
                self._executor, self._write, samples, sample_rate
            )
        except asyncio.CancelledError:
            self.cancel()
            raise

    async def _close(self) -> None:
        try:
            # Queued on the SAME worker: a cancelled write may still be inside
            # PortAudio. Never close its stream concurrently with that write.
            await asyncio.get_running_loop().run_in_executor(
                self._executor, self._close_output
            )
        finally:
            self._session.close()
            self._executor.shutdown(wait=False)

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close())
        # Even repeated cancellation must leave the worker able to close.
        await asyncio.shield(self._close_task)
