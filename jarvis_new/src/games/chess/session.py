"""ChessGame glue for the durable store, series, history and move review.

``SessionMixin`` is mixed into ``ChessGame``; it owns no engine and never
blocks a voice turn.  Everything is lazy (no directory is created until the
first move is saved) and a failing disk never breaks play: store errors are
swallowed into ``game.log``.

Attributes created here (all optional for callers)::

    store          GameStore (lazy; root = $JARVIS_HOME/chess/games, or ``store_root``)
    series         SeriesController | None
    hist_view      HistoryView | None   (``history_view`` is a read-only alias)
    last_analysis  dict | None          (full text for the screen)
    games_list     list[dict]           (latest saved games, for the UI)
    user_speaking  bool | callable      (the voice layer sets it; default False)

Records: one ``GameRecord`` per game (``_rec``), reconciled with the board by
``_sync_record`` after every move / undo / redo, so a kill loses at most the
move in flight.  ``_finish_record`` is idempotent (a game id is finished once).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import Any

import chess

from games.chess import review, text_norm
from games.chess.engine import StockfishEngine
from games.chess.reply import Reply
from games.chess.series import SeriesController
from games.chess.store import GameStore

_TERM = {
    "checkmate": "mate",
    "stalemate": "stalemate",
    "resignation": "resign",
    "agreement": "draw",
}


def termination_of(outcome: str, reason: str) -> str:
    """Map a ``state.Result`` reason onto the store vocabulary."""
    r = (reason or "").lower()
    if r in _TERM:
        return _TERM[r]
    if r.startswith("timeout") and outcome != "1/2-1/2":
        return "timeout"
    return "draw" if outcome == "1/2-1/2" else (r or "mate")


def two_sentences(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return " ".join(parts[:2])


class SeriesProxy:
    """What TakeoverLoop sees: the controller plus ``speak_moves``."""

    def __init__(self, ctl: Any) -> None:
        self._c = ctl

    def __getattr__(self, name: str) -> Any:
        return getattr(self._c, name)

    def speak_moves(self) -> bool:
        s = self._c.state
        return bool(s and s.speak == "moves")


class SessionMixin:
    # ------------------------------------------------------------- set-up
    def _init_session(
        self,
        store: Any = None,
        store_root: Any = None,
        eval_engine_factory: Any = None,
        evaluate: bool | None = None,
    ) -> None:
        self._store = store
        self._store_root = store_root
        self._rec: Any = None
        self.series: Any = None
        self.hist_view: Any = None
        self.last_analysis: dict | None = None
        self.games_list: list[dict] = []
        self.user_speaking: Any = False
        self._takeover_loop: Any = None
        self._takeover_sleep = asyncio.sleep
        self._eval_factory = eval_engine_factory
        self._evaluate = evaluate
        self._evaluator: Any = None
        self._move_t0: float | None = None
        self._series_saved: tuple | None = None

    @property
    def store(self) -> GameStore:
        if self._store is None:
            self._store = GameStore(self._store_root)
        return self._store

    @store.setter
    def store(self, value: Any) -> None:
        self._store = value

    @property
    def history_view(self) -> Any:
        return self.hist_view

    @history_view.setter
    def history_view(self, value: Any) -> None:
        self.hist_view = value

    def _store_log(self, exc: BaseException) -> None:
        self.log.append(f"[store] {type(exc).__name__}: {exc}")

    def _persist_on(self) -> bool:
        return bool(self.autosave or self._series_running())

    # ----------------------------------------------------------- records
    def _new_record(self) -> Any:
        st = self.state
        user_white = st.human_colour == chess.WHITE
        kw: dict[str, Any] = {
            "mode": st.mode if st.mode in ("play", "takeover", "spectate") else "play",
            "white": "user" if user_white else "jarvis",
            "black": "jarvis" if user_white else "user",
        }
        s = self.series.state if self.series is not None else None
        if s is not None and s.status == "running" and st.mode == "takeover":
            kw["series_id"] = s.id
            kw["series_index"] = len(s.game_ids) + 1
        return self.store.new_record(**kw)

    def _sync_record(self, think_ms: int | None = None) -> None:
        """Make the saved record mirror the board (append / truncate / reopen)."""
        st = self.state
        if st.start_fen or not self._persist_on():
            return
        ucis = st.uci_history()
        try:
            rec = self._rec
            if rec is None:
                if not ucis:
                    return
                rec = self._rec = self._new_record()
            have = [m.get("uci") for m in rec.moves]
            k = 0
            while k < len(have) and k < len(ucis) and have[k] == ucis[k]:
                k += 1
            dirty = k < len(have)
            if dirty:
                del rec.moves[k:]
            if rec.status == "finished" and not st.is_over():
                rec.status, rec.result, rec.termination, rec.ended_at = (
                    "active", "*", None, None,
                )
                dirty = True
            new = ucis[k:]
            b = chess.Board()
            for u in ucis[:k]:
                b.push_uci(u)
            batch = []
            for i, u in enumerate(new):
                mv = chess.Move.from_uci(u)
                san = b.san(mv)
                b.push(mv)
                last = i == len(new) - 1
                batch.append({
                    "ply": k + i + 1, "san": san, "uci": u, "fen_after": b.fen(),
                    "eval_cp": None, "eval_mate": None, "depth": None,
                    "best_uci": None,
                    "think_ms": int(think_ms) if (last and think_ms is not None) else 0,
                    "cls": None, "annotation": None,
                })
            if len(batch) == 1 and not dirty:
                self.store.append_move(rec, batch[0])
            else:
                rec.moves.extend(batch)
                self.store.save(rec)
        except Exception as exc:  # disk trouble must never break the game
            self._store_log(exc)

    def _finish_record(self) -> None:
        """Idempotent: a game id is finished exactly once."""
        rec = self._rec
        res = self.state.result()
        if rec is None or res is None or rec.status == "finished":
            return
        try:
            self._sync_record()
            self.store.finish(rec, res.outcome, termination_of(res.outcome, res.reason))
            self.games_list = self.store.list(limit=20)
        except Exception as exc:
            self._store_log(exc)

    def _abandon_record(self, keep: Any = None) -> None:
        """A new game replaces this one: close it (and stale actives) as abandoned."""
        rec, self._rec = self._rec, None
        keep_id = getattr(keep, "id", None)
        try:
            if rec is not None and rec.status == "active" and rec.id != keep_id:
                self.store.finish(rec, "*", "abandoned")
            if not self._persist_on():
                return
            for it in self.store.list():
                if it["status"] == "active" and it["id"] not in (keep_id,):
                    old = self.store.load(it["id"])
                    if old is not None:
                        self.store.finish(old, "*", "abandoned")
        except Exception as exc:
            self._store_log(exc)

    # --------------------------------------------------------- evaluator
    def _eval_enabled(self) -> bool:
        if self._evaluate is not None:
            return bool(self._evaluate)
        if self._eval_factory is not None:
            return True
        return isinstance(self.engine, StockfishEngine)

    def _get_evaluator(self) -> Any:
        if self._evaluator is None:
            self._evaluator = review.MoveEvaluator(
                self._eval_factory or review.make_analysis_engine,
                self.store,
                on_done=self._on_eval,
            )
        return self._evaluator

    def _enqueue_eval(self, fen_before: str, move: chess.Move) -> None:
        """Queue the second engine; sync, never awaited by a voice turn."""
        rec = self._rec
        if rec is None or self._closed or not self.autosave or not self._eval_enabled():
            return
        try:
            self._get_evaluator().enqueue(
                rec.id, self.state.move_count, fen_before, self.state.fen(), move
            )
        except Exception as exc:
            self._store_log(exc)

    def _on_eval(self, gid: str, ply: int, fields: dict) -> None:
        rec = self._rec
        if rec is not None and rec.id == gid and 1 <= ply <= len(rec.moves):
            rec.moves[ply - 1].update(fields)  # keep memory == disk
        self._changed_soon()

    async def drain_evals(self, timeout: float = 5.0) -> None:
        ev = self._evaluator
        if ev is not None:
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(ev.drain(), timeout)

    async def close_session(self) -> None:
        ev, self._evaluator = self._evaluator, None
        if ev is not None:
            with contextlib.suppress(Exception):
                await ev.close()

    # ------------------------------------------------------ series driver
    def _series_running(self) -> bool:
        s = self.series.state if self.series is not None else None
        return bool(s and s.status == "running")

    def _series_proxy(self) -> Any:
        return SeriesProxy(self.series) if self._series_running() else None

    @staticmethod
    def _level_preset(level: int | None) -> str | None:
        return None if level is None else f"level:{max(1, min(10, int(level)))}"

    def _series_apply_settings(self) -> None:
        s = self.series.state
        if self._series_saved is None:
            self._series_saved = (self.settings.difficulty, self.settings.takeover_strength)
        if s.level is not None:
            self.settings.takeover_strength = "match"
            self.settings.difficulty = self._level_preset(s.level)

    def _series_restore_settings(self) -> None:
        if self._series_saved is not None:
            self.settings.difficulty, self.settings.takeover_strength = self._series_saved
            self._series_saved = None

    def _pause_series_for_release(self) -> None:
        if self._series_running():
            self.series.pause()

    def on_series_event(self, event: str, **kw: Any) -> None:
        """start / pause / resume / cancel / option -> drive TakeoverLoop."""
        ser = self.series
        if ser is None or ser.state is None:
            return
        if event == "start":
            self._series_apply_settings()
            self._fire(self._series_begin())
        elif event == "resume":
            self._series_apply_settings()
            self._series_go()
        elif event in ("pause", "cancel"):
            self._drop_takeover()
            self._series_restore_settings()
        elif event == "option":
            s = ser.state
            if s.colour in ("white", "black"):
                s.first_user_colour = s.colour
                ser._save()
            loop = self._takeover_loop
            if loop is not None:
                loop.level = self._level_preset(s.level)
                loop.step_delay = float(s.move_time or 0.0)
            if s.level is not None and self._series_saved is not None:
                self._series_apply_settings()
        self._changed_soon()

    def _detach_loop(self) -> None:
        """Cancel the running loop so its late clean-up can't touch new state."""
        loop = self._takeover_loop
        if loop is not None:
            loop.detach()
        t = self._auto_task
        if t and not t.done() and t is not asyncio.current_task():
            t.cancel()

    def _drop_takeover(self) -> None:
        """Leave takeover now (sync): game kept, series paused, no new game."""
        st = self.state
        self._detach_loop()
        if st.mode != "takeover":
            return
        self._pause_series_for_release()
        st.mode = "play"
        self.takeover_remaining = None
        self._finish_fast = False
        self.thinking = False
        self._series_restore_settings()
        self._changed_soon()
        if not st.is_over() and st.board.turn != st.human_colour:
            self._start_reply()

    async def _release_takeover(self, reason: str = "user") -> None:
        loop = self._takeover_loop
        if loop is not None:
            loop.release(reason)
        self._drop_takeover()
        await self._changed()

    async def _series_begin(self) -> None:
        """Start the next series game in takeover (colours from the series)."""
        s = self.series.state
        if s is None or s.status != "running":
            return
        nc = self.series.next_colours()["user"]
        await self.new_game(colour=nc, preset=self._level_preset(s.level))
        self.state.mode = "takeover"
        self.takeover_remaining = None
        self.pending = None
        await self._changed()
        self._start_auto()

    def _series_go(self) -> None:
        """Continue the series: the game in progress, or the next one."""
        st = self.state
        if st.mode == "takeover" and self._auto_task and not self._auto_task.done():
            return
        if st.move_count and not st.is_over():
            if self._reply_task and not self._reply_task.done():
                self._reply_task.cancel()
            st.mode = "takeover"
            self.takeover_remaining = None
            self.pending = None
            self._start_auto()
        else:
            self._fire(self._series_begin())

    async def wait_series(self, timeout: float = 10.0) -> None:
        """Wait for series bootstrap + takeover loop (tests / shutdown)."""
        for _ in range(50):
            fires = [t for t in list(self._fires) if not t.done()]
            if fires:
                await asyncio.wait(fires, timeout=timeout)
            t = self._auto_task
            if t and not t.done():
                await asyncio.wait([t], timeout=timeout)
                continue
            if not [x for x in list(self._fires) if not x.done()]:
                return

    async def series_wrap_up(self) -> str | None:
        """Spoken summary after the last game (built from saved games)."""
        ctl = self.series
        if ctl is None or ctl.state is None:
            return None
        await self.drain_evals(5.0)
        games = []
        for gid in ctl.state.game_ids:
            rec = self.store.load(gid)
            if rec is not None:
                games.append(rec.to_dict())
        summ = ctl.summary(games)
        order = {g["id"]: i + 1 for i, g in enumerate(games)}
        voice = f"Series complete, Sir: {summ['score']}."
        b = summ.get("biggest_blunder")
        if b and b.get("san"):
            voice += f" Your biggest blunder was {b['san']} in game {order.get(b.get('game_id'), 1)}."
        full = [voice]
        for km in summ.get("key_moments") or []:
            full.append(
                f"Game {order.get(km.get('game_id'), 1)}, move {((km.get('ply') or 1) + 1) // 2}: "
                f"{km.get('san')} swung the evaluation by {abs(int(km.get('swing', 0)))}."
            )
        self.last_analysis = {
            "kind": "series_summary", "text": voice, "full_text": " ".join(full),
            "summary": summ,
        }
        self.games_list = self.store.list(limit=20)
        self._series_restore_settings()
        return voice

    # ------------------------------------------------------- explain etc.
    def _has_saved_evals(self) -> bool:
        rec = self._rec
        return bool(
            rec and any(m.get("cp_loss") is not None or m.get("eval_cp") is not None
                        or m.get("eval_mate") is not None for m in rec.moves)
        )

    def _user_parity(self) -> int:
        return 1 if self.state.human_colour == chess.WHITE else 0

    async def explain_saved(self, n: int | None = None) -> Reply | None:
        """Explain a move from SAVED evals only; None -> legacy engine path."""
        rec = self._rec
        if rec is None or not rec.moves or not self._has_saved_evals():
            return None
        heard = text_norm.clean(self.heard)
        ply: int | None
        if n is None and ("mistake" in heard or "blunder" in heard):
            mine = [k for k in review.key_moments(rec, 200) if k["ply"] % 2 == self._user_parity()]
            if not mine:
                return Reply("I see no clear mistake of yours, Sir.", kind="explain")
            ply = mine[0]["ply"]
        else:
            ply = self._ply_to_explain(n, None)
        if ply is None or ply > len(rec.moves):
            return None
        facts = review.explain_facts(rec, ply)
        if not facts or facts.get("cls") is None:
            return None
        phrased = review.phrase_facts(facts)
        full = phrased["text"]
        voice = two_sentences(full)
        self.last_analysis = {
            "kind": "explain", "ply": ply, "text": voice, "full_text": full, "facts": facts,
        }
        self.badges[ply] = facts["cls"]
        self.panel = {
            "headline": f"Move {(ply + 1) // 2}", "detail": full, "source": "saved",
            "pinned": False, "ply": ply,
        }
        self._changed_soon()
        return Reply(voice, kind="explain", data={"ply": ply, "cls": facts["cls"]})

    async def analyse_board_now(self) -> Reply:
        rec = self._rec
        last = rec.moves[-1] if rec and rec.moves else None
        if last and (last.get("eval_cp") is not None or last.get("eval_mate") is not None):
            ev = {"cp": last.get("eval_cp"), "mate": last.get("eval_mate")}
            text, full = review.analyse_board(self.state.fen(), ev)
            self._set_board_analysis(text, full)
            return Reply(text, kind="analyse_board")
        self._spawn(self._analyse_board_task())
        return Reply(None, kind="analyse_board")

    def _set_board_analysis(self, voice: str, full: dict) -> None:
        detail = " ".join([voice] + list(full.get("statements") or [])[1:])
        self.last_analysis = {
            "kind": "board", "text": voice, "full_text": detail, "facts": full,
        }
        self.panel = {"headline": "Position", "detail": detail, "source": "engine", "pinned": False}
        self._changed_soon()

    async def _analyse_board_task(self) -> None:
        board = self.state.board.copy(stack=False)
        a = await self._analyse_cached(board, 12)
        voice, full = review.analyse_board(board.fen(), a.eval)
        self._set_board_analysis(voice, full)
        await self._say(voice)

    async def _post_game_task(self) -> None:
        """Three key moments from saved evals once the last evals landed."""
        await self.drain_evals(5.0)
        rec = self._rec
        if rec is None:
            return
        kms = review.key_moments(rec, 3)
        if not kms:
            return
        bits = [f"move {(k['ply'] + 1) // 2} {k['san']}, a {k['cls']}" for k in kms]
        text = "Key moments: " + "; ".join(bits) + "."
        self.last_analysis = {
            "kind": "key_moments", "text": text, "full_text": text,
            "moments": kms, "game_id": rec.id,
        }
        await self._changed()
        if not self._series_running():
            await self._say(text)

    # ------------------------------------------------------------ resume
    def _store_active(self) -> Any:
        try:
            return self.store.latest_active()
        except Exception as exc:
            self._store_log(exc)
            return None

    @staticmethod
    def data_from_record(rec: Any) -> dict:
        return {
            "start_fen": None,
            "moves": [m["uci"] for m in rec.moves],
            "human": "white" if rec.white == "user" else "black",
            "mode": "takeover" if rec.mode == "takeover" else "play",
        }

    def _series_offer(self) -> Any:
        """Unfinished series from disk, adopted (paused) and reconciled."""
        try:
            ctl = SeriesController(self.store)
            st = ctl.resume_offer()
            if st is None:
                return None
            # finished games the series never recorded (killed in between)
            for it in sorted(self.store.list(), key=lambda i: i["id"]):
                if it["series_id"] == st.id and it["status"] == "finished" \
                        and it["id"] not in st.game_ids:
                    ctl.record_result(
                        it["id"], it["result"], "white" if it["white"] == "user" else "black"
                    )
            if st.status == "running":
                ctl.pause()
            if ctl.state.status not in ("paused", "running"):
                return None
            self.series = ctl
            return ctl
        except Exception as exc:
            self._store_log(exc)
            return None

    def series_offer_text(self) -> str:
        s = self.series.state
        k = len(s.game_ids) + 1
        of = f" of {s.total}" if s.total else ""
        return f"You have an unfinished series, Sir, at game {k}{of}. Shall I resume it?"
