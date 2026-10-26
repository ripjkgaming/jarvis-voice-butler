"""Single owner of voice-turn state. Pure stdlib, thread-safe, no event loop needed."""

from __future__ import annotations

import contextlib
import enum
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

SLOW_BOOT_LINE = "One moment, sir, still coming online."
SLOW_BOOT_AFTER_S = 2.0
BOOT_QUEUE_WINDOW_S = 30.0

# Process-wide numeric IDs fit JavaScript's exact integer range and never
# collide when another session cancels the shared local playback gate.
_ID_LOCK = threading.Lock()
_NEXT_ID = secrets.randbits(40) << 12


def _mint_id() -> int:
    global _NEXT_ID
    with _ID_LOCK:
        _NEXT_ID += 1
        return _NEXT_ID


_STOP_PHRASES = ("never mind", "nevermind", "forget it", "stop", "cancel")
_CONT_PREFIXES = (
    "no",
    "wait",
    "also",
    "actually",
    "i meant",
    "and",
    "sorry",
    "the other one",
)


class State(enum.Enum):
    LOADING = "loading"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class CancelToken:
    """Event plus callbacks fired once on cancel."""

    def __init__(self) -> None:
        self.event = threading.Event()
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()

    def add_callback(self, fn: Callable[[], None]) -> None:
        with self._lock:
            if not self.event.is_set():
                self._callbacks.append(fn)
                return
        fn()

    def cancel(self) -> bool:
        """Returns True only for the call that actually cancelled."""
        with self._lock:
            if self.event.is_set():
                return False
            self.event.set()
            cbs, self._callbacks = self._callbacks, []
        for cb in cbs:
            with contextlib.suppress(Exception):
                cb()
        return True


@dataclass
class Turn:
    id: int
    text: str
    cancel: CancelToken = field(default_factory=CancelToken)
    side_effect: bool = False
    completed: bool = False
    superseded: bool = False
    filler_sent: bool = False
    filler_cancelled: bool = False

    @property
    def cancelled(self) -> bool:
        return self.cancel.cancelled


@dataclass
class Action:
    kind: str  # 'cancel_all' | 'merge' | 'new' | 'queue'
    turn: Turn | None = None
    merged_text: str | None = None


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())


def classify(text: str, state: State) -> str:
    t = _norm(text)
    if not t:
        return "new"
    for p in _STOP_PHRASES:
        if t == p:
            return "stop"
        if p in {"stop", "cancel"}:
            if t in {
                p + " please",
                "please " + p,
                p + " talking",
                p + " speaking",
                p + " everything",
                p + " that",
                p + " it",
            }:
                return "stop"
        elif t.startswith(p + " "):
            return "stop"
    if state is State.THINKING:
        for p in _CONT_PREFIXES:
            if t == p or t.startswith(p + " "):
                return "continuation"
    return "new"


class TurnManager:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.RLock()
        self._clock = clock
        self._state = State.LOADING
        self._latest: Turn | None = None
        self._turns: dict[int, Turn] = {}
        self._hooks: dict[str, Callable[[int], Any]] = {}
        self._state_cbs: list[Callable[[State], None]] = []
        self._replies: dict[int, Any] = {}
        self._speaking_id: int | None = None
        self._boot_queue: list[tuple[float, str]] = []
        self.user_spoke_during_boot = False
        self._boot_wait_start: float | None = None
        self._slow_line_sent = False

    # ---- state ----
    @property
    def state(self) -> State:
        return self._state

    @property
    def current_turn(self) -> Turn | None:
        return self._latest

    @property
    def speaking(self) -> bool:
        return self._speaking_id is not None

    def on_state(self, cb: Callable[[State], None]) -> None:
        with self._lock:
            self._state_cbs.append(cb)

    def _set_state(self, new: State) -> None:
        with self._lock:
            if new is self._state:
                return
            self._state = new
            cbs = list(self._state_cbs)
        for cb in cbs:
            with contextlib.suppress(Exception):
                cb(new)

    def set_ready(self) -> None:
        with self._lock:
            if self._state is State.LOADING:
                self._set_state(State.LISTENING)

    def register_cancel_hook(self, name: str, fn: Callable[[int], Any]) -> None:
        with self._lock:
            self._hooks[name] = fn

    # ---- turns ----
    def _cancel_turn(self, turn: Turn) -> None:
        if turn.cancel.cancel():
            with self._lock:
                self._replies.pop(turn.id, None)
                hooks = list(self._hooks.values())
            for h in hooks:
                with contextlib.suppress(Exception):
                    h(turn.id)

    def begin_turn(self, text: str, side_effect: bool = False) -> Turn:
        with self._lock:
            prev = self._latest
            turn = Turn(id=_mint_id(), text=text, side_effect=side_effect)
            self._turns[turn.id] = turn
            self._latest = turn
            if prev is not None and not prev.cancelled:
                if prev.side_effect:
                    prev.superseded = True
                    self._replies.pop(prev.id, None)
                else:
                    self._cancel_turn(prev)
            if self._state is not State.LOADING:
                self._speaking_id = None
                self._set_state(State.THINKING)
            return turn

    def is_current(self, turn_id: int) -> bool:
        with self._lock:
            t = self._turns.get(turn_id)
            return (
                t is not None
                and self._latest is not None
                and t.id == self._latest.id
                and not t.cancelled
                and not t.superseded
            )

    # ---- speech handling ----
    def classify(self, text: str, state: State | None = None) -> str:
        return classify(text, state or self._state)

    def on_user_speech(self, text: str) -> Action:
        with self._lock:
            if self._state is State.LOADING:
                now = self._clock()
                self._boot_queue.append((now, text))
                self._trim_boot(now)
                if not self.user_spoke_during_boot:
                    self.user_spoke_during_boot = True
                    self._boot_wait_start = now
                return Action("queue")
            kind = classify(text, self._state)
            cur = self._latest
            if kind == "stop":
                self._cancel_all()
                return Action("cancel_all")
            if (
                kind == "continuation"
                and cur is not None
                and not cur.cancelled
                and not cur.side_effect
                and not cur.completed
            ):
                merged = f"{cur.text} {text}".strip()
                turn = self.begin_turn(merged)
                return Action("merge", turn, merged)
            return Action("new", self.begin_turn(text), text)

    def _cancel_all(self) -> None:
        for t in list(self._turns.values()):
            if not t.cancelled:
                self._cancel_turn(t)
        self._replies.clear()
        self._speaking_id = None
        if self._state is not State.LOADING:
            self._set_state(State.LISTENING)

    def cancel(self, turn_id: int) -> None:
        with self._lock:
            turn = self._turns.get(turn_id)
            if turn is not None:
                self._cancel_turn(turn)
            if self._speaking_id == turn_id:
                self._speaking_id = None
            if self._latest is turn and self._state is not State.LOADING:
                self._set_state(State.LISTENING)

    def cancel_all(self) -> None:
        with self._lock:
            self._cancel_all()

    def _trim_boot(self, now: float) -> None:
        # The transport caps raw PCM at 30 seconds. Recognized requests are
        # small and must survive arbitrarily slow model startup.
        pass

    def pop_boot_queue(self) -> list[str]:
        with self._lock:
            self._trim_boot(self._clock())
            out = [s for _, s in self._boot_queue]
            self._boot_queue = []
            return out

    def slow_boot_line(self, now: float | None = None) -> str | None:
        with self._lock:
            if now is None:
                now = self._clock()
            if (
                self._state is State.LOADING
                and self.user_spoke_during_boot
                and not self._slow_line_sent
                and self._boot_wait_start is not None
                and now - self._boot_wait_start > SLOW_BOOT_AFTER_S
            ):
                self._slow_line_sent = True
                return SLOW_BOOT_LINE
            return None

    # ---- filler ----
    def filler(self, turn: Turn) -> bool:
        with self._lock:
            if turn.filler_sent or turn.filler_cancelled or turn.cancelled:
                return False
            turn.filler_sent = True
            return True

    def cancel_filler(self, turn: Turn) -> None:
        with self._lock:
            turn.filler_cancelled = True

    # ---- replies ----
    def submit_reply(self, turn_id: int, payload: Any) -> bool:
        with self._lock:
            t = self._turns.get(turn_id)
            if t is None or t.cancelled or t.superseded:
                return False
            if self._latest is not None and turn_id < self._latest.id:
                return False
            self._replies[turn_id] = payload
            return True

    def deliver(self) -> tuple[int, Any] | None:
        """Newest valid reply as (turn_id, payload), or None. One speaker at a time."""
        with self._lock:
            if self._speaking_id is not None:
                return None
            latest = self._latest.id if self._latest else 0
            best: int | None = None
            for tid in sorted(self._replies, reverse=True):
                t = self._turns[tid]
                if t.cancelled or t.superseded or tid < latest:
                    continue
                best = tid
                break
            payload = self._replies.pop(best) if best is not None else None
            # drop everything else: stale or invalid
            self._replies.clear()
            if best is None:
                return None
            return best, payload

    def start_speaking(self, turn_id: int) -> bool:
        with self._lock:
            if self._speaking_id is not None or not self.is_current(turn_id):
                return False
            self._speaking_id = turn_id
            self._set_state(State.SPEAKING)
            return True

    def finish_speaking(self, turn_id: int) -> None:
        with self._lock:
            if self._speaking_id != turn_id:
                return
            self._speaking_id = None
            self._turns[turn_id].completed = True
            if self._state is State.SPEAKING:
                self._set_state(State.LISTENING)

    def barge_in(self) -> None:
        with self._lock:
            tid = self._speaking_id
            self._speaking_id = None
            turn = self._turns.get(tid) if tid is not None else None
            if turn is not None:
                self._cancel_turn(turn)
            if self._state is not State.LOADING:
                self._set_state(State.LISTENING)


async def complete_side_effect(awaitable):
    """Drain an already-started mutation even when its caller is interrupted.

    Return the result to the tool wrapper so audit/recovery bookkeeping runs
    exactly once. Repeated cancellation cannot abandon the protected task.
    """
    import asyncio

    task = asyncio.ensure_future(awaitable)
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


class VoiceTurnBinding:
    """Connect native SDK events to turn cancellation without duplicating input.

    Gemini already received audio: normal transcripts only mint IDs. Only a
    correction restarts generation with merged text after interrupt drains.
    """

    def __init__(self, session, echo, publish=None):
        import asyncio

        self.session = session
        self.echo = echo
        self.publish = publish
        self.manager = TurnManager()
        self.tasks = set()
        self.handles = {}
        self.handle_turns = {}
        self.filler_handle = None
        self._loop = asyncio.get_running_loop()
        session.jarvis_turn_manager = self.manager
        session._jarvis_turn_binding = self
        self.generation = 0
        self.restarting = False
        self.manager.register_cancel_hook("native", self._cancel)
        self.manager.on_state(self._state)
        session.on("user_input_transcribed", self.transcript)
        session.on("agent_state_changed", self.agent_state)
        session.on("speech_created", self.speech_created)
        self._state(self.manager.state)
        self._spawn(self._slow_boot_notice())

    async def _slow_boot_notice(self):
        import asyncio
        import contextlib

        while self.manager.state is State.LOADING:
            await asyncio.sleep(0.1)
            line = self.manager.slow_boot_line()
            if line is not None:
                # May lack TTS before startup completes; never delay input
                # readiness or schedule a late loading announcement.
                with contextlib.suppress(Exception):
                    speech = self.session.say(line, add_to_chat_ctx=False)
                    while self.manager.state is State.LOADING:
                        await asyncio.sleep(0.1)
                    speech.interrupt(force=True)
                return

    def _spawn(self, awaitable):
        import asyncio

        task = asyncio.ensure_future(awaitable)
        self.tasks.add(task)

        def done(future):
            self.tasks.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(done)
        return task

    def _emit(self, event, turn_id=None, **extra):
        import inspect

        if self.publish is not None:
            result = self.publish(
                dict(type="jarvis_turn", event=event, turn_id=turn_id, **extra)
            )
            if inspect.isawaitable(result):
                self._spawn(result)

    def _state(self, state):
        turn = self.manager.current_turn
        self._emit("state", turn.id if turn else 0, state=state.value)

    def _cancel(self, turn_id):
        import contextlib

        handle = self.handles.pop(turn_id, None)
        if handle is not None:
            with contextlib.suppress(Exception):
                handle.interrupt(force=True)
        with contextlib.suppress(Exception):
            from playback_gate import get_playback_gate

            get_playback_gate().cancel(turn_id)
        self._emit("cancel", turn_id)

    def speech_created(self, event):
        # No native per-generation ID exists in Gemini audio. While draining
        # a superseded generation reject every handle; only admit the next
        # generation after interrupt() has acknowledged output clearance.
        if self.restarting:
            event.speech_handle.interrupt(force=True)
            return
        turn = self.manager.current_turn
        if turn is not None:
            self.handles[turn.id] = event.speech_handle
            self.handle_turns[id(event.speech_handle)] = turn
            if not self.manager.is_current(turn.id):
                event.speech_handle.interrupt(force=True)

    def turn_for_context(self, context):
        handle = getattr(context, "speech_handle", None)
        if handle is not None:
            return self.handle_turns.get(id(handle))
        return self.manager.current_turn

    def transcript(self, event):
        import contextlib

        text = str(getattr(event, "transcript", "") or "").strip()
        if not text or not getattr(event, "is_final", False):
            return
        if self.echo.is_echo(text):
            # The realtime model has already heard this input; dropping a
            # caption alone would still let it answer the echo.
            if getattr(self.session, "agent_state", None) != "speaking":
                with contextlib.suppress(Exception):
                    self.session.interrupt(force=True)
            return
        self.session._jarvis_wake_request_seen = True
        if self.manager.state is State.LOADING:
            self.manager.on_user_speech(text)
            return
        previous = self.manager.current_turn
        previous_state = self.manager.state
        action = self.manager.on_user_speech(text)
        superseding = (
            previous_state in {State.THINKING, State.SPEAKING}
            and previous is not None
            and not previous.completed
            and previous.id != getattr(action.turn, "id", None)
        )
        if action.kind == "cancel_all" or superseding:
            self.generation += 1
            generation = self.generation
            self.restarting = True
            drain = self.session.interrupt(force=True)

            async def restart():
                import inspect

                if inspect.isawaitable(drain):
                    await drain
                if generation != self.generation:
                    return
                self.restarting = False
                if action.turn is not None and self.manager.is_current(action.turn.id):
                    # Both corrections and independent replacements must
                    # restart after the native old generation drains.
                    await self.session.generate_reply(user_input=action.turn.text)

            self._spawn(restart())
        elif getattr(self.session, "_jarvis_greeting_active", False):
            self._emit("cancel", 0)
            with contextlib.suppress(Exception):
                self.session.interrupt(force=True)

    def agent_state(self, event):
        state = str(getattr(event, "new_state", ""))
        self.echo.speaking = state == "speaking"
        turn = self.manager.current_turn
        if (
            turn is None
            and state in {"speaking", "listening"}
            and self.manager.state is not State.LOADING
        ):
            self.manager._set_state(
                State.SPEAKING if state == "speaking" else State.LISTENING
            )
        if state == "speaking" and turn is not None and not self.restarting:
            self.manager.cancel_filler(turn)
            if self.filler_handle is not None:
                with contextlib.suppress(Exception):
                    self.filler_handle.interrupt(force=True)
                self.filler_handle = None
            self.manager.start_speaking(turn.id)
        elif state == "listening" and turn is not None:
            self.manager.finish_speaking(turn.id)
        elif state == "thinking" and turn is not None and not turn.cancelled:
            self.manager._set_state(State.THINKING)

    def ready(self):
        queued = self.manager.pop_boot_queue()
        self.manager.set_ready()
        if queued:
            self.generation += 1
            generation = self.generation
            self.restarting = True
            drain = self.session.interrupt(force=True)

            async def run_boot_queue():
                import inspect

                if inspect.isawaitable(drain):
                    await drain
                self.restarting = False
                # Serialized turns preserve recognized startup requests.
                # Stop/new live speech can cancel the active one normally.
                for text in queued:
                    if generation != self.generation:
                        break
                    action = self.manager.on_user_speech(text)
                    if action.turn is not None:
                        await self.session.generate_reply(user_input=action.turn.text)

            self._spawn(run_boot_queue())

    async def close(self):
        import asyncio

        self.manager.cancel_all()
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)


# Reads can be cancelled; unknown tools are conservatively mutations.
_READ_ONLY_TOOLS = frozenset(
    {
        "tell_time",
        "do_math",
        "get_weather",
        "weather",
        "search_mail",
        "read_mail",
        "list_mail",
        "read_email",
        "search_email",
        "get_email",
        "calendar_list",
        "list_events",
        "get_events",
        "get_volume",
        "goodnight_status",
        "latency_status",
        "get_system_status",
    }
)


def tool_has_side_effect(name: str) -> bool:
    return name not in _READ_ONLY_TOOLS
