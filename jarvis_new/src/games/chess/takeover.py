"""Take-over loop: Jarvis plays the user's side, game after game, until released.

Stdlib only.  ``TakeoverLoop`` is engine/board agnostic; it talks to the game
through an *adapter* (duck-typed, see below) so it can be tested with fakes.

Adapter protocol (sync unless marked async)::

    is_user_side() -> bool (optional)             # does this move count for max_moves
    async best_move(fen, strength) -> uci str     # may raise (engine failure)
    async apply_engine_move(uci, think_ms) -> san # plays it on the board
    is_over() -> bool
    result() -> (result "1-0"|"0-1"|"1/2-1/2", termination str)
    record() -> game record | None                # handed to store.finish
    fen() -> str
    strength() -> str | None
    user_colour() -> "white" | "black"
    async new_game(user_colour, level)            # fresh game, still in takeover
    async say_result(text); async say_move(san)   # speech (held while user talks)
    user_is_speaking() -> bool
    stop_flag -> bool (attribute/property)        # True when something else left takeover
    async leave_takeover(reason, say)             # mode -> play; NEVER starts a game

Optional injected objects (duck-typed): ``series`` (record_result(game_id,
result, user_colour), next_colours(), is_done(), pause(), score_text()) and
``store`` (finish(rec, result, termination)).

Release reasons: stop, release, user, series_complete, breaker, game_over,
moves_done.  Releasing never starts a new game; a series is paused unless it
completed.  Three consecutive engine errors trip ``ErrorBreaker``: the loop
stops with a spoken reason and never retries a failing engine in a hot loop.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

RESULT_LINES = {
    "1-0": "White wins.",
    "0-1": "Black wins.",
    "1/2-1/2": "A draw.",
}


class ErrorBreaker:
    """Trips after ``limit`` consecutive failures; a success resets it."""

    def __init__(self, limit: int = 3) -> None:
        self.limit = limit
        self.count = 0
        self.last: BaseException | None = None

    def ok(self) -> None:
        self.count = 0

    def fail(self, exc: BaseException | None = None) -> bool:
        """Record a failure; True when the breaker has tripped."""
        self.count += 1
        self.last = exc
        return self.tripped

    @property
    def tripped(self) -> bool:
        return self.count >= self.limit


def _colour_of(x: Any) -> str | None:
    if isinstance(x, (tuple, list)) and x:
        x = x[0]
    if isinstance(x, dict):
        x = x.get("user") or x.get("user_colour")
    return x if x in ("white", "black") else None


class TakeoverLoop:
    def __init__(
        self,
        adapter: Any,
        store: Any = None,
        series: Any = None,
        breaker: ErrorBreaker | None = None,
        *,
        continuous: bool = False,
        level: Any = None,
        max_moves: int | None = None,
        step_delay: float = 0.0,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        error_delay: float = 0.5,
    ) -> None:
        self.a = adapter
        self.store = store
        self.series = series
        self.breaker = breaker or ErrorBreaker()
        self.continuous = continuous
        self.level = level
        self.max_moves = max_moves
        self.step_delay = step_delay
        self._sleep = sleep
        self._now = monotonic
        self.error_delay = error_delay
        self.reason: str | None = None
        self.games_played = 0
        self.moves_played = 0

    # ------------------------------------------------------------ control
    def release(self, reason: str = "release") -> None:
        """Ask the loop to leave takeover (no new game).  Idempotent."""
        if self.reason is None:
            self.reason = reason

    @property
    def stopped(self) -> bool:
        return self.reason is not None or bool(getattr(self.a, "stop_flag", False))

    async def _hold_for_user(self) -> None:
        # never talk over the user; poll, but never hot-spin (sleep yields)
        while not self.stopped and self.a.user_is_speaking():
            await self._sleep(0.1)

    async def _say(self, fn_name: str, text: str) -> None:
        await self._hold_for_user()
        if not self.stopped:
            await getattr(self.a, fn_name)(text)

    # -------------------------------------------------------------- run
    async def run(self) -> str:
        try:
            while not self.stopped:
                if self.a.is_over():
                    if not await self._game_ended():
                        break
                    continue
                await self._one_move()
            if self.reason is None:
                self.reason = "stop"
        except asyncio.CancelledError:
            self.reason = self.reason or "cancelled"
            await self._leave(None)
            raise
        await self._leave(self._farewell())
        return self.reason

    async def _game_ended(self) -> bool:
        """Save + score the finished game.  True -> a new game was started."""
        result, term = self.a.result()
        rec = self.a.record()
        user = self.a.user_colour()
        self.games_played += 1
        if self.store is not None and rec is not None:
            self.store.finish(rec, result, term)
        if self.series is not None:
            gid = getattr(rec, "id", None) or (
                rec.get("id") if isinstance(rec, dict) else None
            )
            self.series.record_result(gid, result, user)
        await self._say("say_result", RESULT_LINES.get(result, "Game over."))
        if self.series is not None:
            if self.series.is_done():
                self.release("series_complete")
                return False
        elif not self.continuous:
            self.release("game_over")
            return False
        if self.stopped:
            return False
        if self.series is not None:
            nxt = _colour_of(self.series.next_colours())
        else:
            nxt = None
        nxt = nxt or ("black" if user == "white" else "white")
        await self.a.new_game(nxt, self.level)
        return True

    async def _one_move(self) -> None:
        await self._hold_for_user()
        if self.stopped:
            return
        fen = self.a.fen()
        t0 = self._now()
        try:
            uci = await self.a.best_move(fen, self.a.strength())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # engine failure -> breaker, never a hot loop
            if self.breaker.fail(exc):
                self.release("breaker")
            else:
                await self._sleep(self.error_delay)
            return
        self.breaker.ok()
        if self.stopped or self.a.fen() != fen:
            return  # position changed under us: re-plan (after a yield)
        think_ms = int((self._now() - t0) * 1000)
        counts = getattr(self.a, "is_user_side", lambda: True)()
        try:
            san = await self.a.apply_engine_move(uci, think_ms)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.breaker.fail(exc):
                self.release("breaker")
            else:
                await self._sleep(self.error_delay)
            return
        self.moves_played += 1 if counts else 0
        if (
            self.series is not None
            and getattr(self.series, "speak_moves", lambda: False)()
            and not self.a.is_over()
        ):
            await self._say("say_move", san)
        if self.max_moves is not None and self.moves_played >= self.max_moves:
            self.release("moves_done")
            return
        if not self.a.is_over():
            await self._sleep(self.step_delay)

    # ------------------------------------------------------------ leaving
    def _farewell(self) -> str | None:
        return {
            "stop": "Back to you, Sir.",
            "release": "Back to you, Sir.",
            "user": "Back to you, Sir.",
            "game_over": None,
            "series_complete": None,
            "moves_done": "That's my moves done, Sir. Back to you.",
            "breaker": "The engine keeps failing, Sir, so I'm handing the board back.",
        }.get(self.reason or "", None)

    async def _leave(self, say: str | None) -> None:
        if self.series is not None and self.reason in (
            "stop",
            "release",
            "user",
            "breaker",
            "cancelled",
        ):
            try:
                self.series.pause()
            except Exception:
                pass
        await self.a.leave_takeover(self.reason, say)


class GameAdapter:
    """Adapter over a ChessGame (``g``); keeps the glue in intents.py tiny."""

    def __init__(self, g: Any, continuous: bool = False) -> None:
        self.g = g
        self.continuous = continuous
        self.record_obj: Any = None

    @property
    def stop_flag(self) -> bool:
        return self.g.state.mode != "takeover" or getattr(self.g, "_closed", False)

    def fen(self) -> str:
        return self.g.state.fen()

    def is_over(self) -> bool:
        return self.g.state.is_over()

    def result(self) -> tuple[str, str]:
        r = self.g.state.result()
        return (r.outcome, r.reason) if r else ("*", "abandoned")

    def record(self) -> Any:
        return self.record_obj

    def user_colour(self) -> str:
        import chess

        return "white" if self.g.state.human_colour == chess.WHITE else "black"

    def strength(self) -> str | None:
        g = self.g
        human_side = g.state.board.turn == g.state.human_colour
        lvl = g.settings.takeover_strength
        return g.preset if (not human_side or lvl in ("match", None)) else lvl

    def is_user_side(self) -> bool:
        return self.g.state.board.turn == self.g.state.human_colour

    def user_is_speaking(self) -> bool:
        f = getattr(self.g, "user_speaking", None)
        return bool(f() if callable(f) else f)

    async def best_move(self, fen: str, strength: str | None) -> str:
        g = self.g
        g.thinking = True
        await g._changed()
        try:
            mv = await g.engine.best_move(g.state.board, strength or g.preset)
        finally:
            g.thinking = False
        await g._wait_runnable()
        return mv.uci()

    async def apply_engine_move(self, uci: str, think_ms: int) -> str:
        import chess

        g = self.g
        mv = chess.Move.from_uci(uci)
        before = g.state.board.copy(stack=False)
        if self.continuous:
            g._over_announced = True  # loop speaks the one-line result itself
        san = await g._apply(mv, "auto")
        if g.settings.announce_moves and not g.state.is_over():
            from . import templates

            await g._say(templates.announce_move(before, mv))
        return san

    async def new_game(self, user_colour: str, level: Any) -> None:
        g = self.g
        me = asyncio.current_task()
        # g.new_game cancels _auto_task/_bg; we are one of them -> shield ourselves
        g._auto_task = None
        was_bg = me in g._bg
        g._bg.discard(me)
        try:
            await g.new_game(colour=user_colour, preset=level)
        finally:
            g._auto_task = me
            if was_bg:
                g._bg.add(me)
        g.state.mode = "takeover"
        g._over_announced = False

    async def say_result(self, text: str) -> None:
        await self.g._say(text)

    async def say_move(self, san: str) -> None:
        await self.g._say(san)

    async def leave_takeover(self, reason: str | None, say: str | None) -> None:
        g = self.g
        g.thinking = False
        if g.state.mode != "takeover":
            return  # someone else (new game, handback) already left cleanly
        g.state.mode = "play"
        g.takeover_remaining = None
        g._finish_fast = False
        if reason != "cancelled":
            await g._changed()
            if say:
                await g._say(say)
        if (
            not getattr(g, "_closed", False)
            and not g.state.is_over()
            and g.state.board.turn != g.state.human_colour
        ):
            g._start_reply()
