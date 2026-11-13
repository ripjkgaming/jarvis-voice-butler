"""Passive voice commands: mute, hide, hide all, stop. No wake word, no call.

While Jarvis is idle the wake listener already hears the mic. This slices out
short utterances (<= 3 s) with a cheap energy gate, transcribes them locally
(Whisper worker, nothing leaves the laptop), and hands the text to
``intent.hud_commands``: strict grammar first, then local Needle 3 through the
bridge (``POST /hud/parse``) for messy phrasing. Long speech is never sent
to Whisper, so ordinary conversation costs nothing.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import numpy as np

MIN_S = 0.25
MAX_S = 3.0
HANG_S = 0.6
PREROLL_S = 0.2
RATE = 16000
MIN_RMS = 350.0
FLOOR_RATIO = 2.5


class UtteranceSlicer:
    """Energy gate over 16 kHz int16 blocks -> short utterances (int16 arrays)."""

    def __init__(self) -> None:
        self.floor = 150.0
        self._active: list[np.ndarray] = []
        self._pre: list[np.ndarray] = []
        self._quiet = 0.0
        self._len = 0.0
        self._overlong = False

    def reset(self) -> None:
        self._active, self._pre = [], []
        self._quiet = self._len = 0.0
        self._overlong = False

    def feed(self, block: np.ndarray) -> np.ndarray | None:
        dur = len(block) / RATE
        rms = (
            float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
            if len(block)
            else 0.0
        )
        loud = rms > max(MIN_RMS, self.floor * FLOOR_RATIO)
        if not self._active and not loud:
            self.floor += (rms - self.floor) * 0.05
            self._pre.append(block)
            del self._pre[: max(0, len(self._pre) - int(PREROLL_S / dur))]
            return None
        if not self._active:
            self._active = list(self._pre)
            self._pre = []
        self._active.append(block)
        self._len += dur
        self._quiet = 0.0 if loud else self._quiet + dur
        if self._len > MAX_S:
            self._overlong = True  # conversation, not a command: drop at the end
        if self._quiet < HANG_S:
            return None
        pcm = np.concatenate(self._active)
        voiced = self._len - self._quiet
        drop = self._overlong or voiced < MIN_S
        self.reset()
        return None if drop else pcm


def _bridge_token() -> str:
    token = os.environ.get("JARVIS_BRIDGE_TOKEN", "").strip()
    if not token:
        home = Path(os.environ.get("JARVIS_HOME", "") or Path.home() / ".jarvis")
        try:
            token = (home / "bridge_token").read_text().strip()
        except OSError:
            token = ""
    return token


def needle_parse(text: str) -> str | None:
    """Bridge-side grammar + Needle 3 (blocking, <= 20 s). None on any failure."""
    headers = {"Content-Type": "application/json"}
    token = _bridge_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    port = os.environ.get("JARVIS_BRIDGE_PORT", "4317")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/hud/parse",
        data=json.dumps({"text": text}).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            cmd = json.load(resp).get("command")
        return cmd if isinstance(cmd, str) else None
    except (OSError, ValueError):
        return None


_CANON = {
    "mute": "mute",
    "hide": "hide",
    "hide all": "hide_all",
    "hideall": "hide_all",
    "stop": "stop",
}
FUZZY_CUTOFF = 0.74


def fuzzy_word(t: str) -> str | None:
    """Whisper mangles bare one-word commands ("mute" -> "Nute", "stop" ->
    "Top", "hide all" -> "Hidal"): match 1-2 word transcripts against the
    command words with a similarity cutoff. Pure."""
    import difflib

    if not t or len(t.split()) > 2:
        return None
    best = difflib.get_close_matches(t, list(_CANON), n=1, cutoff=FUZZY_CUTOFF)
    return _CANON[best[0]] if best else None


def resolve(text: str, parse_remote=needle_parse) -> str | None:
    """Transcript -> one of the four commands (plus unmute), or None."""
    from intent import hud_commands as hc

    cmd = hc.parse(text)
    if cmd is not None:
        return cmd
    cmd = fuzzy_word(hc.clean(text))
    if cmd is not None:
        return cmd
    t = hc.clean(text)
    if t and len(t.split()) <= hc.MAX_WORDS and hc._NEEDLE_GATE.search(t):
        return parse_remote(text)
    return None
