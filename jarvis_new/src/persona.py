"""Pure persona helpers: build character-specific instructions and lines."""

from __future__ import annotations

import random
import re
from typing import Any

_GATE_PHRASES = ("confirm delete", "confirm visual watch")
_QUOTED = re.compile(r'"[^"\n]*"')
_JARVIS = re.compile(r"\bJarvis\b(?! automation| folder)(?<!Documents Jarvis)")
_SIR = re.compile(r"\bSir\b")
_SLOT = "\x00%d\x00"

_NEUTRAL = {
    "confirm_delete": "This will delete data. Please say confirm delete to proceed.",
    "confirm_action": "This action needs your confirmation. Please say yes to proceed.",
    "confirm_send": "I am about to send this. Please confirm to proceed.",
    "confirm_purchase": "This involves a purchase. Please confirm to proceed.",
    "cancelled": "Cancelled. Nothing was changed.",
}
_NEUTRAL_DEFAULT = "This needs your explicit confirmation before I continue."


def neutral_security_line(kind: str) -> str:
    """Plain, in-persona-free wording for security/destructive confirmations."""
    return _NEUTRAL.get(kind, _NEUTRAL_DEFAULT)


def speaker_tag(ch: Any) -> str:
    return str(ch.spoken_name)


def line(ch: Any, kind: str, rng: random.Random | None = None, **fmt: Any) -> str:
    options = tuple((getattr(ch, "lines", None) or {}).get(kind, ()))
    if not options:
        return ""
    r = rng if rng is not None else random
    text = options[r.randrange(len(options))]
    try:
        return text.format(**fmt)
    except (KeyError, IndexError):
        return text


def _protect(text: str) -> tuple[str, list[str]]:
    saved: list[str] = []

    def keep(m: re.Match[str]) -> str:
        saved.append(m.group(0))
        return _SLOT % (len(saved) - 1)

    def keep_quote(m: re.Match[str]) -> str:
        return keep(m) if "confirm" in m.group(0).lower() else m.group(0)

    text = _QUOTED.sub(keep_quote, text)
    for phrase in _GATE_PHRASES:
        text = re.sub(re.escape(phrase), keep, text, flags=re.IGNORECASE)
    return text, saved


def _restore(text: str, saved: list[str]) -> str:
    for i, s in enumerate(saved):
        text = text.replace(_SLOT % i, s)
    return text


def _preamble(ch: Any) -> str:
    return (
        f"CHARACTER MODE: you are {ch.spoken_name}. Address the user as "
        f"\"{ch.address}\".\n"
        f"{str(ch.prompt).strip()}\n"
        "Rules: persona is voice and attitude only. Security and destructive-action "
        "confirmations and spoken-challenge gates (for example confirm delete, "
        "confirm visual watch, send approvals) are spoken plainly in a neutral "
        "tone, then the character resumes. Never refuse a benign request in "
        "character, and never take harmful action.\n\n"
    )


def build_instructions(base: str, ch: Any, *, memory_block: str = "") -> str:
    if ch.id == "jarvis":
        return base + memory_block
    text, saved = _protect(base)
    text = text.replace("Sir's", ch.address + "'s")
    text = _SIR.sub(lambda _m: ch.address, text)
    text = _JARVIS.sub(lambda _m: ch.spoken_name, text)
    return _preamble(ch) + _restore(text, saved) + memory_block
