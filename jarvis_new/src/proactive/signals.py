"""Proactive signals: what an event source reports, before any policy.

A Signal is a plain record; sources build them, ProactiveEngine decides
what to do with them (proactive/engine.py). Urgency ranks low < normal <
high < critical. `key` is the dedupe identity: the same key is only ever
delivered once (the engine remembers delivered keys).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

URGENCIES = ("low", "normal", "high", "critical")
_RANK = {u: i for i, u in enumerate(URGENCIES)}


def rank(urgency: str) -> int:
    return _RANK.get(urgency, 0)


@dataclass
class Signal:
    kind: str  # "calendar" | "mail" | "job" | "system" | "deadline" | ...
    key: str  # dedupe identity, stable across polls
    title: str  # short, speakable
    detail: str = ""
    urgency: str = "normal"
    ts: float = 0.0
    action: dict = field(default_factory=dict)  # optional follow-up hint

    def as_dict(self) -> dict:
        return asdict(self)
