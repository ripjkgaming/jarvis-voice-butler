"""Pure game state on python-chess: moves, undo/redo, clocks, results, saves."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import chess

from games.chess.settings import TIME_CONTROLS

MODES = ("play", "takeover", "spectate")


@dataclass
class Clock:
    base: float = 0.0  # seconds; 0 means untimed
    increment: float = 0.0
    white: float = 0.0
    black: float = 0.0
    running: bool = False
    paused: bool = False
    _last: float | None = None

    @property
    def timed(self) -> bool:
        return self.base > 0

    @classmethod
    def from_spec(cls, spec: str) -> Clock:
        if spec in TIME_CONTROLS:
            base, inc = TIME_CONTROLS[spec]
        else:
            try:
                m, _, i = spec.partition("+")
                base, inc = int(float(m) * 60), int(i or 0)
            except ValueError:
                base, inc = 0, 0
        return cls(base=base, increment=inc, white=base, black=base)


@dataclass
class Result:
    outcome: str  # "1-0" | "0-1" | "1/2-1/2"
    reason: str  # checkmate | stalemate | resignation | timeout | ...

    def to_dict(self) -> dict[str, str]:
        return {"outcome": self.outcome, "reason": self.reason}


@dataclass
class GameState:
    start_fen: str | None = None
    human_colour: chess.Color = chess.WHITE
    mode: str = "play"
    clock: Clock = field(default_factory=Clock)
    now: Callable[[], float] = time.monotonic
    board: chess.Board = field(default_factory=chess.Board)
    redo_stack: list[chess.Move] = field(default_factory=list)
    forced_result: Result | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.start_fen:
            self.board = chess.Board(self.start_fen)

    # --- basics -------------------------------------------------------
    @property
    def turn(self) -> chess.Color:
        return self.board.turn

    @property
    def human_to_move(self) -> bool:
        return self.board.turn == self.human_colour and self.mode == "play"

    @property
    def move_count(self) -> int:
        return len(self.board.move_stack)

    def fen(self) -> str:
        return self.board.fen()

    def san_history(self) -> list[str]:
        b = chess.Board(self.start_fen) if self.start_fen else chess.Board()
        out = []
        for mv in self.board.move_stack:
            out.append(b.san(mv))
            b.push(mv)
        return out

    def uci_history(self) -> list[str]:
        return [m.uci() for m in self.board.move_stack]

    def last_move(self) -> chess.Move | None:
        return self.board.peek() if self.board.move_stack else None

    def legal(self) -> list[chess.Move]:
        return list(self.board.legal_moves)

    # --- moves --------------------------------------------------------
    def push(self, move: chess.Move) -> str:
        """Play a legal move; returns SAN. Raises ValueError if illegal/over."""
        if self.is_over():
            raise ValueError("game is over")
        if move not in self.board.legal_moves:
            raise ValueError(f"illegal move {move.uci()}")
        self._tick()
        mover = self.board.turn
        san = self.board.san(move)
        self.board.push(move)
        self.redo_stack.clear()
        if self.clock.timed:
            self.clock.running = True
            if mover == chess.WHITE:
                self.clock.white += self.clock.increment
            else:
                self.clock.black += self.clock.increment
        self.clock._last = self.now()
        return san

    def push_san(self, text: str) -> str:
        try:
            move = self.board.parse_san(text)
        except ValueError:
            move = chess.Move.from_uci(text)
        return self.push(move)

    def undo(self, n: int = 1) -> int:
        done = 0
        for _ in range(n):
            if not self.board.move_stack:
                break
            self.redo_stack.append(self.board.pop())
            done += 1
        if done:
            self.forced_result = None
        return done

    def redo(self, n: int = 1) -> int:
        done = 0
        for _ in range(n):
            if not self.redo_stack:
                break
            self.board.push(self.redo_stack.pop())
            done += 1
        return done

    # --- clocks -------------------------------------------------------
    def _tick(self) -> None:
        c = self.clock
        if not c.timed or not c.running or c.paused or c._last is None:
            return
        t = self.now()
        dt = max(0.0, t - c._last)
        c._last = t
        if self.board.turn == chess.WHITE:
            c.white = max(0.0, c.white - dt)
        else:
            c.black = max(0.0, c.black - dt)

    def tick(self) -> None:
        self._tick()

    def pause_clock(self) -> None:
        self._tick()
        self.clock.paused = True

    def resume_clock(self) -> None:
        self.clock.paused = False
        self.clock._last = self.now()

    def add_time(self, seconds: float) -> None:
        mover_white = self.human_colour == chess.WHITE
        if mover_white:
            self.clock.white += seconds
        else:
            self.clock.black += seconds

    def time_left(self, colour: chess.Color) -> float:
        self._tick()
        return self.clock.white if colour == chess.WHITE else self.clock.black

    # --- results ------------------------------------------------------
    def result(self) -> Result | None:
        if self.forced_result:
            return self.forced_result
        b = self.board
        if self.clock.timed:
            self._tick()
            for colour, left in (
                (chess.WHITE, self.clock.white),
                (chess.BLACK, self.clock.black),
            ):
                if left <= 0 and self.clock.running:
                    if b.has_insufficient_material(not colour):
                        return Result("1/2-1/2", "timeout vs insufficient material")
                    return Result("0-1" if colour == chess.WHITE else "1-0", "timeout")
        if b.is_checkmate():
            return Result("0-1" if b.turn == chess.WHITE else "1-0", "checkmate")
        if b.is_stalemate():
            return Result("1/2-1/2", "stalemate")
        if b.is_insufficient_material():
            return Result("1/2-1/2", "insufficient material")
        if b.is_fivefold_repetition():
            return Result("1/2-1/2", "fivefold repetition")
        if b.is_seventyfive_moves():
            return Result("1/2-1/2", "seventy-five-move rule")
        return None

    def is_over(self) -> bool:
        return self.result() is not None

    def resign(self, colour: chess.Color) -> Result:
        self.forced_result = Result(
            "0-1" if colour == chess.WHITE else "1-0", "resignation"
        )
        return self.forced_result

    def agree_draw(self) -> Result:
        self.forced_result = Result("1/2-1/2", "agreement")
        return self.forced_result

    def can_claim_draw(self) -> str | None:
        if self.board.can_claim_threefold_repetition():
            return "threefold repetition"
        if self.board.can_claim_fifty_moves():
            return "fifty-move rule"
        return None

    def claim_draw(self) -> Result | None:
        why = self.can_claim_draw()
        if not why:
            return None
        self.forced_result = Result("1/2-1/2", why)
        return self.forced_result

    # --- helpers for the UI -------------------------------------------
    def captured(self) -> dict[str, list[str]]:
        """Pieces each side has lost (symbols) derived from the position."""
        start = {"p": 8, "n": 2, "b": 2, "r": 2, "q": 1}
        out: dict[str, list[str]] = {"white": [], "black": []}
        for colour, key in ((chess.WHITE, "white"), (chess.BLACK, "black")):
            for sym, n in start.items():
                pt = chess.PIECE_SYMBOLS.index(sym)
                have = len(self.board.pieces(pt, colour))
                out[key].extend([sym] * max(0, n - have))
        return out

    def material(self) -> dict[str, int]:
        vals = {
            chess.PAWN: 1,
            chess.KNIGHT: 3,
            chess.BISHOP: 3,
            chess.ROOK: 5,
            chess.QUEEN: 9,
        }
        w = sum(v * len(self.board.pieces(p, chess.WHITE)) for p, v in vals.items())
        b = sum(v * len(self.board.pieces(p, chess.BLACK)) for p, v in vals.items())
        return {"white": w, "black": b, "diff": w - b}

    def phase(self) -> str:
        n = len(self.board.piece_map())
        if self.move_count < 10 and n > 28:
            return "opening"
        return "endgame" if n <= 14 else "middlegame"

    def snapshot(self) -> dict[str, Any]:
        self._tick()
        b = self.board
        last = self.last_move()
        res = self.result()
        king_sq = b.king(b.turn) if b.is_check() else None
        return {
            "fen": b.fen(),
            "turn": "white" if b.turn else "black",
            "human_colour": "white" if self.human_colour else "black",
            "mode": self.mode,
            "last_move": None
            if last is None
            else {
                "from": chess.square_name(last.from_square),
                "to": chess.square_name(last.to_square),
                "uci": last.uci(),
            },
            "check": chess.square_name(king_sq) if king_sq is not None else None,
            "moves": self.san_history(),
            "uci_moves": self.uci_history(),
            "captured": self.captured(),
            "material": self.material(),
            "clock": {
                "timed": self.clock.timed,
                "white": round(self.clock.white, 1),
                "black": round(self.clock.black, 1),
                "paused": self.clock.paused,
                "running": self.clock.running,
            },
            "result": res.to_dict() if res else None,
            "can_undo": bool(b.move_stack),
            "can_redo": bool(self.redo_stack),
            "legal": sorted(m.uci() for m in b.legal_moves),
        }

    # --- persistence ----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        self._tick()
        return {
            "start_fen": self.start_fen,
            "moves": self.uci_history(),
            "redo": [m.uci() for m in reversed(self.redo_stack)],
            "human": "white" if self.human_colour else "black",
            "mode": self.mode,
            "clock": {
                "base": self.clock.base,
                "increment": self.clock.increment,
                "white": self.clock.white,
                "black": self.clock.black,
                "running": self.clock.running,
            },
            "forced": self.forced_result.to_dict() if self.forced_result else None,
            "headers": self.headers,
        }

    @classmethod
    def from_dict(
        cls, d: dict[str, Any], now: Callable[[], float] = time.monotonic
    ) -> GameState:
        gs = cls(
            start_fen=d.get("start_fen"),
            human_colour=chess.WHITE
            if d.get("human", "white") == "white"
            else chess.BLACK,
            mode=d.get("mode", "play") if d.get("mode") in MODES else "play",
            now=now,
        )
        for uci in d.get("moves", []):
            gs.board.push(chess.Move.from_uci(uci))
        for uci in d.get("redo", []):
            gs.redo_stack.insert(0, chess.Move.from_uci(uci))
        c = d.get("clock") or {}
        gs.clock = Clock(
            base=c.get("base", 0.0),
            increment=c.get("increment", 0.0),
            white=c.get("white", 0.0),
            black=c.get("black", 0.0),
            running=False,
            paused=False,
        )
        f = d.get("forced")
        if f:
            gs.forced_result = Result(f["outcome"], f["reason"])
        gs.headers = dict(d.get("headers") or {})
        if gs.clock.timed and gs.move_count:
            gs.clock.running = True
            gs.clock._last = gs.now()
        return gs
