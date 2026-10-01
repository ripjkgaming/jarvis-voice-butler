"""Out-of-call voice for background features (focus drift, posture, gestures).

Background watchers have no LiveKit session to speak through, so they call
speak(): local Piper TTS (free, offline) played with paplay, mirrored to the
HUD caption line and a desktop notification.

Stays polite on its own:
- muted mic / school mode / a live call -> notification only (never talk
  over the agent or out loud in class),
- per-source rate limit (min_gap_s) so a flapping watcher can't nag.

Never raises; synthesis + playback run on a daemon thread.
"""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import threading
import time
import wave
from pathlib import Path

DEFAULT_GAP_S = 20.0

_last_said: dict[str, float] = {}
_lock = threading.Lock()
_voice = {"obj": None}


def _jarvis_home() -> Path:
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def wake_status(timeout: float = 1.0) -> dict:
    """{muted, in_call} from the wake client's control socket; {} if down."""
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(timeout)
            sock.connect(str(_jarvis_home() / "wake.sock"))
            sock.sendall(b'{"status": true}\n')
            return json.loads(sock.recv(4096).decode() or "{}")
    except (OSError, ValueError):
        return {}


def allowed(source: str, now: float, min_gap_s: float) -> bool:
    """Rate limit per source. Pure except for the shared timestamp map."""
    with _lock:
        if now - _last_said.get(source, 0.0) < min_gap_s:
            return False
        _last_said[source] = now
        return True


def announce_call_enabled() -> bool:
    """Announce through a silently opened call? JARVIS_ANNOUNCE_CALL=0 disables."""
    return os.environ.get("JARVIS_ANNOUNCE_CALL", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def announce_via_call(text: str, timeout: float = 2.0) -> bool:
    """Ask the wake client to open a silent call that opens with `text`.

    True when it accepted (Jarvis will say it inside the call, so Sir's reply
    is heard). False on any refusal or when the wake client is down; callers
    then speak it locally.
    """
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(timeout)
            sock.connect(str(_jarvis_home() / "wake.sock"))
            sock.sendall(json.dumps({"announce": text[:300]}).encode() + b"\n")
            reply = json.loads(sock.recv(4096).decode() or "{}")
        return bool(reply.get("ok"))
    except (OSError, ValueError):
        return False


def should_voice(status: dict, school_mode: bool) -> bool:
    """Speak aloud, or notification only? Pure."""
    return not (status.get("muted") or status.get("in_call") or school_mode)


def _notify(text: str, title: str) -> None:
    try:
        subprocess.run(
            ["notify-send", "-a", "Jarvis", "-t", "7000", title, text[:300]],
            timeout=5,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _synth_wav(text: str) -> bytes:
    from local_voice import resolve_voice_path
    from piper import PiperVoice

    if _voice["obj"] is None:
        _voice["obj"] = PiperVoice.load(str(resolve_voice_path()))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        _voice["obj"].synthesize_wav(text, wav)
    return buf.getvalue()


def _play(wav: bytes) -> None:
    subprocess.run(["paplay"], input=wav, timeout=60, capture_output=True)


def _run(text: str, title: str, voice: bool) -> None:
    try:
        from hud_events import caption

        caption("jarvis", text)
    except Exception:
        pass
    _notify(text, title)
    if not voice:
        return
    try:
        _play(_synth_wav(text))
    except Exception:
        pass


def speak(
    text: str,
    *,
    source: str = "jarvis",
    min_gap_s: float = DEFAULT_GAP_S,
    title: str = "Jarvis",
    force_quiet: bool = False,
) -> bool:
    """Say one line out of call. Returns False when rate-limited or empty."""
    text = " ".join(str(text or "").split())
    if not text or not allowed(source, time.time(), min_gap_s):
        return False
    try:
        import school

        school_mode = school.is_school()
    except Exception:
        school_mode = False
    voice = not force_quiet and should_voice(wake_status(), school_mode)
    if voice and announce_call_enabled() and announce_via_call(text):
        # Spoken inside the call (it captions itself); the toast is the
        # visible receipt in case the first words are missed.
        threading.Thread(
            target=_notify, args=(text, title), name=f"toast-{source}", daemon=True
        ).start()
        return True
    threading.Thread(
        target=_run, args=(text, title, voice), name=f"speak-{source}", daemon=True
    ).start()
    return True


def speak_local(text: str, title: str = "Jarvis") -> None:
    """Say one line with the local Piper voice, no call (fallback path)."""
    text = " ".join(str(text or "").split())
    if text:
        threading.Thread(
            target=_run, args=(text, title, True), name="speak-local", daemon=True
        ).start()
