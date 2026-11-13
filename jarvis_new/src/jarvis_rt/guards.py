"""Pure guards for the live voice backend.

``is_foreign_speech``: Gemini Live sometimes transcribes background noise as
Korean/Chinese/etc. Jarvis is English-only, so such a transcript is noise.
``RepeatGuard``: stops Jarvis saying the same sentence twice in a turn, or
repeating the previous turn's sentence when nobody asked.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]+")
MIN_SENTENCE_CHARS = 12


# English hologram commands the speech recogniser often writes in Devanagari
# ("एक्सप्लोर द होलोग्राम" = "explode the hologram"); these are not foreign speech.
_ROMANISED_HOLO = (
    "होलोग्राम",
    "होलोग्रैम",
    "एक्सप्लोड",
    "एक्सप्लोर",
    "डिसमिस",
    "वायरफ्रेम",
    "वायरफ्रेम",
)


def is_foreign_speech(text: str) -> bool:
    if any(w in text for w in _ROMANISED_HOLO):
        return False
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 2:
        return False
    foreign = sum(1 for c in letters if not unicodedata.name(c, "").startswith("LATIN"))
    return foreign / len(letters) > 0.5


def _norm(sentence: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", sentence.lower()).strip()


class RepeatGuard:
    def __init__(
        self, *, window: float = 120.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._window = window
        self._clock = clock
        self._text = ""
        self._seen: set[str] = set()
        self._prev: dict[str, float] = {}
        self._cur_new: list[str] = []

    def start_turn(self) -> None:
        self._text = ""
        self._seen = set()
        self._cur_new = []

    def end_turn(self) -> None:
        now = self._clock()
        self._prev = {s: t for s, t in self._prev.items() if now - t < self._window}
        for s in self._cur_new:
            self._prev[s] = now
        self._cur_new = []

    def feed(self, delta: str, user_text: str = "x") -> bool:
        """Add output transcript text; True when a completed sentence repeats."""
        before = len(_SENTENCE.findall(self._text))
        self._text += delta
        sentences = _SENTENCE.findall(self._text)[before:]
        now = self._clock()
        repeat = False
        for raw in sentences:
            s = _norm(raw)
            if len(s) < MIN_SENTENCE_CHARS:
                continue
            if s in self._seen or (
                not user_text.strip()
                and s in self._prev
                and now - self._prev[s] < self._window
            ):
                repeat = True
            else:
                self._seen.add(s)
                self._cur_new.append(s)
        return repeat
