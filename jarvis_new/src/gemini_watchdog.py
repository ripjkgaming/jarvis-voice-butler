"""Pure stall-detection helpers for the Gemini Live client.

jarvis_rt.live.LiveBackend feeds each mic frame to StallClock and stamps it
whenever Gemini answers (model output, output transcript or tool call).
Input transcripts, usage metadata and keepalives are not answers. A completed
utterance that stays unanswered for SILENT_S, or UTTERANCES unanswered
utterances, asks the backend for a fresh connection. At most one forced
restart per COOLDOWN_S, so a noisy room cannot loop it.

JARVIS_GEMINI_WATCHDOG=0 disables it.
"""

from __future__ import annotations

import os


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


SILENT_S = _num("JARVIS_GEMINI_STALL_S", 15.0)
#: Unanswered utterances needed before a restart (Sir repeating himself).
UTTERANCES = 2
#: An utterance is at least this much speech...
UTTER_MIN_S = 1.0
#: ...ended by this much quiet...
UTTER_GAP_S = 0.8
#: ...and dense: speech fills most of its span, key clicks do not.
UTTER_DENSITY = 0.5
COOLDOWN_S = 60.0
#: Bounded time for a fresh connection and the recovery announcement.
RECOVERY_S = 10.0
#: int16 RMS above which a frame counts as speech (~ -38 dBFS).
VOICE_RMS = 400.0


def enabled() -> bool:
    return os.environ.get("JARVIS_GEMINI_WATCHDOG", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def frame_rms(data) -> float:
    """RMS of int16 PCM samples (memoryview/bytes/array). Pure."""
    import numpy as np

    pcm = np.frombuffer(data, dtype=np.int16)
    if pcm.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))


class StallClock:
    """Unanswered-utterance bookkeeping, separate from the plugin. Pure."""

    def __init__(self) -> None:
        self.forced_at = -COOLDOWN_S
        self.server(0.0)

    def server(self, now: float) -> None:
        """Gemini said something: everything before it was heard."""
        #: Start of the first unanswered utterance (None: none yet).
        self.first_at: float | None = None
        self.unanswered = 0
        self.finished_at: float | None = None
        self._utter_at: float | None = None
        self._utter_s = 0.0
        self._voice_at = 0.0

    def mic(self, seconds: float, rms: float, now: float) -> bool:
        """Record one mic frame; True when the session should restart."""
        if rms >= VOICE_RMS:
            if self._utter_at is None:
                self._utter_at = now
            self._utter_s += seconds
            self._voice_at = now
        elif self._utter_at is not None and now - self._voice_at >= UTTER_GAP_S:
            span = max(1e-6, self._voice_at - self._utter_at)
            if self._utter_s >= UTTER_MIN_S and self._utter_s >= UTTER_DENSITY * span:
                self.unanswered += 1
                self.finished_at = now
                if self.first_at is None:
                    self.first_at = self._utter_at
            self._utter_at = None
            self._utter_s = 0.0
        if (
            self.first_at is not None
            and self._utter_at is None
            and (
                (self.unanswered >= UTTERANCES and now - self.first_at >= SILENT_S)
                or (self.finished_at is not None and now - self.finished_at >= SILENT_S)
            )
            and now - self.forced_at >= COOLDOWN_S
        ):
            self.forced_at = now
            self.server(now)  # give the fresh session a full window
            return True
        return False
