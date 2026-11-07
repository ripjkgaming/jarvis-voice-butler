"""Jarvis instruction and neutral confirmation helpers."""

from __future__ import annotations

import random
from typing import Any

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


def build_instructions(base: str, ch: Any, *, memory_block: str = "") -> str:
    """Compatibility for callers: Jarvis has one instruction set."""
    return base + memory_block
