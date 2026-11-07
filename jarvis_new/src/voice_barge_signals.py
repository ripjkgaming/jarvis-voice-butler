"""Per-room, model-authoritative barge-in synchronization protocol.

All packets contain counters only; no captured user audio/transcripts are sent.
The realtime model remains the sole authority over conversation cancellation.
"""

from __future__ import annotations

import json
import time

TOPIC = "jarvis.voice.barge.v1"


class ProviderEvents:
    def __init__(self):
        self.user_seq = 0
        self.reply_seq = 0

    def user_speaking(self):
        self.user_seq += 1
        return {"kind": "user_start", "user_seq": self.user_seq}

    def reply_created(self):
        self.reply_seq += 1
        return {
            "kind": "reply_start",
            "user_seq": self.user_seq,
            "reply_seq": self.reply_seq,
        }


def encode(event: dict) -> bytes:
    return json.dumps(event, separators=(",", ":")).encode("ascii")


def decode(data: bytes, topic: str):
    if topic != TOPIC or not isinstance(data, bytes) or len(data) > 256:
        return None
    try:
        item = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(item, dict) or item.get("kind") not in {
        "user_start",
        "reply_start",
    }:
        return None
    user_seq = item.get("user_seq")
    if type(user_seq) is not int or user_seq < 0 or user_seq > 1_000_000_000:
        return None
    if item["kind"] == "reply_start":
        reply_seq = item.get("reply_seq")
        if type(reply_seq) is not int or reply_seq <= 0 or reply_seq > 1_000_000_000:
            return None
    return item


class BargeSync:
    """Do not play an obsolete response after local microphone-triggered mute.

    Requires Gemini's user-start event followed by a new speech handle.
    We do not use silence gaps or a timeout to authorize *new* playback.
    This cannot prove individual RTC audio frames belong to that generation.
    """

    def __init__(self):
        self.user_seq = 0
        self.reply_seq = 0
        self.last_user_at = -1e9
        self.pending = False
        self.required_user_seq = 0
        self.confirmed_user_seq = 0
        self.baseline_reply_seq = 0
        self.ready = False

    def interrupt(self, now: float | None = None):
        t = time.monotonic() if now is None else now
        self.pending = True
        self.ready = False
        self.baseline_reply_seq = self.reply_seq
        # Provider speech-start may arrive up to 500 ms before the local
        # detector sees its second 32 ms frame; accept the recent signal.
        self.required_user_seq = (
            self.user_seq if t - self.last_user_at <= 0.5 else self.user_seq + 1
        )
        self.confirmed_user_seq = self.user_seq if t - self.last_user_at <= 0.5 else 0

    def ingest(self, data: dict, now: float | None = None) -> bool:
        t = time.monotonic() if now is None else now
        if data["kind"] == "user_start":
            seq = data["user_seq"]
            if seq > self.user_seq:
                self.user_seq = seq
                self.last_user_at = t
            if self.pending and seq >= self.required_user_seq:
                self.confirmed_user_seq = seq
        elif data["kind"] == "reply_start":
            # Reply packets also state the model's current user-turn counter.
            # A late join can miss earlier user_start messages, but must not
            # mistake a matching old user ID for a fresh interruption.
            self.user_seq = max(self.user_seq, data["user_seq"])
            seq = data["reply_seq"]
            if seq > self.reply_seq:
                self.reply_seq = seq
                if (
                    self.pending
                    and self.confirmed_user_seq >= self.required_user_seq
                    and data["user_seq"] >= self.confirmed_user_seq
                    and seq > self.baseline_reply_seq
                ):
                    self.ready = True
        return self.ready

    def complete(self):
        self.pending = False
        self.ready = False
        self.confirmed_user_seq = 0
