"""Text-level echo backstop: is this "user" transcript just Jarvis's own words?

Acoustic echo cancellation (audio_aec) removes most of the bleed; what slips
through transcribes as a fragment of what Jarvis just said. Matching final
transcripts against his last few seconds of speech lets the agent ignore
them (no call-keepalive, no caption). Pure logic, injectable clock.
"""

from __future__ import annotations

import re
import time
from collections import deque

WINDOW_S = 15.0
MIN_WORDS = 3
MIN_COVERAGE = 0.8

_WORD = re.compile(r"[a-z0-9']+")


def _words(text: str) -> list[str]:
    return _WORD.findall(str(text or "").lower())


class EchoGuard:
    def __init__(self, window_s: float = WINDOW_S) -> None:
        self.window_s = window_s
        self._said: deque[tuple[float, set[str]]] = deque(maxlen=32)

    def note_agent(self, text: str, now: float | None = None) -> None:
        words = set(_words(text))
        if words:
            self._said.append((time.monotonic() if now is None else now, words))

    def is_echo(self, transcript: str, now: float | None = None) -> bool:
        """True when 80%+ of a 3+ word transcript is vocabulary Jarvis just used."""
        heard = _words(transcript)
        if len(heard) < MIN_WORDS:
            return False
        t = time.monotonic() if now is None else now
        recent: set[str] = set()
        for at, words in self._said:
            if t - at <= self.window_s:
                recent |= words
        if not recent:
            return False
        hit = sum(1 for w in heard if w in recent)
        return hit / len(heard) >= MIN_COVERAGE
