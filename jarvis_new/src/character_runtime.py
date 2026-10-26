"""Process-wide character runtime: one SwitchMachine shared by every surface."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

try:
    import characters as _chars
    import command_parse as _parse
    import switch_state as _ss
except ImportError:  # imported as src.character_runtime
    from src import characters as _chars  # type: ignore[no-redef]
    from src import command_parse as _parse  # type: ignore[no-redef]
    from src import switch_state as _ss  # type: ignore[no-redef]

logger = logging.getLogger("jarvis-character-runtime")
BOOT_ID = "jarvis"

Phase = _ss.Phase


class CharacterRuntime:
    def __init__(self) -> None:
        self.chars = _chars.load_all()
        self.machine = _ss.SwitchMachine(
            list(self.chars), boot_id=BOOT_ID, hint=_chars.write_recent
        )
        self._lock = threading.RLock()
        self._listeners: list[Callable[[dict], None]] = []
        self._prewarm: list[Callable[[Any], bool]] = []

    # -- state ------------------------------------------------------
    def active_id(self) -> str:
        with self._lock:
            return self.machine.active

    def active(self):
        return self.chars[self.active_id()]

    def names_map(self) -> dict[str, tuple[str, ...]]:
        out: dict[str, tuple[str, ...]] = {}
        for cid, ch in self.chars.items():
            names = (cid, ch.spoken_name, ch.display_name, *ch.aliases)
            out[cid] = tuple(dict.fromkeys(n.lower() for n in names if n))
        return out

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            m = self.machine
            return {
                "active": m.active,
                "target": m.target,
                "phase": m.phase.value,
                "char_turn": m.turn_id,
                "characters": list(self.chars),
            }

    # -- listeners --------------------------------------------------
    def subscribe(self, cb: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.append(cb)

    def _notify(self) -> None:
        snap = self.snapshot()
        with self._lock:
            cbs = list(self._listeners)
        for cb in cbs:
            try:
                cb(snap)
            except Exception:
                logger.exception("character listener failed")

    # -- switching --------------------------------------------------
    def _line(self, ch, kind: str, **fmt: Any) -> str:
        try:
            return _chars.line(ch, kind, **fmt)
        except (KeyError, IndexError):
            return ""

    def switch(self, target: str, source: str = "ui") -> dict:
        with self._lock:
            active = self.chars[self.machine.active]
            if target not in self.chars:
                names = ", ".join(c.display_name for c in self.chars.values())
                return {
                    "handled": True,
                    "switched": False,
                    "reply": self._line(active, "unknown_name", names=names),
                }
            d = self.machine.request(target, source)
            if d.kind == "already_active":
                return {
                    "handled": True,
                    "switched": False,
                    "reply": self._line(active, "already_active"),
                }
            frm = self.chars.get(d.from_id, active)
            result = {
                "handled": True,
                "switched": True,
                "target": d.to_id,
                "from": d.from_id,
                "char_turn": d.turn_id,
                "play_exit_line": d.play_exit_line,
                "replay_signature": d.replay_signature,
                "exit_line": self._line(frm, "exit"),
                "reply": self._line(self.chars[d.to_id], "switch_in"),
            }
        self._notify()
        return result

    def handle_text(self, text: str, source: str = "text") -> dict | None:
        cmd = _parse.parse_switch(text, self.names_map())
        if cmd is None:
            return None
        if cmd.kind == "unknown" or not cmd.target:
            return self.switch("", source)
        return self.switch(cmd.target, source)

    # -- phases -----------------------------------------------------
    def set_phase(self, phase: Phase) -> Phase:
        with self._lock:
            out = self.machine.advance(phase)
        self._notify()
        return out

    def complete(self, char_turn: int) -> bool:
        with self._lock:
            ok = self.machine.complete(char_turn)
        if ok:
            self._notify()
        return ok

    # -- prewarm ----------------------------------------------------
    def register_prewarm(self, fn: Callable[[Any], bool]) -> None:
        with self._lock:
            self._prewarm.append(fn)

    def run_prewarm(self, turn: int) -> threading.Thread:
        with self._lock:
            hooks = list(self._prewarm)
            target = self.chars.get(self.machine.target)

        def work() -> None:
            ok = True
            for fn in hooks:
                try:
                    if not fn(target):
                        ok = False
                except Exception:
                    logger.exception("prewarm hook failed")
                    ok = False
            changed = False
            try:
                with self._lock:
                    before = self.machine.phase
                    self.machine.prewarm_done(turn, ok)
                    changed = self.machine.phase is not before
            except Exception:
                logger.exception("prewarm_done failed")
            if changed:
                self._notify()

        t = threading.Thread(target=work, name="char-prewarm", daemon=True)
        t.start()
        return t

    # -- buffered input --------------------------------------------
    def buffer_input(self, item: Any) -> None:
        with self._lock:
            self.machine.buffer_input(item)

    def drain_input(self) -> list[Any]:
        with self._lock:
            return self.machine.drain_input()


_runtime: CharacterRuntime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> CharacterRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = CharacterRuntime()
        return _runtime


def reset_runtime() -> None:
    """Drop the singleton (tests)."""
    global _runtime
    with _runtime_lock:
        _runtime = None
