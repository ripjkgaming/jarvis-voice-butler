"""Move review: background per-move eval, static position facts, explanations.

Contract (see series/takeover design): ``MoveEvaluator`` runs a SECOND
engine (Threads=1, Hash=32, depth 12, capped 0.3 s) behind a single-worker
queue so the play engine and voice turn are never blocked. Results are
merged into the store via ``store.set_eval(game_id, ply, **fields)`` with
fields eval_cp, eval_mate, depth, best_uci, pv_uci, cp_loss, cls.

Everything spoken is built from facts (saved evals + static board
analysis); an LLM may only rephrase (``phrase_facts``) and is rejected if
it adds squares or numbers. stdlib + python-chess only.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from typing import Any, Callable

import chess
import chess.engine

from games.chess.engine import (
    Analysis,
    BaseEngine,
    Eval,
    MiniEngine,
    StockfishEngine,
    find_stockfish,
)

CLASSES = ("best", "good", "inaccuracy", "mistake", "blunder")
MATE_CLAMP = 1000
_VAL = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}
_NAME = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
         chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king"}
_COLOUR = {chess.WHITE: "White", chess.BLACK: "Black"}


def classify_loss(cp_loss: float) -> str:
    if cp_loss <= 10:
        return "best"
    if cp_loss <= 50:
        return "good"
    if cp_loss <= 100:
        return "inaccuracy"
    if cp_loss <= 250:
        return "mistake"
    return "blunder"


def _as_eval(e: Any) -> Eval | None:
    if e is None:
        return None
    if isinstance(e, Eval):
        return e
    if isinstance(e, dict):
        return Eval(e.get("cp"), e.get("mate"))
    return Eval(getattr(e, "cp", None), getattr(e, "mate", None))


def _clamped(e: Eval, colour: chess.Color) -> int:
    return max(-MATE_CLAMP, min(MATE_CLAMP, e.for_colour(colour)))


def cp_loss(before: Eval, after: Eval, mover: chess.Color) -> int:
    """Mover-POV centipawn loss from White-POV evals; mates clamp to 1000."""
    return max(0, _clamped(before, mover) - _clamped(after, mover))


def make_analysis_engine() -> BaseEngine:
    """Second engine instance for background review (Threads=1, Hash=32)."""
    if find_stockfish():
        return _ReviewStockfish()
    return MiniEngine()


class _ReviewStockfish(StockfishEngine):
    async def start(self) -> None:
        if self._engine or not self.path:
            return
        _t, self._engine = await chess.engine.popen_uci(self.path)
        await self._engine.configure({"Hash": 32, "Threads": 1})

    async def analyse(self, board: chess.Board, depth: int = 12) -> Analysis:
        await self.start()
        if not self._engine:
            raise RuntimeError("stockfish unavailable")
        async with self._lock:
            info = await self._engine.analyse(
                board, chess.engine.Limit(depth=min(depth, 12), time=0.3))
        score = info["score"].white()
        pv = list(info.get("pv", []))[:5]
        return Analysis(Eval(score.score(), score.mate()), pv[0] if pv else None, pv,
                        info.get("depth", depth))


class MoveEvaluator:
    """Background per-move evaluator. ``enqueue`` is sync and never blocks."""

    DEPTH = 12

    def __init__(self, engine_factory: Callable[[], Any], store: Any, on_done: Callable | None = None):
        self._factory = engine_factory
        self._store = store
        self._on_done = on_done
        self._q: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self._engine: Any = None
        self._cache: dict[str, Analysis] = {}
        self._closed = False

    def enqueue(self, game_id: str, ply: int, fen_before: str, fen_after: str, move: Any) -> bool:
        if self._closed:
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        if self._q is None:
            self._q = asyncio.Queue()
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._run())
        uci = move.uci() if hasattr(move, "uci") else str(move)
        self._q.put_nowait((game_id, ply, fen_before, fen_after, uci))
        return True

    async def drain(self) -> None:
        """Wait until the queue is processed (tests / shutdown)."""
        if self._q is not None and self._task is not None and not self._task.done():
            await self._q.join()

    async def close(self) -> None:
        self._closed = True
        task, self._task = self._task, None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        eng, self._engine = self._engine, None
        if eng is not None:
            with contextlib.suppress(Exception):
                if hasattr(eng, "cancel"):
                    eng.cancel()
                await eng.close()
        self._q = None

    async def _get_engine(self):
        if self._engine is None:
            eng = self._factory()
            if inspect.isawaitable(eng):
                eng = await eng
            self._engine = eng
        return self._engine

    async def _eval(self, fen: str) -> Analysis:
        hit = self._cache.get(fen)
        if hit is not None:
            return hit
        eng = await self._get_engine()
        res = await eng.analyse(chess.Board(fen), self.DEPTH)
        if len(self._cache) > 8:
            self._cache.pop(next(iter(self._cache)))
        self._cache[fen] = res
        return res

    async def _run(self) -> None:
        q = self._q
        while q is not None:
            item = await q.get()
            try:
                await self._process(*item)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # one bad position must not kill the worker
            finally:
                q.task_done()

    async def _process(self, gid, ply, fen_before, fen_after, uci) -> None:
        before = await self._eval(fen_before)
        after = await self._eval(fen_after)  # kept: next move's "before"
        mover = chess.Board(fen_before).turn
        loss = cp_loss(before.eval, after.eval, mover)
        # Played the engine's own top move -> no loss, whatever the noise.
        if before.best is not None and before.best.uci() == uci:
            loss = 0
        fields = {
            "eval_cp": after.eval.cp, "eval_mate": after.eval.mate,
            "depth": after.depth,
            "best_uci": before.best.uci() if before.best else None,
            "pv_uci": [m.uci() for m in before.pv[:5]],
            "cp_loss": loss, "cls": classify_loss(loss),
        }
        r = self._store.set_eval(gid, ply, **fields)
        if inspect.isawaitable(r):
            await r
        if self._on_done:
            with contextlib.suppress(Exception):
                self._on_done(gid, ply, fields)


# ---------------------------------------------------------------- facts


def _hanging_for(board: chess.Board, colour: chess.Color) -> list[dict]:
    out = []
    for sq, p in board.piece_map().items():
        if p.color != colour or p.piece_type == chess.KING:
            continue
        att = board.attackers(not colour, sq)
        if not att:
            continue
        defended = bool(board.attackers(colour, sq))
        low = min(_VAL[board.piece_type_at(a)] for a in att)
        if not defended or low < _VAL[p.piece_type]:
            out.append({"piece": _NAME[p.piece_type], "square": chess.square_name(sq),
                        "colour": _COLOUR[colour].lower(),
                        "reason": "undefended" if not defended else "attacked by a cheaper piece"})
    return out


def position_facts(fen: str) -> dict:
    """Static facts: hanging pieces, null-move threats, available checks."""
    board = chess.Board(fen)
    stm = board.turn
    hanging = _hanging_for(board, chess.WHITE) + _hanging_for(board, chess.BLACK)
    checks = []
    for m in board.legal_moves:
        if board.gives_check(m):
            checks.append(board.san(m))
    threats = []
    if not board.is_check():
        nb = board.copy(stack=False)
        nb.push(chess.Move.null())
        opp_hang = {h["square"] for h in _hanging_for(board, stm)}
        for m in nb.legal_moves:
            if not nb.is_capture(m):
                continue
            victim = nb.piece_at(m.to_square)
            att = nb.piece_at(m.from_square)
            if victim is None or victim.piece_type == chess.KING:
                continue
            sq = chess.square_name(m.to_square)
            wins = sq in opp_hang or _VAL[victim.piece_type] > _VAL[att.piece_type]
            if wins:
                threats.append({"by": _COLOUR[nb.turn], "san": nb.san(m),
                                "victim": _NAME[victim.piece_type], "square": sq})
        for m in nb.legal_moves:
            nb.push(m)
            mate = nb.is_checkmate()
            nb.pop()
            if mate:
                threats.append({"by": _COLOUR[nb.turn], "san": nb.san(m),
                                "victim": "king", "square": chess.square_name(m.to_square)})
    st = []
    for h in hanging:
        st.append(f"The {h['colour']} {h['piece']} on {h['square']} is {h['reason']}.")
    for t in threats:
        if t["victim"] == "king":
            st.append(f"{t['by']} threatens mate with {t['san']}.")
        else:
            st.append(f"{t['by']} threatens {t['san']}, winning the {t['victim']} on {t['square']}.")
    return {"fen": fen, "hanging": hanging, "threats": threats, "checks": checks, "statements": st}


# ------------------------------------------------------------ explaining


def _g(o: Any, k: str, d=None):
    return o.get(k, d) if isinstance(o, dict) else getattr(o, k, d)


def _eval_of(m: dict) -> Eval | None:
    if m.get("eval_cp") is None and m.get("eval_mate") is None:
        return None
    return Eval(m.get("eval_cp"), m.get("eval_mate"))


def _san_line(fen: str, ucis: list[str]) -> list[str]:
    b = chess.Board(fen)
    out = []
    for u in ucis:
        try:
            mv = chess.Move.from_uci(u)
            if mv not in b.legal_moves:
                break
            out.append(b.san(mv))
            b.push(mv)
        except Exception:
            break
    return out


def explain_facts(rec: Any, ply: int) -> dict | None:
    """Facts for one move from SAVED data only (never calls an engine)."""
    moves = _g(rec, "moves", []) or []
    if ply < 1 or ply > len(moves):
        return None
    m = moves[ply - 1]
    fen_before = moves[ply - 2]["fen_after"] if ply >= 2 else (_g(rec, "start_fen") or chess.STARTING_FEN)
    mover = chess.Board(fen_before).turn
    loss = m.get("cp_loss")
    if loss is None:
        prev = _eval_of(moves[ply - 2]) if ply >= 2 else Eval(20)
        cur = _eval_of(m)
        loss = cp_loss(prev, cur, mover) if prev and cur else None
    cls = m.get("cls") or (classify_loss(loss) if loss is not None else None)
    best_uci = m.get("best_uci")
    pv = _san_line(fen_before, list(m.get("pv_uci") or ([best_uci] if best_uci else [])))
    best_san = pv[0] if pv else None
    pf = position_facts(m["fen_after"])
    f = {
        "ply": ply, "mover": _COLOUR[mover], "played": m.get("san"), "played_uci": m.get("uci"),
        "cp_loss": loss, "cls": cls, "best_uci": best_uci, "best_san": best_san, "pv": pv,
        "eval": _eval_of(m).as_dict() if _eval_of(m) else None,
        "hanging": pf["hanging"], "threats": pf["threats"],
    }
    st = []
    if cls in ("inaccuracy", "mistake", "blunder") and m.get("san"):
        s = f"{m['san']} was a {cls}, losing about {int(loss)} centipawns."
        if best_san and best_san != m["san"]:
            s += f" {best_san} was better."
        st.append(s)
    elif cls:
        st.append(f"{m.get('san')} was {'the best move' if cls == 'best' else 'a good move'}.")
    st += pf["statements"][:2]
    f["statements"] = st
    return f


def _eval_phrase(e: Eval | None) -> str:
    if e is None:
        return "I have no evaluation yet"
    if e.mate is not None:
        who = "White" if e.mate > 0 else "Black"
        return f"{who} has a forced mate in {abs(e.mate)}"
    cp = e.cp or 0
    if abs(cp) < 30:
        return "The position is roughly equal"
    side = "White" if cp > 0 else "Black"
    size = "slightly better" if abs(cp) < 100 else "clearly better" if abs(cp) < 300 else "winning"
    return f"{side} is {size} at {abs(cp) / 100:.1f} pawns"


def analyse_board(fen: str, eval: Any = None) -> tuple[str, dict]:
    """(<=2-sentence voice text, full facts dict). Deterministic template."""
    e = _as_eval(eval)
    pf = position_facts(fen)
    first = _eval_phrase(e) + "."
    second = pf["statements"][0] if pf["statements"] else ""
    text = (first + (" " + second if second else "")).strip()
    full = dict(pf)
    full["eval"] = e.as_dict() if e else None
    return text, full


def key_moments(rec: Any, n: int = 3) -> list[dict]:
    """Top-n biggest losses from saved data, biggest first."""
    out = []
    moves = _g(rec, "moves", []) or []
    for i, m in enumerate(moves):
        loss = m.get("cp_loss")
        if loss is None and i >= 1:
            prev, cur = _eval_of(moves[i - 1]), _eval_of(m)
            if prev and cur:
                try:
                    loss = cp_loss(prev, cur, chess.Board(moves[i - 1]["fen_after"]).turn)
                except Exception:
                    loss = None
        if loss is None or loss <= 50:
            continue
        out.append({"ply": m.get("ply", i + 1), "san": m.get("san"), "cp_loss": loss,
                    "cls": m.get("cls") or classify_loss(loss)})
    out.sort(key=lambda d: -d["cp_loss"])
    return out[:n]


def _tokens(s: str) -> set[str]:
    import re
    return set(re.findall(r"[a-h][1-8]|\d+", s.lower()))


def phrase_facts(facts: dict, llm: Callable[[list[str]], str] | None = None) -> dict:
    """Voice text from facts. LLM may only rephrase; invented squares/numbers rejected."""
    st = list(facts.get("statements") or [])
    template = " ".join(st[:2]) if st else "Nothing notable, Sir."
    text = template
    if llm and st:
        try:
            cand = llm(list(st))
        except Exception:
            cand = None
        if isinstance(cand, str) and cand.strip() and _tokens(cand) <= _tokens(" ".join(st)):
            text = cand.strip()
    return {"text": text, "facts": [s for s in st if s in st]}
