"""Process-wide character runtime: one SwitchMachine shared by every surface."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
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

_AUTH_ENV = "JARVIS_CHARACTER_AUTHORITY"  # set only by the bridge; others mirror
_STATE_POLL_S = 0.1
_BRIDGE_TIMEOUT_S = 2.0
_UNREACHABLE = "I could not reach the control bridge"


def state_path() -> Path:
    override = os.environ.get("JARVIS_CHARACTER_STATE", "").strip()
    if override:
        return Path(override)
    home = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".jarvis") / "character_state.json"


def set_authority(flag: bool = True) -> None:
    """Mark this process as the single authority (bridge). Writes boot state."""
    if flag:
        os.environ[_AUTH_ENV] = "1"
    else:
        os.environ.pop(_AUTH_ENV, None)
    if flag:
        # Boot ALWAYS jarvis: a stale file from a previous run never leaks.
        get_runtime()._write_state(boot=True)


def is_authority() -> bool:
    return os.environ.get(_AUTH_ENV) == "1"


def _bridge_token() -> str:
    token = os.environ.get("JARVIS_BRIDGE_TOKEN", "").strip()
    if not token:
        home = os.environ.get("JARVIS_HOME", "").strip()
        base = Path(home) if home else Path.home() / ".jarvis"
        try:
            token = (base / "bridge_token").read_text().strip()
        except (OSError, UnicodeError):
            token = ""
    return token


class CharacterRuntime:
    def __init__(self) -> None:
        self.chars = _chars.load_all()
        self.machine = _ss.SwitchMachine(
            list(self.chars), boot_id=BOOT_ID, hint=_chars.write_recent
        )
        self._lock = threading.RLock()
        self._listeners: list[Callable[[dict], None]] = []
        self._prewarm: list[Callable[[Any], bool]] = []
        self._mirror_key: Any = None
        self._mirror_state: dict[str, Any] | None = None
        self._mirror_checked = 0.0

    # -- shared state file -----------------------------------------
    def _write_state(self, boot: bool = False) -> None:
        with self._lock:
            m = self.machine
            data = {
                "active": BOOT_ID if boot else m.active,
                "target": None if boot else m.target,
                "phase": "idle" if boot else m.phase.value,
                "char_turn": 0 if boot else m.turn_id,
                "ts": time.time(),
            }
            path = state_path()
            tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(data))
                os.replace(tmp, path)
            except OSError:
                logger.exception("character state write failed")

    def _read_mirror(self) -> dict[str, Any]:
        """Authority state from disk, cached by mtime (checked every 100 ms)."""
        now = time.monotonic()
        with self._lock:
            if self._mirror_state is not None and now - self._mirror_checked < _STATE_POLL_S:
                return self._mirror_state
            self._mirror_checked = now
            path = state_path()
            try:
                st = path.stat()
                key = (st.st_mtime_ns, st.st_ino, st.st_size)
            except OSError:
                key = None
            if key is not None and key == self._mirror_key and self._mirror_state:
                return self._mirror_state
            self._mirror_key = key
            state = {"active": BOOT_ID, "target": None, "phase": "idle", "char_turn": 0}
            if key is not None:
                try:
                    raw = json.loads(path.read_text())
                    act = raw["active"]
                    tgt = raw.get("target")
                    phase = _ss.Phase(raw["phase"]).value
                    if act in self.chars and (tgt is None or tgt in self.chars):
                        state = {
                            "active": act,
                            "target": tgt,
                            "phase": phase,
                            "char_turn": int(raw.get("char_turn", 0)),
                        }
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            self._mirror_state = state
            return state

    # -- state ------------------------------------------------------
    def active_id(self) -> str:
        if not is_authority():
            return self._read_mirror()["active"]
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
        if not is_authority():
            return {**self._read_mirror(), "characters": list(self.chars)}
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
        if is_authority():
            self._write_state()
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

    def _mirror_switch(self, target: str, source: str) -> dict:
        active = self.chars[self.active_id()]
        if target not in self.chars:
            names = ", ".join(c.display_name for c in self.chars.values())
            return {
                "handled": True,
                "switched": False,
                "reply": self._line(active, "unknown_name", names=names),
            }
        if target == active.id and self._read_mirror()["phase"] == "idle":
            return {
                "handled": True,
                "switched": False,
                "reply": self._line(active, "already_active"),
            }
        port = os.environ.get("JARVIS_BRIDGE_PORT", "4317").strip() or "4317"
        headers = {"Content-Type": "application/json"}
        token = _bridge_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/character/switch",
            data=json.dumps({"target": target, "source": "voice"}).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_BRIDGE_TIMEOUT_S) as resp:
                res = json.load(resp)
            if not isinstance(res, dict) or not res.get("ok"):
                raise ValueError("bridge refused")
        except (OSError, ValueError, urllib.error.URLError):
            return {"handled": True, "switched": False, "reply": _UNREACHABLE}
        res.pop("ok", None)
        res.pop("character", None)
        self._mirror_checked = 0.0
        return res

    def switch(self, target: str, source: str = "ui") -> dict:
        if not is_authority():
            return self._mirror_switch(target, source)
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
