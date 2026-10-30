"""Text output chain and transcript pacing for captions."""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import Callable


class TextOutput:
    """A link in the transcript chain: ``capture_text`` then ``flush``."""

    def __init__(
        self, *, label: str = "", next_in_chain: TextOutput | None = None
    ) -> None:
        self.label = label
        self.next_in_chain = next_in_chain

    async def capture_text(self, text: str) -> None:
        if self.next_in_chain is not None:
            await self.next_in_chain.capture_text(text)

    def flush(self) -> None:
        if self.next_in_chain is not None:
            self.next_in_chain.flush()


class VideoInput:
    """Source of video frames; the session pulls ``frames()``."""

    async def frames(self):  # pragma: no cover - interface
        raise NotImplementedError
        yield b""


class TranscriptSynchronizer:
    """Release transcript words in step with audio playout.

    Gemini sends output text ahead of the speech the listener has heard.
    Words are released at the pace the audio is actually played: after a
    turn's first audio chunk, ``elapsed_audio * words_per_second`` words are
    allowed, where words_per_second is the running estimate (text words seen
    / audio seconds seen), never faster than the audio received so far.
    ``flush`` releases everything left at end of turn.
    """

    DEFAULT_WPS = 2.7
    TICK = 0.05

    def __init__(
        self, sink: TextOutput, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.sink = sink
        self._clock = clock
        self._words: list[str] = []
        self._released = 0
        self._audio_s = 0.0
        self._started_at: float | None = None
        self._task: asyncio.Task | None = None
        self._seen_words = 0

    def push_audio(self, pcm24k: bytes) -> None:
        if self._started_at is None:
            self._started_at = self._clock()
        self._audio_s += len(pcm24k) / 2 / 24000
        self._ensure()

    def push_text(self, text: str) -> None:
        # whitespace stays attached to the preceding word so re-joined text equals the source
        tokens = re.findall(r"\S+\s*|\s+", text)
        self._words.extend(tokens)
        self._seen_words += sum(1 for t in tokens if t.strip())
        self._ensure()

    def _ensure(self) -> None:
        if self._task is None or self._task.done():
            with contextlib.suppress(RuntimeError):
                self._task = asyncio.get_running_loop().create_task(self._run())

    def _allowed(self) -> int:
        if self._started_at is None:
            return 0
        played = min(self._clock() - self._started_at, self._audio_s)
        wps = (
            (self._seen_words / self._audio_s)
            if self._audio_s > 0.5
            else self.DEFAULT_WPS
        )
        return int(played * max(wps, 0.5)) + 1

    async def _run(self) -> None:
        while self._released < len(self._words):
            target = min(len(self._words), self._allowed())
            if target > self._released:
                chunk = "".join(self._words[self._released : target])
                self._released = target
                await self.sink.capture_text(chunk)
            await asyncio.sleep(self.TICK)

    async def flush(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self._released < len(self._words):
            await self.sink.capture_text("".join(self._words[self._released :]))
        self._words.clear()
        self._released = 0
        self._audio_s = 0.0
        self._seen_words = 0
        self._started_at = None
        self.sink.flush()

    def reset(self) -> None:
        """Interrupted: drop unheard words without releasing them."""
        if self._task is not None:
            self._task.cancel()
        self._words.clear()
        self._released = 0
        self._audio_s = 0.0
        self._seen_words = 0
        self._started_at = None
        self.sink.flush()
