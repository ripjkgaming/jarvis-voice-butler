"""Series / history / takeover-release / board-analysis voice commands.

Covers the phrase table, number words, homophone mishearings and a
no-collision sweep against the move parser and the pre-existing catalogue.
"""

import random

import chess
import pytest

from games.chess import commands, voice_moves
from games.chess import intents_series as isx
from games.chess.settings import ChessSettings

from chess_grammar import POSITIONS, all_cases, noisy


def cls(text):
    return commands.classify(text)


def name(text):
    h = cls(text)
    return h.name if h else None


# ------------------------------------------------------------------ phrase table
TABLE = {
    "series_start": [
        "play five games",
        "play 5 games",
        "play fifteen games",
        "play 10 games",
        "play a hundred games",
        "play one hundred games",
        "play twenty one games",
        "play a series of three games",
        "play three games as white",
        "play three games as black",
        "play four games as alternating colours",
        "play four games alternating colors",
        "play five games at level three",
        "play five games as white at level 4",
        "play until I say stop",
        "keep playing until I say stop",
        "please play five games",
        "can you play seven games",
    ],
    "series_option": [
        "set series colour white",
        "set series colour to black",
        "series colour alternate",
        "series color alternating",
        "series level 5",
        "set series level to seven",
        "series time per move 3",
        "series time per move five seconds",
        "series speak every move",
        "series speak only results",
        "speak every move",
        "speak only results",
        "speak results only",
    ],
    "series_left": [
        "how many games left",
        "how many games are left",
        "games remaining",
        "how many games remain",
        "how many more games",
        "games left",
    ],
    "series_score": [
        "series score",
        "what's the series score",
        "what is the series score",
        "series status",
    ],
    "series_pause": ["pause the series", "pause series"],
    "series_resume": [
        "continue the series",
        "resume the series",
        "resume series",
        "continue series",
    ],
    "series_cancel": [
        "cancel the series",
        "stop the series",
        "end the series",
        "cancel series",
    ],
    "series_resume_offer": [
        "resume the old series",
        "continue the last series",
        "resume my last series",
    ],
    "handback": ["release control", "revert control", "release the control", "my turn"],
    "list_games": [
        "list games",
        "list my games",
        "show my games",
        "list the games",
        "game history",
        "show game history",
    ],
    "open_game": [
        "open game 3",
        "open game three",
        "open game one",
        "open game 2026-10-15",
        "open the last game",
        "open game latest",
    ],
    "review_goto": [
        "go to move 14",
        "go to move fourteen",
        "jump to move 9",
        "go two move 14",
        "go too move 14",
    ],
    "hist_branch": [
        "rewind to move 14 and continue from here",
        "rewind to move fourteen and continue from here",
        "rewind to move 5",
        "rewind to move five and play on",
        "continue from here",
        "branch from here",
    ],
    "hist_close": ["close history", "exit history", "back to the live game"],
    "analyse_board": [
        "analyse the board",
        "analyze the board",
        "analyse board",
        "evaluate the board",
        "what's on the board",
    ],
    "explain": [
        "why did you play that",
        "explain move 5",
        "what was my mistake",
        "what was my blunder",
        "where did I go wrong",
    ],
    "undo": ["undo last move", "undo the last move", "undo"],
    "takeover": ["take over"],
}


@pytest.mark.parametrize("intent,text", [(i, t) for i, ts in TABLE.items() for t in ts])
def test_phrase_maps_to_intent(intent, text):
    assert name(text) == intent, text


# ------------------------------------------------------------------- numbers
@pytest.mark.parametrize(
    "text,n",
    [
        ("play five games", 5),
        ("play fifteen games", 15),
        ("play 10 games", 10),
        ("play a hundred games", 100),
        ("play one hundred games", 100),
        ("play 100 games", 100),
        ("play twenty five games", 25),
        ("play ninety nine games", 99),
        ("play one game", 1),
        ("play a series of two games", 2),
        ("play ate games", 8),
        ("play for games", 4),
        ("play won game", 1),
    ],
)
def test_series_counts(text, n):
    h = cls(text)
    assert h and h.name == "series_start" and h.args["total"] == n


@pytest.mark.parametrize(
    "text",
    [
        "play 101 games",
        "play a hundred and one games",
        "play one hundred and one games",
        "play 0 games",
        "play zero games",
        "play 1000 games",
        "play fifty hundred games",
    ],
)
def test_series_count_out_of_range_rejected(text):
    h = cls(text)
    assert not (h and h.name == "series_start"), (text, h)


def test_series_args():
    h = cls("play five games as black at level four")
    assert h.args["total"] == 5 and h.args["colour"] == "black" and h.args["level"] == 4
    h = cls("play five games alternating colours")
    assert h.args["colour"] == "alternate"
    h = cls("play until I say stop")
    assert h.name == "series_start" and h.args["total"] is None
    assert cls("play three games").args["colour"] == "alternate"
    assert cls("play three games as white").args["colour"] == "white"


def test_series_option_args():
    assert cls("series colour black").args == {"colour": "black"}
    assert cls("series level seven").args == {"level": 7}
    assert cls("series time per move five seconds").args == {"move_time": 5.0}
    assert cls("speak every move").args == {"speak": "moves"}
    assert cls("speak only results").args == {"speak": "results"}


def test_open_game_args():
    assert cls("open game three").args["ref"] == "3"
    assert cls("open game 12").args["ref"] == "12"
    assert cls("open game 2026-10-15").args["ref"] == "2026-10-15"
    assert cls("open the last game").args["ref"] == "1"
    assert cls("open game ate").args["ref"] == "8"
    assert cls("open game to").args["ref"] == "2"


def test_goto_and_branch_args():
    assert cls("go to move 14").args["n"] == 14
    assert cls("go two move 14").args["n"] == 14
    assert cls("go to move fourteen").args["n"] == 14
    assert cls("go to move ate").args["n"] == 8
    h = cls("rewind to move 14 and continue from here")
    assert h.args["n"] == 14
    assert cls("continue from here").args.get("n") is None


# ------------------------------------------------------- series pre-empts moves?
def _boards():
    for fen in POSITIONS.values():
        yield chess.Board(fen)
    yield chess.Board()


NEW_PHRASES = sorted(
    {
        t
        for i, ts in TABLE.items()
        if i not in ("undo", "takeover", "handback", "explain")
        for t in ts
    }
)


@pytest.mark.parametrize("text", NEW_PHRASES)
def test_new_phrases_never_parse_as_moves(text):
    for b in _boards():
        pr = voice_moves.parse_move(text, b, voice_moves.MoveContext())
        assert not (pr.kind == "move" and pr.confidence >= 0.5), (text, pr)
        assert not (pr.kind == "ambiguous" and pr.confidence >= 0.5), (text, pr)


MOVE_TEXTS = [
    "night f three",
    "e four",
    "knight to f three",
    "go e five",
    "pawn to e four",
    "bishop takes c six",
    "queen to d eight",
    "rook a one",
    "age four",
    "h three",
    "castle kingside",
    "night takes e five",
    "e eight queen",
    "b to b four",
    "to e four",
]


@pytest.mark.parametrize("text", MOVE_TEXTS)
def test_moves_are_not_stolen_by_new_rules(text):
    h = cls(text)
    new = {n for n in TABLE} - {"undo", "takeover", "explain", "handback"}
    assert not (h and h.name in new), (text, h)


def test_generated_move_grammar_never_hits_new_intents():
    """Every phrasing the move fixture generator makes (plus noise) skips the new rules."""
    new = {n for n in TABLE} - {"undo", "takeover", "explain", "handback"}
    rng = random.Random(3)
    bad = []
    for _pos, text, _uci in all_cases(seed=11, per_move=2)[:1500]:
        for t in (text, noisy(text, rng)):
            h = cls(t)
            if h and h.name in new:
                bad.append((t, h.name))
    assert not bad, bad[:10]


def test_previous_catalogue_unchanged():
    """The existing phrase corpus still classifies as before (no shadowing)."""
    from test_chess_commands import CASES

    wrong = []
    for intent, texts in CASES.items():
        for t in texts:
            h = cls(t)
            if not h or h.name != intent:
                wrong.append((t, intent, h.name if h else None))
    assert not wrong, wrong[:10]


@pytest.mark.parametrize(
    "text,intent",
    [
        ("what's the score", "analyse"),
        ("score", "analyse"),
        ("step back", "ctl_prev"),
        ("step forward", "ctl_next"),
        ("play chess", "start"),
        ("play a game", "start"),
        ("take over", "takeover"),
        ("go to move 3", "review_goto"),
        ("analyse", "analyse"),
    ],
)
def test_old_behaviour_kept(text, intent):
    assert name(text) == intent


# --------------------------------------------------------------- router (isx)
class FakeState:
    mode = "play"
    move_count = 0

    def is_over(self):
        return False


class FakeGame:
    def __init__(self, store=None):
        self.store = store
        self.series = None
        self.hist_view = None
        self.state = FakeState()
        self.spawned = []

    def _spawn(self, c):
        self.spawned.append(c)
        c.close()

    async def _changed(self):
        pass

    async def new_game(self, **kw):  # branch loads the new record inline
        self.new_game_kw = kw


@pytest.fixture
def game(tmp_path):
    from games.chess.store import GameStore

    return FakeGame(GameStore(tmp_path))


async def say(game, text):
    h = cls(text)
    assert h, text
    r = await isx.handle(game, h, text)
    return r


async def test_score_without_series_falls_through(game):
    assert await say(game, "what's the score") is None  # old analyse path


async def test_series_flow(game):
    r = await say(game, "play three games as white")
    assert r.ok and "three" in r.say.lower()
    assert game.series.state.total == 3
    r = await say(game, "how many games left")
    assert "three" in r.say.lower()
    r = await say(game, "what's the score")  # series active: series score
    assert r is not None and "win" in r.say.lower()
    r = await say(game, "series level 4")
    assert game.series.state.level == 4
    r = await say(game, "pause the series")
    assert game.series.state.status == "paused"
    r = await say(game, "continue the series")
    assert game.series.state.status == "running"
    r = await say(game, "cancel the series")
    assert game.series.state.status == "cancelled"
    assert await say(game, "what's the score") is None


async def test_series_commands_without_series(game):
    for t in (
        "how many games left",
        "pause the series",
        "cancel the series",
        "series level 3",
    ):
        r = await say(game, t)
        assert r is not None and not r.ok


async def test_list_and_open_and_step(game):
    r = await say(game, "list games")
    assert "no games" in r.say.lower()
    r = await say(game, "open game 1")
    assert not r.ok
    rec = game.store.new_record(mode="play", white="user", black="jarvis")
    b = chess.Board()
    for i, u in enumerate(["e2e4", "e7e5", "g1f3"], 1):
        m = chess.Move.from_uci(u)
        san = b.san(m)
        b.push(m)
        game.store.append_move(
            rec, {"ply": i, "san": san, "uci": u, "fen_after": b.fen()}
        )
    game.store.finish(rec, "1-0", "resign")
    r = await say(game, "list games")
    assert r.ok and "one game" in r.say
    r = await say(game, "open game 1")
    assert r.ok and game.hist_view.current_ply == 3
    # step back / go to move are remapped only while history is open
    r = await say(game, "step back")
    assert game.hist_view.current_ply == 2
    r = await say(game, "step forward")
    assert game.hist_view.current_ply == 3
    r = await say(game, "go to move 1")
    assert game.hist_view.current_ply == 1
    r = await say(game, "close history")
    assert game.hist_view is None
    assert await say(game, "step back") is None  # old ctl_prev route


async def test_branch_does_not_touch_original(game):
    rec = game.store.new_record(mode="play", white="user", black="jarvis")
    b = chess.Board()
    for i, u in enumerate(["e2e4", "e7e5"], 1):
        m = chess.Move.from_uci(u)
        san = b.san(m)
        b.push(m)
        game.store.append_move(
            rec, {"ply": i, "san": san, "uci": u, "fen_after": b.fen()}
        )
    game.store.finish(rec, "*", "abandoned")
    await say(game, "open game 1")
    r = await say(game, "rewind to move 1 and continue from here")
    assert r.ok
    assert len(game.store.load(rec.id).moves) == 2
    assert len(game.store.list()) == 2


async def test_missing_modules_tolerated(game, monkeypatch):
    real = isx._mod
    monkeypatch.setattr(
        isx, "_mod", lambda n: None if n in ("series", "history") else real(n)
    )
    for t in ("play three games", "open game 1"):
        r = await say(game, t)
        assert not r.ok and "not available yet" in r.say.lower()


async def test_handlers_are_fast(game):
    import time

    for t in (
        "play three games",
        "how many games left",
        "list games",
        "pause the series",
    ):
        t0 = time.perf_counter()
        await say(game, t)
        assert time.perf_counter() - t0 < 0.05


async def test_end_to_end_through_handle_text(tmp_path):
    from games.chess.engine import MiniEngine
    from games.chess.game import ChessGame
    from games.chess.store import GameStore

    g = ChessGame(
        ChessSettings(announce_moves=False, difficulty="novice", colour="white"),
        engine=MiniEngine(7),
        save_dir=tmp_path,
        autosave=False,
        step_delay=0.0,
    )
    g.listening = True
    g.store = GameStore(tmp_path / "s")
    await g.new_game()
    assert (await g.handle_text("how many games left")).say.startswith(
        "There's no series"
    )
    r = await g.handle_text("play three games")
    assert r.kind == "series" and g.series.state.total == 3
    r = await g.handle_text("what's the score")
    assert "win" in r.say
    await g.handle_text("cancel the series")
    r = await g.handle_text("e four")  # still a move
    assert g.state.uci_history()[0] == "e2e4"
    await g.close()
