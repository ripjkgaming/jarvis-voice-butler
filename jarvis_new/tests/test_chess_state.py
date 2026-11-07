"""Game state: every ending, undo/redo, take-over round trips, clocks, saves."""

import asyncio

import chess
import pytest

from games.chess import saves
from games.chess.engine import MiniEngine
from games.chess.game import ChessGame
from games.chess.settings import ChessSettings
from games.chess.state import Clock, GameState


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def play(gs: GameState, *sans: str) -> None:
    for s in sans:
        gs.push_san(s)


# ----------------------------------------------------------------- game endings
def test_checkmate():
    gs = GameState()
    play(gs, "f3", "e5", "g4", "Qh4#")
    assert gs.result().outcome == "0-1" and gs.result().reason == "checkmate"
    assert gs.is_over()
    with pytest.raises(ValueError):
        gs.push_san("a3")


def test_stalemate():
    # Kb6 vs Ka8: Qc7 leaves black with no legal move and no check.
    gs = GameState(start_fen="k7/8/1K6/8/8/8/8/2Q5 w - - 0 1")
    gs.push(chess.Move.from_uci("c1c7"))
    assert gs.result().reason == "stalemate" and gs.result().outcome == "1/2-1/2"


def test_insufficient_material():
    gs = GameState(start_fen="8/8/4k3/8/8/4K3/8/8 w - - 0 1")
    assert gs.result().reason == "insufficient material"


def test_resignation_and_agreed_draw():
    gs = GameState()
    gs.resign(chess.WHITE)
    assert gs.result().outcome == "0-1" and gs.result().reason == "resignation"
    gs = GameState()
    gs.agree_draw()
    assert gs.result().outcome == "1/2-1/2"


def test_threefold_is_claimable_not_automatic():
    gs = GameState()
    play(gs, "Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "Nf6", "Ng1", "Ng8")
    assert not gs.is_over()
    assert gs.can_claim_draw() == "threefold repetition"
    assert gs.claim_draw().reason == "threefold repetition"
    assert gs.is_over()


def test_fivefold_repetition_is_automatic():
    gs = GameState()
    for _ in range(4):
        play(gs, "Nf3", "Nf6", "Ng1", "Ng8")
    assert gs.result().reason == "fivefold repetition"


def test_fifty_move_rule_is_claimable():
    gs = GameState(start_fen="4k3/8/8/8/8/8/4P3/4K2R w - - 100 80")
    assert gs.can_claim_draw() == "fifty-move rule"


def test_seventyfive_move_rule_is_automatic():
    gs = GameState(start_fen="4k3/8/8/8/8/8/4P3/4K2R w - - 150 100")
    assert gs.result().reason == "seventy-five-move rule"


def test_timeout_and_timeout_vs_insufficient():
    now = FakeClock()
    gs = GameState(clock=Clock.from_spec("5+0"), now=now)
    gs.push_san("e4")
    now.advance(301)
    res = gs.result()
    assert res.reason == "timeout" and res.outcome == "1-0"  # black to move ran out
    now = FakeClock()
    gs = GameState(
        start_fen="4k3/8/8/8/8/8/8/4K2R w - - 0 1",
        clock=Clock.from_spec("1+0"),
        now=now,
    )
    gs.push_san("Rh2")
    gs.clock.black = (
        0.0  # black has only a bare king... white has a rook: black flags, white wins
    )
    now.advance(1)
    assert gs.result().outcome == "1-0"


# ---------------------------------------------------------------- undo / redo
def test_undo_redo_round_trip():
    gs = GameState()
    play(gs, "e4", "e5", "Nf3")
    fen3 = gs.fen()
    assert gs.undo(2) == 2
    assert gs.move_count == 1
    assert gs.redo(2) == 2
    assert gs.fen() == fen3
    assert gs.undo(10) == 3
    assert gs.fen() == chess.STARTING_FEN
    assert gs.undo() == 0


def test_new_move_clears_redo():
    gs = GameState()
    play(gs, "e4", "e5")
    gs.undo()
    assert gs.redo_stack
    gs.push_san("c5")
    assert not gs.redo_stack


def test_undo_reopens_a_finished_forced_result():
    gs = GameState()
    play(gs, "e4")
    gs.resign(chess.BLACK)
    gs.undo()
    assert not gs.is_over()


# ------------------------------------------------------------------- clocks
def test_clock_runs_and_increments():
    now = FakeClock()
    gs = GameState(clock=Clock.from_spec("15+10"), now=now)
    gs.push_san("e4")
    now.advance(7)
    gs.push_san("e5")
    assert gs.clock.black == pytest.approx(900 - 7 + 10)
    now.advance(3)
    assert gs.time_left(chess.WHITE) == pytest.approx(900 + 10 - 3)


def test_clock_pause_resume_loses_nothing():
    now = FakeClock()
    gs = GameState(clock=Clock.from_spec("10+0"), now=now)
    gs.push_san("e4")
    now.advance(5)
    gs.pause_clock()
    now.advance(120)  # Jarvis is talking: nobody's clock moves
    left_black = gs.clock.black
    gs.resume_clock()
    now.advance(2)
    assert gs.time_left(chess.BLACK) == pytest.approx(left_black - 2)


def test_custom_time_control():
    c = Clock.from_spec("3+2")
    assert c.base == 180 and c.increment == 2 and c.timed
    assert not Clock.from_spec("untimed").timed


# --------------------------------------------------------------- save / load
def test_state_round_trip_keeps_everything():
    now = FakeClock()
    gs = GameState(human_colour=chess.BLACK, clock=Clock.from_spec("10+0"), now=now)
    play(gs, "e4", "e5", "Nf3")
    gs.undo()
    d = gs.to_dict()
    gs2 = GameState.from_dict(d, now=now)
    assert gs2.fen() == gs.fen()
    assert gs2.human_colour == chess.BLACK
    assert [m.uci() for m in gs2.redo_stack] == [m.uci() for m in gs.redo_stack]
    assert gs2.clock.base == 600
    assert gs2.redo(1) == 1


def test_forced_result_round_trips():
    gs = GameState()
    play(gs, "e4")
    gs.resign(chess.BLACK)
    gs2 = GameState.from_dict(gs.to_dict())
    assert gs2.result().reason == "resignation"


def test_snapshot_shape():
    gs = GameState()
    play(gs, "e4", "d5", "exd5")
    snap = gs.snapshot()
    for key in (
        "fen",
        "turn",
        "mode",
        "last_move",
        "check",
        "moves",
        "captured",
        "clock",
        "result",
        "legal",
    ):
        assert key in snap
    assert snap["last_move"]["uci"] == "e4d5" and snap["captured"]["black"] == ["p"]
    gs = GameState(start_fen="4k3/8/8/8/8/8/4q3/4K3 w - - 0 1")
    assert gs.snapshot()["check"] == "e1"


def test_saves_names_and_pgn(tmp_path):
    saves.write({"a": 1}, "My Game #1", tmp_path)
    assert saves.list_saves(tmp_path) == ["my-game-1"]
    assert saves.read("my game 1", tmp_path) == {"a": 1}
    assert saves.delete("my game 1", tmp_path)
    pgn = saves.export_pgn(["e2e4", "e7e5", "g1f3"], None, {"White": "Sir"})
    moves, start, headers = saves.import_pgn(pgn)
    assert (
        moves == ["e2e4", "e7e5", "g1f3"]
        and start is None
        and headers["White"] == "Sir"
    )
    with pytest.raises(ValueError):
        saves.import_pgn("")


# --------------------------------------------------------- controller-level
@pytest.fixture
def game(tmp_path):
    g = ChessGame(
        ChessSettings(announce_moves=True, difficulty="novice", commentary_muted=False),
        engine=MiniEngine(3),
        save_dir=tmp_path,
    )
    g.listening = True
    yield g


async def _settle(g: ChessGame):
    for _ in range(40):
        await g.drain(5)
        if not g.thinking and not (g._reply_task and not g._reply_task.done()):
            return
        await asyncio.sleep(0.01)


async def test_takeover_and_return_with_clocks_intact(game):
    await game.new_game(time_control="10+0")
    game.step_delay = 0.0
    for san in ("e4", "d4"):
        r = await game.handle_text(san)
        assert r.ok
        await _settle(game)
    n = game.state.move_count
    await game.handle_text("take over for two moves")
    for _ in range(300):
        await asyncio.sleep(0.02)
        if game.state.mode == "play":
            break
    assert game.state.mode == "play"
    assert game.state.move_count >= n + 3  # two of mine plus replies
    assert True
    assert game.state.clock.white > 0 and game.state.clock.black > 0
    await game.close()


async def test_take_over_for_unlimited_then_take_back(game):
    await game.new_game()
    game.step_delay = 0.01
    await game.handle_text("take over")
    await asyncio.sleep(0.2)
    assert game.state.mode == "takeover"
    assert game.state.move_count >= 2
    r = await game.handle_text("I'll take it from here")
    assert game.state.mode == "play" and "yours" in (r.say or "")
    await _settle(game)
    r = await game.handle_text("e4") if game.state.board.turn == chess.WHITE else None
    await game.close()


async def test_undo_and_redo_via_voice(game):
    await game.new_game()
    await game.handle_text("e4")
    await _settle(game)
    assert game.state.move_count == 2
    r = await game.handle_text("undo")
    assert r.say == "Undone, Sir." and game.state.move_count == 0
    r = await game.handle_text("redo")
    assert game.state.move_count == 2
    r = await game.handle_text("undo")
    r = await game.handle_text("undo")
    assert "nothing to undo" in r.say
    await game.close()


async def test_resignation_and_review_offer(game):
    await game.new_game()
    for san in ("e4", "d4", "Nf3", "c4"):
        await game.handle_text(san)
        await _settle(game)
    await game.handle_text("I resign")
    assert game.state.result().reason == "resignation"
    assert any("review the game" in s.lower() for s in game.speech_log)
    assert game.pending and game.pending["type"] == "review_offer"
    await game.close()


async def test_save_load_round_trip(game):
    await game.new_game()
    await game.handle_text("e4")
    await _settle(game)
    fen = game.state.fen()
    r = await game.handle_text("save as test one")
    assert "test one" in r.say
    await game.handle_text("new game")
    assert game.state.move_count <= 1
    await game.handle_text("load test one")
    assert game.state.fen() == fen
    await game.close()


async def test_autosave_resume(game):
    await game.new_game()
    await game.handle_text("e4")
    await _settle(game)
    fen = game.state.fen()
    g2 = ChessGame(ChessSettings(), engine=MiniEngine(1), save_dir=game.save_dir)
    assert await g2.resume_last()
    assert g2.state.fen() == fen
    await game.close()
    await g2.close()
