"""Tests for games.chess.review (FakeEngine scripted evals; no Stockfish)."""

import asyncio
from types import SimpleNamespace

import chess

from games.chess import review
from games.chess.engine import Analysis, Eval, MiniEngine

START = chess.STARTING_FEN
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


class FakeEngine:
    def __init__(self, evals, delay=0.0):
        self.evals = evals  # fen -> (cp, mate)
        self.calls = []
        self.closed = False
        self.delay = delay

    async def analyse(self, board, depth=14):
        self.calls.append(board.fen())
        if self.delay:
            await asyncio.sleep(self.delay)
        cp, mate = self.evals.get(board.fen(), (0, None))
        mv = next(iter(board.legal_moves), None)
        return Analysis(Eval(cp, mate), mv, [mv] if mv else [], 12)

    async def close(self):
        self.closed = True


class FakeStore:
    def __init__(self):
        self.rows = []

    def set_eval(self, gid, ply, **f):
        self.rows.append((gid, ply, f))


def test_classify_thresholds():
    c = review.classify_loss
    assert [c(x) for x in (0, 10, 11, 50, 51, 100, 101, 250, 251, 900)] == [
        "best",
        "best",
        "good",
        "good",
        "inaccuracy",
        "inaccuracy",
        "mistake",
        "mistake",
        "blunder",
        "blunder",
    ]
    assert c(-30) == "best"


def test_cp_loss_mover_pov_and_mate_clamp():
    # White moves: eval drops 0 -> -200 white pov = loss 200
    assert review.cp_loss(Eval(0), Eval(-200), chess.WHITE) == 200
    # Black moves: white eval rising 0 -> 200 is black's loss
    assert review.cp_loss(Eval(0), Eval(200), chess.BLACK) == 200
    # improvement never negative
    assert review.cp_loss(Eval(0), Eval(300), chess.WHITE) == 0
    # missed mate: clamp to 1000 not 10000
    assert review.cp_loss(Eval(None, 3), Eval(0), chess.WHITE) == 1000
    # walking into mate
    assert review.cp_loss(Eval(0), Eval(None, -2), chess.WHITE) == 1000


async def test_evaluator_reuses_after_as_next_before():
    eng = FakeEngine({START: (20, None), AFTER_E4: (30, None)})
    store = FakeStore()
    ev = review.MoveEvaluator(lambda: eng, store)
    b2 = chess.Board(AFTER_E4)
    b2.push_san("e5")
    ev.enqueue("g", 1, START, AFTER_E4, "e2e4")
    ev.enqueue("g", 2, AFTER_E4, b2.fen(), "e7e5")
    await ev.drain()
    assert eng.calls.count(AFTER_E4) == 1
    assert len(eng.calls) == 3
    assert [r[1] for r in store.rows] == [1, 2]
    f = store.rows[0][2]
    assert f["cls"] == "best" and f["eval_cp"] == 30 and f["best_uci"]
    await ev.close()
    assert eng.closed


async def test_evaluator_blunder_mover_pov():
    after = AFTER_E4
    eng = FakeEngine({START: (20, None), after: (-400, None)})
    store = FakeStore()
    ev = review.MoveEvaluator(lambda: eng, store)
    ev.enqueue("g", 1, START, after, "e2e4")
    await ev.drain()
    assert store.rows[0][2]["cls"] == "blunder"
    assert store.rows[0][2]["cp_loss"] == 420
    await ev.close()


async def test_enqueue_never_blocks_and_single_worker():
    eng = FakeEngine({}, delay=0.05)
    store = FakeStore()
    made = []

    def factory():
        made.append(1)
        return eng

    ev = review.MoveEvaluator(factory, store)
    t = asyncio.get_running_loop().time()
    for i in range(5):
        ev.enqueue("g", i + 1, START, AFTER_E4, "e2e4")
    assert asyncio.get_running_loop().time() - t < 0.02
    await ev.drain()
    assert len(made) == 1
    await ev.close()


async def test_close_cancels_pending():
    eng = FakeEngine({}, delay=0.5)
    store = FakeStore()
    ev = review.MoveEvaluator(lambda: eng, store)
    for i in range(4):
        ev.enqueue("g", i + 1, START, AFTER_E4, "e2e4")
    await asyncio.sleep(0.01)
    await ev.close()
    assert store.rows == []
    ev.enqueue("g", 9, START, AFTER_E4, "e2e4")  # after close: ignored
    await asyncio.sleep(0.01)
    assert store.rows == []


async def test_engine_error_does_not_kill_worker():
    class Bad(FakeEngine):
        async def analyse(self, board, depth=14):
            if len(self.calls) == 0:
                self.calls.append("x")
                raise RuntimeError("boom")
            return await super().analyse(board, depth)

    store = FakeStore()
    ev = review.MoveEvaluator(lambda: Bad({}), store)
    ev.enqueue("g", 1, START, AFTER_E4, "e2e4")
    ev.enqueue("g", 2, START, AFTER_E4, "e2e4")
    await ev.drain()
    assert [r[1] for r in store.rows] == [2]
    await ev.close()


async def test_evaluator_with_mini_engine():
    store = FakeStore()
    ev = review.MoveEvaluator(lambda: MiniEngine(seed=1), store)
    ev.enqueue("g", 1, START, AFTER_E4, "e2e4")
    await ev.drain()
    assert store.rows and store.rows[0][2]["cls"] in review.CLASSES
    await ev.close()


def _kinds(facts, key):
    return {(h["piece"], h["square"]) for h in facts[key]}


def test_hanging_queen():
    # white Qd4 attacked by black Nc6... undefended
    fen = "4k3/8/2n5/8/3Q4/8/8/4K3 w - - 0 1"
    f = review.position_facts(fen)
    assert ("queen", "d4") in _kinds(f, "hanging")
    assert any("queen" in s and "d4" in s for s in f["statements"])


def test_undefended_knight_attacked():
    fen = "4k3/8/8/3n4/8/2B5/8/4K3 w - - 0 1"  # Bc3 doesn't attack d5; use Qd1
    fen = "4k3/8/8/3n4/8/8/8/3QK3 w - - 0 1"
    f = review.position_facts(fen)
    assert ("knight", "d5") in _kinds(f, "hanging")


def test_defended_piece_not_hanging():
    fen = "4k3/8/4p3/3n4/8/8/8/3QK3 w - - 0 1"  # Nd5 defended by e6 pawn
    f = review.position_facts(fen)
    assert ("knight", "d5") not in _kinds(f, "hanging")


def test_null_move_threat():
    # black to threaten Bxf2? white to move; black bishop on b6 hits f2 pawn guarded by king only
    fen = "4k3/8/1b6/8/8/8/5N2/7K w - - 0 1"
    f = review.position_facts(fen)
    assert any(t["victim"] == "knight" and t["square"] == "f2" for t in f["threats"])


def test_checks_available():
    f = review.position_facts("4k3/8/8/8/8/8/8/R3K3 w - - 0 1")
    assert "Ra8+" in f["checks"]


def _rec(moves):
    return SimpleNamespace(id="g", moves=moves, start_fen=START)


def _mv(ply, san, uci, fen_after, cp=None, mate=None, **kw):
    d = dict(
        ply=ply,
        san=san,
        uci=uci,
        fen_after=fen_after,
        eval_cp=cp,
        eval_mate=mate,
        best_uci=None,
        cls=None,
    )
    d.update(kw)
    return d


def test_explain_facts_saved_only(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("engine used")

    monkeypatch.setattr(MiniEngine, "analyse", boom, raising=False)
    b = chess.Board()
    b.push_san("e4")
    f1 = b.fen()
    b.push_san("e5")
    f2 = b.fen()
    rec = _rec(
        [
            _mv(1, "e4", "e2e4", f1, cp=30),
            _mv(2, "e5", "e7e5", f2, cp=400, best_uci="c7c5", pv_uci=["c7c5", "g1f3"]),
        ]
    )
    f = review.explain_facts(rec, 2)
    assert f["cp_loss"] == 370 and f["cls"] == "blunder"
    assert f["best_uci"] == "c7c5" and f["best_san"] == "c5"
    assert f["played"] == "e5" and f["pv"][:2] == ["c5", "Nf3"]
    assert "hanging" in f and "threats" in f
    assert review.explain_facts(rec, 99) is None


def test_analyse_board_short():
    fen = "4k3/8/2n5/8/3Q4/8/8/4K3 w - - 0 1"
    text, full = review.analyse_board(fen, Eval(-250))
    import re

    assert len(re.split(r"(?<=\.)\s+", text.strip())) <= 2 and len(text) < 300
    assert "queen" in text.lower() or "d4" in text
    assert full["eval"]["cp"] == -250
    t2, _ = review.analyse_board(START, None)
    assert t2


def test_key_moments_top3():
    b = chess.Board()
    moves = []
    losses = [0, 10, 500, 20, 120, 300]
    for i, ls in enumerate(losses):
        moves.append(
            _mv(
                i + 1,
                "x",
                "a1a2",
                b.fen(),
                cp=0,
                cp_loss=ls,
                cls=review.classify_loss(ls),
            )
        )
    km = review.key_moments(_rec(moves), 3)
    assert [m["ply"] for m in km] == [3, 6, 5]
    assert review.key_moments(_rec([]), 3) == []


def test_phrase_facts_never_invents():
    facts = {"statements": ["Your queen on d4 is hanging.", "Black threatens Bxf2."]}
    out = review.phrase_facts(facts)
    assert set(out["facts"]) <= set(facts["statements"])
    assert "d4" in out["text"]
    # honest rephrase accepted
    out = review.phrase_facts(
        facts, llm=lambda st: "Careful, the queen on d4 hangs, and Bxf2 looms."
    )
    assert out["text"].startswith("Careful")
    # inventing a square/number rejected -> template
    out = review.phrase_facts(
        facts, llm=lambda st: "Your queen on d4 hangs and the rook on h8 is lost in 3."
    )
    assert "h8" not in out["text"] and set(out["facts"]) <= set(facts["statements"])

    # llm crash -> template
    def bad(st):
        raise RuntimeError

    assert review.phrase_facts(facts, llm=bad)["text"]
    assert review.phrase_facts({"statements": []})["text"]
