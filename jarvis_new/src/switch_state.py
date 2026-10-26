"""Pure character-switch state machine (no I/O except an optional hint callback)."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

SOURCES = ("voice", "text", "ui")
SIGNATURE_SKIP_PROGRESS = 0.7


class Phase(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    EXITING = "exiting"
    SIGNATURE = "signature"
    ENTERING = "entering"
    SETTLING = "settling"
    ROLLBACK = "rollback"


_LEGAL: dict[Phase, frozenset[Phase]] = {
    Phase.IDLE: frozenset({Phase.PREPARING}),
    Phase.PREPARING: frozenset({Phase.EXITING, Phase.ROLLBACK}),
    Phase.EXITING: frozenset({Phase.SIGNATURE, Phase.ROLLBACK}),
    Phase.SIGNATURE: frozenset({Phase.ENTERING, Phase.ROLLBACK}),
    Phase.ENTERING: frozenset({Phase.SETTLING, Phase.ROLLBACK}),
    Phase.SETTLING: frozenset({Phase.IDLE, Phase.ROLLBACK}),
    Phase.ROLLBACK: frozenset({Phase.IDLE}),
}
_PRE_GATE = frozenset({Phase.PREPARING, Phase.EXITING, Phase.SIGNATURE})


class IllegalTransitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Decision:
    kind: str  # ignored_unknown|already_active|started|retargeted|rollback
    turn_id: int
    from_id: str
    to_id: str
    play_exit_line: bool = False
    replay_signature: bool = False
    from_progress: float = 0.0
    reason: str = ""


class SwitchMachine:
    def __init__(
        self,
        ids: Iterable[str],
        boot_id: str = "jarvis",
        clock: Callable[[], float] = time.monotonic,
        hint: Callable[[str], None] | None = None,
    ) -> None:
        self.ids = frozenset(ids) | {boot_id}
        self.active = boot_id
        self.target = boot_id
        self.phase = Phase.IDLE
        self.turn_id = 0
        self.progress = 0.0
        self.source = "voice"
        self.from_progress = 0.0
        self.started_at: float | None = None
        self._clock = clock
        self._hint = hint
        self._prewarm: dict[int, bool] = {}
        self._greeted: set[int] = set()
        self._buffer: list[Any] = []

    # -- requests ---------------------------------------------------
    def request(self, target: str, source: str = "voice") -> Decision:
        if target not in self.ids:
            return Decision("ignored_unknown", self.turn_id, self.active, target)
        if self.phase is Phase.IDLE and target == self.active:
            return Decision("already_active", self.turn_id, self.active, target)
        self.turn_id += 1
        self.source = source if source in SOURCES else "voice"
        if self.phase is Phase.IDLE:
            self.target = target
            self.phase = Phase.PREPARING
            self.progress = 0.0
            self.from_progress = 0.0
            self.started_at = self._clock()
            self._prewarm.pop(self.turn_id, None)
            return Decision(
                "started",
                self.turn_id,
                self.active,
                target,
                play_exit_line=True,
                replay_signature=True,
            )
        prev = self.target
        self.from_progress = self.progress
        self.target = target
        if target == self.active:
            self._prewarm[self.turn_id] = True  # nothing to warm
        replay = not (
            self.phase in (Phase.ENTERING, Phase.SETTLING)
            or self.progress > SIGNATURE_SKIP_PROGRESS
        )
        return Decision(
            "retargeted",
            self.turn_id,
            prev,
            target,
            play_exit_line=self.phase is Phase.PREPARING,
            replay_signature=replay,
            from_progress=self.from_progress,
        )

    def should_greet(self, turn_id: int) -> bool:
        """True once, and only for the newest turn."""
        if turn_id != self.turn_id or turn_id in self._greeted:
            return False
        self._greeted.add(turn_id)
        return True

    # -- prewarm / gate / rollback ---------------------------------
    def prewarm_done(self, turn_id: int, ok: bool) -> Decision | None:
        if turn_id != self.turn_id or self.phase in (Phase.IDLE, Phase.ROLLBACK):
            return None
        self._prewarm[turn_id] = bool(ok)
        if not ok and self.phase in _PRE_GATE:
            return self.begin_rollback("prewarm_failed")
        return None

    def ready_gate(self, turn_id: int) -> bool:
        if turn_id != self.turn_id or self.phase not in _PRE_GATE:
            return False
        if self._prewarm.get(turn_id) is True:
            return True
        self.begin_rollback("prewarm_not_ready")
        return False

    def begin_rollback(self, reason: str = "") -> Decision:
        if self.phase is Phase.IDLE:
            raise IllegalTransitionError("nothing to roll back")
        failed = self.target
        if self.phase is not Phase.ROLLBACK:
            self.advance(Phase.ROLLBACK)
        self.target = self.active
        return Decision(
            "rollback",
            self.turn_id,
            failed,
            self.active,
            reason=reason,
            from_progress=self.progress,
        )

    # -- phases -----------------------------------------------------
    def advance(self, phase: Phase) -> Phase:
        if phase not in _LEGAL[self.phase]:
            raise IllegalTransitionError(f"{self.phase.value} -> {phase.value}")
        self.phase = phase
        return phase

    def set_progress(self, value: float) -> float:
        self.progress = min(1.0, max(0.0, float(value)))
        return self.progress

    def complete(self, turn_id: int) -> bool:
        if turn_id != self.turn_id or self.phase is Phase.IDLE:
            return False
        if self.phase not in (Phase.SETTLING, Phase.ROLLBACK):
            return False
        self.active = self.target
        self.phase = Phase.IDLE
        self.progress = 0.0
        self.from_progress = 0.0
        self._prewarm.pop(turn_id, None)
        if self._hint is not None:
            self._hint(self.active)
        return True

    # -- input buffering -------------------------------------------
    def buffer_input(self, item: Any) -> None:
        self._buffer.append(item)

    def drain_input(self) -> list[Any]:
        items, self._buffer = self._buffer, []
        return items

    def need_fast_forward(self) -> bool:
        return bool(self._buffer) and self.phase is not Phase.IDLE

    def snapshot(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "target": self.target,
            "phase": self.phase.value,
            "turn_id": self.turn_id,
        }
