"""Stockfish over UCI (async, off the event loop) with a built-in fallback.

Stockfish chooses and judges moves. The LLM never does. If the binary is
missing the small ``MiniEngine`` keeps the game playable (weaker, honest).
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import shutil
from dataclasses import dataclass, field

import chess
import chess.engine

from games.chess.settings import PRESET_ELO, level_elo


@dataclass
class Eval:
    cp: int | None = None  # white point of view
    mate: int | None = None  # white pov; +n = white mates in n

    def as_dict(self) -> dict:
        return {"cp": self.cp, "mate": self.mate}

    def for_colour(self, colour: chess.Color) -> int:
        """Centipawns from colour's view, mates mapped to +-10000."""
        v = self._flat()
        return v if colour == chess.WHITE else -v

    def _flat(self) -> int:
        if self.mate is not None:
            return 10000 - abs(self.mate) if self.mate > 0 else -10000 + abs(self.mate)
        return self.cp or 0


@dataclass
class Analysis:
    eval: Eval = field(default_factory=Eval)
    best: chess.Move | None = None
    pv: list[chess.Move] = field(default_factory=list)
    depth: int = 0


def find_stockfish() -> str | None:
    return shutil.which("stockfish") or shutil.which("stockfish-17") or None


def elo_of(preset: str) -> int:
    """Preset name or ``elo:1500`` -> rating."""
    if preset.startswith("level:"):
        try:
            return level_elo(int(preset[6:]))
        except ValueError:
            return 1200
    if preset.startswith("elo:"):
        try:
            return int(preset[4:])
        except ValueError:
            return 1200
    return PRESET_ELO.get(preset, 1200)


class BaseEngine:
    name = "base"

    async def start(self) -> None: ...

    async def best_move(self, board: chess.Board, preset: str = "club") -> chess.Move:
        raise NotImplementedError

    async def analyse(self, board: chess.Board, depth: int = 14) -> Analysis:
        raise NotImplementedError

    def cancel(self) -> None:
        """Abort any running search at once."""

    async def close(self) -> None: ...


class StockfishEngine(BaseEngine):
    name = "stockfish"

    def __init__(self, path: str | None = None):
        self.path = path or find_stockfish()
        self._engine: chess.engine.UciProtocol | None = None
        self._lock = asyncio.Lock()
        self._current: asyncio.Future | None = None
        self._cancelled = False

    async def start(self) -> None:
        if self._engine or not self.path:
            return
        _t, self._engine = await chess.engine.popen_uci(self.path)
        await self._engine.configure({"Hash": 64, "Threads": 2})

    async def _configure(self, preset: str) -> chess.engine.Limit:
        assert self._engine
        elo = elo_of(preset)
        opts = self._engine.options
        if preset == "full" or elo >= 3000:
            if "UCI_LimitStrength" in opts:
                await self._engine.configure({"UCI_LimitStrength": False})
            return chess.engine.Limit(time=0.6)
        if elo >= 1350 and "UCI_Elo" in opts:
            lo = opts["UCI_Elo"].min or 1320
            await self._engine.configure(
                {"UCI_LimitStrength": True, "UCI_Elo": max(lo, elo)}
            )
            return chess.engine.Limit(time=0.35)
        # Below stockfish's UCI_Elo floor: Skill Level plus shallow search.
        cfg = {}
        if "UCI_LimitStrength" in opts:
            cfg["UCI_LimitStrength"] = False
        if "Skill Level" in opts:
            cfg["Skill Level"] = 0 if elo < 1000 else 3
        if cfg:
            await self._engine.configure(cfg)
        return chess.engine.Limit(depth=1 if elo < 1000 else 3, time=0.2)

    async def best_move(self, board: chess.Board, preset: str = "club") -> chess.Move:
        await self.start()
        if not self._engine:
            raise RuntimeError("stockfish unavailable")
        async with self._lock:
            self._cancelled = False
            limit = await self._configure(preset)
            res = await self._engine.play(board, limit)
        if res.move is None:
            raise RuntimeError("engine returned no move")
        return res.move

    async def analyse(self, board: chess.Board, depth: int = 14) -> Analysis:
        await self.start()
        if not self._engine:
            raise RuntimeError("stockfish unavailable")
        async with self._lock:
            if "UCI_LimitStrength" in self._engine.options:
                await self._engine.configure({"UCI_LimitStrength": False})
            info = await self._engine.analyse(
                board, chess.engine.Limit(depth=depth, time=3)
            )
        score = info["score"].white()
        ev = Eval(cp=score.score(), mate=score.mate())
        pv = list(info.get("pv", []))[:5]
        return Analysis(ev, pv[0] if pv else None, pv, info.get("depth", depth))

    def cancel(self) -> None:
        self._cancelled = True
        eng = self._engine
        if eng is None:
            return
        # Send UCI "stop" so a running search returns immediately.
        with contextlib.suppress(Exception):
            eng.send_line("stop")

    async def close(self) -> None:
        if self._engine:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._engine.quit(), 2)
            self._engine = None


# ---------------------------------------------------------------- fallback
_VAL = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}
_CENTRE = [
    chess.D4,
    chess.E4,
    chess.D5,
    chess.E5,
]


def _static(board: chess.Board) -> int:
    """White-pov static eval: material plus a small centre/mobility term."""
    if board.is_checkmate():
        return -30000 if board.turn == chess.WHITE else 30000
    if board.is_stalemate() or board.is_insufficient_material():
        return 0
    score = 0
    for sq, p in board.piece_map().items():
        v = _VAL[p.piece_type]
        if sq in _CENTRE and p.piece_type in (chess.PAWN, chess.KNIGHT):
            v += 25
        if p.piece_type in (chess.KNIGHT, chess.BISHOP):
            r = chess.square_rank(sq)
            home = 0 if p.color == chess.WHITE else 7
            if r == home:
                v -= 20
        score += v if p.color == chess.WHITE else -v
    return score


class MiniEngine(BaseEngine):
    """Alpha-beta fallback. Not Stockfish; used only when it is missing."""

    name = "mini"

    def __init__(self, seed: int | None = None):
        self._rng = random.Random(seed)
        self._stop = False  # analysis only; the opponent's move is never cut

    def _search(
        self,
        board: chess.Board,
        depth: int,
        alpha: int,
        beta: int,
        stoppable: bool = False,
    ) -> int:
        if depth == 0 or board.is_game_over():
            s = _static(board)
            return s if board.turn == chess.WHITE else -s
        best = -(10**9)
        moves = sorted(board.legal_moves, key=lambda m: -board.is_capture(m))
        for m in moves:
            if stoppable and self._stop:
                break
            board.push(m)
            v = -self._search(board, depth - 1, -beta, -alpha, stoppable)
            board.pop()
            best = max(best, v)
            alpha = max(alpha, v)
            if alpha >= beta:
                break
        return best

    def _scored(
        self, board: chess.Board, depth: int, stoppable: bool = False
    ) -> list[tuple[int, chess.Move]]:
        out = []
        for m in board.legal_moves:
            if stoppable and self._stop:
                break
            board.push(m)
            v = -self._search(board, depth - 1, -(10**9), 10**9, stoppable)
            board.pop()
            out.append((v + self._rng.randint(0, 3), m))
        out.sort(key=lambda x: -x[0])
        return out

    async def best_move(self, board: chess.Board, preset: str = "club") -> chess.Move:
        elo = elo_of(preset)
        depth = 1 if elo < 1000 else 2 if elo < 1800 else 3
        b = board.copy(stack=False)
        scored = await asyncio.to_thread(self._scored, b, depth, False)
        if not scored:
            raise RuntimeError("no legal moves")
        if elo < 1000:
            return self._rng.choice(scored[: max(2, len(scored) // 2)])[1]
        if elo < 1500 and self._rng.random() < 0.25 and len(scored) > 1:
            return scored[1][1]
        return scored[0][1]

    async def analyse(self, board: chess.Board, depth: int = 14) -> Analysis:
        self._stop = False
        b = board.copy(stack=False)
        scored = await asyncio.to_thread(self._scored, b, 3, True)
        if not scored:
            return Analysis(Eval(cp=_static(b)))
        v, best = scored[0]
        cp = v if board.turn == chess.WHITE else -v
        ev = Eval(mate=None, cp=cp)
        if abs(cp) > 9000:
            ev = Eval(mate=(1 if cp > 0 else -1) * 2)
        pv = [best]
        b.push(best)
        reply = await asyncio.to_thread(self._scored, b, 2, True)
        if reply:
            pv.append(reply[0][1])
        return Analysis(ev, best, pv, 3)

    def cancel(self) -> None:
        self._stop = True


async def make_engine() -> BaseEngine:
    """Stockfish when installed and working, else the fallback."""
    if find_stockfish():
        eng = StockfishEngine()
        try:
            await asyncio.wait_for(eng.start(), 10)
            return eng
        except Exception:
            await eng.close()
    return MiniEngine()
