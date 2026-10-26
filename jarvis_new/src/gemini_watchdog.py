"""Reconnect a Gemini Live session that has gone deaf.

Seen 2 Oct 20:38-20:40: after a handoff reconnect, Gemini sent nothing
for 108 s (no speech events, no transcript, no error) while Sir kept
talking, then flushed everything at once and re-ran a finished tool.

The watchdog wraps each RealtimeSession the google plugin hands out:
  - model output or a tool call stamps "answered by the server";
  - mic frames pushed to Gemini are split into utterances (plain RMS
    gate; the wake client already denoises).
When a completed utterance has gone unanswered for SILENT_S, or Sir
has finished UTTERANCES utterances that Gemini never answered,
the first at least SILENT_S ago, the session is marked for restart, the same path the plugin
uses for its own errors (it resumes with the session handle). At most one
forced restart per COOLDOWN_S, so a noisy room cannot loop it.

JARVIS_GEMINI_WATCHDOG=0 disables it. Never raises into the audio path.
"""

from __future__ import annotations

import logging
import os
import time

log = logging.getLogger("jarvis.gemini_watchdog")


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
#: int16 RMS above which a frame counts as speech (~ -38 dBFS).
VOICE_RMS = 400.0

_SERVER_HANDLERS = (
    "_handle_server_content",
    "_handle_tool_calls",
)


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


def _guard(session) -> None:
    clock = StallClock()

    for name in _SERVER_HANDLERS:
        original = getattr(session, name, None)
        if original is None:
            continue

        def stamped(*args, _orig=original, _name=name, **kwargs):
            # Input transcripts and usage/keepalive traffic prove transport
            # liveness, not an answer. They must not mask a deaf model.
            content = args[0] if args else kwargs.get("server_content")
            answered = _name == "_handle_tool_calls" or any(
                getattr(content, field, None)
                for field in ("model_turn", "output_transcription")
            )
            if answered:
                clock.server(time.monotonic())
            return _orig(*args, **kwargs)

        setattr(session, name, stamped)

    push = session.push_audio

    def push_audio(frame) -> None:
        try:
            seconds = frame.samples_per_channel / max(1, frame.sample_rate)
            if clock.mic(seconds, frame_rms(frame.data), time.monotonic()):
                log.warning(
                    "Gemini silent %.0fs after Sir began speaking; forcing reconnect",
                    SILENT_S,
                )
                with _suppress():
                    from system import log_action

                    log_action("gemini-watchdog", "stalled session, reconnecting")
                session._mark_restart_needed(on_error=True)
        except Exception:
            pass
        push(frame)

    session.push_audio = push_audio


def _suppress():
    import contextlib

    return contextlib.suppress(Exception)


def install(model):
    """Wrap `model.session()` so every session it opens is guarded."""
    if not enabled():
        return model
    make = model.session

    def session(*args, **kwargs):
        sess = make(*args, **kwargs)
        with _suppress():
            _guard(sess)
        return sess

    model.session = session
    return model
