"""Microphone capture: a shared 16 kHz mono sounddevice stream.

PipeWire lets several apps capture the same device, so this never mutes or
touches the wake client; both hear the mic at once.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable

import numpy as np

from games.voice.vad import FRAME, RATE


class MicCapture:
    """Frames of FRAME samples delivered to ``on_frame(frame, monotonic_t)``."""

    def __init__(
        self,
        on_frame: Callable[[np.ndarray, float], None],
        device: str | int | None = None,
    ):
        self.on_frame = on_frame
        self.device = device
        self._stream = None
        self._carry = np.zeros(0, dtype=np.float32)
        self.level = 0.0
        self.running = False
        self.error: str | None = None

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> bool:
        import sounddevice as sd

        self._loop = loop

        def cb(indata, frames, time_info, status):  # audio thread
            mono = (
                indata[:, 0].astype(np.float32)
                if indata.ndim > 1
                else indata.astype(np.float32)
            )
            buf = np.concatenate([self._carry, mono])
            n = buf.size // FRAME
            for i in range(n):
                fr = buf[i * FRAME : (i + 1) * FRAME]
                self.level = float(np.sqrt(np.mean(fr * fr)))
                t = time.monotonic()
                if self._loop is not None:
                    self._loop.call_soon_threadsafe(self.on_frame, fr.copy(), t)
                else:
                    self.on_frame(fr.copy(), t)
            self._carry = buf[n * FRAME :]

        try:
            self._stream = sd.InputStream(
                samplerate=RATE,
                channels=1,
                dtype="float32",
                blocksize=FRAME,
                device=self.device,
                callback=cb,
            )
            self._stream.start()
        except Exception as exc:
            self.error = str(exc)
            self.running = False
            return False
        self.running = True
        return True

    def stop(self) -> None:
        s, self._stream = self._stream, None
        self.running = False
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:
                pass


class ListInputs:
    @staticmethod
    def names() -> list[str]:
        import sounddevice as sd

        return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]


_ = threading  # keep import for future thread-safety hooks
