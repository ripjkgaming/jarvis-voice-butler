"""Local microphone-first barge-in with bounded onset pre-roll.

This module never pauses the live microphone or sends model cancellation.
Gemini Live remains the authority for conversation interruption. The local
detector only mutes the speaker sooner and preserves mic onset while deciding.
"""

from __future__ import annotations

import math
import os
from collections import deque

import numpy as np

PRE_ROLL_FRAMES = 3  # 96 ms at the wake client's normal 32 ms mic frame
MIN_LOUD_FRAMES = 2


def enabled() -> bool:
    # Stay opt-in until a live microphone/echo/playout validation is completed.
    return os.environ.get("JARVIS_FAST_BARGE_IN", "0").strip().lower() not in {
        "0",
        "off",
        "false",
        "no",
    }


class MicBargeIn:
    """Gate output normally; hold up to 3 blocks to preserve earliest syllables.

    All input blocks are transmitted in their original order, exactly once.
    A *confirmed* foreground voice receives original PCM, including the
    pending onset frames. Non-foreground audio retains the existing talk-gate
    attenuation. No Whisper/STT/network request is on this hot path.
    """

    def __init__(self, gate, on_interrupt=None, *, pre_roll=PRE_ROLL_FRAMES):
        self.gate = gate
        self.on_interrupt = on_interrupt
        self.pre_roll = max(0, int(pre_roll))
        self.pending = deque()
        self.loud_frames = 0
        self.interrupted_at = None
        self.last_loud_at = None
        self._last_loud_candidate_at = None

    def feed(self, raw: bytes, now: float) -> list[bytes]:
        gate = self.gate
        if not raw:
            return []
        if gate is None:
            return [raw]
        speaking = gate.jarvis_speaking(now)
        samples = np.frombuffer(raw, dtype=np.int16)
        processed = gate.process(samples, now)
        result = raw if processed is samples else processed.tobytes()
        if self.interrupted_at is not None:
            # The input NEVER stops while output cancellation is in progress.
            self.note_user_audio(raw, now)
            return [raw]
        if not speaking:
            self.loud_frames = 0
            ready = [data for _, data in self.pending]
            self.pending.clear()
            ready.append(result)
            return ready
        rms = math.sqrt(float(np.mean(samples.astype(np.float32) ** 2)))
        level = 20 * math.log10(max(rms, 1.0))
        # Use the already-established talk gate's noise and echo estimates.
        from talk_gate import MARGIN_DB

        # The existing gate deliberately starts with an extremely conservative
        # echo estimate. Don't inherit its multi-second warm-up for barge-in:
        # cap that estimate at +10.5 dB above the learned ambient floor and
        # require consecutive strong blocks. PipeWire AEC still runs first.
        # False-positive trade-off must be characterized on real room audio.
        capped_echo = min(
            gate._playback_db + gate.echo_path_db + MARGIN_DB, gate.floor_db + 10.5
        )
        foreground = level >= max(gate.floor_db + MARGIN_DB, capped_echo)
        self.loud_frames = self.loud_frames + 1 if foreground else 0
        self.pending.append((raw, result))
        if self.loud_frames >= MIN_LOUD_FRAMES:
            self.interrupted_at = now
            self.last_loud_at = now
            ready = [original for original, _ in self.pending]
            self.pending.clear()
            if self.on_interrupt is not None:
                self.on_interrupt(now)
            return ready
        ready = []
        while len(self.pending) > self.pre_roll:
            _, gated = self.pending.popleft()
            ready.append(gated)
        return ready

    def note_user_audio(self, raw: bytes, now: float) -> None:
        """Track activity after muting without ever modifying input PCM."""
        if self.interrupted_at is None:
            return
        samples = np.frombuffer(raw, dtype=np.int16)
        if not samples.size:
            return
        rms = math.sqrt(float(np.mean(samples.astype(np.float32) ** 2)))
        gate = self.gate
        floor = 200.0 if gate is None else 10 ** (gate.floor_db / 20.0)
        if rms >= max(500.0, floor * 2.5):
            self.last_loud_at = now

    def resume(self) -> None:
        self.interrupted_at = None
        self.last_loud_at = None
        self.loud_frames = 0
        self.pending.clear()
