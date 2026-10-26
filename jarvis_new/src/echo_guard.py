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
MIN_WORDS = 4
MIN_COVERAGE = 0.8
# A reply that opens with one of these is the user answering Jarvis ("yes open
# Spotify" after "shall I open Spotify"), not Jarvis's own words bleeding back.
_AFFIRM = frozenset(
    [
        "yes",
        "yeah",
        "yep",
        "yup",
        "sure",
        "ok",
        "okay",
        "please",
        "no",
        "nope",
        "go",
        "do",
        "confirm",
        "cancel",
        "stop",
    ]
)
_AFFIRM_MAX_WORDS = 8

_WORD = re.compile(r"[a-z0-9']+")


def _words(text: str) -> list[str]:
    return _WORD.findall(str(text or "").lower())


class EchoGuard:
    def __init__(self, window_s: float = WINDOW_S) -> None:
        self.window_s = window_s
        self.speaking = False
        self._said: deque[tuple[float, list[str]]] = deque(maxlen=32)

    def note_agent(self, text: str, now: float | None = None) -> None:
        words = _words(text)
        if words:
            self._said.append((time.monotonic() if now is None else now, words))

    def is_echo(self, transcript: str, now: float | None = None) -> bool:
        """True when 80%+ of a 4+ word transcript is vocabulary Jarvis just used."""
        heard = _words(transcript)
        if not heard:
            return False
        if heard[0] in _AFFIRM and len(heard) <= _AFFIRM_MAX_WORDS:
            return False
        t = time.monotonic() if now is None else now
        for at, words in self._said:
            if t - at > self.window_s:
                continue
            if len(heard) < MIN_WORDS:
                # Three-word exact fragments are useful only during active
                # playout. Short controls and confirmations remain user input.
                if (
                    self.speaking
                    and len(heard) >= 3
                    and any(
                        words[i : i + len(heard)] == heard
                        for i in range(len(words) - len(heard) + 1)
                    )
                ):
                    return True
                continue
            # Ordered matching avoids suppressing a real command composed of
            # the same vocabulary as our previous sentence.
            cursor = 0
            hits = 0
            for word in heard:
                try:
                    cursor = words.index(word, cursor) + 1
                    hits += 1
                except ValueError:
                    pass
            if hits / len(heard) >= MIN_COVERAGE:
                return True
        return False
