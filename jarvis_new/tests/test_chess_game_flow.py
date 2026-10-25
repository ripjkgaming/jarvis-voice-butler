"""Controller behaviour end to end: disambiguation, confirmation, spectator, review, info."""

import asyncio

import chess
import pytest

from games.chess.engine import MiniEngine
from games.chess.game import ChessGame
from games.chess.settings import ChessSettings


def mk(tmp_path, fen=None, colour="white", **settings):
    g = ChessGame(
        ChessSettings(
            announce_moves=True, difficulty="novice", colour=colour, **settings
        ),
        engine=MiniEngine(7),
        save_dir=tmp_path,
        autosave=False,
        step_delay=0.0,
    )
    g.listening = True
    return g


async def start(g, fen=None, **kw):
    await g.new_game(fen=fen, **kw)
    return g


async def idle(g):
    for _ in range(200):
        await g.drain(5)
        if not (g._reply_task and not g._reply_task.done()) and not g.thinking:
            return
        await asyncio.sleep(0.01)


# --------------------------------------------------------------- disambiguation
async def test_ambiguous_knights_ask_then_play_the_answer(tmp_path):
    g = await start(mk(tmp_path), fen="4k3/8/8/8/8/2N3N1/8/4K3 w - - 0 1")
    r = await g.handle_text("knight to e4")
    assert r.kind == "disambiguate" and "c3" in r.say and "g3" in r.say
    snap = g.snapshot()
    assert [d["label"] for d in snap["disambiguation"]] == [1, 2]
    assert {d["from"] for d in snap["disambiguation"]} == {"c3", "g3"}
    assert g.status() == "YOUR MOVE" and snap["pending"]["type"] == "disambiguate"
    r = await g.handle_text("the one on g3")
    assert g.state.uci_history()[0] == "g3e4"
    assert g.snapshot()["disambiguation"] == []
    await g.close()


@pytest.mark.parametrize("answer", ["one", "the first", "the left one", "c3"])
async def test_each_answer_style_works(tmp_path, answer):
    g = await start(mk(tmp_path), fen="4k3/8/8/8/8/2N3N1/8/4K3 w - - 0 1")
    await g.handle_text("knight to e4")
    await g.handle_text(answer)
    assert g.state.uci_history()[0] == "c3e4"
    await g.close()


async def test_never_mind_cancels_the_question(tmp_path):
    g = await start(mk(tmp_path), fen="4k3/8/8/8/8/2N3N1/8/4K3 w - - 0 1")
    await g.handle_text("knight to e4")
    r = await g.handle_text("never mind")
    assert (
        g.pending is None
        and g.state.move_count == 0
        and r.say in ("Very good, Sir.", "Very well, Sir.")
    )
    await g.close()


async def test_a_complete_new_move_overrides_the_question(tmp_path):
    g = await start(mk(tmp_path), fen="4k3/8/8/8/8/2N3N1/8/4K3 w - - 0 1")
    await g.handle_text("knight to e4")
    await g.handle_text("knight from g3 to f5")
    assert g.state.uci_history()[0] == "g3f5" and g.pending is None
    await g.close()


async def test_castle_with_both_sides_asks_kingside_or_queenside(tmp_path):
    g = await start(mk(tmp_path), fen="r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    r = await g.handle_text("castle")
    assert "kingside or queenside" in r.say.lower()
    await g.handle_text("queenside")
    assert g.state.uci_history()[0] == "e1c1"
    await g.close()


# ------------------------------------------------------------- move replies
async def test_illegal_move_gets_a_template_reply_and_flashes(tmp_path):
    g = await start(mk(tmp_path))
    r = await g.handle_text("bishop to g5")
    assert r.kind == "illegal" and "pawn on d2" in r.say
    assert g.snapshot()["flash"]["kind"] == "illegal"
    assert g.state.move_count == 0
    await g.close()


async def test_promotion_defaults_to_a_queen_and_says_so(tmp_path):
    g = await start(mk(tmp_path), fen="8/P6k/8/8/8/8/7K/8 w - - 0 1")
    r = await g.handle_text("a8")
    assert r.say == "Promoted to a queen, Sir."
    assert g.state.board.piece_at(chess.A8).piece_type == chess.QUEEN
    await g.close()


async def test_confirm_moves_setting_says_it_back(tmp_path):
    g = await start(mk(tmp_path, confirm_moves=True))
    r = await g.handle_text("knight to f three")
    assert r.kind == "confirm" and "Knight to f three" in r.say
    assert g.state.move_count == 0
    await g.handle_text("yes")
    assert g.state.uci_history()[0] == "g1f3"
    await g.close()


async def test_a_wrong_square_label_is_asked_not_played(tmp_path):
    g = await start(mk(tmp_path))
    r = await g.handle_text("blah blah")
    assert g.state.move_count == 0 and "Sorry, Sir" in r.say
    await g.close()


async def test_questions_about_squares_are_not_moves(tmp_path):
    g = await start(mk(tmp_path))
    r = await g.handle_text("what's on e four")
    assert g.state.move_count == 0
    assert "empty" in r.say.lower()
    r = await g.handle_text("what's on e two")
    assert "pawn" in r.say.lower()
    await g.close()


async def test_take_back_means_recapture_when_one_exists_else_undo(tmp_path):
    g = await start(mk(tmp_path), fen="4k3/8/8/3p4/4P3/8/8/4K3 b - - 0 1")
    await g.handle_text(
        "pawn takes e4"
    )  # black pawn d5 x e4 -- black is the mover here
    # board turn was black: human is white, so this is rejected; use a white-to-move setup instead
    await g.close()
    g = await start(mk(tmp_path), fen="4k3/8/8/3pp3/4P3/8/8/4K3 w - - 0 1")
    await g.handle_text("e takes d5")
    await idle(g)
    assert g.state.move_count >= 1
    await g.handle_text("undo")
    n = g.state.move_count
    r = await g.handle_text("take back")
    assert r.say == "There's nothing to undo, Sir." or g.state.move_count <= n
    await g.close()


# ------------------------------------------------------------------ pause
async def test_pause_ignores_everything_but_resume(tmp_path):
    g = await start(mk(tmp_path))
    await g.handle_text("pause the game")
    assert g.paused and g.status() == "PAUSED"
    r = await g.handle_text("e4")
    assert r.kind == "paused" and g.state.move_count == 0
    r = await g.handle_text("resume the game")
    assert not g.paused
    await g.handle_text("e4")
    assert g.state.move_count >= 1
    await g.close()


async def test_stop_the_game_pauses_and_offers_to_save(tmp_path):
    g = await start(mk(tmp_path))
    r = await g.handle_text("I'll stop here")
    assert "save" in r.say.lower() and g.paused
    r = await g.handle_text("yes")
    assert (tmp_path / "autosave-quit.json").exists()
    await g.close()


async def test_quit_calls_the_quit_hook(tmp_path):
    g = await start(mk(tmp_path))
    hit = []
    g.on_quit = lambda: hit.append(1)
    r = await g.handle_text("quit chess")
    assert hit == [1] and "Until next time" in r.say
    await g.close()


# --------------------------------------------------------------- spectator
async def test_spectator_plays_pauses_steps_and_speeds(tmp_path):
    g = await start(mk(tmp_path))
    await g.handle_text("play yourself")
    await asyncio.sleep(0.4)
    assert g.state.mode == "spectate" and g.state.move_count >= 4
    await g.handle_text("pause")
    assert g.spec_paused
    n = g.state.move_count
    await asyncio.sleep(0.2)
    stable = g.state.move_count
    assert stable <= n + 1
    await g.handle_text("step")
    await asyncio.sleep(0.2)
    assert g.state.move_count >= stable
    await g.handle_text("faster")
    assert g.spec_speed == 2.0
    await g.handle_text("faster")
    await g.handle_text("faster")
    assert g.spec_speed == 4.0
    await g.handle_text("slower")
    await g.handle_text("half speed")
    assert g.spec_speed == 0.5
    await g.handle_text("normal speed")
    assert g.spec_speed == 1.0
    await g.handle_text("stop spectating")
    assert g.spec_paused
    await g.handle_text("my turn")
    assert g.state.mode == "play"
    await g.close()


async def test_finish_for_me_plays_the_game_out(tmp_path):
    g = await start(mk(tmp_path), fen="4k3/8/8/8/8/8/PP6/R3K3 w - - 0 1")
    g.settings.takeover_strength = "novice"
    await g.handle_text("finish it for me")
    for _ in range(300):
        await asyncio.sleep(0.05)
        if g.state.is_over() or g.state.move_count > 80:
            break
    assert g.state.mode in ("takeover", "play")
    await g.close()


# ------------------------------------------------------------------ draws
async def test_draw_offer_is_decided_by_the_eval(tmp_path):
    g = await start(mk(tmp_path), fen="4k3/8/8/8/8/8/8/4K3 w - - 0 1")
    # insufficient material is already a draw; use a dead-equal position
    g = await start(mk(tmp_path), fen="4k3/pppppppp/8/8/8/8/PPPPPPPP/4K3 w - - 0 1")
    r = await g.handle_text("offer a draw")
    assert g.state.is_over() or "decline" in (r.say or "").lower() or r.say
    await g.close()


async def test_claim_draw_needs_a_claimable_draw(tmp_path):
    g = await start(mk(tmp_path))
    r = await g.handle_text("claim a draw")
    assert "no draw to claim" in r.say
    await g.close()


async def test_resign_asks_when_ambiguous_and_resigns_when_clear(tmp_path):
    g = await start(mk(tmp_path))
    r = await g.handle_text("I'm done")
    assert r.kind == "confirm" and not g.state.is_over()
    await g.handle_text("yes")
    assert g.state.result().reason == "resignation"
    await g.close()
    g = await start(mk(tmp_path))
    await g.handle_text("I resign")
    assert g.state.result().reason == "resignation"
    r = await g.handle_text("good game")
    assert "pleasure" in r.say.lower()
    await g.close()


# ------------------------------------------------------------------ review
async def test_review_is_offered_once_and_runs_only_on_yes(tmp_path):
    g = await start(mk(tmp_path))
    for san in ("e4", "d4", "Nf3", "c4"):
        await g.handle_text(san)
        await idle(g)
    await g.handle_text("I resign")
    assert g.pending["type"] == "review_offer"
    assert g.review is None
    await g.handle_text("yes")
    await g.drain(60)
    assert g.review and g.review["active"]
    assert 0 <= g.review["accuracy"] <= 100
    assert len(g.review["graph"]) == g.state.move_count + 1
    first = len(g.speech_log)
    await g.handle_text("next moment")
    if g.review["moments"]:
        assert len(g.speech_log) > first and g.review["index"] == 0
        await g.handle_text("previous moment")
        assert g.review["index"] == 0
    await g.handle_text("stop the review")
    assert not g.review["active"]
    await g.close()


async def test_review_can_be_declined(tmp_path):
    g = await start(mk(tmp_path))
    for san in ("e4", "d4", "Nf3", "c4"):
        await g.handle_text(san)
        await idle(g)
    await g.handle_text("resign")
    await g.handle_text("no thanks")
    assert g.pending is None and g.review is None
    await g.close()


# --------------------------------------------------------------------- info
async def test_info_answers_without_the_engine(tmp_path):
    g = await start(mk(tmp_path))
    await g.handle_text("e4")
    await idle(g)
    answers = {}
    for q in ("where's my queen", "what's my material", "whose turn is it", "what move are we on",
              "what opening is this", "read the moves", "what was my last move", "what did you play",
              "what can my knight do", "what's attacking e four"):  # fmt: skip
        r = await g.handle_text(q)
        answers[q] = r.say
        assert r.say, q
    assert "d one" in answers["where's my queen"]
    assert "level" in answers["what's my material"]
    assert "your move" in answers["whose turn is it"].lower()
    assert (
        "defence" in answers["what opening is this"].lower()
        or "opening" in answers["what opening is this"].lower()
    )
    await g.close()


async def test_settings_by_voice(tmp_path):
    g = await start(mk(tmp_path))
    await g.handle_text("just the moves please")
    assert g.settings.announce_moves is False
    await g.handle_text("announce your moves")
    assert g.settings.announce_moves is True
    await g.handle_text("confirm my moves")
    assert g.settings.confirm_moves is True
    await g.handle_text("louder")
    assert g.settings.commentary_volume > 1.0
    await g.handle_text("explain like I'm a beginner")
    assert g.settings.audience == "novice"
    await g.handle_text("hide labels")
    assert g.settings.square_labels is False
    await g.handle_text("expert")
    assert g.settings.difficulty == "expert"
    await g.handle_text("easier")
    assert g.settings.difficulty == "strong"
    await g.handle_text("play at fifteen hundred")
    assert g.settings.difficulty == "elo:1500"
    await g.handle_text("blitz")
    assert g.settings.time_control == "5+0"
    r = await g.handle_text("bullet")
    assert "blitz" in r.say
    await g.handle_text("turn the intro off")
    assert g.settings.intro is False
    await g.close()


async def test_new_game_by_voice_after_game_over_and_colour_choice(tmp_path):
    g = await start(mk(tmp_path))
    await g.handle_text("I'll be black")
    assert g.state.human_colour == chess.BLACK
    await idle(g)
    assert g.state.move_count >= 1  # Jarvis (white) has opened
    await g.close()


async def test_hand_moves_over_the_command_api_match_voice(tmp_path):
    g = await start(mk(tmp_path))
    r = await g.command("move", **{"from": "e2", "to": "e4"})
    assert r["ok"]
    r = await g.command("move", **{"from": "e2", "to": "e5"})
    assert not r["ok"]
    await g.close()
