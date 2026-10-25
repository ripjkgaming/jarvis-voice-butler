"""Reply type and shared constants for the chess controller."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from games.chess.settings import PRESET_ELO

PRESETS = list(PRESET_ELO)
AMBIGUOUS_RESIGN = {"i'm done", "i am done", "gg", "good game", "you win"}


@dataclass
class Reply:
    """What a command produced. ``say`` is spoken; ``text`` goes on screen."""

    say: str | None = None
    text: str | None = None
    ok: bool = True
    kind: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "say": self.say,
            "text": self.text or self.say,
            "kind": self.kind,
            "data": self.data,
        }
