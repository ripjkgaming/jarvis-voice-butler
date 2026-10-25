"""Seven intro scenes as pure functions of time -> ``rich.text.Text``.

Each scene function takes ``(lt, width, height, colour)`` where ``lt`` is the
local time inside the scene. Nothing here touches the terminal, so every
scene is unit-testable at any size. All art is original: box-drawing
characters, Unicode chess glyphs and a bundled pyfiglet font.
"""

from __future__ import annotations

import math
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

import chess
from rich.text import Text

NAVY = "#01050e"
CYAN = "#22d3ee"
CYAN_DIM = "#0e5f6e"
CYAN_FAINT = "#0a3640"
WHITE = "#ffffff"
AMBER = "#f59e0b"
RED = "#ef4444"
GREY = "#5b6b78"

TOTAL = 14.0
SUBTITLE = "A game of kings, sir. Shall we?"

GLYPHS_W = {"K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙"}
GLYPHS_B = {"K": "♚", "Q": "♛", "R": "♜", "B": "♝", "N": "♞", "P": "♟"}


def use_letters() -> bool:
    return os.environ.get("JARVIS_INTRO_ASCII", "").strip() in {"1", "true", "yes"}


def glyph(piece: chess.Piece) -> str:
    sym = piece.symbol().upper()
    if use_letters():
        return sym
    return (GLYPHS_W if piece.color == chess.WHITE else GLYPHS_B)[sym]


def piece_style(piece: chess.Piece) -> str:
    return CYAN if piece.color == chess.WHITE else AMBER


# ---------------------------------------------------------------- canvas
class Canvas:
    """A character grid with a style per cell, flattened to rich Text."""

    def __init__(self, w: int, h: int, colour: str = "truecolor"):
        self.w, self.h, self.colour = max(1, w), max(1, h), colour
        self.cells: list[list[tuple[str, str]]] = [
            [(" ", "")] * self.w for _ in range(self.h)
        ]

    def put(self, x: int, y: int, ch: str, style: str = "") -> None:
        if 0 <= x < self.w and 0 <= y < self.h and ch:
            self.cells[y][x] = (ch[0], style)

    def text(self, x: int, y: int, s: str, style: str = "") -> None:
        for i, ch in enumerate(s):
            if ch != "\0":
                self.put(x + i, y, ch, style)

    def centre(self, y: int, s: str, style: str = "") -> None:
        self.text((self.w - len(s)) // 2, y, s, style)

    def to_text(self) -> Text:
        out = Text(no_wrap=True, overflow="crop")
        mono = self.colour == "mono"
        bg = f" on {NAVY}" if self.colour == "truecolor" else ""
        for y, row in enumerate(self.cells):
            run, style = "", None
            for ch, st in row:
                if style is None or st == style:
                    run += ch
                    style = st
                    continue
                out.append(run, None if mono else ((style + bg).strip() or None))
                run, style = ch, st
            if run:
                out.append(run, None if mono else ((style + bg).strip() or None))
            if y < self.h - 1:
                out.append("\n")
        return out


def lerp_hex(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    ca = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(
        f"{int(x + (y - x) * t):02x}" for x, y in zip(ca, cb, strict=True)
    )


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


# ------------------------------------------------------------------ scene 1
_BOOT = [
    ("JARVIS GAMES SUBSYSTEM v1.0", False),
    ("> initialising tactical core ............", True),
    ("> loading stockfish neural net ..........", True),
    ("> calibrating holographic grid ..........", True),
    ("> summoning opponent ....................", True),
]


@lru_cache(maxsize=1)
def _stockfish_present() -> bool:
    return shutil.which("stockfish") is not None


def scene_boot(lt: float, w: int, h: int, colour: str = "truecolor") -> Text:
    c = Canvas(w, h, colour)
    x0 = max(1, (w - 48) // 2)
    y0 = max(1, h // 2 - 4)
    step = 0.36
    for i, (line, has_ok) in enumerate(_BOOT):
        start = i * step
        if lt < start:
            break
        typed = int((lt - start) * 110)
        shown = line[:typed]
        style = WHITE if i == 0 else CYAN
        c.text(x0, y0 + i * 2, shown, style)
        if has_ok and typed > len(line) + 2:
            warn = i == 2 and not _stockfish_present()
            tag = "[ WARN ]" if warn else "[ OK ]"
            c.text(x0 + len(line) + 1, y0 + i * 2, tag, AMBER if warn else CYAN)
        elif typed <= len(line) and int(lt * 4) % 2 == 0:
            c.put(x0 + len(shown), y0 + i * 2, "█", CYAN)
    if lt > len(_BOOT) * step and int(lt * 4) % 2 == 0:
        c.put(x0, y0 + len(_BOOT) * 2, "█", CYAN)
    return c.to_text()


# ------------------------------------------------------------------ scene 2
_SPIN = "-\\|/"


def scene_reactor(lt: float, w: int, h: int, colour: str = "truecolor") -> Text:
    c = Canvas(w, h, colour)
    cx, cy = w // 2, h // 2
    rmax = max(2, min(h // 2 - 1, w // 4))
    grow = ease(lt / 1.1)
    heat = ease(lt / 2.0)
    core = lerp_hex(CYAN, WHITE, heat)
    for k, scale in enumerate((1.0, 0.7, 0.4)):
        r = rmax * scale * grow
        if r < 1:
            continue
        n = max(12, int(r * 10))
        for i in range(n):
            a = 2 * math.pi * i / n
            x = cx + round(2 * r * math.cos(a))
            y = cy + round(r * math.sin(a))
            idx = int((a / (2 * math.pi) * 8) + lt * 10 * (1 if k % 2 == 0 else -1)) % 4
            c.put(x, y, _SPIN[idx], CYAN if k < 2 else lerp_hex(CYAN, WHITE, 0.5))
    if grow > 0.2:
        pr = ((lt * 5) % rmax) if lt > 0.6 else 0
        if pr > 1:
            n = int(pr * 8)
            fade = lerp_hex(CYAN, NAVY, pr / rmax)
            for i in range(n):
                a = 2 * math.pi * i / n
                c.put(
                    cx + round(2 * pr * math.cos(a)),
                    cy + round(pr * math.sin(a)),
                    "·",
                    fade,
                )
    c.put(cx, cy, "◉" if not use_letters() else "@", core)
    if lt < 0.2:
        c.put(cx, cy, ".", CYAN)
    return c.to_text()


# ------------------------------------------------------------------ board art
CELL = 5  # "│ a8 "
BOARD_W = CELL * 8 + 1
BOARD_H = 17


def board_origin(w: int, h: int) -> tuple[int, int]:
    return max(2, (w - BOARD_W) // 2), max(0, (h - BOARD_H - 1) // 2)


def square_xy(f: int, r: int, w: int, h: int) -> tuple[int, int]:
    bx, by = board_origin(w, h)
    return bx + 1 + f * CELL + 2, by + 1 + (7 - r) * 2


def draw_grid(c: Canvas, upto_row: float, style: str = CYAN_DIM) -> None:
    bx, by = board_origin(c.w, c.h)
    top = "┌" + "┬".join(["────"] * 8) + "┐"
    mid = "├" + "┼".join(["────"] * 8) + "┤"
    bot = "└" + "┴".join(["────"] * 8) + "┘"
    for j in range(BOARD_H):
        if j > upto_row:
            break
        if j == 0:
            line = top
        elif j == BOARD_H - 1:
            line = bot
        elif j % 2 == 0:
            line = mid
        else:
            line = "│" + "│".join(["    "] * 8) + "│"
        c.text(bx, by + j, line, style)
        if j % 2 == 1:
            c.text(bx - 2, by + j, str(8 - (j - 1) // 2), style)
    if upto_row >= BOARD_H - 1:
        for f in range(8):
            c.put(bx + 1 + f * CELL + 2, by + BOARD_H, "abcdefgh"[f], style)


def draw_codes(c: Canvas, reveal: Callable[[int, int], float]) -> None:
    for r in range(8):
        for f in range(8):
            a = reveal(f, r)
            if a <= 0:
                continue
            x, y = square_xy(f, r, c.w, c.h)
            code = "abcdefgh"[f] + str(r + 1)
            c.text(x - 1, y, code, lerp_hex(CYAN_FAINT, CYAN, a))


def scene_grid(lt: float, w: int, h: int, colour: str = "truecolor") -> Text:
    c = Canvas(w, h, colour)
    sweep = ease(lt / 1.4) * (BOARD_H + 2)
    draw_grid(c, sweep)
    bx, by = board_origin(w, h)
    if lt < 1.5:
        sy = by + int(sweep)
        c.text(max(0, bx - 3), sy, "━" * (BOARD_W + 6), WHITE)

    # staggered code fade (a8 first ... h1 last)
    def reveal(f: int, r: int) -> float:
        idx = (7 - r) * 8 + f
        t0 = 0.5 + idx * 0.022
        if (7 - r) * 2 + 1 > sweep:
            return 0.0
        return max(0.0, min(1.0, (lt - t0) / 0.35))

    draw_codes(c, reveal)
    return c.to_text()


# ------------------------------------------------------------------ scene 4
_BACK = "RNBQKBNR"


def _home_pieces() -> list[tuple[int, int, chess.Piece, float]]:
    out = []
    for f in range(8):
        for r, col, kind in ((1, chess.WHITE, "P"), (6, chess.BLACK, "P")):
            out.append(
                (
                    f,
                    r,
                    chess.Piece.from_symbol(kind if col else kind.lower()),
                    0.0 + 0.045 * f,
                )
            )
        for r, col in ((0, chess.WHITE), (7, chess.BLACK)):
            sym = _BACK[f]
            out.append(
                (
                    f,
                    r,
                    chess.Piece.from_symbol(sym if col else sym.lower()),
                    0.55 + 0.05 * f,
                )
            )
    return out


def scene_drop(lt: float, w: int, h: int, colour: str = "truecolor") -> Text:
    c = Canvas(w, h, colour)
    draw_grid(c, BOARD_H)
    draw_codes(c, lambda f, r: 0.35)
    _bx, by = board_origin(w, h)
    fall = 0.4
    for f, r, piece, t0 in _home_pieces():
        p = (lt - t0) / fall
        if p < 0:
            continue
        x, ty = square_xy(f, r, w, h)
        if p < 1:
            y = round((by - 3) + (ty - (by - 3)) * p * p)
        else:
            y = ty - 1 if (lt - t0 - fall) < 0.08 else ty
        c.put(x, ty, " ")
        c.text(x - 1, ty, "  ", "")
        c.put(x, y, glyph(piece), piece_style(piece))
    return c.to_text()


# ------------------------------------------------------------------ scene 5
SKIRMISH = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "f3e5", "c6e5"]
PLY_T = 0.3
MOVE_T = 0.24


@lru_cache(maxsize=1)
def skirmish_boards() -> list[chess.Board]:
    """Position before each ply (index 0..len). Legal by construction."""
    b = chess.Board()
    boards = [b.copy()]
    for u in SKIRMISH:
        mv = chess.Move.from_uci(u)
        if mv not in b.legal_moves:
            raise ValueError(f"illegal intro move {u}")
        b.push(mv)
        boards.append(b.copy())
    return boards


_SHARDS = "*+.·"


def scene_skirmish(lt: float, w: int, h: int, colour: str = "truecolor") -> Text:
    c = Canvas(w, h, colour)
    draw_grid(c, BOARD_H)
    draw_codes(c, lambda f, r: 0.3)
    boards = skirmish_boards()
    ply = min(len(SKIRMISH) - 1, int(lt / PLY_T))
    local = lt - ply * PLY_T
    before = boards[ply]
    mv = chess.Move.from_uci(SKIRMISH[ply])
    prog = ease(local / MOVE_T)
    captured_sq = mv.to_square if before.is_capture(mv) else None
    for sq, piece in before.piece_map().items():
        if sq == mv.from_square:
            continue
        if sq == captured_sq and prog >= 1.0:
            continue
        x, y = square_xy(chess.square_file(sq), chess.square_rank(sq), w, h)
        c.text(x - 1, y, "  ")
        c.put(x, y, glyph(piece), piece_style(piece))
    mover = before.piece_at(mv.from_square)
    assert mover is not None
    fx, fy = square_xy(
        chess.square_file(mv.from_square), chess.square_rank(mv.from_square), w, h
    )
    tx, ty = square_xy(
        chess.square_file(mv.to_square), chess.square_rank(mv.to_square), w, h
    )
    c.text(fx - 1, fy, "  ")
    # trail
    for k in range(1, 5):
        pk = max(0.0, prog - 0.12 * k)
        if pk <= 0 or prog >= 1.0:
            break
        x = fx + (tx - fx) * pk
        y = fy + (ty - fy) * pk
        c.put(round(x), round(y), "·", lerp_hex(CYAN, CYAN_FAINT, k / 5))
    x = fx + (tx - fx) * prog
    y = fy + (ty - fy) * prog
    if mover.piece_type == chess.KNIGHT:
        y -= 3 * math.sin(math.pi * prog)
    c.text(round(x) - 1, round(y), "  ")
    c.put(round(x), round(y), glyph(mover), piece_style(mover))
    # capture shatter
    if captured_sq is not None and local > MOVE_T:
        victim = before.piece_at(captured_sq)
        age = (local - MOVE_T) / (PLY_T - MOVE_T + 0.0001)
        for i in range(10):
            a = 2 * math.pi * i / 10 + 0.3
            dist = 1 + age * 5
            px = tx + 2 * dist * math.cos(a)
            py = ty + dist * math.sin(a) * 0.8
            ch = _SHARDS[i % 4]
            col = lerp_hex(piece_style(victim) if victim else AMBER, NAVY, age)
            c.put(round(px), round(py), ch, col)
    return c.to_text()


# ------------------------------------------------------------------ scene 6
@lru_cache(maxsize=8)
def _banner_lines(width: int) -> tuple[str, ...]:
    import pyfiglet

    for font, text in (
        ("ansi_shadow", "JARVIS CHESS"),
        ("big", "JARVIS CHESS"),
        ("small", "JARVIS CHESS"),
    ):
        try:
            rendered = pyfiglet.figlet_format(text, font=font, width=300)
        except Exception:
            continue
        lines = [ln.rstrip() for ln in rendered.splitlines()]
        while lines and not lines[-1].strip():
            lines.pop()
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and max(len(ln) for ln in lines) <= width - 2:
            return tuple(lines)
    # stack the two words if even the small font is too wide
    for font in ("ansi_shadow", "small", "standard"):
        try:
            parts = [
                [
                    ln.rstrip()
                    for ln in pyfiglet.figlet_format(
                        word, font=font, width=300
                    ).splitlines()
                    if ln.strip()
                ]
                for word in ("JARVIS", "CHESS")
            ]
        except Exception:
            continue
        lines = parts[0] + parts[1]
        if max(len(ln) for ln in lines) <= width - 2:
            return tuple(lines)
    return ("J A R V I S   C H E S S",)


def scene_title(lt: float, w: int, h: int, colour: str = "truecolor") -> Text:
    c = Canvas(w, h, colour)
    # the board dims away behind the title
    dim = max(0.0, 1.0 - lt / 0.5)
    if dim > 0:
        draw_grid(c, BOARD_H, lerp_hex(NAVY, CYAN_DIM, dim))
    lines = _banner_lines(w)
    top = max(0, (h - len(lines)) // 2 - 2)
    drop = max(0, int((0.25 - lt) * 40)) if lt < 0.25 else 0
    glitch = 0.35 <= lt < 0.35 + 3 / 30
    frame_i = int(lt * 30) % 3
    shine_x = (lt - 0.6) / 1.1 * (w + 24) - 12
    if lt >= 0.12:
        for j, line in enumerate(lines):
            off = 0
            if glitch:
                off = (
                    ((-2, 2, 0)[(frame_i + j) % 3])
                    if j % 2 == 0
                    else ((2, -2, 0)[(frame_i + j) % 3])
                )
            x0 = (w - len(line)) // 2 + off
            for i, ch in enumerate(line):
                if ch == " ":
                    continue
                near = abs((x0 + i) - shine_x - j) < 3
                c.put(x0 + i, top + j - drop, ch, WHITE if near else CYAN)
    sub_y = top + len(lines) + 2
    typed = int(max(0.0, lt - 0.9) * 32)
    if typed:
        c.centre(sub_y, SUBTITLE[:typed].ljust(len(SUBTITLE)), WHITE)
    return c.to_text()


# ------------------------------------------------------------------ scene 7
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def scene_handover(lt: float, w: int, h: int, colour: str = "truecolor") -> Text:
    c = Canvas(w, h, colour)
    cx, cy = w // 2, h // 2
    msg = "> launching board ..."
    typed = int(lt * 60)
    spin = (
        _SPINNER[int(lt * 14) % len(_SPINNER)]
        if not use_letters()
        else "|/-\\"[int(lt * 14) % 4]
    )
    c.text(
        max(1, (w - len(msg) - 2) // 2),
        min(h - 2, cy + 6),
        msg[:typed] + (" " + spin if typed >= len(msg) else ""),
        CYAN,
    )
    # the title collapses into a single cyan dot
    collapse = ease((lt - 0.15) / 0.7)
    lines = _banner_lines(w)
    if collapse < 1.0:
        scale = 1.0 - collapse
        top = cy - len(lines) // 2
        for j, line in enumerate(lines):
            for i, ch in enumerate(line):
                if ch == " ":
                    continue
                x = cx + round((i - len(line) / 2) * scale)
                y = cy + round((j - len(lines) / 2) * scale)
                c.put(x, y, ch if scale > 0.5 else "·", CYAN)
        _ = top
    else:
        c.put(cx, cy, "●" if not use_letters() else "o", WHITE if lt < 0.97 else CYAN)
    return c.to_text()


# --------------------------------------------------------------------- timeline
@dataclass(frozen=True)
class Scene:
    name: str
    start: float
    end: float
    fn: Callable[[float, int, int, str], Text]

    @property
    def duration(self) -> float:
        return self.end - self.start


SCENES: list[Scene] = [
    Scene("cold boot", 0.0, 2.0, scene_boot),
    Scene("arc reactor", 2.0, 4.0, scene_reactor),
    Scene("grid", 4.0, 6.5, scene_grid),
    Scene("pieces", 6.5, 8.5, scene_drop),
    Scene("skirmish", 8.5, 11.0, scene_skirmish),
    Scene("title", 11.0, 13.0, scene_title),
    Scene("handover", 13.0, 14.0, scene_handover),
]
TITLE_SCENE = SCENES[5]


def total_duration() -> float:
    return SCENES[-1].end


def scene_at(t: float) -> tuple[Scene, float]:
    t = max(0.0, min(total_duration() - 1e-6, t))
    for s in SCENES:
        if s.start <= t < s.end:
            return s, t - s.start
    return SCENES[-1], SCENES[-1].duration


def frame(t: float, w: int, h: int, colour: str = "truecolor") -> Text:
    s, lt = scene_at(t)
    return s.fn(lt, w, h, colour)


def end_card(w: int, h: int, colour: str = "truecolor") -> Text:
    """Final title frame: the card shown when a key skips the cinematic."""
    return TITLE_SCENE.fn(TITLE_SCENE.duration, w, h, colour)


def compact_card(w: int, h: int, colour: str = "truecolor") -> Text:
    """Title card for terminals below 80x24."""
    c = Canvas(w, h, colour)
    y = max(0, h // 2 - 2)
    c.centre(y, "♞  JARVIS CHESS" if not use_letters() else "N  JARVIS CHESS", CYAN)
    c.centre(y + 2, SUBTITLE, WHITE)
    return c.to_text()
