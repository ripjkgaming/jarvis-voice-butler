"""On-demand analysis, explanations, hints, review, info and settings intents.

Everything here runs only when asked (or while a "keep ..." mode is on) and
all of it is cancelled by ``ChessGame.stop_all``.
"""

from __future__ import annotations

import math
from typing import Any

import chess

from games.chess import templates, text_norm
from games.chess.commentary import Cancelled, build_facts, classify
from games.chess.engine import Analysis
from games.chess.reply import Reply
from games.chess.voice_moves import piece_name


def _win_pct(cp: int) -> float:
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * max(-1500, min(1500, cp)))) - 1)


class AnalysisMixin:
    # ------------------------------------------------------------- helpers
    def _board_at(self, ply: int) -> chess.Board:
        st = self.state
        b = chess.Board(st.start_fen) if st.start_fen else chess.Board()
        for mv in st.board.move_stack[:ply]:
            b.push(mv)
        return b

    async def _analyse_cached(self, board: chess.Board, depth: int = 12) -> Analysis:
        key = (board.fen(), depth)
        if key in self._an_cache:
            return self._an_cache[key]
        assert self.engine
        a = await self.engine.analyse(board, depth)
        self._an_cache[key] = a
        return a

    def _eval_dict(self, a: Analysis, board: chess.Board) -> dict[str, Any]:
        d = a.eval.as_dict()
        d["best"] = (
            board.san(a.best) if a.best and a.best in board.legal_moves else None
        )
        d["depth"] = a.depth
        return d

    async def _facts_for_ply(self, ply: int, depth: int = 12) -> dict | None:
        st = self.state
        if ply < 1 or ply > st.move_count:
            return None
        before = self._board_at(ply - 1)
        move = st.board.move_stack[ply - 1]
        a0 = await self._analyse_cached(before, depth)
        after = before.copy()
        after.push(move)
        a1 = await self._analyse_cached(after, depth)
        mover = before.turn
        cp0, cp1 = a0.eval.for_colour(mover), a1.eval.for_colour(mover)
        loss = 0 if move == a0.best else max(0, cp0 - cp1)
        mate_flag = (cp0 >= 9000 and cp1 < 9000) or (cp1 <= -9000 < cp0)
        label = classify(loss, mate_flag)
        mat = {"white": 0, "black": 0}
        vals = {
            chess.PAWN: 1,
            chess.KNIGHT: 3,
            chess.BISHOP: 3,
            chess.ROOK: 5,
            chess.QUEEN: 9,
        }
        for col, key in ((chess.WHITE, "white"), (chess.BLACK, "black")):
            mat[key] = sum(v * len(after.pieces(p, col)) for p, v in vals.items())
        n = len(after.piece_map())
        phase = (
            "opening" if ply < 20 and n > 28 else "endgame" if n <= 14 else "middlegame"
        )
        return build_facts(
            fen_before=before.fen(),
            move=move,
            eval_before=a0.eval.as_dict(),
            eval_after=a1.eval.as_dict(),
            cp_loss=int(loss),
            classification=label,
            best=a0.best,
            pv=a0.pv,
            preset=self.settings.audience,
            phase=phase,
            material=mat,
        )

    def _human_spoken(self) -> bool:
        return bool(self.listening or self.settings.speak_explanations)

    # --------------------------------------------------------- analyse once
    async def _i_analyse(self) -> Reply:
        self._spawn(self._analyse_task())
        return Reply("Let me look, Sir.", kind="analyse")

    async def _analyse_task(self) -> None:
        st = self.state
        board = st.board.copy(stack=False)
        a = await self._analyse_cached(board, 14)
        self.last_eval = self._eval_dict(a, board)
        self.eval_shown_fen = board.fen()
        await self._changed()
        human_white = st.human_colour == chess.WHITE
        line = templates.eval_phrase(a.eval.cp, a.eval.mate, human_white)
        if a.best and not board.is_game_over() and board.turn == st.human_colour:
            line += (
                " I'd suggest "
                + templates.announce_move(board, a.best)[:-1].lower()
                + "."
            )
        self.panel = {
            "headline": "Position",
            "detail": line,
            "source": "engine",
            "pinned": False,
        }
        await self._changed()
        await self._say(line)

    async def _i_threats(self) -> Reply:
        self._spawn(self._threats_task())
        return Reply(None, kind="threats")

    async def _threats_task(self) -> None:
        st = self.state
        board = st.board.copy(stack=False)
        if board.is_check():
            await self._say("You are in check, Sir. Deal with that first.")
            return
        if board.turn != st.human_colour:
            await self._say("It is my move, Sir.")
            return
        null = board.copy(stack=False)
        null.push(chess.Move.null())
        a = await self._analyse_cached(null, 10)
        if not a.best:
            await self._say("I see no immediate threats, Sir.")
            return
        cp = a.eval.for_colour(not board.turn)
        if cp > 60 and (null.is_capture(a.best) or a.eval.mate):
            await self._say(
                "I'd watch for "
                + templates.announce_move(null, a.best)[:-1].lower()
                + ", Sir."
            )
        else:
            await self._say("I see no immediate threats, Sir.")

    async def _i_plans(self) -> Reply:
        self._spawn(self._plans_task())
        return Reply(None, kind="plans")

    async def _plans_task(self) -> None:
        board = self.state.board.copy(stack=False)
        a = await self._analyse_cached(board, 12)
        if not a.best or board.is_game_over():
            await self._say("There is little left to plan, Sir.")
            return
        idea = templates.idea_for(board, a.best)
        await self._say(
            f"A sound plan: {templates.announce_move(board, a.best)[:-1].lower()}, since {idea}."
        )

    # ------------------------------------------------------------- explain
    def _ply_to_explain(self, n: int | None, piece: str | None) -> int | None:
        st = self.state
        total = st.move_count
        if not total:
            return None
        heard = text_norm.clean(self.heard)
        who_human_ply = lambda p: (p % 2 == 1) == (st.human_colour == chess.WHITE)  # noqa: E731
        if n is not None:
            ply = 2 * (n - 1) + (1 if st.human_colour == chess.WHITE else 2)
            return ply if 1 <= ply <= total else None
        if piece:
            for ply in range(total, 0, -1):
                mv = st.board.move_stack[ply - 1]
                before = self._board_at(ply - 1)
                p = before.piece_at(mv.from_square)
                if p and chess.piece_name(p.piece_type) == piece:
                    return ply
            return None
        if "my move" in heard or "my last" in heard:
            for ply in range(total, 0, -1):
                if who_human_ply(ply):
                    return ply
        if "your move" in heard:
            for ply in range(total, 0, -1):
                if not who_human_ply(ply):
                    return ply
        return total

    async def _i_explain(self, n: int | None = None, which: str | None = None) -> Reply:
        ply = self._ply_to_explain(n, None)
        if ply is None:
            return Reply("There's nothing to explain yet, Sir.", ok=False)
        self._spawn(self._explain_task(ply))
        return Reply("Let me think that through, Sir.", kind="explain")

    async def _i_explain_piece(self, piece: str) -> Reply:
        ply = self._ply_to_explain(None, piece)
        if ply is None:
            return Reply(f"I can't find a {piece} move, Sir.", ok=False)
        self._spawn(self._explain_task(ply))
        return Reply("Let me think that through, Sir.", kind="explain")

    async def _explain_task(
        self, ply: int, *, speak: bool = True, template_only: bool = False
    ) -> None:
        facts = await self._facts_for_ply(ply)
        if facts is None:
            return
        try:
            exp = await self.commentary.explain(facts, force_template=template_only)
        except Cancelled:
            return
        self.badges[ply] = facts["classification"]
        self.panel = {**exp.as_dict(), "ply": ply, "pinned": False}
        await self._changed()
        if self.commentary.last_notice:
            note, self.commentary.last_notice = self.commentary.last_notice, None
            await self._say(note)
        if speak:
            await self._say(exp.spoken)

    # --------------------------------------------------------------- hints
    async def _i_hint(self) -> Reply:
        fen = self.state.fen()
        level = self.settings.hint_default
        if self.hint and self.hint.get("fen") == fen:
            level = min(3, self.hint["level"] + 1)
        self._spawn(self._hint_task(level))
        return Reply(None, kind="hint")

    async def _i_hint_more(self) -> Reply:
        return await self._i_hint()

    async def _i_hint_full(self) -> Reply:
        self._spawn(self._hint_task(3))
        return Reply(None, kind="hint")

    async def _i_hint_piece(self) -> Reply:
        self._spawn(self._hint_task(1))
        return Reply(None, kind="hint")

    async def _hint_task(self, level: int) -> None:
        st = self.state
        board = st.board.copy(stack=False)
        if board.turn != st.human_colour or board.is_game_over():
            await self._say("It isn't your move, Sir.")
            return
        a = await self._analyse_cached(board, 12)
        if not a.best:
            return
        mv = a.best
        p = board.piece_at(mv.from_square)
        name = piece_name(p.piece_type) if p else "piece"
        frm, to = chess.square_name(mv.from_square), chess.square_name(mv.to_square)
        text = templates.hint_level_text(
            level, name, frm, to, templates.idea_for(board, mv)
        )
        if level >= 3:
            text = f"Play {templates.announce_move(board, mv)[:-1].lower()}, Sir."
        self.hint = {
            "level": level,
            "fen": board.fen(),
            "from": frm,
            "to": to if level >= 3 else None,
            "text": text,
        }
        await self._changed()
        await self._say(text)

    # -------------------------------------------------------- continuous
    async def _i_continuous(self, mode: str) -> Reply:
        if mode == "analysis":
            self.analysis_on = True
        elif mode == "commentary":
            self.commentary_on = True
        elif mode == "eval":
            self.eval_on = True
        elif mode == "hints":
            self.hints_on = True
        elif mode == "blunders":
            self.blunder_warn = True
        elif mode == "time":
            self.time_warn = True
        await self._changed()
        if mode in ("eval", "analysis"):
            self._spawn(self._eval_refresh())
        say = {
            "analysis": "Very good, Sir. I'll keep analysing until you say stop.",
            "commentary": "Very good, Sir. Commentary is on; say stop when you've had enough.",
            "eval": "Very good, Sir. The evaluation will stay on.",
            "hints": "Very good, Sir. Hints every move until you say stop.",
            "blunders": "Very good, Sir. I'll mention any blunders.",
            "time": "Very good, Sir. I'll warn you about the clock.",
        }[mode]
        return Reply(say, kind="continuous")

    async def _i_remember_analysis(self) -> Reply:
        self.settings.remember_analysis = True
        await self._changed()
        return Reply("I'll remember that, Sir.", kind="settings")

    async def _eval_refresh(self) -> None:
        board = self.state.board.copy(stack=False)
        a = await self._analyse_cached(board, 12)
        self.last_eval = self._eval_dict(a, board)
        await self._changed()

    async def _features_after(self, move: chess.Move, san: str, who: str) -> None:
        """Run only the continuous modes the user switched on."""
        if not any(
            [
                self.analysis_on,
                self.commentary_on,
                self.eval_on,
                self.hints_on,
                self.blunder_warn,
            ]
        ):
            return
        ply = self.state.move_count
        if self.eval_on or self.analysis_on:
            self._spawn(self._eval_refresh())
        if self.commentary_on:
            self._spawn(self._explain_task(ply))
        elif self.analysis_on or (self.blunder_warn and who == "human"):
            self._spawn(self._quiet_assess(ply, who))
        if self.hints_on and who != "human" and self.state.mode == "play":
            self._spawn(self._hint_task(1))

    async def _quiet_assess(self, ply: int, who: str) -> None:
        facts = await self._facts_for_ply(ply)
        if not facts:
            return
        self.badges[ply] = facts["classification"]
        await self._changed()
        label = facts["classification"]
        if self.analysis_on:
            await self._say(
                f"{facts['move_san']}: {templates.classification_phrase(label)}."
            )
        elif self.blunder_warn and label in ("Mistake", "Blunder"):
            await self._say(f"That was {templates.classification_phrase(label)}, Sir.")

    async def tick(self) -> None:
        """Called by the server ticker: clock flags and optional warnings."""
        await self.check_clock()
        st = self.state
        if self.time_warn and st.clock.timed and not st.is_over() and not self.paused:
            left = st.time_left(st.human_colour)
            if left < 30 and not getattr(self, "_warned", False):
                self._warned = True
                await self._say("You have under thirty seconds, Sir.")
            elif left >= 30:
                self._warned = False

    # ---------------------------------------------------------------- review
    async def _i_review(self) -> Reply:
        if self.state.move_count < 2:
            return Reply("There's not enough of a game to review yet, Sir.", ok=False)
        self.review_active = True
        self._spawn(self._review_task())
        return Reply("Very well, Sir. Give me a moment.", kind="review")

    async def _review_task(self) -> None:
        st = self.state
        depth = 10
        graph: list[int] = []
        moments: list[dict[str, Any]] = []
        accs: list[float] = []
        mistakes = 0
        first = self._board_at(0)
        a = await self._analyse_cached(first, depth)
        graph.append(
            max(
                -1000,
                min(
                    1000,
                    a.eval.cp
                    if a.eval.cp is not None
                    else (1000 if (a.eval.mate or 0) > 0 else -1000),
                ),
            )
        )
        for ply in range(1, st.move_count + 1):
            facts = await self._facts_for_ply(ply, depth)
            if not facts:
                continue
            after = self._board_at(ply)
            an = await self._analyse_cached(after, depth)
            ev = an.eval
            graph.append(
                max(
                    -1000,
                    min(
                        1000,
                        ev.cp
                        if ev.cp is not None
                        else (1000 if (ev.mate or 0) > 0 else -1000),
                    ),
                )
            )
            mover_white = facts["mover"] == "white"
            is_human = mover_white == (st.human_colour == chess.WHITE)
            self.badges[ply] = facts["classification"]
            if is_human:
                before = self._board_at(ply - 1)
                mover = before.turn
                cp0 = (await self._analyse_cached(before, depth)).eval.for_colour(mover)
                cp1 = ev.for_colour(mover)
                diff = max(0.0, _win_pct(cp0) - _win_pct(cp1))
                accs.append(
                    max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * diff) - 3.1669))
                )
                if facts["classification"] in ("Mistake", "Blunder"):
                    mistakes += 1
            moments.append(
                {
                    "ply": ply,
                    "san": facts["move_san"],
                    "label": facts["classification"],
                    "cp_loss": facts["centipawn_loss"],
                    "human": is_human,
                    "fen": after.fen(),
                    "text": templates.template_explanation(facts)["spoken"],
                    "move_no": (ply + 1) // 2,
                }
            )
        key = sorted([m for m in moments if m["human"]], key=lambda m: -m["cp_loss"])[
            :3
        ]
        key = sorted(key, key=lambda m: m["ply"])
        accuracy = round(sum(accs) / len(accs), 1) if accs else 100.0
        self.review = {
            "active": True,
            "accuracy": accuracy,
            "mistakes": mistakes,
            "moments": key,
            "graph": graph,
            "index": -1,
        }
        self.review_active = True
        await self._changed()
        head = f"You played with {accuracy:.0f} percent accuracy, Sir, with {templates.number_words(mistakes)} mistake{'s' if mistakes != 1 else ''}."
        if key:
            head += f" I found {templates.number_words(len(key))} key moment{'s' if len(key) != 1 else ''}. Say 'next moment' when ready."
        await self._say(head)

    async def _review_go(self, idx: int) -> Reply:
        r = self.review
        if not r or not r.get("moments"):
            return Reply("There is no review running, Sir.", ok=False)
        idx = max(0, min(len(r["moments"]) - 1, idx))
        r["index"] = idx
        m = r["moments"][idx]
        r["fen"] = m["fen"]
        r["ply"] = m["ply"]
        await self._changed()
        say = f"Move {templates.number_words(m['move_no'])}: {m['text']}"
        await self._say(say)
        return Reply(None, kind="review")

    async def _i_review_next(self) -> Reply:
        r = self.review
        if not r:
            return Reply("There is no review running, Sir.", ok=False)
        if r["index"] + 1 >= len(r["moments"]):
            await self._say("That was the last moment, Sir.")
            return Reply(None)
        return await self._review_go(r["index"] + 1)

    async def _i_review_prev(self) -> Reply:
        r = self.review
        if not r:
            return Reply("There is no review running, Sir.", ok=False)
        return await self._review_go(max(0, r["index"] - 1))

    async def _i_review_goto(self, n: int) -> Reply:
        r = self.review
        if not r:
            return Reply("There is no review running, Sir.", ok=False)
        ply = 2 * (n - 1) + (1 if self.state.human_colour == chess.WHITE else 2)
        for i, m in enumerate(r["moments"]):
            if m["ply"] >= ply:
                return await self._review_go(i)
        return await self._review_go(len(r["moments"]) - 1)

    async def _i_review_skip(self) -> Reply:
        self.pending = None
        return Reply("Very well, Sir.", kind="decline")

    # ------------------------------------------------------------------ info
    async def _i_info_where(self, piece: str) -> Reply:
        b = self.state.board
        pt = chess.PIECE_NAMES.index(piece)
        sqs = [chess.square_name(s) for s in b.pieces(pt, self.state.human_colour)]
        self.highlights = sqs
        await self._changed()
        if not sqs:
            return Reply(f"You have no {piece} left, Sir.", kind="info")
        spoken = ", ".join(templates.say_square(chess.parse_square(s)) for s in sqs)
        return Reply(
            f"Your {piece}{'s are' if len(sqs) > 1 else ' is'} on {spoken}.",
            kind="info",
        )

    async def _i_info_square(self, square: str) -> Reply:
        p = self.state.board.piece_at(chess.parse_square(square))
        sp = templates.say_square(chess.parse_square(square))
        if not p:
            return Reply(f"{sp.capitalize()} is empty, Sir.", kind="info")
        owner = "your" if p.color == self.state.human_colour else "my"
        return Reply(
            f"{owner.capitalize()} {piece_name(p.piece_type)} stands on {sp}.",
            kind="info",
        )

    async def _i_info_attackers(self, square: str) -> Reply:
        b = self.state.board
        sq = chess.parse_square(square)
        mine = [chess.square_name(s) for s in b.attackers(self.state.human_colour, sq)]
        theirs = [
            chess.square_name(s) for s in b.attackers(not self.state.human_colour, sq)
        ]
        self.highlights = mine + theirs
        await self._changed()
        sp = templates.say_square(sq)

        def names(lst: list[str]) -> str:
            return ", ".join(
                f"the {piece_name(b.piece_at(chess.parse_square(s)).piece_type)} on {s}"
                for s in lst
            )

        if not mine and not theirs:
            return Reply(f"Nothing attacks {sp}, Sir.", kind="info")
        parts = []
        if mine:
            parts.append(f"You attack it with {names(mine)}")
        if theirs:
            parts.append(f"I attack it with {names(theirs)}")
        return Reply(f"{sp.capitalize()}: " + "; ".join(parts) + ".", kind="info")

    async def _i_info_piece_moves(self, piece: str) -> Reply:
        b = self.state.board
        pt = chess.PIECE_NAMES.index(piece)
        moves = [m for m in b.legal_moves if b.piece_at(m.from_square).piece_type == pt]
        self.highlights = sorted({chess.square_name(m.to_square) for m in moves})
        await self._changed()
        if not moves:
            return Reply(f"Your {piece} has no moves just now, Sir.", kind="info")
        return Reply(
            f"Your {piece} has {templates.number_words(len(moves))} move{'s' if len(moves) != 1 else ''}, shown on the board.",
            kind="info",
        )

    async def _i_info_material(self) -> Reply:
        m = self.state.material()
        diff = m["diff"] if self.state.human_colour == chess.WHITE else -m["diff"]
        if diff == 0:
            return Reply("Material is level, Sir.", kind="info")
        who = "You are" if diff > 0 else "I am"
        return Reply(
            f"{who} up {templates.number_words(abs(diff))} point{'s' if abs(diff) != 1 else ''} of material, Sir.",
            kind="info",
        )

    async def _i_info_read(self) -> Reply:
        return Reply(templates.read_moves(self.state.san_history()), kind="info")

    async def _i_info_read_last(self, n: int) -> Reply:
        return Reply(templates.read_moves(self.state.san_history(), n), kind="info")

    async def _i_info_last_ai(self) -> Reply:
        return self._last_by(False)

    async def _i_info_last_mine(self) -> Reply:
        return self._last_by(True)

    def _last_by(self, human: bool) -> Reply:
        st = self.state
        for ply in range(st.move_count, 0, -1):
            if ((ply % 2 == 1) == (st.human_colour == chess.WHITE)) == human:
                san = st.san_history()[ply - 1]
                return Reply(
                    ("You played " if human else "I played ")
                    + templates.read_moves([san]).rstrip(".").split(", ", 1)[-1]
                    + ", Sir.",
                    kind="info",
                )
        return Reply("No moves yet, Sir.", kind="info")

    async def _i_info_movenum(self) -> Reply:
        n = self.state.board.fullmove_number
        return Reply(f"We are on move {templates.number_words(n)}, Sir.", kind="info")

    async def _i_info_turn(self) -> Reply:
        mine = self.state.board.turn == self.state.human_colour
        return Reply(
            "It is your move, Sir." if mine else "It is my move, Sir.", kind="info"
        )

    async def _i_info_time(self) -> Reply:
        st = self.state
        if not st.clock.timed:
            return Reply("The game is untimed, Sir.", kind="info")
        left = st.time_left(st.human_colour)
        return Reply(f"You have {templates.time_phrase(left)} left, Sir.", kind="info")

    async def _i_info_opening(self) -> Reply:
        name = templates.opening_name(self.state.san_history())
        if not name:
            return Reply("I can't place an opening yet, Sir.", kind="info")
        return Reply(f"That is {name}, Sir.", kind="info")

    # -------------------------------------------------------------- settings
    async def _setting(self, say: str | None = "Very good, Sir.", **kw: Any) -> Reply:
        self.settings.update(**kw)
        if self._persist:
            from games.chess.settings import save

            save(self.settings)
        await self._changed()
        return Reply(say, kind="settings")

    async def _i_volume(self, direction: int) -> Reply:
        v = round(
            max(0.2, min(1.5, self.settings.commentary_volume + 0.2 * direction)), 2
        )
        return await self._setting(commentary_volume=v)

    async def _i_mute(self, on: bool) -> Reply:
        return await self._setting(
            None if on else "Commentary is back, Sir.", commentary_muted=on
        )

    async def _i_announce(self, on: bool) -> Reply:
        return await self._setting(announce_moves=on)

    async def _i_detail(self, level: str) -> Reply:
        return await self._setting(detail=level)

    async def _i_audience(self, audience: str) -> Reply:
        return await self._setting(audience=audience)

    async def _i_confirm(self, on: bool) -> Reply:
        return await self._setting(confirm_moves=on)

    async def _i_stop_ack(self, mode: str) -> Reply:
        return await self._setting(
            "Very good, Sir." if mode == "spoken" else None, stop_ack=mode
        )

    async def _i_ai_online(self, on: bool) -> Reply:
        self.commentary.enabled = self.chat.enabled = on
        return await self._setting(online_explanations=on)

    async def _i_eval_bar(self, on: bool) -> Reply:
        self.eval_on = on
        if on:
            self._spawn(self._eval_refresh())
        else:
            self.eval_shown_fen = None
        return await self._setting(eval_bar=on)

    async def _i_theme(self, theme: str) -> Reply:
        return await self._setting(board_theme=theme)

    async def _i_intro(self, mode: str) -> Reply:
        return await self._setting(intro=mode != "off", intro_length=mode)

    async def _i_ai_model(self) -> Reply:
        if not self.commentary.router.key:
            return Reply(
                "No commentary model is configured, Sir. I'm working from templates.",
                kind="info",
            )
        m = (
            self.settings.models[0]
            .split("/")[-1]
            .replace(":free", "")
            .replace("-", " ")
        )
        return Reply(f"I'm using {m}, Sir, with fallbacks.", kind="info")

    async def _i_ai_calls_left(self) -> Reply:
        n = self.commentary.left_today()
        return Reply(
            f"You have {templates.number_words(n)} explanation calls left today, Sir.",
            kind="info",
        )

    async def _i_ai_test(self) -> Reply:
        self._spawn(self._ai_test_task())
        return Reply(None, kind="ai_test")

    async def _ai_test_task(self) -> None:
        if not self.commentary.router.available():
            await self._say(
                "The commentary connection is offline, Sir. Templates only."
            )
            return
        try:
            await self.commentary.router.complete(
                [{"role": "user", "content": "Reply with the single word: ready"}],
                max_tokens=8,
            )
        except Cancelled:
            return
        except Exception:
            await self._say("I couldn't reach the commentary service, Sir.")
            return
        await self._say("The commentary connection is working, Sir.")

    # ------------------------------------------------------------------ chat
    async def _chat(self, raw: str) -> Reply:
        if not self.chat.online():
            self.misses = 0
            return Reply(templates.OFFLINE_CHAT, ok=False, kind="offline")
        self.misses = 0
        self._spawn(self._chat_task(raw))
        return Reply(None, kind="chat")

    async def _chat_task(self, raw: str) -> None:
        facts = None
        if self.analysis_on:
            a = await self._analyse_cached(self.state.board.copy(stack=False), 10)
            facts = {"eval": a.eval.as_dict(), "best": a.best.uci() if a.best else None}
        try:
            reply = await self.chat.ask(
                raw,
                fen=self.state.fen(),
                last_moves=self.state.san_history(),
                engine_facts=facts,
            )
        except Cancelled:
            return
        await self._say(reply)
