"""Fixes from verification: continuous takeover, spoken fallbacks, no unawaited coroutines."""
import asyncio
import gc
import warnings
from pathlib import Path

import pytest

from games.chess.settings import ChessSettings, load, save
from test_chess_verify_a import mk


async def _wait(pred, n=600):
    for _ in range(n):
        await asyncio.sleep(0.005)
        if pred():
            return True
    return False


def _finished(g):
    return [i for i in g.store.list() if i["status"] == "finished"]


def test_takeover_continuous_setting(tmp_path):
    assert ChessSettings().takeover_continuous is True
    s = ChessSettings()
    assert s.update(takeover_continuous="off") == ["takeover_continuous"]
    assert s.takeover_continuous is False
    p = tmp_path / "s.json"
    save(s, p)
    assert load(p).takeover_continuous is False


async def test_plain_takeover_plays_three_games_then_stop(tmp_path):
    g = mk(tmp_path)
    order = []
    orig = g._new_record

    def nr():
        r = orig()
        order.append(r.id)
        return r

    g._new_record = nr
    await g.new_game()
    await g.handle_text("take over")
    assert await _wait(lambda: len(_finished(g)) >= 3)
    await g.handle_text("stop")
    await asyncio.sleep(0.05)
    assert g.state.mode != "takeover"
    assert g._auto_task is None or g._auto_task.done()
    fin = {i["id"] for i in _finished(g)}
    recs = [g.store.load(x) for x in order if x in fin]
    assert len(recs) >= 3
    cols = [r.white for r in recs]
    assert all(cols[i] != cols[i + 1] for i in range(len(cols) - 1)), cols


async def test_scoped_takeover_stays_single(tmp_path):
    g = mk(tmp_path)
    await g.new_game()
    await g.handle_text("take over")
    await g.handle_text("stop")
    g2 = mk(tmp_path / "b")
    await g2.new_game()
    await g2.handle_text("play 2 moves for me")
    assert await _wait(lambda: g2.state.mode != "takeover")
    assert not _finished(g2)
    g3 = mk(tmp_path / "c")
    await g3.new_game()
    await g3.handle_text("finish the game")
    assert await _wait(lambda: g3.state.mode != "takeover" or len(_finished(g3)) >= 1)
    await asyncio.sleep(0.2)
    assert len(_finished(g3)) <= 1


@pytest.mark.parametrize("phrase", ["analyse the board", "explain", "what's the score",
                                    "explain the last move"])
@pytest.mark.parametrize("moved", [False, True])
async def test_analysis_phrases_always_reply_spoken(tmp_path, phrase, moved):
    g = mk(tmp_path)
    await g.new_game()
    if moved:
        await g.handle_text("e4")
    r = await g.handle_text(phrase)
    assert r.say and r.say.strip()
    await asyncio.sleep(0.05)


async def test_no_never_awaited_warning(tmp_path):
    g = mk(tmp_path)
    await g.new_game()
    await g.handle_text("e4")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        t = g._spawn(g._explain_task(1))
        t.cancel()  # cancelled before its first step
        t2 = g._spawn(g._auto_entry())
        t2.cancel()
        await asyncio.gather(t, t2, return_exceptions=True)
        gc.collect()
    assert not [x for x in w if "never awaited" in str(x.message)], [str(x.message) for x in w]
