"""Spoken-move parser: grammar-expanded fixtures plus hand-picked corpus rows."""

import json
import random
import sys
from pathlib import Path

import chess
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import chess_grammar as G  # noqa: N812

from games.chess import voice_moves as vm

CORPUS = json.loads((Path(__file__).parent / "voice_corpus.json").read_text())[
    "chess_moves"
]


def _check(board: chess.Board, text: str, expect: str) -> bool:
    r = vm.parse_move(text, board)
    if expect.startswith("ambiguous:"):
        want = None if expect.endswith("*") else set(expect[10:].split(","))
        return r.kind == "ambiguous" and (
            want is None or want <= {m.uci() for m in r.moves}
        )
    if expect == "illegal":
        return r.kind in ("illegal", "castle_illegal")
    if expect == "none":
        return r.kind == "none"
    return r.kind == "move" and r.moves[0].uci() == expect


# ------------------------------------------------------------- grammar fixtures
def test_grammar_expansion_is_large():
    cases = G.all_cases()
    assert len(cases) > 2000
    assert len({n for n, _, _ in cases}) == len(G.POSITIONS)


def test_clean_text_is_at_least_95_percent_exact():
    cases = G.all_cases()
    bad = []
    for name, text, exp in cases:
        r = vm.parse_move(text, chess.Board(G.POSITIONS[name]))
        if not (r.kind == "move" and r.moves[0].uci() == exp):
            bad.append((name, text, exp, r.kind))
    assert 1 - len(bad) / len(cases) >= 0.95, bad[:10]


def test_homophone_noised_text_is_at_least_90_percent_exact():
    rng = random.Random(11)
    cases = G.all_cases(seed=5)
    bad = []
    for name, text, exp in cases:
        noisy = G.noisy(text, rng)
        r = vm.parse_move(noisy, chess.Board(G.POSITIONS[name]))
        if not (r.kind == "move" and r.moves[0].uci() == exp):
            bad.append((name, noisy, exp, r.kind))
    assert 1 - len(bad) / len(cases) >= 0.90, bad[:10]


def test_no_illegal_move_is_ever_returned():
    """Fuzz: whatever is said, a 'move' result is always legal."""
    rng = random.Random(1234)
    vocab = [
        "knight",
        "bishop",
        "rook",
        "queen",
        "king",
        "pawn",
        "takes",
        "to",
        "on",
        "from",
        "castle",
        "kingside",
        "queenside",
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
        "g",
        "h",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "e4",
        "d5",
        "f3",
        "g1",
        "a8",
        "h1",
        "c6",
        "promote",
        "en",
        "passant",
        "take",
        "back",
        "it",
        "the",
        "my",
        "left",
        "right",
        "horse",
        "night",
        "echo",
        "alpha",
        "for",
        "be",
        "see",
    ]
    n = 0
    for name, fen in G.POSITIONS.items():
        board = chess.Board(fen)
        legal = set(board.legal_moves)
        for _ in range(400):
            text = " ".join(rng.choice(vocab) for _ in range(rng.randint(1, 7)))
            r = vm.parse_move(text, board)
            n += 1
            if r.kind == "move":
                assert r.moves[0] in legal, (name, text)
            for m in r.moves:
                assert m in legal, (name, text)
    assert n > 5000


# ------------------------------------------------------------ hand-picked corpus
def test_corpus_has_at_least_300_chess_rows():
    assert len(CORPUS) >= 300


@pytest.mark.parametrize("row", CORPUS, ids=lambda r: f"{r['position']}::{r['text']}")
def test_corpus_row(row):
    board = chess.Board(row["fen"])
    assert _check(board, row["text"], row["expect"]), vm.parse_move(row["text"], board)


# ------------------------------------------------------------------ behaviours
def test_ambiguity_asks_and_lists_both_pieces():
    b = chess.Board(G.POSITIONS["two_knights"])
    r = vm.parse_move("knight to e4", b)
    assert r.kind == "ambiguous"
    assert {m.from_square for m in r.moves} == {chess.C3, chess.G3}


def test_illegal_reply_names_the_blocker():
    b = chess.Board()
    r = vm.parse_move("bishop to g5", b)
    assert r.kind == "illegal"
    assert "can't reach g5" in r.reason and "pawn on d2" in r.reason


def test_castling_explains_why_not():
    b = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w - - 0 1")
    assert (
        "rook has moved" in vm.parse_move("castle kingside", b).reason
        or "king has moved" in vm.parse_move("castle kingside", b).reason
    )
    b = chess.Board("4k3/8/8/8/8/8/4r3/R3K2R w KQ - 0 1")
    assert "in check" in vm.parse_move("castle", b).reason
    b = chess.Board("4k3/8/8/8/8/8/5r2/R3K2R w KQ - 0 1")
    assert "attacked" in vm.parse_move("castle kingside", b).reason


def test_promotion_defaults_to_queen_and_says_so():
    b = chess.Board(G.POSITIONS["promotion"])
    r = vm.parse_move("promote", b)
    assert r.kind == "move" and r.moves[0].promotion == chess.QUEEN
    r = vm.parse_move("a8", b)
    assert r.promotion_defaulted


def test_context_words_take_it_and_recapture():
    b = chess.Board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 2")
    ctx = vm.MoveContext(last_to=chess.D5, last_capture_sq=chess.D5)
    assert vm.parse_move("take it", b, ctx).moves[0].uci() == "e4d5"
    assert vm.parse_move("recapture", b, ctx).moves[0].uci() == "e4d5"
    assert vm.parse_move("take back", b, ctx).moves[0].uci() == "e4d5"
    assert vm.parse_move("take it", b).kind == "none"


def test_wake_words_are_never_a_move():
    from games.chess.text_norm import is_wake_phrase

    assert is_wake_phrase("hey Jarvis knight f3")
    # callers discard it before parsing; as a suffix it is fine
    assert vm.parse_move("knight f3 Jarvis", chess.Board()).moves[0].uci() == "g1f3"


def test_discard_profanity_noise():
    r = vm.parse_move("f-ing e4", chess.Board())
    assert r.kind == "none" and r.reason == "discard"


def test_en_passant_stt_variants():
    b = chess.Board(G.POSITIONS["en_passant"])
    for t in (
        "en passant",
        "on passant",
        "take en passant",
        "e takes f6 en passant",
        "take it en passant",
    ):
        assert vm.parse_move(t, b).moves[0].uci() == "e5f6", t


@pytest.mark.parametrize(
    ("answer", "uci"),
    [
        ("one", "c3e4"),
        ("two", "g3e4"),
        ("the first", "c3e4"),
        ("the second", "g3e4"),
        ("first one", "c3e4"),
        ("second", "g3e4"),
        ("number one", "c3e4"),
        ("c3", "c3e4"),
        ("the one on g3", "g3e4"),
        ("g3", "g3e4"),
        ("the left one", "c3e4"),
        ("the right one", "g3e4"),
        ("the one on the king's side", "g3e4"),
        ("queen's side", "c3e4"),
        ("the front one", None),
        ("1", "c3e4"),
        ("2", "g3e4"),
    ],
)
def test_disambiguation_answers(answer, uci):
    b = chess.Board(G.POSITIONS["two_knights"])
    cands = vm.parse_move("knight to e4", b).moves
    got = vm.resolve_choice(answer, cands, b)
    if uci is None:
        assert got is None or isinstance(got, chess.Move)
    else:
        assert isinstance(got, chess.Move) and got.uci() == uci


@pytest.mark.parametrize("answer", ["never mind", "cancel", "cancel that"])
def test_disambiguation_cancel(answer):
    b = chess.Board(G.POSITIONS["two_knights"])
    cands = vm.parse_move("knight to e4", b).moves
    assert vm.resolve_choice(answer, cands, b) == "cancel"


def test_black_left_right_are_relative_to_black():
    b = chess.Board(G.POSITIONS["start_black"])
    # black's left is the h-side (the king's knight g8), right is the a-side
    assert vm.parse_move("the left knight to f6", b).moves[0].uci() == "g8f6"
    assert vm.parse_move("the right knight to c6", b).moves[0].uci() == "b8c6"


def test_known_merged_stt_errors():
    b = chess.Board()
    for text, uci in [("before", "b2b4"), ("be for", "b2b4"), ("d for", "d2d4"), ("eat for", "e2e4"),
                      ("each four", "e2e4"), ("see three", "c2c3"), ("sea tree", "c2c3"), ("a tree", "a2a3"),
                      ("deep four", "d2d4"), ("age three", "h2h3")]:  # fmt: skip
        assert vm.parse_move(text, b).moves[0].uci() == uci, text
    assert vm.parse_move(
        "gee five", chess.Board("4k3/8/8/8/8/8/6P1/4K3 w - - 0 1")
    ).kind in ("illegal", "none")


def test_homophone_pieces_from_stt():
    b = chess.Board()
    assert vm.parse_move("porn to e four", b).moves[0].uci() == "e2e4"
    assert vm.parse_move("prawn e4", b).moves[0].uci() == "e2e4"
    assert vm.parse_move("pond to d4", b).moves[0].uci() == "d2d4"
