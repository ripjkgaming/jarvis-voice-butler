"""Echo cancellation for the laptop-speaker case (PipeWire module-echo-cancel).

The wake client plays Jarvis's voice on the speakers and listens on the mic
through raw sounddevice streams, so without this his own voice bleeds back in
and Silero VAD / Whisper hear it as a user. This loads PipeWire's WebRTC echo
canceller on top of the current laptop speaker + mic and makes the
echo-cancelled pair the system defaults, so every stream that opens after
that (mic capture and playback alike) goes through it.

Only engages when the default sink is the built-in speaker: with a headset
there is no acoustic path, so nothing is touched. Everything is best-effort
and never raises. JARVIS_AEC=0 disables, JARVIS_AEC=1 forces it on.
"""

from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger("jarvis.aec")

AEC_MIC = "jarvis_aec_mic"
AEC_SPEAKER = "jarvis_aec_speaker"
#: Substring of the built-in speaker sink name (Intel HDA: "...HiFi__Speaker__sink").
SPEAKER_MARK = "Speaker__sink"
AEC_ARGS = (
    "analog_gain_control=0 digital_gain_control=1 noise_suppression=1 "
    "high_pass_filter=1 voice_detection=1"
)


def _pactl(*args: str) -> str:
    out = subprocess.run(
        ["pactl", *args], capture_output=True, text=True, timeout=5, check=True
    )
    return out.stdout.strip()


def mode() -> str:
    """'off' | 'on' | 'auto' from JARVIS_AEC. Pure (env only)."""
    raw = os.environ.get("JARVIS_AEC", "auto").strip().lower()
    if raw in ("0", "off", "false", "no"):
        return "off"
    if raw in ("1", "on", "true", "yes"):
        return "on"
    return "auto"


def should_engage(default_sink: str, cfg_mode: str = "auto") -> bool:
    """Engage on the built-in speaker (or when forced); not for headsets. Pure."""
    if cfg_mode == "off" or not default_sink:
        return False
    if default_sink == AEC_SPEAKER:
        return True  # already ours
    return cfg_mode == "on" or SPEAKER_MARK in default_sink


def build_load_args(source_master: str, sink_master: str) -> list[str]:
    """pactl args that load the WebRTC echo canceller on the given masters. Pure."""
    return [
        "load-module",
        "module-echo-cancel",
        "use_master_format=1",
        "aec_method=webrtc",
        f"aec_args={AEC_ARGS}",
        f"source_name={AEC_MIC}",
        f"sink_name={AEC_SPEAKER}",
        f"source_master={source_master}",
        f"sink_master={sink_master}",
    ]


def _loaded() -> bool:
    try:
        return AEC_MIC in _pactl("list", "short", "sources")
    except Exception:
        return False


def _first_speaker_master() -> tuple[str, str] | None:
    """Built-in (source, sink) masters when the default sink is not a speaker."""
    try:
        sinks = [ln.split("\t")[1] for ln in _pactl("list", "short", "sinks").splitlines()]
        sources = [
            ln.split("\t")[1] for ln in _pactl("list", "short", "sources").splitlines()
        ]
    except Exception:
        return None
    spk = next((s for s in sinks if SPEAKER_MARK in s), "")
    mic = next(
        (s for s in sources if ".monitor" not in s and "Mic1" in s), ""
    ) or next((s for s in sources if ".monitor" not in s and "pci" in s), "")
    return (mic, spk) if mic and spk else None


def engage() -> str:
    """Load the canceller if needed and make it the default pair.

    Returns a short reason-coded status. Never raises.
    """
    try:
        cfg = mode()
        sink = _pactl("get-default-sink")
        if not should_engage(sink, cfg):
            return f"skipped ({'off' if cfg == 'off' else 'not on laptop speakers'})"
        if not _loaded():
            if sink == AEC_SPEAKER:
                return "skipped (default is stale aec sink)"
            source_master = _pactl("get-default-source")
            if ".monitor" in source_master:
                masters = _first_speaker_master()
                if not masters:
                    return "skipped (no built-in mic)"
                source_master = masters[0]
            _pactl(*build_load_args(source_master, sink))
        _pactl("set-default-source", AEC_MIC)
        _pactl("set-default-sink", AEC_SPEAKER)
        logger.warning("echo cancellation on (%s / %s)", AEC_MIC, AEC_SPEAKER)
        return "engaged"
    except Exception as exc:
        logger.warning("echo cancellation unavailable: %s", exc)
        return f"error ({exc})"


def release() -> str:
    """Unload the canceller; PipeWire falls back to the real default devices."""
    try:
        for line in _pactl("list", "short", "modules").splitlines():
            parts = line.split("\t")
            if len(parts) > 1 and parts[1] == "module-echo-cancel" and AEC_MIC in line:
                _pactl("unload-module", parts[0])
        return "released"
    except Exception as exc:
        return f"error ({exc})"
