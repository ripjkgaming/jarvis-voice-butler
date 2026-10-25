"""Expand the Appendix A grammar into (text, expected move) fixtures.

Slot combinations are generated from real positions, so the expected move is
known by construction (never taken from the parser's own output). Templates
that would not identify the move uniquely are skipped: an ambiguous phrase
is tested separately and must ask, not guess.
"""

from __future__ import annotations

import itertools
import random

import chess

NUM = ["", "one", "two", "three", "four", "five", "six", "seven", "eight"]
NATO = dict(
    zip(
        "abcdefgh",
        ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"],
        strict=True,
    )
)
PIECE_WORDS = {
    chess.KNIGHT: ["knight", "horse"],
    chess.BISHOP: ["bishop"],
    chess.ROOK: ["rook"],
    chess.QUEEN: ["queen"],
    chess.KING: ["king"],
    chess.PAWN: ["pawn"],
}
PROMO_WORDS = {
    chess.QUEEN: "queen",
    chess.ROOK: "rook",
    chess.BISHOP: "bishop",
    chess.KNIGHT: "knight",
}
PREFIX = [
    "",
    "",
    "",
    "please ",
    "could you ",
    "I'd like to ",
    "let's ",
    "now ",
    "okay ",
    "um ",
    "I'll ",
]
SUFFIX = ["", "", "", " please", " Jarvis", " sir", " thanks", " for me", " now"]

POSITIONS = {
    "start": chess.STARTING_FEN,
    "start_black": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
    "italian": "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
    "open_centre": "r1bqkbnr/pppp1ppp/2n5/8/3pP3/5N2/PPP2PPP/RNBQKB1R w KQkq - 0 4",
    "castle_ready": "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQK2R w KQkq - 0 5",
    "castle_both": "r3k2r/pppqbppp/2npbn2/4p3/2B1P3/2NP1N2/PPP1QPPP/R3K2R w KQkq - 0 8",
    "black_castle": "r3k2r/pppqbppp/2npbn2/4p3/2B1P3/2NP1N2/PPP1QPPP/R1B2RK1 b kq - 1 8",
    "en_passant": "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3",
    "promotion": "8/P6k/8/8/8/8/7K/8 w - - 0 1",
    "promo_capture": "1n5k/P7/8/8/8/8/7K/8 w - - 0 1",
    "black_promotion": "8/7k/8/8/8/8/p6K/8 b - - 0 1",
    "two_knights": "4k3/8/8/8/8/2N3N1/8/4K3 w - - 0 1",
    "two_rooks": "4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1",
    "middlegame": "r2q1rk1/ppp2ppp/2n1bn2/3pp3/1bPP4/2N1PN2/PP2BPPP/R1BQ1RK1 w - - 0 9",
    "queens": "3qk3/8/8/3Q4/8/8/8/3QK3 w - - 0 1",
    "endgame": "8/5pk1/6p1/8/8/6P1/5PK1/3R4 w - - 0 1",
    "check": "4k3/8/8/8/1b6/8/8/4K3 w - - 0 1",
}


def sq_forms(sq: int) -> list[str]:
    _f, r = chess.square_file(sq), chess.square_rank(sq) + 1
    name = chess.square_name(sq)
    return [
        name,
        f"{name[0]} {name[1]}",
        f"{name[0]} {NUM[r]}",
        f"{NATO[name[0]]} {NUM[r]}",
    ]


def _unique(board: chess.Board, pred) -> bool:
    return sum(1 for m in board.legal_moves if pred(m)) == 1


def phrasings(
    board: chess.Board, m: chess.Move, rng: random.Random, limit: int = 14
) -> list[str]:
    p = board.piece_at(m.from_square)
    assert p is not None
    pt = p.piece_type
    frm, to = sq_forms(m.from_square), sq_forms(m.to_square)
    cap = board.is_capture(m)
    out: list[str] = []

    def uniq_piece_to() -> bool:
        return _unique(
            board,
            lambda x: (
                board.piece_at(x.from_square).piece_type == pt  # type: ignore[union-attr]
                and x.to_square == m.to_square
            ),
        )

    if board.is_castling(m):
        k = chess.square_file(m.to_square) == 6
        side = "kingside" if k else "queenside"
        out += [
            f"castle {side}",
            f"castle {'king' if k else 'queen'} side",
            f"{'short' if k else 'long'} castle",
            f"castle {'short' if k else 'long'}",
            f"{'kingside' if k else 'queenside'} castle",
            "O-O" if k else "O-O-O",
            "oh oh" if k else "oh oh oh",
            "zero zero" if k else "zero zero zero",
            f"king to {chess.square_name(m.to_square)}",
            f"king castles {side}",
        ]
        return out[:limit]
    promo = m.promotion
    if promo:
        w = PROMO_WORDS[promo]
        for t in to[:3]:
            out += [
                f"{t} {w}",
                f"{t} equals {w}",
                f"pawn to {t} promote to {w}",
                f"pawn to {t} {w}",
                f"push to {t} and make it a {w}",
            ]
        if cap:
            ff = chess.FILE_NAMES[chess.square_file(m.from_square)]
            out += [f"{ff} takes {to[0]} {w}", f"{frm[0]} takes {to[0]} {w}"]
        for a, b in itertools.islice(itertools.product(frm[:2], to[:2]), 3):
            out.append(f"{a} to {b} {w}")
        return out[:limit]
    if board.is_en_passant(m):
        ff = chess.FILE_NAMES[chess.square_file(m.from_square)]
        out += [
            f"{ff} takes {to[0]} en passant",
            f"pawn takes {to[0]} en passant",
            f"{frm[0]} takes {to[0]}",
            f"{ff}x{to[0]}",
            f"{frm[0]} to {to[0]}",
        ]
        return out[:limit]
    # from-to forms always identify the move
    for a, b in [(frm[0], to[0]), (frm[1], to[1]), (frm[2], to[2]), (frm[0], to[2])]:
        out += [f"{a} to {b}", f"{a} {b}", f"from {a} to {b}"]
    out.append(f"{frm[0]}{to[0]}")
    names = PIECE_WORDS[pt]
    if pt == chess.PAWN:
        if not cap:
            if _unique(
                board,
                lambda x: (
                    x.to_square == m.to_square
                    and not board.is_capture(x)
                    and board.piece_at(x.from_square).piece_type == chess.PAWN
                ),
            ):  # type: ignore[union-attr]
                for t in to[:3]:
                    out += [t, f"pawn to {t}", f"pawn {t}", f"play {t}", f"push {t}"]
        else:
            ff = chess.FILE_NAMES[chess.square_file(m.from_square)]
            out += [
                f"{ff} takes {to[0]}",
                f"{ff}x{to[0]}",
                f"{ff} takes {to[2]}",
                f"pawn takes {to[0]}"
                if _unique(
                    board,
                    lambda x: (
                        board.piece_at(x.from_square).piece_type == chess.PAWN
                        and x.to_square == m.to_square
                    ),
                )
                else f"{ff} takes {to[0]}",
            ]
        return out[:limit]
    if uniq_piece_to():
        for nm in names:
            for t in to[:3]:
                if cap:
                    out += [
                        f"{nm} takes {t}",
                        f"{nm} captures {t}",
                        f"{nm} takes on {t}",
                        f"{nm} to {t}",
                    ]
                else:
                    out += [
                        f"{nm} to {t}",
                        f"{nm} {t}",
                        f"{nm} goes to {t}",
                        f"{nm} moves to {t}",
                        f"move my {nm} to {t}",
                    ]
        sym = {
            chess.KNIGHT: "N",
            chess.BISHOP: "B",
            chess.ROOK: "R",
            chess.QUEEN: "Q",
            chess.KING: "K",
        }[pt]
        out += [f"{sym}{to[0]}", f"{sym}x{to[0]}" if cap else f"{sym}{to[0]}"]
    for nm in names[:1]:
        for a, b in [(frm[0], to[0]), (frm[2], to[2])]:
            out += [f"{nm} from {a} to {b}", f"{nm} {a} {b}", f"the {nm} on {a} to {b}"]
    rng.shuffle(out)
    return out[:limit]


def wrap(text: str, rng: random.Random) -> str:
    return f"{rng.choice(PREFIX)}{text}{rng.choice(SUFFIX)}".strip()


# Speech-to-text slips from Appendix A.2 (rank and file homophones).
_NOISE = [
    (" two ", " to "), (" four ", " for "), (" one ", " won "), (" three ", " tree "),
    (" eight ", " ate "), (" six ", " sicks "), (" b ", " be "), (" c ", " see "),
    (" f ", " ef "), (" g ", " gee "), (" knight ", " night "), (" pawn ", " porn "),
]  # fmt: skip


def noisy(text: str, rng: random.Random) -> str:
    t = f" {text} "
    cands = [(a, b) for a, b in _NOISE if a in t]
    rng.shuffle(cands)
    for a, b in cands[: rng.choice([1, 1, 2])]:
        t = t.replace(a, b, 1)
    return t.strip()


def all_cases(seed: int = 7, per_move: int = 6) -> list[tuple[str, str, str]]:
    """(position name, text, expected uci) for every legal move in every position."""
    rng = random.Random(seed)
    cases = []
    for name, fen in POSITIONS.items():
        board = chess.Board(fen)
        for m in board.legal_moves:
            texts = phrasings(board, m, rng, limit=40)
            rng.shuffle(texts)
            for t in texts[:per_move]:
                cases.append((name, wrap(t, rng), m.uci()))
    return cases
