"""Spoken-move parser (Appendix A.2-A.7).

Layered: normalise -> candidate token readings -> semantic items -> constraint
spec -> *legality decides*. Cheapest legal reading wins; several distinct legal
moves at the best cost mean "ask which one". Never guesses between pieces.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

import chess

from games.chess import text_norm

FILES = "abcdefgh"
_PROMO_PIECES = (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)

# --------------------------------------------------------------------- lexicon
_NATO = {
    "alpha": "a", "alfa": "a", "bravo": "b", "charlie": "c", "delta": "d",
    "echo": "e", "foxtrot": "f", "golf": "g", "hotel": "h",
}  # fmt: skip
# homophone -> (file, cost, needs_rank_next)
_FILE_HOMO = {
    "ay": ("a", 0.05, False), "eh": ("a", 0.1, False),
    "be": ("b", 0.05, False), "bee": ("b", 0.05, False), "bea": ("b", 0.1, False),
    "see": ("c", 0.05, False), "sea": ("c", 0.05, False), "si": ("c", 0.1, False),
    "dee": ("d", 0.05, False), "the": ("d", 0.2, True), "deep": ("d", 0.1, True),
    "ee": ("e", 0.05, True), "he": ("e", 0.15, True),
    "eat": ("e", 0.1, True), "each": ("e", 0.1, True),
    "ef": ("f", 0.05, False), "eff": ("f", 0.05, False),
    "gee": ("g", 0.05, False), "jee": ("g", 0.05, False),
    "aitch": ("h", 0.05, False), "haitch": ("h", 0.05, False),
    "age": ("h", 0.1, True),
}  # fmt: skip
_RANK_STRONG = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8,
}  # fmt: skip
_RANK_WEAK = {
    "won": (1, 0.05), "wan": (1, 0.1),
    "to": (2, 0.15), "too": (2, 0.1), "tu": (2, 0.1),
    "tree": (3, 0.1), "free": (3, 0.15),
    "for": (4, 0.1), "fore": (4, 0.1), "far": (4, 0.2),
    "fife": (5, 0.1),
    "sicks": (6, 0.15), "sex": (6, 0.25),
    "ate": (8, 0.1), "ait": (8, 0.1),
}  # fmt: skip
_PIECES = {
    "king": (chess.KING, 0), "kings": (chess.KING, 0),
    "queen": (chess.QUEEN, 0), "queens": (chess.QUEEN, 0), "lady": (chess.QUEEN, 0.05),
    "rook": (chess.ROOK, 0), "rooks": (chess.ROOK, 0), "tower": (chess.ROOK, 0.05),
    "bishop": (chess.BISHOP, 0), "bishops": (chess.BISHOP, 0),
    "knight": (chess.KNIGHT, 0), "knights": (chess.KNIGHT, 0),
    "horse": (chess.KNIGHT, 0), "horsey": (chess.KNIGHT, 0), "horses": (chess.KNIGHT, 0),
    "night": (chess.KNIGHT, 0.1), "nite": (chess.KNIGHT, 0.1),
    "pawn": (chess.PAWN, 0), "pawns": (chess.PAWN, 0),
    "porn": (chess.PAWN, 0.25), "prawn": (chess.PAWN, 0.25), "pond": (chess.PAWN, 0.25),
    "pound": (chess.PAWN, 0.3), "john": (chess.PAWN, 0.3),
}  # fmt: skip
_PIECE_LETTER = {
    "k": chess.KING, "q": chess.QUEEN, "r": chess.ROOK, "n": chess.KNIGHT,
    "p": chess.PAWN, "b": chess.BISHOP,
}  # fmt: skip
_CAP_WORDS = {
    "takes", "take", "taking", "captures", "capture", "capturing", "x", "eats",
    "eat", "kills", "grabs", "grab", "snaps", "hits", "strikes", "wins",
    "trades", "exchanges", "recaptures", "capped",
}  # fmt: skip
_VERB_WORDS = {
    "goes", "go", "moves", "move", "onto", "on", "steps", "advances", "retreats",
    "comes", "jumps", "hops", "slides", "heads", "across", "over", "down", "at",
    "in", "into", "is", "play", "plays", "put", "bring", "send", "develop", "develops",
    "push", "pushes", "advance", "from", "run", "swing",
}  # fmt: skip
_IGN = {
    "the", "my", "a", "an", "of", "with", "that", "this", "your", "his", "her", "its",
    "it", "other", "squared", "square", "side", "but", "just", "only",
    "check", "checkmate", "mate", "giving", "make", "makes", "becomes", "become",
    "turn", "turns", "equals", "equal", "as", "be", "me", "out", "pls", "please",
}  # fmt: skip
_PROMO_WORDS = {
    "promote", "promotes", "promoted", "promotion", "underpromote", "underpromotes",
    "equals", "becomes", "become", "into", "queening",
}  # fmt: skip
_BAD_PHRASE = re.compile(r"\bf-?ing\b|\bfuck")

# canonical token kinds ------------------------------------------------------
# items are (kind, value) tuples


@dataclass
class _Reading:
    items: list[tuple]
    cost: float = 0.0


def _alts_for_word(w: str) -> list[tuple[list[tuple], float, bool]]:
    """Possible (items, cost, needs_rank_next) readings of one word."""
    out: list[tuple[list[tuple], float, bool]] = []
    # exact squares / glued forms ------------------------------------------------
    if re.fullmatch(r"[a-h][1-8]", w):
        return [([("SQ", chess.parse_square(w))], 0.0, False)]
    m = re.fullmatch(r"([a-h][1-8])-?([a-h][1-8])([qrbn])?", w)
    if m:
        its: list[tuple] = [
            ("SQ", chess.parse_square(m.group(1))),
            ("SQ", chess.parse_square(m.group(2))),
        ]
        if m.group(3):
            its.append(("P", _PIECE_LETTER[m.group(3)]))
        return [(its, 0.0, False)]
    m = re.fullmatch(r"([a-h][1-8])=?([qrbn])", w)
    if m:
        return [
            (
                [
                    ("SQ", chess.parse_square(m.group(1))),
                    ("P", _PIECE_LETTER[m.group(2)]),
                ],
                0.0,
                False,
            )
        ]
    m = re.fullmatch(r"([nbrqk])?([a-h])?([1-8])?(x)?([a-h][1-8])(?:=?([nbrq]))?", w)
    if m and (m.group(1) or m.group(2) or m.group(3) or m.group(4)):
        base: list[tuple] = []
        piece, f, r, x, sq, promo = m.groups()
        tail: list[tuple] = []
        if f:
            tail.append(("F", f))
        if r:
            tail.append(("R", int(r)))
        if x:
            tail.append(("CAP",))
        tail.append(("SQ", chess.parse_square(sq)))
        if promo:
            tail.append(("P", _PIECE_LETTER[promo]))
        if piece:
            out.append(([("P", _PIECE_LETTER[piece]), *tail], 0.0, False))
            if piece == "b" and not f and not r and x:  # "bxc6": pawn on the b-file
                out.append(([("F", "b"), *tail], 0.0, False))
        else:
            out.append((tail, 0.0, False))
        _ = base
        return out
    # words -------------------------------------------------------------------
    if w in _NATO:
        return [([("F", _NATO[w])], 0.0, False)]
    if w in _PIECES:
        pt, c = _PIECES[w]
        out.append(([("P", pt)], c, False))
    if w == "castle" or w in ("castles", "castling", "castled"):
        out.append(([("CASTLE",)], 0.0, False))
        if w == "castle":
            out.append(([("P", chess.ROOK)], 0.1, False))
    if w in ("short",):
        out.append(([("SIDE", "k")], 0.0, False))
    if w in ("long",):
        out.append(([("SIDE", "q")], 0.0, False))
    if w in ("kingside",):
        out.append(([("SIDE", "k")], 0.0, False))
    if w in ("queenside",):
        out.append(([("SIDE", "q")], 0.0, False))
    if w in ("enpassant", "enpass"):
        out.append(([("EP",)], 0.0, False))
    if w in _CAP_WORDS:
        out.append(([("CAP",)], 0.0, False))
    if w in ("back", "backwards", "retreat", "retreats"):
        out.append(([("BACK",)], 0.0, False))
    if w in ("forward", "forwards", "ahead", "up"):
        out.append(([("UP",)], 0.0, False))
    if w in ("left",):
        out.append(([("LEFT",)], 0.0, False))
    if w in ("right",):
        out.append(([("RIGHT",)], 0.0, False))
    if w in ("light",):
        out.append(([("LIGHT",)], 0.0, False))
    if w in ("dark",):
        out.append(([("DARK",)], 0.0, False))
    if w in ("first", "second", "front", "closer"):
        out.append(([("IGN",)], 0.2, False))
    if w == "it":
        out.append(([("IT",)], 0.0, False))
    if w == "free":
        out.append(([("FREE",)], 0.0, False))
    if w in ("trade", "trades", "exchange"):
        out.append(([("TRADE",)], 0.0, False))
    if w in ("recapture", "recaptures"):
        out.append(([("RECAP",)], 0.0, False))
    if w in _PROMO_WORDS:
        out.append(([("PROMOTE",)], 0.0, False))
    if w in ("from",):
        out.append(([("FROM",)], 0.0, False))
    if w in ("to", "too", "onto", "into", "up", "towards", "toward"):
        out.append(([("TO",)], 0.0, False))
    if w in _VERB_WORDS or w in _IGN:
        out.append(([("IGN",)], 0.0 if w in _IGN or w in _VERB_WORDS else 0.2, False))
    # single letters ----------------------------------------------------------
    if re.fullmatch(r"[a-h]", w):
        out.append(([("F", w)], 0.0, False))
        if w in _PIECE_LETTER:
            cost = 0.0 if w in ("n", "q", "r", "b") else 0.1
            out.append(([("P", _PIECE_LETTER[w])], cost, False))
    elif w in _PIECE_LETTER:
        out.append(([("P", _PIECE_LETTER[w])], 0.1, False))
    if w in _FILE_HOMO:
        f, c, need = _FILE_HOMO[w]
        out.append(([("F", f)], c, need))
    if re.fullmatch(r"[1-8]", w):
        out.append(([("R", int(w))], 0.0, False))
    if w in _RANK_STRONG:
        out.append(([("R", _RANK_STRONG[w])], 0.0, False))
    if w in _RANK_WEAK:
        r, c = _RANK_WEAK[w]
        out.append(([("R", r)], c, False))
    if w == "before":
        out.append(([("F", "b"), ("R", 4)], 0.1, False))
    if w in ("oh", "zero", "nought", "naught", "0", "o"):
        out.append(([("ZERO",)], 0.0, False))
    if w in ("o-o", "0-0", "ooo", "o-o-o", "0-0-0", "oo"):
        out.append(
            (
                [("CASTLE",), ("SIDE", "q" if len(w.replace("-", "")) == 3 else "k")],
                0.0,
                False,
            )
        )
    if not out:
        out.append(([("IGN",)], 0.3, False))
    return out


def _preprocess(text: str) -> str:
    t = text_norm.strip_wrappers(text)
    # multi-word fixes before tokenising
    t = re.sub(r"\b(?:en|on|in)[ -]pass(?:ant|ont|on)?\b", " enpassant ", t)
    t = re.sub(r"\bking'?s? ?side\b", " kingside ", t)
    t = re.sub(r"\bqueen'?s? ?side\b", " queenside ", t)
    t = re.sub(r"\b(king|queen)'s (knight|bishop|rook|pawn)\b", r" \1side \2 ", t)
    t = re.sub(r"\btuck the king in\b", " castle kingside ", t)
    t = re.sub(r"\b(?:o|0)-(?:o|0)-(?:o|0)\b", " castle queenside ", t)
    t = re.sub(r"\b(?:o|0)-(?:o|0)\b", " castle kingside ", t)
    t = re.sub(
        r"\b(?:oh|zero|nought) (?:oh|zero|nought) (?:oh|zero|nought)\b",
        " castle queenside ",
        t,
    )
    t = re.sub(r"\b(?:oh|zero|nought) (?:oh|zero|nought)\b", " castle kingside ", t)
    t = re.sub(r"\b(?:with|and that'?s|that'?s) (?:check|mate|checkmate)\b", " ", t)
    t = re.sub(r"\b(light|dark)[ -]squared\b", r" \1 ", t)
    t = re.sub(r"\bunder[ -]?promote\b", " underpromote ", t)
    t = re.sub(r"\bsquare of\b", " ", t)
    t = re.sub(r"\bpass(?:es)? on\b", " ", t)
    t = t.replace("-", " ")
    return re.sub(r"\s+", " ", t).strip()


# --------------------------------------------------------------------- spec
@dataclass
class Spec:
    mover: int | None = None
    from_sq: int | None = None
    to_sq: int | None = None
    from_file: str | None = None
    victim: int | None = None
    victim_file: str | None = None
    capture: bool = False
    promo: int | None = None
    ep: bool = False
    castle: bool = False
    side: str | None = None
    back: bool = False
    up: bool = False
    dist: int | None = None
    left: bool = False
    right: bool = False
    light: bool = False
    dark: bool = False
    kingside_q: str | None = None
    it: bool = False
    free: bool = False
    trade: bool = False
    recap: bool = False
    promote_only: bool = False
    junk: float = 0.0

    def anchored(self) -> bool:
        return bool(
            self.to_sq is not None
            or self.from_sq is not None
            or self.castle
            or self.recap
            or (self.capture and (self.it or self.free or self.trade or self.back))
            or self.trade
            or (self.promote_only)
            or (
                self.mover == chess.PAWN
                and (self.up or self.dist or self.from_file)
                and not self.capture
            )
            or (self.mover is not None and self.back)
            or (self.ep)
            or (self.capture and (self.victim is not None or self.victim_file))
            or (self.mover == chess.PAWN and self.from_file and self.capture)
        )


def _merge_squares(items: list[tuple]) -> list[tuple]:
    out: list[tuple] = []
    i = 0
    while i < len(items):
        it = items[i]
        if it[0] == "F" and i + 1 < len(items) and items[i + 1][0] == "R":
            out.append(("SQ", chess.square(FILES.index(it[1]), items[i + 1][1] - 1)))
            i += 2
            continue
        out.append(it)
        i += 1
    return out


def _build_spec(items: list[tuple]) -> Spec:
    items = _merge_squares(items)
    s = Spec()
    seen_cap = False
    seen_from = False
    promote_seen = False
    squares: list[int] = []
    for _idx, it in enumerate(items):
        k = it[0]
        if k == "P":
            pt = it[1]
            if promote_seen and s.promo is None and pt in _PROMO_PIECES:
                s.promo = pt
            elif s.mover is None and not seen_cap and s.to_sq is None and not squares:
                s.mover = pt
            elif squares and pt in (
                chess.KNIGHT,
                chess.BISHOP,
                chess.ROOK,
                chess.QUEEN,
            ):
                s.promo = pt
            elif seen_cap and s.victim is None:
                s.victim = pt
            elif s.mover is None and not seen_cap:
                s.mover = pt
            elif s.promote_only or (
                s.promo is None
                and pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
                and s.mover in (None, chess.PAWN)
            ):
                s.promo = pt
            else:
                s.junk += 0.3
        elif k == "SQ":
            squares.append(it[1])
            if seen_from and s.from_sq is None:
                s.from_sq = it[1]
                seen_from = False
        elif k == "F":
            f = it[1]
            if seen_cap:
                if s.victim_file is None:
                    s.victim_file = f
                else:
                    s.junk += 0.3
            elif s.from_file is None and s.mover in (None, chess.PAWN) and not squares:
                s.from_file = f
                if s.mover is None:
                    s.mover = None  # decided below once we know more
            elif s.from_file is None and s.mover is not None and not squares:
                s.from_file = f  # "knight g f3": file disambiguator
            else:
                s.junk += 0.3
        elif k == "R":
            r = it[1]
            if (s.mover == chess.PAWN and s.dist is None and not squares) or (
                s.mover is None and s.from_file and s.dist is None and not squares
            ):
                s.dist = r
            else:
                s.junk += 0.3
        elif k == "CAP":
            seen_cap = True
            s.capture = True
        elif k == "EP":
            s.ep = True
            s.capture = True
            if s.mover is None:
                s.mover = chess.PAWN
        elif k == "CASTLE":
            s.castle = True
        elif k == "SIDE":
            s.side = it[1]
            if s.castle is False and s.mover is None:
                pass
        elif k == "FROM":
            seen_from = True
        elif k == "BACK":
            s.back = True
        elif k == "UP":
            s.up = True
        elif k == "LEFT":
            s.left = True
        elif k == "RIGHT":
            s.right = True
        elif k == "LIGHT":
            s.light = True
        elif k == "DARK":
            s.dark = True
        elif k == "IT":
            s.it = True
        elif k == "FREE":
            s.free = True
        elif k == "TRADE":
            s.trade = True
            s.capture = True
        elif k == "RECAP":
            s.recap = True
            s.capture = True
        elif k == "PROMOTE":
            s.promote_only = True
            promote_seen = True
        elif k in ("TO", "IGN", "ZERO"):
            pass
    # square allocation
    if s.from_sq is not None and squares:
        others = [q for q in squares if q != s.from_sq]
        s.to_sq = others[-1] if others else None
    elif len(squares) >= 3:
        s.from_sq, s.to_sq = squares[0], squares[-1]
        s.junk += 0.3 * (len(squares) - 2)
    elif len(squares) == 2:
        s.from_sq, s.to_sq = squares
    elif len(squares) == 1:
        s.to_sq = squares[0]
    # castle with a square = rook move
    if s.castle and (s.to_sq is not None or s.from_sq is not None):
        s.castle = False
        if s.mover is None:
            s.mover = chess.ROOK
    # "king castles" -> mover king + castle word
    if s.castle and s.mover not in (None, chess.KING):
        s.castle = False
    # side words only mean something with a castle verb, or as a piece qualifier
    if s.side and not s.castle:
        s.kingside_q = s.side
        s.side = None
    # "take back" = recapture (not undo) when there is a square to retake
    if s.capture and s.back and s.mover is None and s.to_sq is None:
        s.recap = True
        s.back = False
    # "pawn takes" w/ file implies pawn
    if s.from_file and s.capture and s.mover is None:
        s.mover = chess.PAWN
    # pure "promote" / "queen it" -> promotion with the chosen piece
    if (
        s.promote_only
        and s.to_sq is None
        and s.from_sq is None
        and s.promo is None
        and s.mover in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
    ):
        s.promo = s.mover
        s.mover = None
    if (
        s.it
        and not s.capture
        and s.mover in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
        and s.to_sq is None
    ):
        s.promote_only = True
        s.promo = s.mover
        s.mover = None
    if s.promo and not (s.to_sq or s.from_sq):
        s.promote_only = True
    return s


# --------------------------------------------------------------------- matching
@dataclass
class MoveContext:
    """What the parser may know about the game so far."""

    last_to: int | None = None  # square the opponent's last move landed on
    last_capture_sq: int | None = None
    last_mover_colour: chess.Color | None = None


def _is_light(sq: int) -> bool:
    return (chess.square_file(sq) + chess.square_rank(sq)) % 2 == 1


def _victim_piece(board: chess.Board, m: chess.Move) -> tuple[int | None, int | None]:
    if board.is_en_passant(m):
        return chess.PAWN, chess.square_file(m.to_square)
    p = board.piece_at(m.to_square)
    if p is None:
        return None, None
    return p.piece_type, chess.square_file(m.to_square)


def _match(s: Spec, board: chess.Board, ctx: MoveContext | None) -> list[chess.Move]:
    legal = list(board.legal_moves)
    me = board.turn
    out: list[chess.Move] = []
    if s.castle:
        for m in legal:
            if not board.is_castling(m):
                continue
            if s.side == "k" and chess.square_file(m.to_square) != 6:
                continue
            if s.side == "q" and chess.square_file(m.to_square) != 2:
                continue
            out.append(m)
        return out
    for m in legal:
        piece = board.piece_at(m.from_square)
        assert piece is not None
        if s.mover is not None and piece.piece_type != s.mover:
            continue
        if s.to_sq is not None and m.to_square != s.to_sq:
            continue
        if s.from_sq is not None and m.from_square != s.from_sq:
            continue
        if s.from_file is not None and chess.square_file(m.from_square) != FILES.index(
            s.from_file
        ):
            continue
        cap = board.is_capture(m)
        if s.capture and not cap:
            continue
        if s.ep and not board.is_en_passant(m):
            continue
        if s.victim is not None or s.victim_file is not None:
            vp, vf = _victim_piece(board, m)
            if s.victim is not None and vp != s.victim:
                continue
            if s.victim_file is not None and (
                vf is None or vf != FILES.index(s.victim_file)
            ):
                continue
        if m.promotion is not None:
            if s.promo is not None and m.promotion != s.promo:
                continue
            if s.promo is None and m.promotion != chess.QUEEN and not s.promote_only:
                # default to queen later; keep only the queen variant
                continue
            if s.promo is None and m.promotion != chess.QUEEN:
                continue
        elif (s.promo is not None and s.promote_only) or s.promote_only:
            continue
        rank_delta = chess.square_rank(m.to_square) - chess.square_rank(m.from_square)
        if me == chess.BLACK:
            rank_delta = -rank_delta
        if s.back and not rank_delta < 0:
            continue
        if s.up and s.to_sq is None and not rank_delta > 0:
            continue
        if (
            s.dist is not None
            and s.mover == chess.PAWN
            and s.to_sq is None
            and abs(rank_delta) != s.dist
        ):
            continue
        if s.mover == chess.PAWN and s.to_sq is None and (s.up or s.dist) and cap:
            continue
        if s.light and not _is_light(m.from_square):
            continue
        if s.dark and _is_light(m.from_square):
            continue
        if s.kingside_q == "k" and chess.square_file(m.from_square) < 4:
            continue
        if s.kingside_q == "q" and chess.square_file(m.from_square) >= 4:
            continue
        if s.free:
            if not (cap and not board.attackers(not me, m.to_square)):
                continue
            vp, _ = _victim_piece(board, m)
            if vp != chess.PAWN:
                continue
        if s.recap and (
            ctx is None
            or ctx.last_capture_sq is None
            or m.to_square != ctx.last_capture_sq
        ):
            continue
        if (
            s.it
            and s.capture
            and (ctx is None or ctx.last_to is None or m.to_square != ctx.last_to)
        ):
            continue
        out.append(m)
    if (s.left or s.right) and s.mover is not None:
        out = _side_filter(out, board, s.mover, me, s.left)
    return out


def _side_filter(
    moves: list[chess.Move],
    board: chess.Board,
    piece: int,
    colour: chess.Color,
    left: bool,
) -> list[chess.Move]:
    """ "The left rook" is judged against every such piece, not just the movers."""
    files = sorted({chess.square_file(q) for q in board.pieces(piece, colour)})
    if len(files) < 2:
        return moves
    # "left" is relative to the mover's side of the board.
    pick = files[0] if (left == (colour == chess.WHITE)) else files[-1]
    return [m for m in moves if chess.square_file(m.from_square) == pick]


# --------------------------------------------------------------------- result
@dataclass
class ParseResult:
    kind: str  # move | ambiguous | illegal | castle_illegal | none
    moves: list[chess.Move] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    promotion_defaulted: bool = False
    caption: str = ""
    spec: Spec | None = None

    @property
    def move(self) -> chess.Move | None:
        return self.moves[0] if len(self.moves) == 1 else None


def _word_alternatives(words: list[str]) -> list[list[tuple[list[tuple], float, bool]]]:
    return [_alts_for_word(w) for w in words]


def _readings(words: list[str], limit: int = 1500) -> list[_Reading]:
    alts = _word_alternatives(words)
    # prune hopeless alternatives when the product explodes
    prod = 1
    for a in alts:
        prod *= len(a)
    if prod > limit:
        alts = [sorted(a, key=lambda x: x[1])[:2] for a in alts]
    out: list[_Reading] = []
    for combo in itertools.islice(itertools.product(*alts), limit * 4):
        items: list[tuple] = []
        cost = 0.0
        ok = True
        for i, (its, c, need_rank) in enumerate(combo):
            if need_rank:
                nxt = combo[i + 1][0] if i + 1 < len(combo) else []
                if not (nxt and nxt[0][0] == "R"):
                    ok = False
                    break
            items.extend(its)
            cost += c
        if ok:
            out.append(_Reading(items, cost))
    return out


def parse_move(
    text: str, board: chess.Board, ctx: MoveContext | None = None
) -> ParseResult:
    raw = text_norm.clean(text)
    if _BAD_PHRASE.search(raw):
        return ParseResult("none", reason="discard")
    pre = _preprocess(text)
    if not pre:
        return ParseResult("none")
    words = pre.split(" ")
    if len(words) > 14:
        return ParseResult("none")
    best_cost = 9.0
    best: dict[tuple[int, int, int | None], chess.Move] = {}
    best_spec: Spec | None = None
    best_anchor: tuple[float, Spec] | None = None
    promo_default = False
    for rd in _readings(words):
        spec = _build_spec(rd.items)
        if not spec.anchored():
            continue
        cost = rd.cost + spec.junk
        if best_anchor is None or cost < best_anchor[0]:
            best_anchor = (cost, spec)
        if cost > best_cost + 0.04:
            continue
        ms = _match(spec, board, ctx)
        if not ms:
            continue
        if (
            spec.mover is None
            and spec.from_sq is None
            and spec.from_file is None
            and not spec.capture
            and not spec.castle
            and spec.to_sq is not None
        ):
            pawns = [
                m for m in ms if board.piece_at(m.from_square).piece_type == chess.PAWN
            ]  # type: ignore[union-attr]
            if pawns:
                ms = pawns
        if cost < best_cost - 0.04:
            best.clear()
            best_cost = cost
            best_spec = spec
            promo_default = False
        elif cost < best_cost:
            best_cost = cost
        for m in ms:
            best.setdefault((m.from_square, m.to_square, m.promotion), m)
        if any(m.promotion for m in ms) and spec.promo is None:
            promo_default = True
    if not best:
        if best_anchor and best_anchor[0] <= 0.6:
            return _illegal(best_anchor[1], board, 1 - best_anchor[0])
        return ParseResult("none")
    moves = list(best.values())
    # distinct (from,to) only
    seen: set[tuple[int, int]] = set()
    uniq = []
    for m in moves:
        key = (m.from_square, m.to_square)
        if key not in seen:
            seen.add(key)
            uniq.append(m)
    conf = max(0.0, 1.0 - best_cost)
    if len(uniq) == 1:
        return ParseResult(
            "move",
            uniq,
            conf,
            promotion_defaulted=promo_default and bool(uniq[0].promotion),
            caption=caption_for(board, uniq[0]),
            spec=best_spec,
        )
    uniq.sort(
        key=lambda m: (
            chess.square_file(m.from_square),
            chess.square_rank(m.from_square),
        )
    )
    return ParseResult("ambiguous", uniq, conf, spec=best_spec)


def caption_for(board: chess.Board, m: chess.Move) -> str:
    return f"{chess.square_name(m.from_square)}–{chess.square_name(m.to_square)}"  # noqa: RUF001


# --------------------------------------------------------------------- illegal
_PNAME = {
    chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
    chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king",
}  # fmt: skip


def spoken_square(sq: int) -> str:
    return f"{chess.square_name(sq)[0]} {['one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight'][chess.square_rank(sq)]}"


def piece_name(pt: int) -> str:
    return _PNAME[pt]


def _illegal(s: Spec, board: chess.Board, conf: float) -> ParseResult:
    if s.castle:
        return ParseResult(
            "castle_illegal",
            reason=castle_reason(board, s.side),
            spec=s,
            confidence=conf,
        )
    if s.to_sq is None:
        return ParseResult("none", confidence=conf, spec=s)
    reason = explain_illegal(board, s)
    return ParseResult("illegal", reason=reason, confidence=conf, spec=s)


def castle_reason(board: chess.Board, side: str | None) -> str:
    me = board.turn
    if board.is_check():
        return "You can't castle, Sir. You're in check."
    sides = [side] if side else ["k", "q"]
    king_sq = board.king(me)
    home = chess.E1 if me == chess.WHITE else chess.E8
    if (
        king_sq != home
        or not (board.castling_rights & (chess.BB_RANKS[0 if me else 7]))
    ) and (king_sq != home or not board.has_castling_rights(me)):
        return "You can't castle, Sir. Your king has moved."
    for sd in sides:
        if sd == "k" and not board.has_kingside_castling_rights(me):
            return "You can't castle that way, Sir. The rook has moved."
        if sd == "q" and not board.has_queenside_castling_rights(me):
            return "You can't castle that way, Sir. The rook has moved."
    for sd in sides:
        files = [5, 6] if sd == "k" else [3, 2]
        rank = 0 if me == chess.WHITE else 7
        for f in files:
            sq = chess.square(f, rank)
            if board.attackers(not me, sq):
                return f"You can't castle, Sir. You'd pass through {chess.square_name(sq)}, which is attacked."
        between = [5, 6] if sd == "k" else [1, 2, 3]
        for f in between:
            if board.piece_at(chess.square(f, rank)):
                return "You can't castle yet, Sir. There are pieces in the way."
    return "You can't castle just now, Sir."


def explain_illegal(board: chess.Board, s: Spec) -> str:
    me = board.turn
    if s.to_sq is None:
        return "I'm afraid that isn't a legal move, Sir."
    dest = chess.square_name(s.to_sq)
    if s.mover is None:
        return f"I'm afraid nothing of yours can reach {dest}, Sir."
    name = _PNAME[s.mover]
    pieces = list(board.pieces(s.mover, me))
    if s.from_sq is not None:
        pieces = [sq for sq in pieces if sq == s.from_sq] or pieces
    if not pieces:
        return f"You have no {name} left, Sir."
    if board.is_check():
        return "I'm afraid that doesn't deal with the check, Sir."
    target = board.piece_at(s.to_sq)
    if target is not None and target.color == me:
        return f"I'm afraid your own {_PNAME[target.piece_type]} is on {dest}, Sir."
    for sq in pieces:
        empty = chess.Board(None)
        empty.set_piece_at(sq, board.piece_at(sq))
        empty.turn = me
        reach = chess.SquareSet() if s.mover == chess.PAWN else empty.attacks(sq)
        geometric = s.to_sq in reach
        if s.mover == chess.PAWN:
            direction = 1 if me == chess.WHITE else -1
            f0, r0 = chess.square_file(sq), chess.square_rank(sq)
            f1, r1 = chess.square_file(s.to_sq), chess.square_rank(s.to_sq)
            if f0 == f1 and r1 - r0 in (direction, 2 * direction):
                geometric = True
        if geometric:
            blocker = _first_blocker(board, sq, s.to_sq)
            if blocker is not None:
                bp = board.piece_at(blocker)
                owner = "your" if bp.color == me else "the enemy"  # type: ignore[union-attr]
                return (
                    f"I'm afraid the {name} can't reach {dest}, Sir. "
                    f"{owner.capitalize()} {_PNAME[bp.piece_type]} on {chess.square_name(blocker)} is in the way."  # type: ignore[union-attr]
                )
            return "I'm afraid that move would leave your king in danger, Sir."
    return f"I'm afraid the {name} can't reach {dest}, Sir."


def _first_blocker(board: chess.Board, a: int, b: int) -> int | None:
    between = chess.SquareSet(chess.between(a, b))
    for sq in between:
        if board.piece_at(sq):
            return sq
    if board.piece_at(b) and board.piece_at(b).color == board.turn:  # type: ignore[union-attr]
        return b
    return None


# --------------------------------------------------------------------- choices
_CANCEL = {"never mind", "nevermind", "cancel", "cancel that", "forget it", "no"}
_ORD = {
    "one": 0, "first": 0, "1": 0, "number one": 0, "first one": 0, "won": 0,
    "two": 1, "second": 1, "2": 1, "number two": 1, "second one": 1, "to": 1, "too": 1,
    "three": 2, "third": 2, "3": 2,
}  # fmt: skip


def numbered(candidates: list[chess.Move]) -> list[chess.Move]:
    """Stable 1,2,3 order shared with the board labels: a-file first."""
    return sorted(
        candidates,
        key=lambda m: (
            chess.square_file(m.from_square),
            chess.square_rank(m.from_square),
            m.to_square,
        ),
    )


def resolve_choice(
    text: str, candidates: list[chess.Move], board: chess.Board
) -> chess.Move | str | None:
    """Answer to "which one?": a move, ``"cancel"``, or None if unclear."""
    t = text_norm.strip_wrappers(text)
    t = re.sub(r"\b(the|one on|on|that|my)\b", " ", t) if False else t
    if t in _CANCEL or t.startswith(("never mind", "cancel")):
        return "cancel"
    cands = numbered(candidates)
    t2 = re.sub(r"^(the|that|number)\s+", "", t)
    t2 = re.sub(r"\s+one$", "", t2) if t2 not in ("one",) else t2
    if t2 in _ORD and _ORD[t2] < len(cands):
        return cands[_ORD[t2]]
    # a square: "b1" / "the one on b1" / "b one"
    words = [
        w
        for w in re.sub(r"\b(the|one on|on|from|that|my|piece)\b", " ", t).split()
        if w
    ]
    for rd in _readings(words, limit=300):
        sqs = [it[1] for it in _merge_squares(rd.items) if it[0] == "SQ"]
        if len(sqs) == 1:
            hit = [m for m in cands if m.from_square == sqs[0]]
            if not hit:
                hit = [m for m in cands if m.to_square == sqs[0]]
            if len(hit) == 1:
                return hit[0]
    me = board.turn
    if re.search(r"\bleft\b", t):
        return _extreme(cands, me, left=True)
    if re.search(r"\bright\b", t):
        return _extreme(cands, me, left=False)
    if re.search(r"\b(king|kingside)\b", t):
        k = [m for m in cands if chess.square_file(m.from_square) >= 4]
        return k[0] if len(k) == 1 else None
    if re.search(r"\bqueen", t):
        q = [m for m in cands if chess.square_file(m.from_square) < 4]
        return q[0] if len(q) == 1 else None
    if re.search(r"\b(front|forward|advanced)\b", t):
        return _by_rank(cands, me, front=True)
    if re.search(r"\b(back|rear|behind)\b", t):
        return _by_rank(cands, me, front=False)
    if re.search(r"\bcloser|nearer|nearest\b", t):
        tgt = cands[0].to_square
        d = sorted(cands, key=lambda m: chess.square_distance(m.from_square, tgt))
        if len(d) > 1 and chess.square_distance(
            d[0].from_square, tgt
        ) == chess.square_distance(d[1].from_square, tgt):
            return None
        return d[0]
    return None


def _extreme(
    cands: list[chess.Move], colour: chess.Color, left: bool
) -> chess.Move | None:
    files = sorted(cands, key=lambda m: chess.square_file(m.from_square))
    if len(files) < 2:
        return files[0] if files else None
    pick_low = left == (colour == chess.WHITE)
    a, b = files[0], files[-1]
    if chess.square_file(a.from_square) == chess.square_file(b.from_square):
        return None
    return a if pick_low else b


def _by_rank(
    cands: list[chess.Move], colour: chess.Color, front: bool
) -> chess.Move | None:
    def adv(m: chess.Move) -> int:
        r = chess.square_rank(m.from_square)
        return r if colour == chess.WHITE else 7 - r

    s = sorted(cands, key=adv, reverse=front)
    if len(s) > 1 and adv(s[0]) == adv(s[1]):
        return None
    return s[0]


# --------------------------------------------------------------------- helpers
def find_squares(text: str) -> list[int]:
    """Squares mentioned anywhere in a phrase (cheapest reading)."""
    words = _preprocess(text).split(" ")
    best: tuple[float, list[int]] | None = None
    for rd in _readings([w for w in words if w], limit=300):
        sqs = [it[1] for it in _merge_squares(rd.items) if it[0] == "SQ"]
        if sqs and (best is None or rd.cost < best[0]):
            best = (rd.cost, sqs)
    return best[1] if best else []


def find_piece_type(text: str) -> int | None:
    for w in text_norm.clean(text).split(" "):
        if w in _PIECES and _PIECES[w][1] <= 0.1:
            return _PIECES[w][0]
    return None
