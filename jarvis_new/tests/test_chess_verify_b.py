"""Verifier B: levels, router, analysis, latency."""
import asyncio, socket, statistics, time

import chess
import pytest

from games.chess.engine import Analysis, Eval
from games.chess.settings import difficulty_level
from test_chess_verify_a import Fake, mk, SCRIPTS
from test_chess_integration import FakeEval


@pytest.fixture
def nonet(monkeypatch):
    calls = []
    def boom(*a, **k):
        calls.append(a); raise OSError("network blocked")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    for k in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return calls


async def test_levels_and_series_levels(tmp_path):
    e = Fake(delay=0.01)
    presets = []
    real = e.best_move
    async def spy(board, preset="club"):
        presets.append(preset); return await real(board, preset)
    e.best_move = spy
    g = mk(tmp_path, e)
    g._takeover_sleep = lambda s: asyncio.sleep(0.004)
    await g.new_game()
    r = await g.handle_text("level seven"); print(r.say)
    assert difficulty_level(g.settings.difficulty) == 7
    r = await g.handle_text("a bit harder"); print(r.say)
    assert difficulty_level(g.settings.difficulty) == 8
    r = await g.handle_text("level eleven"); print("L11", r.ok, r.say)
    assert not r.ok and difficulty_level(g.settings.difficulty) == 8
    # mid-game apply on next engine move
    await g.handle_text("e4")
    await g.drain(2)
    print("presets", presets)
    assert presets and presets[-1] == "level:8", presets
    # mid-series
    presets.clear()
    await g.handle_text("play three games at level 4")
    await asyncio.sleep(0.03)
    r = await g.handle_text("level six"); print("series lvl", r.say)
    r = await g.handle_text("a bit easier"); print(r.say)
    await g.wait_series(timeout=20)
    print("presets", sorted(set(presets)), g.series.state.level)
    assert presets[0] == "level:4" and "level:5" in set(presets)
    # applies to subsequent engine move
    i = presets.index("level:6") if "level:6" in presets else None
    await g.close()


async def test_router_no_key_no_network(tmp_path, nonet):
    g = mk(tmp_path)
    await g.new_game()
    for ph in ["could you make it a bit easier for me", "can you go a little tougher", "I'd like to play several games in a row",
               "what is the meaning of life and the universe", "please hand me back the board"]:
        r = await g.handle_text(ph); print(repr(ph), "->", r.kind, r.say)
    assert nonet == []
    assert g.router.llm_calls == 0
    await g.close()


@pytest.mark.parametrize("ph,expect", [("could you make it a bit easier", 3), ("make it harder please", 5)])
async def test_fuzzy_paraphrase(tmp_path, nonet, ph, expect):
    g = mk(tmp_path)
    await g.new_game()
    g.settings.difficulty = "level:4"
    r = await g.handle_text(ph); print(ph, r.say)
    assert difficulty_level(g.settings.difficulty) == expect
    assert nonet == []
    await g.close()


async def test_moves_never_hijacked(tmp_path, nonet):
    g = mk(tmp_path)
    await g.new_game()
    for mv in ["e4", "knight f3", "pawn to d4", "bishop to c4", "castle kingside", "take it", "e2 e4"]:
        g.router.llm_calls = 0
        pr = g.router.route_offline(mv, g._route_ctx())
        print(mv, "->", pr and pr.intent and pr.intent.name)
        assert pr is None, mv
    # whole flow: e4 plays a move
    r = await g.handle_text("e4"); assert r.kind != "difficulty"
    assert g.state.board.move_stack and g.state.board.move_stack[0] == chess.Move.from_uci("e2e4")
    await g.close()


# 6 --------------------------------------------------------------
def test_classify_thresholds():
    from games.chess import review
    f = review.classify_loss
    got = [f(x) for x in (0, 10, 11, 50, 51, 100, 101, 250, 251, 5000)]
    print(got)
    assert got == ["best","best","good","good","inaccuracy","inaccuracy","mistake","mistake","blunder","blunder"]


async def test_analysis_saved_only_and_board(tmp_path):
    g = mk(tmp_path, eval_engine_factory=FakeEval)
    await g.new_game()
    await g.handle_text("f3"); await g.drain(3)
    await g.handle_text("g4"); await g.drain(3)
    await g.drain_evals()
    ec = g.engine.calls
    r = await g.handle_text("why did you play that"); print("explain", r.say)
    r2 = await g.handle_text("what was my mistake"); print("mistake", r2.say)
    r3 = await g.handle_text("analyse the board"); print("board", r3.say)
    n = lambda t: len([x for x in t.replace("!", ".").split(".") if x.strip()])
    assert n(r3.say) <= 2 and n(r2.say) <= 2
    assert g.engine.calls == ec
    await g.close()


# 7 --------------------------------------------------------------
def pct(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


async def test_latency(tmp_path):
    class Slow(FakeEval):
        async def analyse(self, board, depth=12):
            await asyncio.sleep(2.0); return await super().analyse(board, depth)
    gate_engine = Fake(delay=0.2)
    g = mk(tmp_path, gate_engine, eval_engine_factory=Slow)
    spoken = []
    async def on_say(t): spoken.append((time.perf_counter(), t))
    g.on_say = on_say
    await g.new_game()
    # build some history so list/step back work
    await g.handle_text("f3"); await g.drain(3); await g.handle_text("g4"); await g.drain(3)
    await g.handle_text("new game")
    await g.handle_text("e4"); await g.drain(3)
    res = {}
    async def timed(ph, setup=None, teardown=None):
        xs = []
        for _ in range(20):
            if setup: await setup()
            t = time.perf_counter(); r = await g.handle_text(ph); xs.append((time.perf_counter() - t) * 1000)
            if teardown: await teardown()
        res[ph] = (statistics.median(xs), pct(xs, .95), r.say)
    await timed("list games")
    await timed("level 5")
    await timed("how many games left")
    async def cleanup():
        await g.handle_text("close")
    await g.handle_text("open game 1")
    await timed("step back")
    await g.handle_text("release control") if g.state.mode == "takeover" else None
    await g.handle_text("play five games")
    await timed("pause the series", setup=lambda: g.handle_text("continue the series"))
    await timed("how many games left")
    await timed("what's the score")
    await timed("analyse the board")
    await g.handle_text("release control")
    await g.handle_text("cancel the series")
    for k, v in res.items(): print("LAT %-22s p50=%.1fms p95=%.1fms  %r" % (k, v[0], v[1], v[2]))
    assert all(v[1] < 1500 for v in res.values())
    await g.close()


async def test_slow_evaluator_never_delays_turn(tmp_path):
    class Slow(FakeEval):
        async def analyse(self, board, depth=12):
            await asyncio.sleep(2.0); return await super().analyse(board, depth)
    g = mk(tmp_path, Fake(), eval_engine_factory=Slow)
    await g.new_game()
    t = time.perf_counter(); r = await g.handle_text("f3"); d1 = (time.perf_counter() - t) * 1000
    await g.drain(3)
    t = time.perf_counter(); r2 = await g.handle_text("what was my mistake"); d2 = (time.perf_counter() - t) * 1000
    t = time.perf_counter(); r3 = await g.handle_text("analyse the board"); d3 = (time.perf_counter() - t) * 1000
    t = time.perf_counter(); r4 = await g.handle_text("g4"); d4 = (time.perf_counter() - t) * 1000
    print("SLOWEVAL move=%.0fms explain=%.0fms(%r) board=%.0fms(%r) move2=%.0fms" % (d1, d2, r2.say, d3, r3.say, d4))
    assert max(d1, d2, d3, d4) < 1500
    t = time.perf_counter(); await g.close(); print("close ms", (time.perf_counter() - t) * 1000)
