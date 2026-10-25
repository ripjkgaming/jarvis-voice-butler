"""Game module protocol and registry. No livekit / google.genai imports."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GameModule(Protocol):
    """A playable game: one command API shared by voice, mouse and keyboard."""

    name: str

    def snapshot(self) -> dict[str, Any]:
        """Full JSON-safe state for the board UI."""
        ...

    async def command(self, cmd: str, /, **args: Any) -> dict[str, Any]:
        """Run a structured command (mouse / keyboard / voice all land here)."""
        ...

    async def handle_text(self, text: str) -> Any:
        """Handle a spoken or typed utterance."""
        ...


_REGISTRY: dict[str, Callable[..., GameModule]] = {}


def register(name: str, factory: Callable[..., GameModule]) -> None:
    _REGISTRY[name] = factory


def create(name: str, **kwargs: Any) -> GameModule:
    try:
        factory = _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown game {name!r}; known: {sorted(_REGISTRY)}") from None
    return factory(**kwargs)


def available() -> list[str]:
    return sorted(_REGISTRY)
