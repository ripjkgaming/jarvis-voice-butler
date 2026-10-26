"""Game-management, take-over, save and spectator intents for ChessGame."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import chess

from games.chess import intents_level, saves, templates, text_norm, voice_moves
from games.chess.reply import AMBIGUOUS_RESIGN, PRESETS, Reply
from games.chess.settings import PRESET_LEVEL
from games.chess.state import Clock, GameState


class IntentsMixin:
    # ---------------------------------------------------------------- game mgmt
    async def drain(self, timeout: float = 10.0) -> None:
        """Wait for background analysis / speech tasks (tests, shutdown)."""
        tasks = [t for t in list(self._bg) if not t.done()]
        for t in (self._reply_task,):
            if t and not t.done():
                tasks.append(t)
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)

    async def _i_start(self) -> Reply:
        if self.state.move_count and not self.state.is_over():
            return Reply(
                "We're already in a game, Sir. Say 'new game' to start afresh.",
                kind="info",
            )
        await self.new_game()
        return Reply("Very good, Sir. The board awaits.", kind="new")

    async def _i_new_game(self) -> Reply:
        if self.state.move_count and not self.state.is_over():
            self._autosave()
            saves.write(self.state.to_dict(), "previous", self.save_dir)
        await self.new_game()
        colour = "white" if self.state.human_colour == chess.WHITE else "black"
        return Reply(f"A new game, Sir. You have {colour}.", kind="new")

    async def _i_colour(self, colour: str) -> Reply:
        self.settings.colour = colour
        if not self.state.move_count:
            await self.new_game(colour=colour)
            c = "white" if self.state.human_colour == chess.WHITE else "black"
            return Reply(f"You'll play {c}, Sir.", kind="colour")
        return Reply(f"Noted, Sir. You'll be {colour} in the next game.", kind="colour")

    async def _i_difficulty(
        self, level: str | None = None, elo: int | None = None
    ) -> Reply:
        if elo:
            self.settings.difficulty = f"elo:{elo}"
            await self._changed()
            return Reply(
                f"Playing at about {templates.number_words(elo)}, Sir.",
                kind="difficulty",
            )
        if level not in PRESETS:
            return Reply("I'm afraid I don't know that level, Sir.", ok=False)
        return await self._i_set_level(PRESET_LEVEL[level])

    async def _i_difficulty_adjust(self, direction: int) -> Reply:
        return await self._i_level_adjust(direction)

    async def _i_set_level(self, n: int) -> Reply:
        return await intents_level._i_set_level(self, n)

    async def _i_level_adjust(self, direction: int | str) -> Reply:
        if isinstance(direction, str):
            direction = -1 if direction.lower() in ("down", "-1", "easier") else 1
        return await intents_level._i_level_adjust(self, direction)

    async def _i_level_query(self) -> Reply:
        return await intents_level._i_level_query(self)

    async def _i_time_control(self, spec: str) -> Reply:
        self.settings.time_control = spec
        if not self.state.move_count:
            self.state.clock = Clock.from_spec(spec)
        await self._changed()
        label = "untimed" if spec == "untimed" else spec.replace("+", " plus ")
        return Reply(
            f"Very good, Sir. {label.capitalize()} from the next game.", kind="time"
        )

    async def _i_time_bullet(self) -> Reply:
        return Reply(
            "Bullet is rather quick for voice, Sir. May I suggest blitz?",
            ok=False,
            kind="time",
        )

    async def _i_add_time(self) -> Reply:
        if not self.state.clock.timed:
            return Reply("There's no clock running, Sir.", ok=False)
        self.state.add_time(60)
        await self._changed()
        return Reply("A minute added, Sir.", kind="time")

    async def _i_pause_clock(self) -> Reply:
        self.state.pause_clock()
        await self._changed()
        return Reply("Clock paused, Sir.", kind="time")

    async def _i_resume_clock(self) -> Reply:
        self.state.resume_clock()
        await self._changed()
        return Reply("Clock running, Sir.", kind="time")

    async def _undo_plies(self, n: int) -> tuple[int, str]:
        st = self.state
        if self._reply_task and not self._reply_task.done():
            self._reply_task.cancel()
        self.thinking = False
        done = 0
        for _ in range(max(1, n)):
            if not st.board.move_stack:
                break
            popped = 0
            while st.board.move_stack:
                st.undo(1)
                popped += 1
                if st.board.turn == st.human_colour or st.mode != "play":
                    break
            done += popped
        self.hint = None
        self.pending = None
        self.badges = {k: v for k, v in self.badges.items() if k <= st.move_count}
        self._over_announced = False
        self._ctx = voice_moves.MoveContext()
        self._autosave()
        self._sync_record()
        await self._changed()
        if not st.is_over() and st.mode == "play" and st.board.turn != st.human_colour:
            self._start_reply()
        return done, "ok"

    async def _i_undo(self, n: int = 1, via: str | None = None) -> Reply:
        if not self.state.move_count:
            return Reply("There's nothing to undo, Sir.", ok=False)
        if self.state.mode != "play":
            done = self.state.undo(n)
            self._sync_record()
            await self._changed()
            return Reply(None, kind="undo")
        done, _ = await self._undo_plies(n)
        if not done:
            return Reply("There's nothing to undo, Sir.", ok=False)
        if via == "take back":
            return Reply("Taking back your last move, Sir.", kind="undo")
        return Reply("Undone, Sir.", kind="undo")

    async def _i_redo(self) -> Reply:
        st = self.state
        if not st.redo_stack:
            return Reply("There's nothing to redo, Sir.", ok=False)
        while st.redo_stack:
            st.redo(1)
            if st.board.turn == st.human_colour:
                break
        self._autosave()
        self._sync_record()
        await self._changed()
        return Reply("Restored, Sir.", kind="redo")

    async def _i_flip(self) -> Reply:
        self.flipped = not self.flipped
        await self._changed()
        return Reply(None, kind="flip")

    async def _i_zoom(self, direction: int) -> Reply:
        v = self.settings.board_size + 0.1 * direction
        self.settings.board_size = round(max(0.6, min(1.6, v)), 2)
        await self._changed()
        return Reply(None, kind="zoom")

    async def _i_fullscreen(self, on: bool) -> Reply:
        self._ui_event("fullscreen", on=on)
        return Reply(None, kind="ui")

    def _ui_event(self, kind: str, **kw: Any) -> None:
        self._flash_id += 1
        self.flash = {
            "id": self._flash_id,
            "squares": [],
            "kind": "ui",
            "ui": {"cmd": kind, **kw},
        }
        self._changed_soon()

    async def _i_labels(self, on: bool | None = None) -> Reply:
        self.settings.square_labels = (
            (not self.settings.square_labels) if on is None else on
        )
        await self._changed()
        return Reply(None, kind="labels")

    async def _i_animations(self, reduce: bool) -> Reply:
        self.settings.reduce_animations = reduce
        await self._changed()
        return Reply("Very good, Sir.", kind="ui")

    async def _i_legal_moves(self) -> Reply:
        b = self.state.board
        dests = {chess.square_name(m.to_square) for m in b.legal_moves}
        self.highlights = sorted(dests)
        await self._changed()
        return Reply(
            f"You have {templates.number_words(b.legal_moves.count())} legal moves, Sir.",
            kind="info",
        )

    async def _i_clear_highlights(self) -> Reply:
        self.highlights = []
        self.hint = None
        await self._changed()
        return Reply(None, kind="ui")

    async def _resign(self) -> Reply:
        self.state.resign(self.state.human_colour)
        self._autosave()
        await self._changed()
        await self._on_over()
        return Reply(kind="resign")

    async def _i_resign(self, via: str | None = None) -> Reply:
        if self.state.is_over():
            return Reply("A pleasure, Sir.", kind="info")
        t = (via or self.last_heard_norm()).strip()
        if t in AMBIGUOUS_RESIGN:
            self.pending = {"type": "confirm_resign"}
            await self._changed()
            return Reply("Do you wish to resign, Sir?", kind="confirm")
        return await self._resign()

    def last_heard_norm(self) -> str:
        return text_norm.strip_wrappers(self.heard)

    async def _i_offer_draw(self) -> Reply:
        if self.state.is_over():
            return Reply(ok=False)
        claim = self.state.claim_draw()
        if claim:
            await self._changed()
            await self._on_over()
            return Reply(kind="draw")
        assert self.engine
        a = await self.engine.analyse(self.state.board, depth=10)
        ai = not self.state.human_colour
        cp = a.eval.for_colour(ai)
        if cp <= 30:
            self.state.agree_draw()
            self._autosave()
            await self._changed()
            await self._on_over()
            return Reply("I accept, Sir. A draw.", kind="draw")
        return Reply(
            "I'm afraid I must decline, Sir. I rather like my position.", kind="draw"
        )

    async def _i_accept(self) -> Reply:
        return Reply(ok=False, kind="ignored")

    async def _i_decline(self) -> Reply:
        return Reply(ok=False, kind="ignored")

    async def _i_claim_draw(self) -> Reply:
        r = self.state.claim_draw()
        if not r:
            return Reply("There's no draw to claim just now, Sir.", ok=False)
        self._autosave()
        await self._changed()
        await self._on_over()
        return Reply(kind="draw")

    # ------------------------------------------------------------------- saves
    def _save_named(self, name: str) -> str:
        data = self.state.to_dict()
        data["preset"] = self.settings.difficulty
        saves.write(data, name, self.save_dir)
        return saves.safe_name(name)

    async def _i_save(self, name: str | None = None) -> Reply:
        nm = name or time.strftime("game-%Y%m%d-%H%M%S")
        n = self._save_named(nm)
        await self._changed()
        return Reply(
            f"Saved as {n.replace('-', ' ')}, Sir.", kind="save", data={"name": n}
        )

    async def _i_load(self, name: str | None = None, last: bool = False) -> Reply:
        data = None
        if name:
            data = saves.read(name, self.save_dir)
        else:
            nm = saves.last_save_name(self.save_dir)
            data = saves.read(nm, self.save_dir) if nm else None
            if data is None:
                data = saves.read(None, self.save_dir)
        if not data:
            return Reply("I couldn't find that game, Sir.", ok=False)
        await self.restore(data)
        return Reply("Game loaded, Sir.", kind="load")

    async def restore(self, data: dict) -> None:
        self._cancel_game_tasks()
        self._rec = None
        self.state = GameState.from_dict(data, now=self.now)
        if data.get("preset"):
            self.settings.difficulty = data["preset"]
        self._reset_view()
        self.flipped = self.state.human_colour == chess.BLACK
        await self._changed()
        if (
            not self.state.is_over()
            and self.state.mode == "play"
            and self.state.board.turn != self.state.human_colour
        ):
            self._start_reply()
        elif not self.state.is_over() and self.state.mode == "takeover":
            self._start_auto()

    async def resume_last(self) -> bool:
        """Resume the last game (store record wins over the legacy autosave)
        and offer an unfinished series ('yes' resumes at game k)."""
        data = saves.read(None, self.save_dir)
        rec = self._store_active()
        if rec is not None and rec.moves:
            if not data or data.get("moves") != [m["uci"] for m in rec.moves]:
                data = self.data_from_record(rec)
            data.setdefault("preset", None)
        else:
            rec = None
        offer = self._series_offer()
        if not data and offer is None:
            return False
        await self.start()
        if data:
            if offer is not None:
                data = {**data, "mode": "play"}  # the series waits for a 'yes'
            await self.restore(data)
            self._rec = rec
        if offer is not None:
            self.pending = {"type": "series_resume"}
            await self._say(self.series_offer_text())
            await self._changed()
        return True

    async def _i_list_saves(self) -> Reply:
        names = saves.list_saves(self.save_dir)
        if not names:
            return Reply("You have no saved games, Sir.", kind="info")
        say = (
            "You have "
            + templates.number_words(len(names))
            + " saved game"
            + ("s" if len(names) != 1 else "")
            + ": "
            + ", ".join(n.replace("-", " ") for n in names[:5])
            + "."
        )
        return Reply(say, kind="info", data={"saves": names})

    async def _i_export_pgn(self) -> Reply:
        pgn = saves.export_pgn(
            self.state.uci_history(),
            self.state.start_fen,
            {
                "Event": "Jarvis Chess",
                "Site": "Local",
                "White": "Sir" if self.state.human_colour else "Jarvis",
                "Black": "Jarvis" if self.state.human_colour else "Sir",
            },
        )
        if self.on_clipboard:
            self.on_clipboard(pgn)
        return Reply("PGN copied, Sir.", kind="pgn", data={"pgn": pgn})

    async def _import_pgn_text(self, text: str) -> Reply:
        try:
            moves, start, headers = saves.import_pgn(text)
        except ValueError as exc:
            return Reply(f"I couldn't read that PGN, Sir: {exc}.", ok=False)
        await self.new_game(fen=start, moves=moves)
        self.state.headers = {k: str(v) for k, v in headers.items()}
        self._autosave()
        return Reply("PGN imported, Sir.", kind="pgn")

    async def _i_import_pgn(self) -> Reply:
        getter = getattr(self, "get_clipboard", None)
        if getter is None:
            return Reply(
                "PGN import is for Classic Mode, Sir. Paste it in the import dialogue.",
                ok=False,
            )
        return await self._import_pgn_text(getter())

    async def _i_delete_save(self, name: str, confirmed: bool = False) -> Reply:
        if confirmed:  # the UI's Delete button is its own confirmation
            return await self._do_confirmed("confirm_delete", {"name": name})
        self.pending = {"type": "confirm_delete", "name": name}
        await self._changed()
        return Reply(f"Delete the save called {name}, Sir?", kind="confirm")

    # ------------------------------------------------------------ take-over etc.
    def _start_auto(self) -> None:
        if self._auto_task and not self._auto_task.done():
            return
        self._auto_task = self._spawn(self._auto_entry())

    async def _auto_entry(self) -> None:
        if self.state.mode != "takeover":
            return await self._auto_loop()
        from .takeover import GameAdapter, TakeoverLoop

        series = self._series_proxy()
        scoped = self.takeover_remaining is not None or bool(getattr(self, "_finish_fast", False))
        cont = bool(series) or (
            bool(getattr(self.settings, "takeover_continuous", True)) and not scoped
        )
        s = series.state if series else None
        loop = TakeoverLoop(
            GameAdapter(self, continuous=cont, quiet=bool(series)),
            store=self.store if self._persist_on() else None,
            series=series,
            continuous=cont,
            level=self._level_preset(s.level) if s else None,
            max_moves=self.takeover_remaining,
            step_delay=(
                float(s.move_time or 0.0)
                if s
                else min(self.step_delay, 0.02)
                if getattr(self, "_finish_fast", False)
                else self.step_delay
            ),
            sleep=self._takeover_sleep,
        )
        self._takeover_loop = loop
        try:
            await loop.run()
        finally:
            if self._takeover_loop is loop:
                self._takeover_loop = None

    async def _auto_loop(self) -> None:
        st = self.state
        fast = getattr(self, "_finish_fast", False)
        from .takeover import ErrorBreaker

        breaker = ErrorBreaker()
        try:
            while not st.is_over() and st.mode in ("takeover", "spectate"):
                await self._wait_runnable()
                if (
                    st.mode == "spectate"
                    and self.spec_paused
                    and not self._step_event.is_set()
                ):
                    await asyncio.sleep(0.03)
                    continue
                self._step_event.clear()
                if breaker.tripped:
                    await self._handback("The engine keeps failing, Sir.")
                    return
                mode = st.mode
                human_side = st.board.turn == st.human_colour
                preset = self.preset
                if mode == "takeover" and human_side:
                    lvl = self.settings.takeover_strength
                    preset = self.preset if lvl in ("match", None) else lvl
                fen = st.fen()
                assert self.engine
                self.thinking = True
                await self._changed()
                try:
                    mv = await self.engine.best_move(st.board, preset)
                except Exception as exc:
                    self.thinking = False
                    breaker.fail(exc)
                    await asyncio.sleep(0.5)
                    continue
                breaker.ok()
                self.thinking = False
                await self._wait_runnable()
                if st.fen() != fen or st.mode != mode:
                    await asyncio.sleep(0.01)  # no busy-spin
                    continue
                before = st.board.copy(stack=False)
                await self._apply(mv, "auto")
                if self.settings.announce_moves:
                    await self._say(templates.announce_move(before, mv))
                if (
                    mode == "takeover"
                    and human_side
                    and self.takeover_remaining is not None
                ):
                    self.takeover_remaining -= 1
                    if self.takeover_remaining <= 0:
                        await self._handback("That's my moves done, Sir. Back to you.")
                        return
                delay = (
                    (self.step_delay / max(0.1, self.spec_speed))
                    if mode == "spectate"
                    else self.step_delay
                )
                if fast:
                    delay = min(delay, 0.02)
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self.thinking = False
            raise
        finally:
            self.thinking = False

    async def _handback(self, say: str | None = "Back to you, Sir.") -> None:
        if asyncio.current_task() is not self._auto_task:
            self._detach_loop()
        if self.state.mode == "takeover":
            self._pause_series_for_release()
            self._series_restore_settings()
        self.state.mode = "play"
        self.takeover_remaining = None
        self._finish_fast = False
        self.spec_paused = False
        await self._changed()
        if say:
            await self._say(say)
        if (
            not self.state.is_over()
            and self.state.board.turn != self.state.human_colour
        ):
            self._start_reply()

    async def _i_takeover(
        self, n: int | None = None, level: str | None = None, kind: str | None = None
    ) -> Reply:
        st = self.state
        if st.is_over():
            return Reply("The game is over, Sir.", ok=False)
        if self._reply_task and not self._reply_task.done():
            self._reply_task.cancel()
        if level and level != "match":
            self.settings.takeover_strength = level
        elif level == "match":
            self.settings.takeover_strength = "match"
        st.mode = "takeover"
        self.takeover_remaining = n
        self._finish_fast = False
        self.pending = None
        await self._changed()
        self._start_auto()
        return Reply(
            "Very good, Sir. I have the board."
            if not n
            else f"Very good, Sir. I'll play {templates.number_words(n)} move{'s' if n != 1 else ''}.",
            kind="takeover",
        )

    async def _i_takeover_finish(self) -> Reply:
        rep = await self._i_takeover()
        self._finish_fast = True
        rep.say = "Finishing the game, Sir."
        return rep

    async def _i_handback(self) -> Reply:
        if self.state.mode == "play":
            return Reply("It's already your move to play, Sir.", ok=False, kind="info")
        await self._handback(None)
        return Reply("The board is yours, Sir.", kind="handback")

    async def _i_spectate(self) -> Reply:
        if self.state.move_count <= 2 or self.state.is_over():
            await self.new_game()
        st = self.state  # new_game replaces the state object
        if self._reply_task and not self._reply_task.done():
            self._reply_task.cancel()
        st.mode = "spectate"
        self.spec_paused = False
        await self._changed()
        self._start_auto()
        return Reply("Very good, Sir. I shall play myself.", kind="spectate")
