"""Talk-over gate: keep room noise and echo from interrupting Jarvis.

While Jarvis speaks, Gemini hears the mic and treats any voice as Sir
interrupting. In a canteen that voice is usually someone else, or Jarvis's
own reply leaking past the echo canceller, so replies get cut off.

The wake client plays Jarvis's audio itself, so it knows exactly when and
how loud he is. During playback (plus a short tail) mic blocks are ducked
by DUCK_DB unless they clear BOTH the room's noise floor and the learned
echo level by MARGIN_DB. Sir leaning in and saying "stop" passes; the
next table's chatter and Jarvis's own voice do not. Outside playback
every block passes untouched.

Pure and allocation-light: a few dB trackers, no model work.
JARVIS_TALK_GATE=off disables.
"""

from __future__ import annotations

import math
import os

import numpy as np

DUCK_DB = 30.0
MARGIN_DB = 10.0
TAIL_S = 0.35  # room reverb + playback latency after the last loud frame
HANGOVER_S = 0.3  # once open, keep word endings
PLAYBACK_MIN_RMS = 150.0
FLOOR_RISE_DB_S = 3.0
FLOOR_FALL_TAU_S = 0.5
#: Echo path starts pessimistic (echo as loud as playback); learning only
#: ever lowers it toward the real coupling (or raises it slowly).
ECHO_PATH_START_DB = 0.0
ECHO_RISE_DB_S = 1.0
ECHO_FALL_TAU_S = 1.0


def gate_enabled() -> bool:
    """JARVIS_TALK_GATE (default on). Pure."""
    return os.environ.get("JARVIS_TALK_GATE", "on").strip().lower() not in (
        "0", "off", "false", "no",
    )


def _db(rms: float) -> float:
    return 20.0 * math.log10(max(rms, 1.0))


def _track(value: float, target: float, dt: float, rise_db_s: float, fall_tau: float) -> float:
    """Asymmetric tracker: rises at most rise_db_s, falls with fall_tau. Pure."""
    if target > value:
        return min(target, value + rise_db_s * dt)
    return value - (value - target) * (1.0 - math.exp(-dt / fall_tau)) if dt else value


class TalkOverGate:
    def __init__(self) -> None:
        self.floor_db = _db(200.0) - 9.0
        self.echo_path_db = ECHO_PATH_START_DB
        self._playback_db = 0.0
        self._playback_at: float | None = None
        self._open_until = 0.0
        self._last_at: float | None = None
        self._duck = 10.0 ** (-DUCK_DB / 20.0)

    def note_playback(self, rms: float, now: float) -> None:
        """Called per played frame of Jarvis's audio."""
        if rms >= PLAYBACK_MIN_RMS:
            self._playback_db = _db(rms)
            self._playback_at = now

    def jarvis_speaking(self, now: float) -> bool:
        return self._playback_at is not None and now - self._playback_at <= TAIL_S

    def process(self, block: np.ndarray, now: float) -> np.ndarray:
        """Mic int16 block in; same block or a ducked copy out."""
        if not len(block):
            return block
        dt = 0.0 if self._last_at is None else min(max(now - self._last_at, 0.0), 0.5)
        self._last_at = now
        level = _db(float(np.sqrt(np.mean(block.astype(np.float32) ** 2))))
        speaking = self.jarvis_speaking(now)
        echo_db = self._playback_db + self.echo_path_db
        if speaking:
            # Mic minus playback is lowest when only echo is present.
            self.echo_path_db = _track(
                self.echo_path_db, level - self._playback_db, dt,
                ECHO_RISE_DB_S, ECHO_FALL_TAU_S,
            )
        else:
            self.floor_db = _track(self.floor_db, level, dt, FLOOR_RISE_DB_S, FLOOR_FALL_TAU_S)
        if not speaking:
            return block
        if level >= max(self.floor_db, echo_db) + MARGIN_DB:
            self._open_until = now + HANGOVER_S
        if now <= self._open_until:
            return block
        return (block.astype(np.float32) * self._duck).astype(np.int16)
