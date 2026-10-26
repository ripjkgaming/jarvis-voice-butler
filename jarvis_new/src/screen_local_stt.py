"""Local Whisper for screen host; cancellation owns/drains the decode thread."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from concurrent.futures import ThreadPoolExecutor

from livekit.agents import stt

from local_stt import TARGET_RATE, FasterWhisperSTT, frames_to_mono16k


class DrainingScreenSTT(FasterWhisperSTT):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._decode_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="screen-stt"
        )
        self._decodes = {}
        self._closed = False

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
                local_files_only=True,
            )
        return self._model

    async def _recognize_impl(self, buffer, **kwargs):
        if self._closed:
            raise RuntimeError("Screen STT is closed")
        audio = frames_to_mono16k(buffer)
        if audio.size < TARGET_RATE // 4:
            segments = []
        else:
            cancelled = threading.Event()
            worker = asyncio.get_running_loop().run_in_executor(
                self._decode_pool, self._transcribe, audio, "en", cancelled
            )
            self._decodes[worker] = cancelled
            try:
                segments = await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancelled.set()
                while not worker.done():
                    with contextlib.suppress(asyncio.CancelledError):
                        # Keep the retiring loop progressing even after media
                        # callbacks stop; this does not cancel the decode task.
                        await asyncio.wait({worker}, timeout=0.05)
                with contextlib.suppress(Exception):
                    worker.result()
                raise
            finally:
                self._decodes.pop(worker, None)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language="en", text=text)],
        )

    async def aclose(self):
        self._closed = True
        for cancelled in self._decodes.values():
            cancelled.set()
        workers = set(self._decodes)
        while workers:
            _, workers = await asyncio.wait(workers, timeout=0.05)
        # Decode futures are drained; join the owned pool, not the process-wide
        # asyncio executor. No decode thread survives this provider generation.
        self._decode_pool.shutdown(wait=True)
