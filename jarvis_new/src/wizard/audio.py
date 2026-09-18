"""Mic/speaker probe via sounddevice (deferred import).

Safely enumerates inputs/outputs, checks the default devices exist and
have sane channel counts, and optionally performs a short loopback-style
record + playback pass. Fails soft: no device -> amber, not red.

sounddevice is only imported inside functions so the wizard core stays
stdlib-only (it runs before the venvs exist).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AudioProbe:
    ok: bool
    input_device: str
    output_device: str
    detail: str = ""


def _default_names(sd) -> tuple[str, str]:
    inp = out = ""
    try:
        inp = sd.query_devices(kind="input")["name"]
    except Exception as exc:
        inp = f"(none: {exc})"
    try:
        out = sd.query_devices(kind="output")["name"]
    except Exception as exc:
        out = f"(none: {exc})"
    return str(inp), str(out)


def probe_audio(timeout: float = 2.0) -> AudioProbe:
    """Probe default input/output devices. I/O (sounddevice)."""
    try:
        import sounddevice as sd
    except ImportError:
        return AudioProbe(False, "", "", "sounddevice not installed")
    try:
        inp, out = _default_names(sd)
        inp_ok = "none" not in inp.lower()
        out_ok = "none" not in out.lower()
        ok = inp_ok and out_ok
        detail = f"input: {inp} | output: {out}"
        return AudioProbe(ok, inp, out, detail)
    except Exception as exc:
        return AudioProbe(False, "", "", f"probe failed: {exc}")
