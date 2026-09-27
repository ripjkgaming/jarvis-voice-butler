"""Second wake phrase: "daddy" ("wake up, daddy's home").

openWakeWord only ships "hey jarvis", and training a custom model is an
hours-long job, so this spots the word with a tiny local Whisper instead:
an energy segmenter cuts short utterances out of the wake client's 16 kHz
stream (only while no call is live), and a separate worker process (the
main venv, which has faster-whisper; the wake venv's onnxruntime stays
untouched) transcribes each one. Nothing leaves the laptop.

JARVIS_DADDY_WAKE=0 turns it off.
"""

from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import threading
from pathlib import Path

RATE = 16000
FRAME = 512  # samples per wake-client block at 16 kHz (32 ms)
MIN_SEG_S = 0.35
MAX_SEG_S = 3.0
HANG_S = 0.45  # silence that ends an utterance
COOLDOWN_S = 60.0

_DADDY = re.compile(
    r"\bdadd(?:y|ie|ies|y's|ys)\b"
    # Whisper's usual mishearings of the full line ("that is home").
    r"|\bwake up,? (?:that is|that's|dad'?s|dad is|daddy is) home\b"
    r"|\bdad'?s home\b",
    re.I,
)


def enabled() -> bool:
    return os.environ.get("JARVIS_DADDY_WAKE", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def has_daddy(text: str) -> bool:
    """The trigger word, whole-word ("daddy's home" yes, "caddy" no). Pure."""
    return bool(_DADDY.search(text or ""))


class Segmenter:
    """Energy-gated utterance cutter over int16 16 kHz frames. Pure.

    feed() returns finished utterances (int16 arrays) between MIN_SEG_S
    and MAX_SEG_S. The threshold adapts to the room: speech must sit
    `ratio` x above the running noise floor.
    """

    def __init__(self, ratio: float = 2.5, floor_min: float = 60.0) -> None:
        self.ratio = ratio
        self.floor = 300.0
        self.floor_min = floor_min
        self._buf: list = []
        self._quiet = 0
        self._speaking = False

    def feed(self, frame) -> list:
        import numpy as np

        x = np.asarray(frame, dtype=np.int16)
        if x.size == 0:
            return []
        rms = float(np.sqrt(np.mean(x.astype(np.float32) ** 2)))
        loud = rms > max(self.floor * self.ratio, self.floor_min * self.ratio)
        if not self._speaking and not loud:
            # Track the noise floor only in silence (slow, so speech doesn't drag it).
            self.floor = max(self.floor_min, 0.95 * self.floor + 0.05 * rms)
        out = []
        if loud:
            self._speaking = True
            self._quiet = 0
            self._buf.append(x)
        elif self._speaking:
            self._buf.append(x)
            self._quiet += x.size
            if self._quiet >= HANG_S * RATE:
                out += self._close()
        if self._speaking and sum(b.size for b in self._buf) >= MAX_SEG_S * RATE:
            out += self._close()
        return out

    def _close(self) -> list:
        import numpy as np

        seg = np.concatenate(self._buf) if self._buf else np.zeros(0, np.int16)
        self._buf, self._quiet, self._speaking = [], 0, False
        voiced = seg.size - int(HANG_S * RATE)
        if voiced < MIN_SEG_S * RATE:
            return []
        return [seg]


class WhisperWorker:
    """Client for src/stt_worker.py: length-prefixed PCM in, JSON text out.

    One request at a time; transcribe() returns "" when the worker is
    down (and restarts it on the next call). Thread-safe.
    """

    def __init__(self, python: str | None = None) -> None:
        repo = Path(__file__).resolve().parent.parent
        self._argv = [python or str(repo / ".venv" / "bin" / "python"), str(repo / "src" / "stt_worker.py")]
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> subprocess.Popen | None:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        try:
            self._proc = subprocess.Popen(
                self._argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self._proc = None
        return self._proc

    def transcribe(self, pcm16) -> str:
        with self._lock:
            proc = self._ensure()
            if proc is None:
                return ""
            try:
                data = bytes(memoryview(pcm16).cast("B"))
                proc.stdin.write(struct.pack("<I", len(data)) + data)
                proc.stdin.flush()
                line = proc.stdout.readline()
                return str(json.loads(line or b"{}").get("text", ""))
            except (OSError, ValueError):
                self.close()
                return ""

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
