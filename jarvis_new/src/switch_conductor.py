"""Orchestrates one character switch on a monotonic clock.

Timeline (ms, from characters.motion()): t=0 EXITING (+ old exit line, + prewarm in
parallel), phases.signature[0] SIGNATURE, phases.in_chrome[0] ENTERING (after the
ready gate: prewarm must be ok by ready_gate_ms or the switch rolls back and the
rollback line is spoken in the PREVIOUS voice), greeting_at the new character
greets (whoosh), then SETTLING, and at total_ms the turn completes.

Every step is bound to its char turn id: a retarget bumps the id and all steps
of the older turn become no-ops. In-flight tasks are never touched.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

try:
    import characters as _chars
    import persona as _persona
    import switch_state as _ss
except ImportError:  # imported as src.switch_conductor
    from src import characters as _chars  # type: ignore[no-redef]
    from src import persona as _persona  # type: ignore[no-redef]
    from src import switch_state as _ss  # type: ignore[no-redef]

logger = logging.getLogger("jarvis-switch-conductor")
Phase = _ss.Phase
_ORDER = [
    Phase.PREPARING,
    Phase.EXITING,
    Phase.SIGNATURE,
    Phase.ENTERING,
    Phase.SETTLING,
]
_SLICE_S = 0.05
_REASONS = {
    "prewarm_failed": "The new voice would not load.",
    "prewarm_not_ready": "The new voice was not ready in time.",
}


def _cv():
    """character_voice module, or None when the voice agent has not shipped it."""
    try:
        import character_voice as cv
    except ImportError:
        try:
            from src import character_voice as cv  # type: ignore[no-redef]
        except ImportError:
            return None
    return cv


def default_wake_rpc(payload: dict, timeout: float = 3.0) -> dict | None:
    """One JSON message to the wake client control socket. Fail-soft."""
    import json
    import os
    import socket
    from pathlib import Path

    try:
        home = os.environ.get("JARVIS_HOME", "").strip()
        path = (Path(home) if home else Path.home() / ".jarvis") / "wake.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as cli:
            cli.settimeout(timeout)
            cli.connect(str(path))
            cli.sendall(json.dumps(payload).encode())
            chunks = []
            while True:
                data = cli.recv(4096)
                if not data:
                    break
                chunks.append(data)
            reply = json.loads(b"".join(chunks).decode())
        return reply if isinstance(reply, dict) else None
    except Exception:
        return None


def _thread_spawn(fn: Callable[[], None]) -> Any:
    t = threading.Thread(target=fn, name="char-switch", daemon=True)
    t.start()
    return t


class _RollbackNowError(Exception):
    pass


class Conductor:
    def __init__(
        self,
        runtime: Any,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wake_rpc: Callable[[dict], dict | None] | None = None,
        spawn: Callable[[Callable[[], None]], Any] = _thread_spawn,
        voice_on: Callable[[], bool] | None = None,
        motion: dict | None = None,
    ) -> None:
        self.rt = runtime
        self._clock = clock
        self._sleep = sleep
        self._rpc = wake_rpc or default_wake_rpc
        self._spawn = spawn
        self._voice_on_fn = voice_on
        self._motion = motion
        self._lock = threading.RLock()
        self._gen = 0
        self._ff: tuple[float, float] | None = None  # (elapsed_ms at ff, ff_ms)
        self._t0_cur = clock()
        self._prev: Any = None
        self._exit_spoken = False
        self._interrupted = False
        self._replayer: Callable[[Any], None] | None = None
        self._complete_cbs: list[Callable[[Any], None]] = []
        self._installed = False
        self.log: list[tuple[str, int, float]] = []  # (event, turn, elapsed_ms)

    # -- wiring -----------------------------------------------------
    def install(self) -> Conductor:
        if not self._installed:
            self._installed = True
            self.rt.register_prewarm(self._prewarm_hook)
        return self

    def set_replayer(self, fn: Callable[[Any], None] | None) -> None:
        self._replayer = fn

    def on_complete(self, fn: Callable[[Any], None]) -> None:
        self._complete_cbs.append(fn)

    def motion(self) -> dict:
        if self._motion is None:
            self._motion = _chars.motion()
        return self._motion

    @staticmethod
    def _prewarm_hook(char: Any) -> bool:
        cv = _cv()
        fn = getattr(cv, "prewarm", None) if cv else None
        if fn is None:
            return True  # voice agent absent: nothing to warm
        return bool(fn(char))

    def note_user_interrupt(self) -> None:
        """The user spoke over us: the pending exit line is skipped."""
        self._interrupted = True

    # -- voice helpers ----------------------------------------------
    def voice_on(self) -> bool:
        if self._voice_on_fn is not None:
            try:
                return bool(self._voice_on_fn())
            except Exception:
                return False
        reply = self._rpc({"status": True})
        if not isinstance(reply, dict):
            return False
        return bool(reply.get("voice_output", reply.get("ok", True)))

    def _say(self, text: str, char_id: str, *, whoosh: bool, interrupt: bool) -> dict | None:
        if not text:
            return None
        try:
            return self._rpc(
                {
                    "character_say": {
                        "text": text,
                        "character": char_id,
                        "whoosh": whoosh,
                        "interrupt": interrupt,
                    }
                }
            )
        except Exception:
            logger.exception("character_say failed")
            return None

    # -- entry ------------------------------------------------------
    def start(self, res: dict, *, source: str = "ui", interrupted: bool = False) -> bool:
        """Begin (or retarget) a switch from a runtime.switch() result."""
        if not isinstance(res, dict) or not res.get("switched"):
            return False
        turn = int(res["char_turn"])
        with self._lock:
            self._gen += 1
            gen = self._gen
            self._ff = None
            if interrupted:
                self._interrupted = True
        t0 = self._clock()
        self._spawn(lambda: self._run(turn, gen, t0, res, source))
        return True

    def submit_input(self, item: Any) -> bool:
        """Buffer input that arrives mid-switch; True when it was buffered."""
        if self.rt.snapshot()["phase"] == Phase.IDLE.value:
            return False
        self.rt.buffer_input(item)
        with self._lock:
            if self._ff is None:
                ms = float(self.motion().get("fast_forward_ms", 150))
                self._ff = ((self._clock() - self._t0_cur) * 1000.0, ms)
        return True

    # -- run --------------------------------------------------------
    def _live(self, turn: int, gen: int) -> bool:
        return gen == self._gen and self.rt.snapshot()["char_turn"] == turn

    def _eff(self, ms: float, t0: float, total: float) -> float:
        """Absolute clock time for timeline offset ms (compressed under ff)."""
        ff = self._ff
        if ff is None or ms <= ff[0]:
            return t0 + ms / 1000.0
        el, ff_ms = ff
        span = max(1.0, total - el)
        return t0 + (el + (ms - el) * ff_ms / span) / 1000.0

    def _wait(self, ms: float, t0: float, total: float, turn: int, gen: int) -> bool:
        while True:
            if not self._live(turn, gen):
                return False
            if self.rt.machine.phase is Phase.ROLLBACK:
                raise _RollbackNowError
            left = self._eff(ms, t0, total) - self._clock()
            if left <= 0:
                return True
            self._sleep(min(left, _SLICE_S))

    def _advance(self, target: Phase) -> bool:
        cur = self.rt.machine.phase
        if cur is Phase.ROLLBACK or cur is Phase.IDLE:
            return False
        if cur is target:
            return True
        if _ORDER.index(target) < _ORDER.index(cur):
            return True
        # walk the legal chain one phase at a time
        for ph in _ORDER[_ORDER.index(cur) + 1 : _ORDER.index(target) + 1]:
            try:
                self.rt.set_phase(ph)
            except Exception:
                return False
        return True

    def _mark(self, ev: str, turn: int, t0: float) -> None:
        self.log.append((ev, turn, round((self._clock() - t0) * 1000.0, 1)))

    def _run(self, turn: int, gen: int, t0: float, res: dict, source: str) -> None:
        try:
            self._run_inner(turn, gen, t0, res, source)
        except _RollbackNowError:
            if self._live(turn, gen):
                self._rollback(turn, gen, t0, self._prev, None)
        except Exception:
            logger.exception("switch conductor failed")

    def _run_inner(self, turn: int, gen: int, t0: float, res: dict, source: str) -> None:
        mo = self.motion()
        total = float(mo["total_ms"])
        ph = mo["phases"]
        sig_at = float(ph["signature"][0])
        chrome_at = float(ph["in_chrome"][0])
        greet_at = float(ph["greeting_at"])
        gate_ms = float(mo.get("ready_gate_ms", 1000))
        self._t0_cur = t0
        chars = self.rt.chars
        active = chars[self.rt.active_id()]
        self._prev = active
        target = chars[res["target"]]

        # t=0: EXITING, exit line (old voice), prewarm in parallel
        if not self._advance(Phase.EXITING):
            return
        self._mark("exiting", turn, t0)
        exit_thread = None
        if res.get("play_exit_line") and not self._exit_spoken and not self._interrupted:
            self._exit_spoken = True
            line = _persona.line(active, "exit")

            def speak_exit() -> None:
                if not self.voice_on() or self._interrupted or not self._live(turn, gen):
                    return
                self._mark("exit_say", turn, t0)
                self._say(line, active.id, whoosh=False, interrupt=False)

            exit_thread = self._spawn(speak_exit)
        prewarm = self.rt.run_prewarm(turn)

        if not self._wait(sig_at, t0, total, turn, gen):
            return
        if self._advance(Phase.SIGNATURE):
            self._mark("signature", turn, t0)

        # ready gate: wait (bounded by ready_gate_ms) for prewarm
        if not self._wait(chrome_at, t0, total, turn, gen):
            return
        remaining = gate_ms / 1000.0 - (self._clock() - t0)
        if prewarm is not None and hasattr(prewarm, "join"):
            prewarm.join(timeout=max(0.0, remaining))
        if not self._live(turn, gen):
            return
        if not self._gate(turn):
            self._rollback(turn, gen, t0, active, exit_thread)
            return
        if not self._advance(Phase.ENTERING):
            return
        self._mark("entering", turn, t0)

        # greeting
        if not self._wait(greet_at, t0, total, turn, gen):
            return
        quiet = self._ff is not None or self._buffered()
        if not quiet and self.rt.machine.should_greet(turn) and self.voice_on():
            self._mark("greet_say", turn, t0)
            self._say(_persona.line(target, "greet"), target.id, whoosh=True, interrupt=True)
        if self._advance(Phase.SETTLING):
            self._mark("settling", turn, t0)

        if not self._wait(total, t0, total, turn, gen):
            return
        self._finish(turn, gen, t0)

    def _buffered(self) -> bool:
        return bool(self.rt.machine._buffer)

    def _gate(self, turn: int) -> bool:
        with self.rt._lock:
            if self.rt.machine.phase is Phase.ROLLBACK:
                return False
            return self.rt.machine.ready_gate(turn)

    def _rollback(self, turn, gen, t0, active, exit_thread) -> None:
        with self.rt._lock:
            m = self.rt.machine
            if m.phase is not Phase.ROLLBACK:
                m.begin_rollback("prewarm_not_ready")
            reason = "prewarm_failed" if m._prewarm.get(turn) is False else "prewarm_not_ready"
        self.rt._notify()
        self._mark("rollback", turn, t0)
        if hasattr(exit_thread, "join"):
            exit_thread.join(timeout=1.5)
        text = _persona.line(
            active, "rollback", name=active.spoken_name, reason=_REASONS.get(reason, "")
        ).strip()
        if self.voice_on():
            self._say(text, active.id, whoosh=False, interrupt=True)
        self._finish(turn, gen, t0)

    def _finish(self, turn: int, gen: int, t0: float) -> None:
        if not self._live(turn, gen):
            return
        if not self.rt.complete(turn):
            return
        self._mark("complete", turn, t0)
        self._exit_spoken = False
        self._interrupted = False
        self._ff = None
        char = self.rt.active()
        for cb in list(self._complete_cbs):
            try:
                cb(char)
            except Exception:
                logger.exception("switch complete callback failed")
        replay = self._replayer
        if replay is not None:
            for item in self.rt.drain_input():
                try:
                    replay(item)
                except Exception:
                    logger.exception("buffered input replay failed")


_conductor: Conductor | None = None
_lock = threading.Lock()


def get_conductor() -> Conductor:
    global _conductor
    with _lock:
        try:
            from character_runtime import get_runtime
        except ImportError:
            from src.character_runtime import get_runtime
        rt = get_runtime()
        if _conductor is None or _conductor.rt is not rt:
            _conductor = Conductor(rt).install()
        return _conductor


def reset_conductor() -> None:
    global _conductor
    with _lock:
        _conductor = None


def start_switch(res: dict | None, source: str = "ui") -> bool:
    """Fire-and-forget hook for call sites. Never raises."""
    try:
        if not isinstance(res, dict) or not res.get("switched"):
            return False
        return get_conductor().start(res, source=source)
    except Exception:
        logger.exception("could not start switch conductor")
        return False
