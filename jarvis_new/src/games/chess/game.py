"""Chess controller: one command API for voice, mouse and keyboard.

On-demand rule: nothing analyses, comments or calls the LLM unless asked, and
``stop_all`` cancels speech, engine analysis and HTTP at once.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import chess

from games.chess import commands, saves, templates, text_norm, voice_moves
from games.chess.analysis import AnalysisMixin
from games.chess.chat import Chat
from games.chess.commentary import (
    Cancelled,
    Commentary,
    OpenRouter,
)
from games.chess.engine import BaseEngine, make_engine
from games.chess.intents import IntentsMixin
from games.chess.reply import Reply
from games.chess.settings import ChessSettings
from games.chess.settings import load as load_settings
from games.chess.settings import save as save_settings
from games.chess.state import Clock, GameState


class ChessGame(IntentsMixin, AnalysisMixin):
    name = "chess"

    def __init__(
        self,
        settings: ChessSettings | None = None,
        *,
        engine: BaseEngine | None = None,
        commentary: Commentary | None = None,
        chat: Chat | None = None,
        save_dir=None,
        now: Callable[[], float] = time.monotonic,
        autosave: bool = True,
        ai_delay: float = 0.0,
        step_delay: float = 0.6,
        persist_settings: bool = False,
    ):
        self.settings = settings or load_settings()
        self._persist = persist_settings
        self.engine = engine
        router = OpenRouter(self.settings.models)
        self.commentary = commentary or Commentary(
            router, enabled=self.settings.online_explanations
        )
        self.chat = chat or Chat(
            self.commentary.router, enabled=self.settings.online_explanations
        )
        self.save_dir = save_dir
        self.now = now
        self.autosave = autosave
        self.ai_delay = ai_delay
        self.step_delay = step_delay
        self.state = GameState(now=now)
        # hooks (all optional)
        self.on_say: Callable[[str], Awaitable | None] | None = None
        self.on_stop_speech: Callable[[], None] | None = None
        self.on_change: Callable[[], Awaitable | None] | None = None
        self.on_quit: Callable[[], Awaitable | None] | None = None
        self.on_back_to_jarvis: Callable[[], Awaitable | None] | None = None
        self.on_clipboard: Callable[[str], None] | None = None
        # on-demand switches (all off for every new game)
        self.analysis_on = False
        self.commentary_on = False
        self.eval_on = False
        self.hints_on = False
        self.blunder_warn = False
        self.time_warn = False
        self.review_active = False
        # live display state
        self.eval_shown_fen: str | None = None
        self.last_eval: dict | None = None
        self.badges: dict[int, str] = {}
        self.panel: dict | None = None
        self.caption = ""
        self.heard = ""
        self.heard_squares: list[str] = []
        self.listening = False
        self.speaking = False
        self.mic_state = "off"
        self.jarvis_active = False
        self.banner: str | None = None
        self.paused = False
        self.pending: dict | None = None
        self.thinking = False
        self.arrow: dict | None = None
        self.highlights: list[str] = []
        self.flash: dict | None = None
        self.hint: dict | None = None
        self.review: dict | None = None
        self.spec_speed = 1.0
        self.spec_paused = False
        self.takeover_remaining: int | None = None
        self.flipped = False
        self.misses = 0
        self.legend_offered = False
        self.review_offered = False
        self.last_say = ""
        self.log: list[str] = []
        self._ctx = voice_moves.MoveContext()
        self._bg: set[asyncio.Task] = set()
        self._fires: set[asyncio.Task] = set()
        self._reply_task: asyncio.Task | None = None
        self._auto_task: asyncio.Task | None = None
        self._run_ev = asyncio.Event()
        self._run_ev.set()
        self._an_cache: dict[tuple[str, int], Any] = {}
        self._flash_id = 0
        self._over_announced = False
        self._step_event = asyncio.Event()
        self._closed = False
        self.speech_log: list[str] = []  # everything handed to Piper (tests)
        self.ui_mode = "classic"  # classic | voice (voice: display only)

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self.engine is None:
            self.engine = await make_engine()

    async def close(self) -> None:
        self._closed = True
        self.stop_all(quiet=True)
        for t in (self._reply_task, self._auto_task):
            if t:
                t.cancel()
        if self.engine:
            await self.engine.close()

    @property
    def preset(self) -> str:
        return self.settings.difficulty

    async def new_game(
        self,
        *,
        colour: str | None = None,
        preset: str | None = None,
        time_control: str | None = None,
        fen: str | None = None,
        moves: list[str] | None = None,
        keep_analysis: bool | None = None,
    ) -> None:
        await self.start()
        kept = (
            self.analysis_on,
            self.commentary_on,
            self.eval_on,
            self.hints_on,
            self.blunder_warn,
            self.time_warn,
        )
        self._cancel_game_tasks()
        if colour is not None:
            self.settings.colour = colour
        if preset is not None:
            self.settings.difficulty = preset
        if time_control is not None:
            self.settings.time_control = time_control
        col = self.settings.colour
        if col == "random":
            col = random.choice(["white", "black"])
        human = chess.WHITE if col == "white" else chess.BLACK
        self.state = GameState(
            start_fen=fen,
            human_colour=human,
            clock=Clock.from_spec(self.settings.time_control),
            now=self.now,
        )
        for u in moves or []:
            self.state.board.push(chess.Move.from_uci(u))
        keep = (
            self.settings.remember_analysis if keep_analysis is None else keep_analysis
        )
        if keep:
            (
                self.analysis_on,
                self.commentary_on,
                self.eval_on,
                self.hints_on,
                self.blunder_warn,
                self.time_warn,
            ) = kept
        else:
            self._reset_passive()
        self._reset_view()
        self.flipped = not human
        self._changed_soon()
        if not self.state.is_over() and self.state.board.turn != human:
            self._start_reply()

    def _reset_passive(self) -> None:
        self.analysis_on = self.commentary_on = self.eval_on = False
        self.hints_on = self.blunder_warn = self.time_warn = False

    def _reset_view(self) -> None:
        self.eval_shown_fen = None
        self.last_eval = None
        self.badges.clear()
        self.panel = None
        self.caption = ""
        self.pending = None
        self.hint = None
        self.review = None
        self.review_active = False
        self.highlights = []
        self.paused = False
        self.takeover_remaining = None
        self.spec_paused = False
        self.thinking = False
        self.misses = 0
        self._ctx = voice_moves.MoveContext()
        self._over_announced = False
        self.review_offered = False
        self._an_cache.clear()
        self._run_ev.set()

    def _cancel_game_tasks(self) -> None:
        for t in (self._reply_task, self._auto_task):
            if t and not t.done():
                t.cancel()
        self._reply_task = self._auto_task = None
        self.state.mode = "play"
        self.stop_all(quiet=True)

    # ---------------------------------------------------------------- output
    async def _say(self, text: str | None) -> None:
        if not text:
            return
        self.last_say = text
        self.log.append(text)
        if self.settings.commentary_muted:
            return
        if not (self.listening or self.settings.speak_explanations):
            return  # Classic Mode without Listen: text on screen only
        self.speech_log.append(text)
        if self.on_say:
            r = self.on_say(text)
            if inspect.isawaitable(r):
                await r

    def _spawn(self, coro: Awaitable) -> asyncio.Task:
        async def guarded() -> None:
            # a stop aborted it: its reply must never be spoken
            with contextlib.suppress(Cancelled):
                await coro

        t = asyncio.ensure_future(guarded())
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        return t

    def _changed_soon(self) -> None:
        if self.on_change:
            r = self.on_change()
            if inspect.isawaitable(r):
                self._fire(r)

    def _fire(self, coro: Awaitable) -> None:
        """Run a fire-and-forget hook (publish, quit) that a stop must not cancel."""
        t = asyncio.ensure_future(coro)
        self._fires.add(t)
        t.add_done_callback(self._fires.discard)

    async def _changed(self) -> None:
        if self.on_change:
            r = self.on_change()
            if inspect.isawaitable(r):
                await r

    def _flash(self, squares: list[str], kind: str = "illegal") -> None:
        self._flash_id += 1
        self.flash = {"id": self._flash_id, "squares": squares, "kind": kind}

    # -------------------------------------------------------------- snapshot
    def status(self) -> str:
        if self.state.is_over():
            return "GAME OVER"
        if self.jarvis_active:
            return "JARVIS ACTIVE"
        if self.paused:
            return "PAUSED"
        if self.state.mode == "spectate":
            return "SPECTATING"
        if self.state.mode == "takeover":
            return "TAKE-OVER"
        asked = self.analysis_on or self.commentary_on or self.eval_on or self.hints_on
        if (
            asked
            and not self.thinking
            and self.state.board.turn == self.state.human_colour
        ):
            return "ANALYSIS ON"
        if self.thinking:
            return "JARVIS THINKING"
        return "YOUR MOVE"

    def snapshot(self) -> dict[str, Any]:
        s = self.state.snapshot()
        fen = self.state.fen()
        show_eval = self.eval_on or self.eval_shown_fen == fen
        disamb = []
        if self.pending and self.pending["type"] == "disambiguate":
            for i, m in enumerate(voice_moves.numbered(self.pending["moves"]), start=1):
                disamb.append(
                    {
                        "label": i,
                        "from": chess.square_name(m.from_square),
                        "to": chess.square_name(m.to_square),
                    }
                )
        s.update(
            {
                "game": "chess",
                "ui_mode": self.ui_mode,
                "status": self.status(),
                "analysis_on": any(
                    [self.analysis_on, self.commentary_on, self.eval_on, self.hints_on]
                ),
                "flags": {
                    "analysis": self.analysis_on,
                    "commentary": self.commentary_on,
                    "eval": self.eval_on,
                    "hints": self.hints_on,
                    "blunders": self.blunder_warn,
                    "time": self.time_warn,
                },
                "eval": self.last_eval if show_eval else None,
                "badges": dict(self.badges),
                "caption": self.caption,
                "heard": self.heard,
                "heard_squares": list(self.heard_squares),
                "listening": self.listening,
                "speaking": self.speaking,
                "mic_state": self.mic_state,
                "jarvis_active": self.jarvis_active,
                "banner": self.banner,
                "paused": self.paused,
                "pending": None
                if not self.pending
                else {k: v for k, v in self.pending.items() if k != "moves"},
                "disambiguation": disamb,
                "thinking": self.thinking,
                "arrow": self.arrow,
                "highlights": list(self.highlights),
                "flash": self.flash,
                "hint": self.hint,
                "panel": self.panel,
                "review": self.review,
                "spectate": {"speed": self.spec_speed, "paused": self.spec_paused},
                "takeover_remaining": self.takeover_remaining,
                "flipped": self.flipped,
                "settings": self.settings.to_dict(),
                "engine": self.engine.name if self.engine else None,
                "ai_left": self.commentary.left_today(),
                "ai_online": bool(self.commentary.router.key)
                and self.settings.online_explanations,
                "last_say": self.last_say,
                "preset": self.settings.difficulty,
                "saves": saves.list_saves(self.save_dir),
            }
        )
        return s

    # ----------------------------------------------------------- stop / wake
    def stop_all(self, quiet: bool = False) -> None:
        """Cut speech, engine analysis and HTTP; switch off continuous modes."""
        if self.on_stop_speech:
            self.on_stop_speech()
        self.commentary.cancel()
        if self.engine:
            self.engine.cancel()
        for t in list(self._bg):
            t.cancel()
        self._bg.clear()
        self.analysis_on = self.commentary_on = self.eval_on = False
        self.hints_on = self.blunder_warn = self.time_warn = False
        self.review_active = False
        self.eval_shown_fen = None
        self.speaking = False
        if not quiet:
            self._changed_soon()

    def jarvis_wake(self) -> None:
        """ "Hey Jarvis" fired: go quiet, freeze the clock, show the banner."""
        self.stop_all(quiet=True)
        self.jarvis_active = True
        self.mic_state = "paused"
        self.banner = "JARVIS ACTIVE — chess listening paused"
        if self.settings.paused_clock_during_jarvis:
            self.state.pause_clock()
        self._run_ev.clear()
        self.pending = None
        self._changed_soon()

    def jarvis_done(self) -> None:
        self.jarvis_active = False
        self.banner = None
        self.mic_state = "listening" if self.listening else "off"
        if not self.paused:
            self.state.resume_clock()
            self._run_ev.set()
        self._changed_soon()

    # ------------------------------------------------------------ move flow
    def _is_human_turn(self) -> bool:
        return (
            self.state.mode == "play"
            and self.state.board.turn == self.state.human_colour
        )

    async def play_human(self, move: chess.Move) -> Reply:
        st = self.state
        if st.is_over():
            return Reply("The game is over, Sir.", ok=False, kind="over")
        if st.mode == "spectate":
            return Reply("You are spectating, Sir. Say 'my turn' to join in.", ok=False)
        if st.mode == "takeover":
            return Reply(
                "I have the board, Sir. Say 'my turn' to take it back.", ok=False
            )
        if self.paused or self.jarvis_active:
            return Reply("The game is paused, Sir.", ok=False)
        if st.board.turn != st.human_colour:
            return Reply("One moment, Sir, it isn't your move yet.", ok=False)
        board_before = st.board.copy(stack=False)
        if move not in st.board.legal_moves:
            return Reply(
                "I'm afraid that isn't a legal move, Sir.", ok=False, kind="illegal"
            )
        await self._apply(move, "human")
        say = None
        if self.settings.confirm_moves:
            say = templates.confirm_line(board_before, move)
        return Reply(say, kind="move", data={"uci": move.uci()})

    async def _apply(self, move: chess.Move, who: str) -> str:
        st = self.state
        san = st.push(move)
        self.hint = None
        self.pending = None
        self.arrow = None
        self.thinking = False
        self._ctx = voice_moves.MoveContext(
            last_to=move.to_square,
            last_capture_sq=move.to_square
            if st.board.peek() and self._was_capture(move)
            else self._ctx.last_capture_sq,
        )
        self._last_who = who
        self.caption = ""
        self._autosave()
        res = st.result()
        await self._changed()
        if res:
            await self._on_over()
            return san
        if who == "human" and st.mode == "play":
            self._start_reply()
        await self._features_after(move, san, who)
        return san

    def _was_capture(self, move: chess.Move) -> bool:
        b = self.state.board
        b.pop()
        try:
            return b.is_capture(move)
        finally:
            b.push(move)

    def _autosave(self) -> None:
        if not self.autosave:
            return
        try:
            data = self.state.to_dict()
            data["preset"] = self.settings.difficulty
            saves.write(data, None, self.save_dir)
        except OSError:
            pass

    def _start_reply(self) -> None:
        if self._reply_task and not self._reply_task.done():
            return
        self._reply_task = asyncio.ensure_future(self._ai_reply())

    async def _wait_runnable(self) -> None:
        while not self._run_ev.is_set():
            await self._run_ev.wait()

    async def _ai_reply(self) -> None:
        st = self.state
        try:
            while not st.is_over():
                if st.mode != "play" or st.board.turn == st.human_colour:
                    return
                fen = st.fen()
                self.thinking = True
                await self._changed()
                await self._wait_runnable()
                assert self.engine
                move = await self.engine.best_move(st.board, self.preset)
                if self.ai_delay:
                    await asyncio.sleep(self.ai_delay)
                await self._wait_runnable()
                if st.fen() != fen or st.mode != "play":
                    self.thinking = False
                    return
                board_before = st.board.copy(stack=False)
                await self._apply(move, "ai")
                if (
                    self.settings.announce_moves and not st.is_over()
                ) or self.settings.announce_moves:
                    await self._say(templates.announce_move(board_before, move))
                return
        except asyncio.CancelledError:
            self.thinking = False
            raise
        finally:
            self.thinking = False

    # -------------------------------------------------------------- game over
    async def _on_over(self) -> None:
        if self._over_announced:
            return
        self._over_announced = True
        res = self.state.result()
        if not res:
            return
        self.paused = False
        self.state.clock.running = False
        line = templates.result_line(
            res.outcome, res.reason, self.state.human_colour == chess.WHITE
        )
        await self._say(line)
        if not self.review_offered and self.state.move_count >= 4:
            self.review_offered = True
            self.pending = {"type": "review_offer"}
            await self._say("Shall I review the game, Sir?")
        await self._changed()

    async def check_clock(self) -> None:
        """Called by the ticker: flags falling end the game."""
        st = self.state
        if st.clock.timed and not st.is_over():
            if st.result():
                await self._on_over()
        elif st.is_over():
            await self._on_over()

    # ================================================================ input
    async def handle_text(self, text: str, *, source: str = "voice") -> Reply:
        """One utterance -> reply, following the A.13 intent priority."""
        raw = text.strip()
        if source == "voice":
            self.heard = raw
        if not raw:
            return Reply(ok=False, kind="empty")
        # 1. wake phrase belongs to Jarvis: pause, discard the utterance
        if text_norm.is_wake_phrase(raw):
            self.jarvis_wake()
            return Reply(ok=False, kind="wake")
        if self.jarvis_active:
            return Reply(ok=False, kind="deaf")
        # 2. stop always wins and never goes near the LLM
        if commands.is_stop(raw):
            return await self._stop_cmd()
        narrow = commands.match_narrow_stop(raw)
        if narrow:
            return await self.run_intent(narrow)
        # 3. an answer to a pending question (even while paused: "save before
        # we stop?" is asked after pausing)
        if self.pending:
            out = await self._answer_pending(raw)
            if out is not None:
                return out
        # paused / deaf corner: only resume, quit and stop get through
        if self.paused:
            hit = commands.classify(raw, {"exit"})
            if hit and hit.name in (
                "resume_game",
                "quit",
                "quit_offer",
                "back_to_jarvis",
            ):
                return await self.run_intent(hit)
            return Reply(ok=False, kind="paused")
        st = self.state
        # 4. moves
        if (
            not commands.looks_like_question(raw)
            and not st.is_over()
            and st.mode == "play"
        ):
            pr = voice_moves.parse_move(raw, st.board, self._ctx)
            if pr.kind != "none" and pr.confidence >= 0.5:
                return await self._on_parse(pr, raw)
        elif st.mode in ("takeover", "spectate") and not commands.looks_like_question(
            raw
        ):
            pr = voice_moves.parse_move(raw, st.board, self._ctx)
            if pr.kind == "move" and pr.confidence >= 0.5:
                return Reply(
                    "I have the board, Sir. Say 'my turn' to take it back.",
                    ok=False,
                    kind="busy",
                )
        # 5. everything else in catalogue order
        hit = commands.classify(raw)
        if hit:
            if hit.name == "undo" and raw_is_take_back(raw) and not st.is_over():
                pass
            return await self.run_intent(hit)
        # 6. "take back" with no recapture available means undo
        if raw_is_take_back(raw):
            return await self.run_intent(
                commands.Intent("undo", {"n": 1, "via": "take back"})
            )
        # 7. unparsed: a question goes to chat, otherwise one short miss line
        if commands.looks_like_question(raw) or len(raw.split()) >= 5:
            return await self._chat(raw)
        return await self._miss()

    async def _miss(self) -> Reply:
        self.misses += 1
        if self.misses >= 3 and not self.legend_offered:
            self.legend_offered = True
            self.settings.square_labels = True
            self._changed_soon()
            return Reply(templates.NATO_LEGEND, ok=False, kind="legend")
        return Reply(templates.DIDNT_CATCH, ok=False, kind="miss")

    async def _on_parse(self, pr: voice_moves.ParseResult, raw: str) -> Reply:
        st = self.state
        if pr.kind == "move" and pr.move:
            self.misses = 0
            before = st.board.copy(stack=False)
            self.caption = f"“{raw}” → {pr.caption}"
            self.heard_squares = [
                chess.square_name(pr.move.from_square),
                chess.square_name(pr.move.to_square),
            ]
            self.arrow = {
                "from": chess.square_name(pr.move.from_square),
                "to": chess.square_name(pr.move.to_square),
                "id": self._flash_id + 1,
            }
            self._flash_id += 1
            if (
                self.settings.confirm_moves
                and pr.confidence >= 0.8
                and not self.pending
            ):
                self.pending = {"type": "confirm_move", "moves": [pr.move]}
                return Reply(
                    f"{templates.announce_move(before, pr.move)[:-1]}, Sir?",
                    kind="confirm",
                )
            rep = await self.play_human(pr.move)
            if not rep.ok:
                return rep
            extras = []
            if pr.promotion_defaulted:
                extras.append("Promoted to a queen, Sir.")
            elif pr.confidence < 0.8:
                extras.append(templates.confirm_line(before, pr.move))
            if extras:
                rep.say = " ".join(extras)
            return rep
        if pr.kind == "ambiguous":
            self.misses = 0
            moves = voice_moves.numbered(pr.moves)
            if pr.confidence < 0.5:
                return await self._miss()
            self.pending = {"type": "disambiguate", "moves": moves}
            self.heard_squares = sorted(
                {chess.square_name(m.from_square) for m in moves}
                | {chess.square_name(m.to_square) for m in moves}
            )
            await self._changed()
            return Reply(
                self._castle_or_pick_question(moves),
                kind="disambiguate",
                data={"count": len(moves)},
            )
        if pr.kind in ("illegal", "castle_illegal"):
            if pr.spec and pr.spec.to_sq is not None:
                path = [chess.square_name(pr.spec.to_sq)]
                if pr.spec.from_sq is not None:
                    path.insert(0, chess.square_name(pr.spec.from_sq))
                self._flash(path, "illegal")
            self._changed_soon()
            return Reply(pr.reason, ok=False, kind="illegal")
        return await self._miss()

    def _castle_or_pick_question(self, moves: list[chess.Move]) -> str:
        b = self.state.board
        if all(b.is_castling(m) for m in moves):
            return "Castle kingside or queenside, Sir?"
        if len(moves) > 4 or len({m.from_square for m in moves}) > 3:
            return "Which piece did you mean, Sir? Name the square it stands on."
        return templates.disambiguation_question(b, moves)

    async def _answer_pending(self, raw: str) -> Reply | None:
        p = self.pending
        assert p
        typ = p["type"]
        if typ == "disambiguate":
            moves: list[chess.Move] = p["moves"]
            b = self.state.board
            if all(b.is_castling(m) for m in moves):
                t = text_norm.strip_wrappers(raw)
                pick = None
                if t in (
                    "kingside",
                    "king side",
                    "short",
                    "castle kingside",
                    "castle short",
                ):
                    pick = "k"
                elif t in (
                    "queenside",
                    "queen side",
                    "long",
                    "castle queenside",
                    "castle long",
                ):
                    pick = "q"
                if t in ("never mind", "cancel", "nevermind"):
                    self.pending = None
                    await self._changed()
                    return Reply("Very well, Sir.", kind="cancel")
                if pick:
                    for m in moves:
                        if chess.square_file(m.to_square) == (6 if pick == "k" else 2):
                            self.pending = None
                            return await self.play_human(m)
                return None
            ans = voice_moves.resolve_choice(raw, moves, b)
            if ans == "cancel":
                self.pending = None
                await self._changed()
                return Reply("Very well, Sir.", kind="cancel")
            if isinstance(ans, chess.Move):
                self.pending = None
                return await self.play_human(ans)
            # a fresh, complete move overrides the question
            return None
        if typ == "confirm_move":
            hit = commands.classify(raw, {"answer"})
            if hit and hit.name == "yes":
                m = p["moves"][0]
                self.pending = None
                return await self.play_human(m)
            if hit and hit.name == "no":
                self.pending = None
                await self._changed()
                return Reply("Very well, Sir.", kind="cancel")
            return None
        hit = commands.classify(raw, {"answer", "review"})
        if typ == "review_offer":
            if hit and hit.name in ("yes",):
                self.pending = None
                return await self.run_intent(commands.Intent("review", {}))
            if hit and hit.name in ("no", "review_skip"):
                self.pending = None
                return Reply("Very well, Sir.", kind="decline")
            hit2 = commands.classify(raw, {"game"})
            if hit2 and hit2.name == "review":
                self.pending = None
                return await self.run_intent(hit2)
            return None
        if typ in (
            "confirm_resign",
            "confirm_delete",
            "confirm_quit",
            "save_quit",
            "draw_offer",
        ):
            yes = hit and hit.name == "yes"
            no = hit and hit.name in ("no",)
            if typ == "draw_offer":
                return None
            if yes:
                self.pending = None
                return await self._do_confirmed(typ, p)
            if no:
                self.pending = None
                if typ == "save_quit":
                    return await self._do_confirmed("quit_nosave", p)
                await self._changed()
                return Reply("Very well, Sir.", kind="decline")
            return None
        return None

    async def _do_confirmed(self, typ: str, p: dict) -> Reply:
        if typ == "confirm_resign":
            return await self._resign()
        if typ == "confirm_delete":
            ok = saves.delete(p["name"], self.save_dir)
            return Reply(
                f"Deleted {p['name']}, Sir."
                if ok
                else "I couldn't find that save, Sir.",
                ok=ok,
            )
        if typ in ("confirm_quit", "quit_nosave"):
            return await self._quit()
        if typ == "save_quit":
            self._save_named("autosave-quit")
            return await self._quit(saved=True)
        return Reply()

    # ================================================================ intents
    async def command(self, cmd: str, /, **args: Any) -> dict[str, Any]:
        """Structured entry used by mouse, keyboard and the websocket."""
        rep = await self._command(cmd, args)
        return rep.as_dict()

    async def _command(self, name: str, args: dict[str, Any]) -> Reply:
        if name == "move":
            if "uci" in args:
                uci = args["uci"]
            else:
                uci = args["from"] + args["to"] + (args.get("promotion") or "")
            try:
                mv = chess.Move.from_uci(uci)
            except ValueError:
                return Reply("That isn't a move, Sir.", ok=False)
            b = self.state.board
            if mv not in b.legal_moves and not args.get("promotion"):
                promo = chess.Move(mv.from_square, mv.to_square, chess.QUEEN)
                if promo in b.legal_moves:
                    mv = promo
            if mv not in b.legal_moves:
                self._flash([uci[:2], uci[2:4]], "illegal")
                self._changed_soon()
                return Reply(
                    "I'm afraid that isn't a legal move, Sir.", ok=False, kind="illegal"
                )
            return await self.play_human(mv)
        if name == "say":
            return await self.handle_text(
                args.get("text", ""), source=args.get("source", "text")
            )
        if name == "stop":
            return await self._stop_cmd()
        if name == "set":
            return self._set_settings(args)
        if name == "heard":
            self.heard = args.get("text", "")
            self.heard_squares = list(args.get("squares", []))
            self._changed_soon()
            return Reply()
        if name == "legal_from":
            sq = chess.parse_square(args["square"])
            dests = [
                chess.square_name(m.to_square)
                for m in self.state.board.legal_moves
                if m.from_square == sq
            ]
            return Reply(kind="legal", data={"targets": sorted(set(dests))})
        if name == "new":
            await self.new_game(
                colour=args.get("colour"),
                preset=args.get("preset") or args.get("level"),
                time_control=args.get("time_control"),
                fen=args.get("fen"),
            )
            return Reply(kind="new")
        if name == "import_pgn_text":
            return await self._import_pgn_text(args.get("pgn", ""))
        if name == "flip_set":
            self.flipped = bool(args.get("flipped"))
            self._changed_soon()
            return Reply()
        return await self.run_intent(commands.Intent(_ALIAS.get(name, name), args))

    async def _stop_cmd(self) -> Reply:
        was_busy = bool(
            self._bg
            or self.speaking
            or self.analysis_on
            or self.commentary_on
            or self.eval_on
            or self.hints_on
            or self.review_active
        )
        self.stop_all()
        if self.pending:  # "stop" / "never mind" also abandons a pending question
            self.pending = None
            was_busy = True
        self.panel = (
            None if self.panel and self.panel.get("pinned") is False else self.panel
        )
        await self._changed()
        say = (
            "Very good, Sir."
            if self.settings.stop_ack == "spoken" and was_busy
            else None
        )
        return Reply(say, kind="stop", data={"was_busy": was_busy})

    def _set_settings(self, args: dict[str, Any]) -> Reply:
        changed = self.settings.update(**args)
        if "difficulty" in args and "difficulty" in changed:
            pass
        if self._persist:
            save_settings(self.settings)
        self.commentary.enabled = self.settings.online_explanations
        self.chat.enabled = self.settings.online_explanations
        self._changed_soon()
        return Reply(kind="settings", data={"changed": changed})

    async def run_intent(self, it: commands.Intent) -> Reply:
        fn = getattr(self, f"_i_{it.name}", None)
        if fn is None:
            return Reply(
                "I'm afraid I can't do that yet, Sir.", ok=False, kind="unknown"
            )
        out = fn(**_filter_args(fn, it.args))
        if inspect.isawaitable(out):
            out = await out
        return out

    # ----- exit / pause --------------------------------------------------
    async def _quit(self, saved: bool = False) -> Reply:
        self._autosave()
        self.stop_all(quiet=True)
        if self.on_quit:
            r = self.on_quit()
            if inspect.isawaitable(r):
                self._fire(r)
        return Reply("Very good, Sir. Until next time.", kind="quit")

    async def _i_quit(self) -> Reply:
        return await self._quit()

    async def _i_quit_offer(self) -> Reply:
        self.paused = True
        self.state.pause_clock()
        self.pending = {"type": "save_quit"}
        await self._changed()
        return Reply("Shall I save the game before we stop, Sir?", kind="offer")

    async def _i_pause_game(self) -> Reply:
        self.paused = True
        self.state.pause_clock()
        self._run_ev.clear()
        await self._changed()
        return Reply(
            "Game paused, Sir. Say 'resume the game' when ready.", kind="pause"
        )

    async def _i_resume_game(self) -> Reply:
        self.paused = False
        if not self.jarvis_active:
            self.state.resume_clock()
            self._run_ev.set()
        await self._changed()
        return Reply("Resuming, Sir.", kind="resume")

    async def _i_back_to_jarvis(self) -> Reply:
        self._autosave()
        if self.on_back_to_jarvis:
            r = self.on_back_to_jarvis()
            if inspect.isawaitable(r):
                self._fire(r)
        return Reply("Very good, Sir. Handing you back to Jarvis.", kind="back")

    # ----- context controls ---------------------------------------------
    def _ctl_target(self) -> str:
        if self.review_active or (self.review and self.review.get("active")):
            return "review"
        if self.state.mode == "spectate":
            return "spectate"
        return "game"

    async def _i_ctl_pause(self) -> Reply:
        t = self._ctl_target()
        if t == "spectate":
            self.spec_paused = True
            await self._changed()
            return Reply(None, kind="spec")
        return await self._i_pause_game()

    async def _i_ctl_resume(self) -> Reply:
        t = self._ctl_target()
        if t == "spectate":
            self.spec_paused = False
            self._step_event.set()
            await self._changed()
            return Reply(None, kind="spec")
        if self.paused:
            return await self._i_resume_game()
        return Reply(ok=False, kind="ignored")

    async def _i_ctl_next(self) -> Reply:
        t = self._ctl_target()
        if t == "review":
            return await self._i_review_next()
        if t == "spectate":
            self.spec_paused = True
            self._step_event.set()
            return Reply(None, kind="spec")
        return Reply(ok=False, kind="ignored")

    async def _i_ctl_prev(self) -> Reply:
        t = self._ctl_target()
        if t == "review":
            return await self._i_review_prev()
        if t == "spectate":
            self.state.undo(1)
            self.spec_paused = True
            await self._changed()
            return Reply(None, kind="spec")
        return await self._i_undo(n=1)

    async def _i_ctl_faster(self) -> Reply:
        self.spec_speed = min(4.0, self.spec_speed * 2)
        await self._changed()
        return Reply(None, kind="spec")

    async def _i_ctl_slower(self) -> Reply:
        self.spec_speed = max(0.5, self.spec_speed / 2)
        await self._changed()
        return Reply(None, kind="spec")

    async def _i_ctl_normal(self) -> Reply:
        self.spec_speed = 1.0
        await self._changed()
        return Reply(None, kind="spec")

    async def _i_ctl_half(self) -> Reply:
        self.spec_speed = 0.5
        await self._changed()
        return Reply(None, kind="spec")

    async def _i_stop_spectating(self) -> Reply:
        self.spec_paused = True
        await self._changed()
        return Reply("Very good, Sir.", kind="spec")

    async def _i_stop_eval(self) -> Reply:
        self.eval_on = False
        self.eval_shown_fen = None
        await self._changed()
        return Reply(self._ack(), kind="narrow_stop")

    async def _i_stop_hints(self) -> Reply:
        self.hints_on = False
        self.hint = None
        await self._changed()
        return Reply(self._ack(), kind="narrow_stop")

    async def _i_stop_commentary(self) -> Reply:
        self.commentary_on = False
        self.analysis_on = False
        self.commentary.cancel()
        if self.on_stop_speech:
            self.on_stop_speech()
        await self._changed()
        return Reply(self._ack(), kind="narrow_stop")

    async def _i_stop_review(self) -> Reply:
        self.review_active = False
        if self.review:
            self.review["active"] = False
        if self.on_stop_speech:
            self.on_stop_speech()
        await self._changed()
        return Reply(self._ack(), kind="narrow_stop")

    async def _i_stop_announce(self) -> Reply:
        self.settings.announce_moves = False
        await self._changed()
        return Reply(self._ack(), kind="narrow_stop")

    async def _i_stop_time_warn(self) -> Reply:
        self.time_warn = False
        return Reply(self._ack(), kind="narrow_stop")

    async def _i_stop_confirm(self) -> Reply:
        self.settings.confirm_moves = False
        return Reply(self._ack(), kind="narrow_stop")

    def _ack(self) -> str | None:
        return "Very good, Sir." if self.settings.stop_ack == "spoken" else None

    async def _i_yes(self) -> Reply:
        return Reply(ok=False, kind="ignored")

    async def _i_no(self) -> Reply:
        return Reply(ok=False, kind="ignored")


_ALIAS = {
    "analyze": "analyse",
    "takeback": "undo",
    "take_over": "takeover",
    "hand_back": "handback",
    "flip_board": "flip",
    "new_game_dialog": "new_game",
    "export": "export_pgn",
    "show_labels": "labels",
    "resume": "resume_game",
    "pause": "pause_game",
    "continuous_on": "continuous",
}


def raw_is_take_back(raw: str) -> bool:
    t = text_norm.strip_wrappers(raw)
    return t in {"take back", "takes back"}


def _filter_args(fn: Callable, args: dict) -> dict:
    sig = inspect.signature(fn)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return dict(args)
    return {k: v for k, v in args.items() if k in sig.parameters}
