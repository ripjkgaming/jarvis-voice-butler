"""Offline wording: spoken moves, replies, template explanations and hints.

Everything here works with no network, no LLM. British English, butler tone.
"""

from __future__ import annotations

import chess

from games.chess.voice_moves import piece_name, spoken_square

_NUM = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_PIECE_CAP = {
    chess.PAWN: "Pawn", chess.KNIGHT: "Knight", chess.BISHOP: "Bishop",
    chess.ROOK: "Rook", chess.QUEEN: "Queen", chess.KING: "King",
}  # fmt: skip

OFFLINE_CHAT = (
    "I'm afraid I can't discuss that offline, Sir. Moves and hints still work."
)
DIDNT_CATCH = (
    "Sorry, Sir, I didn't catch a move. Try something like 'knight to f three'."
)
NATO_LEGEND = (
    "Files by name, Sir: alpha, bravo, charlie, delta, echo, foxtrot, golf, hotel. "
    "The square codes are shown on the board."
)


def number_words(n: int) -> str:
    if 0 <= n < 10:
        return _NUM[n]
    ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    teens = [
        "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
        "seventeen", "eighteen", "nineteen",
    ]  # fmt: skip
    tens = [
        "",
        "",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
    ]
    if n < 20:
        return teens[n - 10]
    if n < 100:
        return tens[n // 10] + (("-" + ones[n % 10]) if n % 10 else "")
    if n < 1000:
        rest = n % 100
        return (
            ones[n // 100]
            + " hundred"
            + ((" and " + number_words(rest)) if rest else "")
        )
    return str(n)


def say_square(sq: int) -> str:
    return spoken_square(sq)


def announce_move(board_before: chess.Board, move: chess.Move) -> str:
    """One-line announcement: "Knight to f six." / "Pawn takes d five."."""
    if board_before.is_castling(move):
        side = "kingside" if chess.square_file(move.to_square) == 6 else "queenside"
        line = f"Castles {side}."
    else:
        piece = board_before.piece_at(move.from_square)
        assert piece is not None
        name = _PIECE_CAP[piece.piece_type]
        cap = board_before.is_capture(move)
        dest = say_square(move.to_square)
        if board_before.is_en_passant(move):
            line = f"Pawn takes {dest}, en passant."
        elif cap:
            line = f"{name} takes {dest}."
        else:
            line = f"{name} to {dest}."
        if move.promotion:
            line = line[:-1] + f", promoting to a {piece_name(move.promotion)}."
    b = board_before.copy()
    b.push(move)
    if b.is_checkmate():
        line += " Checkmate."
    elif b.is_check():
        line += " Check."
    return line


def confirm_line(board: chess.Board, move: chess.Move) -> str:
    return (
        "Playing "
        + announce_move(board, move)[0].lower()
        + announce_move(board, move)[1:]
    )


def result_line(outcome: str, reason: str, human_white: bool) -> str:
    if outcome == "1/2-1/2":
        return f"A draw, Sir, by {reason}."
    white_won = outcome == "1-0"
    you_won = white_won == human_white
    if reason == "checkmate":
        return (
            "Checkmate. A fine game, Sir."
            if you_won
            else "Checkmate. Better luck next time, Sir."
        )
    if reason == "resignation":
        return "As you wish, Sir. Game over." if not you_won else "I resign, Sir."
    if reason == "timeout":
        return (
            "Time's up, Sir."
            if not you_won
            else "Your opponent's flag has fallen, Sir."
        )
    return f"Game over, Sir: {reason}."


def disambiguation_question(board: chess.Board, moves: list[chess.Move]) -> str:
    names = []
    for m in moves:
        p = board.piece_at(m.from_square)
        names.append(
            (
                piece_name(p.piece_type) if p else "piece",
                chess.square_name(m.from_square),
            )
        )
    if len(names) == 2 and names[0][0] == names[1][0]:
        return f"The {names[0][0]} on {names[0][1]} or the one on {names[1][1]}, Sir?"
    parts = [f"the {n} on {sq}" for n, sq in names]
    return "Which, Sir: " + ", or ".join(parts) + "?"


# ---------------------------------------------------------------- explanations
def classification_phrase(label: str) -> str:
    return {
        "Best": "the best move",
        "Good": "a good move",
        "Inaccuracy": "a slight inaccuracy",
        "Mistake": "a mistake",
        "Blunder": "a blunder",
    }.get(label, "a move")


def eval_phrase(cp: int | None, mate: int | None, human_white: bool) -> str:
    """Plain-English evaluation from the human's point of view."""
    if mate is not None:
        n = abs(mate)
        who_white = mate > 0
        yours = who_white == human_white
        return (
            f"There is a forced mate for you in {number_words(n)}, Sir."
            if yours
            else f"I have a forced mate in {number_words(n)}, Sir."
        )
    v = cp or 0
    if not human_white:
        v = -v
    a = abs(v)
    if a < 30:
        return "The position is level, Sir."
    size = "slightly" if a < 80 else "clearly" if a < 200 else "decisively"
    if a >= 500:
        size = "overwhelmingly"
    return f"You are {size} {'better' if v > 0 else 'worse'}, Sir."


def template_explanation(facts: dict) -> dict:
    """Offline stand-in for the LLM: built only from engine facts."""
    san = facts.get("move_san", "that move")
    label = facts.get("classification") or "Good"
    mover = facts.get("mover", "white")
    best = facts.get("best_san")
    spoken = f"{san} was {classification_phrase(label)}."
    if best and label not in ("Best",) and best != san:
        spoken += f" {best} was stronger."
    detail = f"{mover.capitalize()} played {san}, which the engine rates as {classification_phrase(label)}."
    if best and best != san:
        detail += f" The engine preferred {best}."
    if facts.get("pv_san"):
        detail += " Likely continuation: " + " ".join(facts["pv_san"]) + "."
    return {
        "headline": f"{san}: {label}",
        "spoken": spoken,
        "detail": detail,
        "source": "template",
    }


def hint_level_text(level: int, piece: str, from_sq: str, to_sq: str, idea: str) -> str:
    if level <= 1:
        return f"Consider your {piece} on {from_sq}, Sir."
    if level == 2:
        return f"Your {piece} on {from_sq} has a useful idea: {idea}."
    return f"Play {piece} to {to_sq}, Sir."


def idea_for(board: chess.Board, move: chess.Move) -> str:
    if board.is_capture(move):
        return "it wins material"
    if board.is_castling(move):
        return "it tucks the king away safely"
    b = board.copy()
    b.push(move)
    if b.is_checkmate():
        return "it delivers mate"
    if b.is_check():
        return "it gives check"
    p = board.piece_at(move.from_square)
    if (
        p
        and p.piece_type in (chess.KNIGHT, chess.BISHOP)
        and chess.square_rank(move.from_square) in (0, 7)
    ):
        return "it develops a minor piece"
    if chess.square_name(move.to_square) in ("d4", "e4", "d5", "e5"):
        return "it claims the centre"
    return "it improves your position"


# ---------------------------------------------------------------- info answers
def read_moves(san: list[str], n: int | None = None) -> str:
    if not san:
        return "No moves have been played yet, Sir."
    chunk = san[-n:] if n else san
    start = len(san) - len(chunk)
    parts = []
    for i, m in enumerate(chunk, start=start):
        num = i // 2 + 1
        prefix = f"{number_words(num)}, " if i % 2 == 0 else ""
        parts.append(prefix + _speak_san(m))
    return " ".join(p + "." for p in parts)


def _speak_san(san: str) -> str:
    s = san.rstrip("+#")
    if s.startswith("O-O-O"):
        out = "castles queenside"
    elif s.startswith("O-O"):
        out = "castles kingside"
    else:
        piece = {
            "N": "knight",
            "B": "bishop",
            "R": "rook",
            "Q": "queen",
            "K": "king",
        }.get(s[0], "pawn")
        body = s[1:] if s[0] in "NBRQK" else s
        cap = "x" in body
        body = body.replace("x", "")
        promo = None
        if "=" in body:
            body, promo = body.split("=")
        sq = body[-2:]
        out = f"{piece} {'takes' if cap else 'to'} {sq[0]} {_NUM[int(sq[1])]}"
        if promo:
            out += f", promoting to a {piece_name({'Q': chess.QUEEN, 'R': chess.ROOK, 'B': chess.BISHOP, 'N': chess.KNIGHT}[promo])}"
    if san.endswith("#"):
        out += ", checkmate"
    elif san.endswith("+"):
        out += ", check"
    return out


def time_phrase(seconds: float) -> str:
    s = round(seconds)
    m, sec = divmod(s, 60)
    if m and sec:
        return f"{number_words(m)} minute{'s' if m != 1 else ''} {number_words(sec)} second{'s' if sec != 1 else ''}"
    if m:
        return f"{number_words(m)} minute{'s' if m != 1 else ''}"
    return f"{number_words(sec)} second{'s' if sec != 1 else ''}"


# A tiny offline opening table (ECO-flavoured) keyed by SAN prefix.
_OPENINGS = [
    ("e4 e5 Nf3 Nc6 Bb5", "the Ruy Lopez"),
    ("e4 e5 Nf3 Nc6 Bc4", "the Italian Game"),
    ("e4 e5 Nf3 Nc6 d4", "the Scotch Game"),
    ("e4 e5 Nf3 Nf6", "the Petrov Defence"),
    ("e4 e5 f4", "the King's Gambit"),
    ("e4 e5", "the Open Game"),
    ("e4 c5", "the Sicilian Defence"),
    ("e4 e6", "the French Defence"),
    ("e4 c6", "the Caro-Kann Defence"),
    ("e4 d5", "the Scandinavian Defence"),
    ("e4 d6", "the Pirc Defence"),
    ("e4 g6", "the Modern Defence"),
    ("d4 d5 c4", "the Queen's Gambit"),
    ("d4 Nf6 c4 g6", "the King's Indian Defence"),
    ("d4 Nf6 c4 e6", "the Nimzo or Queen's Indian setup"),
    ("d4 d5", "the Queen's Pawn Game"),
    ("d4 Nf6", "the Indian Defence"),
    ("c4", "the English Opening"),
    ("Nf3", "the Reti Opening"),
    ("f4", "Bird's Opening"),
    ("b3", "the Nimzo-Larsen Attack"),
    ("g3", "the King's Fianchetto Opening"),
    ("e4", "King's Pawn Opening"),
    ("d4", "Queen's Pawn Opening"),
]


def opening_name(san: list[str]) -> str | None:
    played = " ".join(san[:12])
    for prefix, name in _OPENINGS:
        if played == prefix or played.startswith(prefix + " "):
            return name
    return None
