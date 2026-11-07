"""``chess --tui``: play in the terminal with a mouse (Textual).

Same core and same §2.4 on-demand rules as the window: this is just another
front door onto ``ChessGame``. ASCII board with square codes, click-to-move,
a typed command line, motion-trail slides, capture shatter, check flash and a
mate sequence.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import ClassVar

import chess
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, RichLog, Static

from games.chess.engine import BaseEngine, make_engine
from games.chess.game import ChessGame
from games.chess.settings import ChessSettings
from games.chess.settings import load as load_settings
from games.ui_state import status_suffix

CELL_W = 7
CELL_H = 3
CYAN = "#22d3ee"
CYAN_DIM = "#0e5f6e"
AMBER = "#f59e0b"
RED = "#ef4444"
NAVY_A = "#03101c"
NAVY_B = "#06223a"
GLYPH = {"K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙"}
GLYPH_B = {"K": "♚", "Q": "♛", "R": "♜", "B": "♝", "N": "♞", "P": "♟"}
SHARDS = "*+.·"

SLASH = {
    "hint": "hint",
    "analyse": "analyse",
    "analyze": "analyse",
    "explain": "explain",
    "takeover": "takeover",
    "stop": "stop",
    "undo": "undo",
    "redo": "redo",
    "flip": "flip",
    "save": "save",
    "quit": "quit",
    "review": "review",
    "new": "new_game",
    "listen": "listen",
}


@dataclass
class Anim:
    piece: chess.Piece
    frm: int
    to: int
    t0: float
    dur: float = 0.26
    capture: chess.Piece | None = None
    knight: bool = False
    shatter_from: float = 0.0
    trail: list[tuple[float, float]] = field(default_factory=list)


def sq_cell(sq: int, flipped: bool) -> tuple[int, int]:
    f, r = chess.square_file(sq), chess.square_rank(sq)
    return (7 - f, r) if flipped else (f, 7 - r)


class BoardView(Widget):
    """The ASCII board. Mouse: click a piece, then a highlighted square."""

    DEFAULT_CSS = """
    BoardView { width: 62; height: 27; }
    """

    flash = reactive(0)

    def __init__(self, game: ChessGame, on_move) -> None:
        super().__init__()
        self.game = game
        self.on_move = on_move
        self.selected: int | None = None
        self.anim: Anim | None = None
        self.mated_since: float | None = None
        self._prev_moves = 0
        self._prev_board: chess.Board | None = None
        self._flash_until = 0.0
        self.pending_promo: tuple[int, int] | None = None

    # ------------------------------------------------------------- state sync
    def sync(self) -> None:
        st = self.game.state
        board = st.board
        n = len(board.move_stack)
        moved = n != self._prev_moves
        if n == self._prev_moves + 1 and self._prev_board is not None:
            mv = board.peek()
            piece = self._prev_board.piece_at(mv.from_square)
            if piece:
                cap = self._prev_board.piece_at(mv.to_square)
                if self._prev_board.is_en_passant(mv):
                    cap = chess.Piece(chess.PAWN, not piece.color)
                self.anim = Anim(
                    piece=piece,
                    frm=mv.from_square,
                    to=mv.to_square,
                    t0=time.monotonic(),
                    capture=cap,
                    knight=piece.piece_type == chess.KNIGHT,
                )
            if board.is_check():
                self._flash_until = time.monotonic() + 0.9
        elif n != self._prev_moves:
            self.anim = None
        if (
            st.result()
            and st.result().reason == "checkmate"
            and self.mated_since is None
        ):
            self.mated_since = time.monotonic()
        if not st.result():
            self.mated_since = None
        self._prev_moves = n
        self._prev_board = board.copy()
        if moved:
            self.selected = None
        self.refresh()

    # ---------------------------------------------------------------- render
    @property
    def flipped(self) -> bool:
        return bool(self.game.flipped)

    def render(self) -> Text:
        now = time.monotonic()
        st = self.game.state
        board = st.board
        flipped = self.flipped
        legal_to: set[int] = set()
        if self.selected is not None:
            legal_to = {
                m.to_square for m in board.legal_moves if m.from_square == self.selected
            }
        last = board.peek() if board.move_stack else None
        check_sq = board.king(board.turn) if board.is_check() else None
        blink = (
            check_sq is not None
            and now < self._flash_until
            and int((self._flash_until - now) / 0.15) % 2 == 0
        )
        mated = self.mated_since is not None
        width = 8 * CELL_W + 4
        height = 8 * CELL_H + 2
        grid: list[list[tuple[str, str]]] = [[(" ", "")] * width for _ in range(height)]

        def put(x: int, y: int, ch: str, style: str) -> None:
            if 0 <= x < width and 0 <= y < height and ch:
                grid[y][x] = (ch, style)

        ox, oy = 2, 1
        for sq in chess.SQUARES:
            cx, cy = sq_cell(sq, flipped)
            dark = (chess.square_file(sq) + chess.square_rank(sq)) % 2 == 0
            bg = NAVY_A if dark else NAVY_B
            if last and sq in (last.from_square, last.to_square):
                bg = "#0b3b52"
            if sq == self.selected:
                bg = "#0f5f7a"
            if sq == check_sq:
                bg = "#5c1010" if blink or not mated else "#7a1515"
            x0 = ox + cx * CELL_W
            y0 = oy + cy * CELL_H
            for dy in range(CELL_H):
                for dx in range(CELL_W):
                    put(x0 + dx, y0 + dy, " ", f"on {bg}")
            put(x0, y0, chess.square_name(sq)[0], f"{CYAN_DIM} on {bg}")
            put(x0 + 1, y0, chess.square_name(sq)[1], f"{CYAN_DIM} on {bg}")
            if sq in legal_to:
                occupied = board.piece_at(sq) is not None
                put(
                    x0 + CELL_W // 2,
                    y0 + 1,
                    "◯" if occupied else "•",
                    f"bold {AMBER if occupied else CYAN} on {bg}",
                )
        # pieces
        anim = self.anim
        moving_done = anim is None or (now - anim.t0) >= anim.dur
        for sq, piece in board.piece_map().items():
            if anim and not moving_done and sq == anim.to:
                continue
            cx, cy = sq_cell(sq, flipped)
            x0 = ox + cx * CELL_W
            y0 = oy + cy * CELL_H
            bg = self._bg(sq, board, last, check_sq, blink)
            col = CYAN if piece.color == chess.WHITE else AMBER
            g = (GLYPH if piece.color == chess.WHITE else GLYPH_B)[
                piece.symbol().upper()
            ]
            if mated and sq == check_sq:
                g = "⚰" if now - (self.mated_since or now) > 0.5 else g
            put(x0 + CELL_W // 2, y0 + 1, g, f"bold {col} on {bg}")
        # slide with a motion trail (dots fade behind the mover; the knight hops)
        if anim and not moving_done:
            p = (now - anim.t0) / anim.dur
            fx, fy = sq_cell(anim.frm, flipped)
            tx, ty = sq_cell(anim.to, flipped)
            for k in range(4, 0, -1):
                pk = max(0.0, p - 0.1 * k)
                if pk <= 0:
                    continue
                x = ox + (fx + (tx - fx) * pk) * CELL_W + CELL_W // 2
                y = oy + (fy + (ty - fy) * pk) * CELL_H + 1
                put(round(x), round(y), "·", CYAN_DIM)
            x = ox + (fx + (tx - fx) * p) * CELL_W + CELL_W // 2
            y = oy + (fy + (ty - fy) * p) * CELL_H + 1
            if anim.knight:
                y -= math.sin(math.pi * p) * 2.5
            col = CYAN if anim.piece.color == chess.WHITE else AMBER
            g = (GLYPH if anim.piece.color == chess.WHITE else GLYPH_B)[
                anim.piece.symbol().upper()
            ]
            put(round(x), round(y), g, f"bold {col}")
        elif anim and anim.capture is not None and now - anim.t0 < anim.dur + 0.5:
            age = (now - anim.t0 - anim.dur) / 0.5
            tx, ty = sq_cell(anim.to, flipped)
            cx = ox + tx * CELL_W + CELL_W // 2
            cy = oy + ty * CELL_H + 1
            col = CYAN if anim.capture.color == chess.WHITE else AMBER
            for i in range(10):
                a = 2 * math.pi * i / 10
                put(
                    round(cx + math.cos(a) * (1 + age * 6)),
                    round(cy + math.sin(a) * (1 + age * 2.4)),
                    SHARDS[i % 4],
                    col,
                )
        # rank / file rulers
        for i in range(8):
            f = 7 - i if flipped else i
            put(ox + i * CELL_W + CELL_W // 2, oy + 8 * CELL_H, "abcdefgh"[f], CYAN_DIM)
            r = i + 1 if flipped else 8 - i
            put(0, oy + i * CELL_H + 1, str(r), CYAN_DIM)
        border = (
            RED if (blink or mated) else (AMBER if st.mode == "takeover" else CYAN_DIM)
        )
        for x in range(width):
            put(x, 0, "─", border)
        out = Text(no_wrap=True, overflow="crop")
        for y, row in enumerate(grid):
            run, sty = "", None
            for ch, s in row:
                if sty is None or s == sty:
                    run += ch
                    sty = s
                else:
                    out.append(run, sty or None)
                    run, sty = ch, s
            if run:
                out.append(run, sty or None)
            if y < height - 1:
                out.append("\n")
        return out

    def _bg(self, sq: int, board: chess.Board, last, check_sq, blink) -> str:
        dark = (chess.square_file(sq) + chess.square_rank(sq)) % 2 == 0
        bg = NAVY_A if dark else NAVY_B
        if last and sq in (last.from_square, last.to_square):
            bg = "#0b3b52"
        if sq == self.selected:
            bg = "#0f5f7a"
        if sq == check_sq:
            bg = "#5c1010" if blink else "#7a1515"
        return bg

    # ----------------------------------------------------------------- mouse
    def square_at(self, x: int, y: int) -> int | None:
        cx = (x - 2) // CELL_W
        cy = (y - 1) // CELL_H
        if not (0 <= cx < 8 and 0 <= cy < 8) or x < 2 or y < 1:
            return None
        f, r = (7 - cx, cy) if self.flipped else (cx, 7 - cy)
        return chess.square(f, r)

    async def on_click(self, event: events.Click) -> None:
        sq = self.square_at(event.x, event.y)
        if sq is None:
            return
        st = self.game.state
        board = st.board
        if st.mode != "play" or st.result() or board.turn != st.human_colour:
            return
        if self.selected is not None and sq != self.selected:
            moves = [
                m
                for m in board.legal_moves
                if m.from_square == self.selected and m.to_square == sq
            ]
            if moves:
                frm = self.selected
                self.selected = None
                promo = "q" if any(m.promotion for m in moves) else None
                await self.on_move(chess.square_name(frm), chess.square_name(sq), promo)
                return
        piece = board.piece_at(sq)
        self.selected = sq if piece and piece.color == st.human_colour else None
        self.refresh()


class TuiApp(App):
    CSS = """
    Screen { background: #01050e; color: #cdeffb; }
    #row { height: 1fr; }
    #side { width: 1fr; padding: 0 1; }
    #status { height: 3; color: #22d3ee; }
    #moves { height: 1fr; border: round #0e5f6e; }
    #say { height: 8; border: round #0e5f6e; }
    #cmd { dock: bottom; border: round #22d3ee; }
    #banner { height: 1; text-align: center; color: #f59e0b; }
    """
    BINDINGS: ClassVar = [
        ("escape", "stop", "Stop"),
        ("ctrl+z", "undo", "Undo"),
        ("ctrl+y", "redo", "Redo"),
        ("ctrl+c", "quit_app", "Quit"),
        ("ctrl+q", "quit_app", "Quit"),
    ]

    def __init__(self, game: ChessGame, listen: bool = False) -> None:
        super().__init__()
        self.game = game
        self.want_listen = listen
        self.voice = None
        self.board_view: BoardView | None = None
        self.exit_summary: dict = {}

    def compose(self) -> ComposeResult:
        self.board_view = BoardView(self.game, self._board_move)
        yield Static("", id="banner")
        with Horizontal(id="row"):
            yield self.board_view
            with Vertical(id="side"):
                yield Static("", id="status")
                yield RichLog(id="moves", markup=False, wrap=True)
                yield RichLog(id="say", markup=False, wrap=True)
        yield Input(
            placeholder="e4, Nf3, /hint /analyse /explain /takeover /stop /undo /flip /save /quit",
            id="cmd",
        )

    async def on_mount(self) -> None:
        self.game.on_change = self._changed
        orig = self.game.on_log

        async def say(text: str) -> None:
            self.query_one("#say", RichLog).write(f"♞ {text}")
            if orig:
                r = orig(text)
                if asyncio.iscoroutine(r):
                    await r

        self.game.on_log = say
        self.game.on_quit = self._quit
        self.set_interval(1 / 30, self._tick)
        self.query_one("#cmd", Input).focus()
        if self.want_listen:
            await self._listen(True)
        self._changed()

    async def _quit(self) -> None:
        self.exit()

    def _changed(self) -> None:
        if self.board_view is None:
            return
        self.board_view.sync()
        g = self.game
        st = g.state
        snap = g.snapshot()
        mic = snap["mic_state"].upper()
        line = f"{snap['status']}"
        line += status_suffix(g)
        if snap["analysis_on"]:
            line += "  · ANALYSIS ON"
        if st.clock.timed:
            line += f"\nyou {int(st.time_left(st.human_colour)):>4}s  jarvis {int(st.time_left(not st.human_colour)):>4}s"
        else:
            line += "\nuntimed"
        line += f"  · mic {mic}"
        self.query_one("#status", Static).update(line)
        banner = ""
        if snap["banner"]:
            banner = snap["banner"]
        elif st.mode == "takeover":
            banner = "JARVIS IN CONTROL"
        elif st.result():
            banner = f"{st.result().outcome}  {st.result().reason}"
        self.query_one("#banner", Static).update(banner)
        moves = self.query_one("#moves", RichLog)
        moves.clear()
        sans = snap["moves"]
        for i in range(0, len(sans), 2):
            moves.write(
                f"{i // 2 + 1}. {sans[i]} {sans[i + 1] if i + 1 < len(sans) else ''}"
            )
        if snap["panel"] and snap["panel"].get("detail"):
            pass

    def _tick(self) -> None:
        if self.board_view is not None:
            a = self.board_view.anim
            busy = a is not None and time.monotonic() - a.t0 < a.dur + 0.55
            if (
                busy
                or self.board_view.mated_since is not None
                or time.monotonic() < self.board_view._flash_until
            ):
                self.board_view.refresh()

    async def _board_move(self, frm: str, to: str, promo: str | None) -> None:
        r = await self.game.command(
            "move", **{"from": frm, "to": to, "promotion": promo}
        )
        if not r["ok"] and (r["say"] or r["text"]):
            self.query_one("#say", RichLog).write(r["say"] or r["text"])

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        log = self.query_one("#say", RichLog)
        if text.startswith("/"):
            cmd = text[1:].split()[0].lower() if len(text) > 1 else ""
            name = SLASH.get(cmd)
            if name == "listen":
                await self._listen(self.voice is None)
                return
            if name:
                rest = text.split(maxsplit=1)[1] if " " in text else ""
                args = {"name": rest} if (name == "save" and rest) else {}
                r = await self.game.command(name, **args)
                if r["say"]:
                    log.write(r["say"])
                return
        r = await self.game.command("say", text=text, source="keyboard")
        if r["say"]:
            log.write(r["say"])

    async def _listen(self, on: bool) -> None:
        log = self.query_one("#say", RichLog)
        if on and self.voice is None:
            try:
                from games.voice.factory import build_voice_engine

                self.voice = build_voice_engine(self.game)
                await self.voice.start()
                log.write(
                    "(listening: local whisper + Piper, nothing leaves the machine)"
                )
            except Exception as exc:
                self.voice = None
                log.write(f"(voice unavailable: {exc})")
        elif not on and self.voice is not None:
            await self.voice.stop()
            self.voice = None
            log.write("(listening off)")

    async def action_stop(self) -> None:
        await self.game.command("stop")

    async def action_undo(self) -> None:
        await self.game.command("undo")

    async def action_redo(self) -> None:
        await self.game.command("redo")

    async def action_quit_app(self) -> None:
        self.exit()


async def run_tui_async(args) -> int:
    settings: ChessSettings = load_settings()
    engine: BaseEngine = await make_engine()
    game = ChessGame(settings, engine=engine, persist_settings=True)
    await game.start()
    if args.level:
        game.settings.difficulty = args.level
    if args.colour:
        game.settings.colour = args.colour
    if args.time:
        game.settings.time_control = args.time
    resumed = await game.resume_last() if args.resume else False
    if not resumed:
        await game.new_game(fen=args.fen)
    if args.spectate:
        await game.command("spectate")
    app = TuiApp(game, listen=args.listen)
    try:
        await app.run_async(mouse=True)
    finally:
        if app.voice is not None:
            await app.voice.stop()
        game._autosave()
        await game.close()
    return 0


def run_tui(args) -> int:
    try:
        return asyncio.run(run_tui_async(args))
    except KeyboardInterrupt:
        return 130
