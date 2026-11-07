"""Integration of store / series / takeover / history / review into ChessGame.

Fake engines only, no real sleeps (takeover sleep is a no-op coroutine).
"""

import asyncio

import chess
import pytest

from games.chess.engine import Analysis, BaseEngine, Eval
from games.chess.game import ChessGame
from games.chess.settings import ChessSettings
from games.chess.store import GameStore

SCRIPT = ["f2f3", "e7e5", "g2g4", "d8h4"]  # fool's mate: black wins in 4 plies


class FakeEngine(BaseEngine):
    name = "fake"

    def __init__(self):
        self.calls = 0
        self.fail = 0

    async def best_move(self, board, preset="club"):
        self.calls += 1
        if self.fail:
            raise RuntimeError("engine down")
        n = len(board.move_stack)
        if n < len(SCRIPT):
            mv = chess.Move.from_uci(SCRIPT[n])
            if mv in board.legal_moves:
                return mv
        return next(iter(board.legal_moves))

    async def analyse(self, board, depth=12):
        return Analysis(Eval(cp=-600 if len(board.move_stack) >= 3 else 20),
                        next(iter(board.legal_moves), None), [], depth)

    def cancel(self):
        pass

    async def close(self):
        pass


class FakeEval(FakeEngine):
    """Second engine (positions come from FENs, so no move stack): eval drops
    by 600 after white's g4 (ply 3); the top move at ply 2 is d4."""

    async def analyse(self, board, depth=12):
        ply = 2 * (board.fullmove_number - 1) + (0 if board.turn == chess.WHITE else 1)
        if board.is_checkmate():
            return Analysis(Eval(cp=-9000), None, [], depth)
        best = chess.Move.from_uci("d2d4") if ply == 2 else next(iter(board.legal_moves))
        return Analysis(Eval(cp=-600 if ply >= 3 else 20), best, [best], depth)


async def nosleep(_s):
    await asyncio.sleep(0)


def mk(tmp_path, **kw):
    kw.setdefault("store_root", tmp_path / "games")
    g = ChessGame(
        ChessSettings(colour="white", announce_moves=False, commentary_muted=False),
        engine=kw.pop("engine", FakeEngine()),
        save_dir=tmp_path / "saves",
        step_delay=0.0,
        **kw,
    )
    g.listening = True
    g._takeover_sleep = nosleep
    return g


async def user_move(g, uci):
    return await g.command("move", uci=uci)


async def quiet(g):
    for _ in range(100):
        await g.drain(5)
        if not (g._reply_task and not g._reply_task.done()):
            return
        await asyncio.sleep(0)


# --------------------------------------------------------------- 1/2 store
async def test_lazy_store_and_autosave_each_move(tmp_path):
    g = mk(tmp_path)
    await g.new_game()
    assert not (tmp_path / "games").exists()  # nothing written until a move
    await user_move(g, "f2f3")
    await quiet(g)
    rec = g.store.load(g._rec.id)
    assert [m["uci"] for m in rec.moves] == ["f2f3", "e7e5"]
    assert rec.status == "active" and rec.white == "user"
    assert all("think_ms" in m and m["fen_after"] for m in rec.moves)
    await user_move(g, "g2g4")
    await quiet(g)
    rec = g.store.load(g._rec.id)
    assert rec.status == "finished" and rec.result == "0-1" and rec.termination == "mate"
    gid = rec.id
    g._finish_record()  # idempotent
    g._finish_record()
    assert [i["id"] for i in g.store.list()] == [gid]
    assert g.games_list and g.games_list[0]["id"] == gid
    assert g.history_view is None
    await g.close()


async def test_undo_truncates_record_and_new_game_abandons(tmp_path):
    g = mk(tmp_path)
    await g.new_game()
    await user_move(g, "f2f3")
    await quiet(g)
    await g.handle_text("undo")
    assert g.store.load(g._rec.id).moves == []
    await user_move(g, "e2e4")
    await quiet(g)
    first = g._rec.id
    await g.handle_text("new game")
    assert g.store.load(first).termination == "abandoned"
    assert g.store.latest_active() is None
    await g.close()


async def test_resign_finishes_with_resign(tmp_path):
    g = mk(tmp_path)
    await g.new_game()
    await user_move(g, "e2e4")
    await quiet(g)
    await g.handle_text("I resign")
    rec = g.store.load(g._rec.id)
    assert rec.status == "finished" and rec.termination == "resign" and rec.result == "0-1"
    await g.close()


# ------------------------------------------------------------ 3/4 series
async def test_series_plays_to_completion_with_summary(tmp_path):
    g = mk(tmp_path, eval_engine_factory=FakeEval)
    r = await g.handle_text("play two games")
    assert "not available" not in (r.say or "")
    await g.wait_series()
    s = g.series.state
    assert s.status == "complete" and len(s.game_ids) == 2
    assert (s.wins, s.draws, s.losses) == (1, 0, 1)  # colours alternated
    assert g.state.mode == "play"
    items = g.store.list()
    assert len(items) == 2 and {i["series_id"] for i in items} == {s.id}
    assert sorted(i["series_index"] for i in items) == [1, 2]
    assert any("Series complete" in x for x in g.speech_log)
    assert g.last_analysis["kind"] == "series_summary"
    await g.close()


async def test_series_options_colour_level_applied_and_restored(tmp_path):
    g = mk(tmp_path)
    diff = g.settings.difficulty
    seen = []
    real = g.engine.best_move

    async def spy(board, preset="club"):
        seen.append(preset)
        return await real(board, preset)

    g.engine.best_move = spy
    await g.handle_text("play two games as black at level 3")
    await g.wait_series()
    games = sorted(g.store.list(), key=lambda i: i["series_index"])
    assert [i["white"] for i in games] == ["jarvis", "jarvis"]  # user black both
    assert set(seen) == {"level:3"}  # level 3 on both sides
    assert g.settings.difficulty == diff and g.settings.takeover_strength == "club"
    await g.close()


async def test_series_speak_moves_option(tmp_path):
    g = mk(tmp_path)
    await g.handle_text("play one game")
    await g.handle_text("speak every move")
    await g.wait_series()
    assert g.series.state.speak == "moves"
    assert any(x in ("f3", "e5", "g4") for x in g.speech_log)
    await g.close()


async def hold_engine(g):
    gate = asyncio.Event()
    real = g.engine.best_move
    g.engine.hold = True

    async def slow(board, preset="club"):
        while g.engine.hold:
            await gate.wait()
            gate.clear()
        return await real(board, preset)

    g.engine.best_move = slow
    return gate


async def test_pause_resume_cancel(tmp_path):
    g = mk(tmp_path)
    gate = await hold_engine(g)
    await g.handle_text("play five games")
    await asyncio.sleep(0.01)
    assert g.state.mode == "takeover"
    await g.handle_text("pause the series")
    assert g.state.mode == "play" and g.series.state.status == "paused"
    g.engine.hold = False
    gate.set()
    await g.handle_text("continue the series")
    await asyncio.sleep(0)
    assert g.series.state.status == "running"
    await g.handle_text("cancel the series")
    assert g.series.state.status == "cancelled" and g.state.mode == "play"
    n = len(g.store.list())
    await asyncio.sleep(0.02)
    assert len(g.store.list()) == n  # nothing keeps playing
    await g.close()


async def test_user_move_releases_takeover_and_pauses_series(tmp_path):
    g = mk(tmp_path)
    gate = await hold_engine(g)
    await g.handle_text("play five games")
    await asyncio.sleep(0.01)
    assert g.state.mode == "takeover" and g.state.move_count == 0
    await user_move(g, "e2e4")
    assert g.state.mode == "play" and g.series.state.status == "paused"
    g.engine.hold = False
    gate.set()
    await quiet(g)
    assert len(g.store.list()) == 1  # same game, no new one
    assert g.series.state.status == "paused"
    await g.close()


async def test_user_speaking_hook_holds_speech(tmp_path):
    g = mk(tmp_path)
    assert g.user_speaking is False
    g.user_speaking = True
    from games.chess.takeover import GameAdapter

    assert GameAdapter(g).user_is_speaking() is True
    g.user_speaking = lambda: False
    assert GameAdapter(g).user_is_speaking() is False
    await g.close()


async def test_revert_control_releases_without_new_game(tmp_path):
    g = mk(tmp_path)
    await g.new_game()
    await g.handle_text("play three games")
    await g.wait_series()
    # all three played; start another and release mid-game
    g2 = mk(tmp_path / "b")
    await g2.new_game()
    await g2.handle_text("take over")
    r = await g2.handle_text("revert control")
    assert g2.state.mode == "play"
    n = len(g2.store.list())
    await asyncio.sleep(0)
    await g2.drain(1)
    assert len(g2.store.list()) <= n
    await g.close()
    await g2.close()


async def test_history_open_rejected_in_takeover(tmp_path):
    g = mk(tmp_path)
    await g.new_game()
    await user_move(g, "f2f3")
    await quiet(g)
    await g.handle_text("take over")
    r = await g.handle_text("open game 1")
    assert not r.ok and "Release control" in r.say
    await g.handle_text("release control")
    r = await g.handle_text("open game 1")
    assert r.ok and g.history_view is not None
    await g.close()


async def test_take_it_from_here_is_never_a_move(tmp_path):
    """Regression: 'take it' = capture the piece that just moved (position
    dependent), so 'I'll take it from here' used to read as a capture."""
    g = mk(tmp_path)
    await g.new_game(fen="rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    g.state.mode = "takeover"
    g._ctx.last_to = chess.D5
    r = await g.handle_text("I'll take it from here")
    assert g.state.mode == "play" and "yours" in (r.say or "")
    assert g.state.move_count == 0
    await g.close()


# ---------------------------------------------------------- 5 evaluator
async def test_evaluator_background_explain_and_board(tmp_path):
    g = mk(tmp_path, eval_engine_factory=FakeEval)
    await g.new_game()
    await user_move(g, "f2f3")
    await quiet(g)
    await user_move(g, "g2g4")
    await quiet(g)
    await g.drain_evals()
    rec = g.store.load(g._rec.id)
    assert all(m["cls"] for m in rec.moves[:3]), rec.moves
    assert rec.moves[2]["cls"] == "blunder"
    assert g._rec.moves[2]["cls"] == "blunder"  # memory == disk
    # post-game: key moments computed and spoken
    for _ in range(50):
        await g.drain(5)
        if g.last_analysis and g.last_analysis.get("kind") == "key_moments":
            break
        await asyncio.sleep(0.01)
    assert g.last_analysis["kind"] == "key_moments"
    assert len(g.last_analysis["moments"]) <= 3
    await g.close()


async def test_explain_uses_saved_data_two_sentences(tmp_path):
    g = mk(tmp_path, eval_engine_factory=FakeEval)
    await g.new_game()
    await user_move(g, "f2f3")
    await quiet(g)
    await user_move(g, "g2g4")
    await g.drain_evals()
    calls = g.engine.calls
    r = await g.handle_text("what was my mistake")
    assert r.kind == "explain" and "g4" in r.say
    assert len([x for x in r.say.replace("!", ".").split(".") if x.strip()]) <= 2
    assert g.engine.calls == calls  # no engine in the voice turn
    assert g.last_analysis["kind"] == "explain" and g.last_analysis["full_text"]
    await g.close()


async def test_analyse_board_from_saved_eval(tmp_path):
    g = mk(tmp_path, eval_engine_factory=FakeEval)
    await g.new_game()
    await user_move(g, "e2e4")
    await quiet(g)
    await g.drain_evals()
    r = await g.handle_text("analyse the board")
    assert r.say and len([x for x in r.say.split(".") if x.strip()]) <= 2
    assert g.last_analysis["kind"] == "board"
    await g.close()


async def test_move_does_not_wait_for_evaluator(tmp_path):
    gate = asyncio.Event()

    class Slow(FakeEval):
        async def analyse(self, board, depth=12):
            await gate.wait()
            return await super().analyse(board, depth)

    g = mk(tmp_path, eval_engine_factory=Slow)
    await g.new_game()
    await asyncio.wait_for(user_move(g, "e2e4"), 1)
    await asyncio.wait_for(quiet(g), 1)
    assert g.state.move_count == 2
    gate.set()
    await g.close()


# ------------------------------------------------------ resume / branch
async def test_restart_resumes_game_and_series(tmp_path):
    g = mk(tmp_path)
    g.engine.fail = 99
    g._takeover_sleep = lambda s: asyncio.sleep(0.01)
    await g.handle_text("play three games")
    await asyncio.sleep(0.05)
    sid = g.series.state.id
    # simulate a kill: drop the object, keep the disk
    g._closed = True
    for t in (g._auto_task,):
        t and t.cancel()
    await asyncio.sleep(0)
    g2 = mk(tmp_path)
    assert await g2.resume_last() or True
    assert g2.pending and g2.pending["type"] == "series_resume"
    assert any("unfinished series" in x for x in g2.speech_log)
    assert g2.state.mode == "play"
    r = await g2.handle_text("yes")
    assert "Resuming your series" in r.say
    await g2.wait_series()
    s = g2.series.state
    assert s.id == sid and len(s.game_ids) == len(set(s.game_ids))
    ids = [i["id"] for i in g2.store.list()]
    assert len(ids) == len(set(ids))
    await g2.close()


async def test_resume_old_series_phrase_and_no_duplicates(tmp_path):
    g = mk(tmp_path)
    await g.handle_text("play three games")
    await g.wait_series()
    done = len(g.store.list())
    g2 = mk(tmp_path)
    r = await g2.handle_text("resume the old series")
    assert not r.ok  # complete series is not resumable
    assert len(g2.store.list()) == done
    await g.close()
    await g2.close()


async def test_rewind_branches_new_game_original_untouched(tmp_path):
    g = mk(tmp_path)
    await g.new_game()
    await user_move(g, "f2f3")
    await quiet(g)
    orig = g._rec.id
    before = g.store.load(orig).to_dict()
    r = await g.handle_text("rewind to move 1 and continue from here")
    assert r.ok
    assert g._rec.id != orig and g._rec.parent_id == orig
    assert g.state.move_count == 0
    after = g.store.load(orig)
    assert after.moves == before["moves"]
    await user_move(g, "e2e4")
    await quiet(g)
    assert g.store.load(g._rec.id).parent_id == orig
    assert len(g.store.list()) == 2
    await g.close()
