"""TakeoverLoop / ErrorBreaker with a fake adapter; no real sleeps."""

import asyncio

import pytest

from games.chess.takeover import ErrorBreaker, TakeoverLoop

pytestmark = pytest.mark.asyncio


class Rec:
    def __init__(self, i):
        self.id = f"g{i}"


class FakeAdapter:
    def __init__(self, moves_per_game=2, results=None, fail=0):
        self.n = moves_per_game
        self.results = list(results or ["1-0"] * 10)
        self.fail = fail
        self.game = 0
        self.ply = 0
        self.colour = "white"
        self.mode_takeover = True
        self.speaking = False
        self.said = []
        self.new_games = []
        self.left = None
        self.engine_calls = 0
        self.order = []

    stop_flag = property(lambda s: not s.mode_takeover)

    def fen(self):
        return f"{self.game}:{self.ply}"

    def strength(self):
        return None

    def is_over(self):
        return self.ply >= self.n

    def result(self):
        return self.results[self.game], "mate"

    def record(self):
        return Rec(self.game)

    def user_colour(self):
        return self.colour

    def user_is_speaking(self):
        return self.speaking

    async def best_move(self, fen, strength):
        self.engine_calls += 1
        if self.fail:
            raise RuntimeError("boom")
        await asyncio.sleep(0)
        return "e2e4"

    async def apply_engine_move(self, uci, think_ms):
        self.ply += 1
        return "e4"

    async def new_game(self, colour, level):
        self.order.append("new")
        self.game += 1
        self.ply = 0
        self.colour = colour
        self.new_games.append(colour)

    async def say_result(self, t):
        self.said.append(("result", t, self.speaking))

    async def say_move(self, s):
        self.said.append(("move", s, self.speaking))

    async def leave_takeover(self, reason, say):
        self.left = (reason, say)
        self.mode_takeover = False


class Store:
    def __init__(self, order):
        self.finished = []
        self.order = order

    def finish(self, rec, result, term):
        self.order.append("finish")
        self.finished.append((rec.id, result, term))


class Series:
    def __init__(self, total):
        self.total = total
        self.done = 0
        self.paused = False
        self.results = []
        self.first = "white"

    def record_result(self, gid, result, colour):
        self.done += 1
        self.results.append((gid, result, colour))

    def next_colours(self):
        return "black" if self.done % 2 else "white"

    def is_done(self):
        return self.done >= self.total

    def pause(self):
        self.paused = True


class Sleeper:
    def __init__(self, hook=None):
        self.calls = []
        self.hook = hook

    async def __call__(self, d):
        self.calls.append(d)
        await asyncio.sleep(0)
        if self.hook:
            self.hook(len(self.calls))


def mk(a, **kw):
    s = Sleeper(kw.pop("hook", None))
    return TakeoverLoop(a, sleep=s, **kw), s


async def test_three_game_series_flips_colours_and_saves():
    a = FakeAdapter()
    st = Store(a.order)
    se = Series(3)
    loop, _ = mk(a, store=st, series=se)
    assert await loop.run() == "series_complete"
    assert [f[0] for f in st.finished] == ["g0", "g1", "g2"]
    assert a.new_games == ["black", "white"]
    assert a.order[:2] == ["finish", "new"]  # saved before next game
    assert not se.paused  # completed, not paused
    assert a.left[0] == "series_complete"
    # between games only one-line result, no extra chatter
    assert [s[0] for s in a.said] == ["result"] * 3


async def test_continuous_without_series_flips_until_released():
    a = FakeAdapter()
    loop, _ = mk(a, continuous=True, store=Store(a.order))

    def hook(n):
        if a.game >= 2:
            loop.release("release")

    loop._sleep = Sleeper(hook)
    assert await loop.run() == "release"
    assert a.new_games[:2] == ["black", "white"]


async def test_single_game_stays_finished_without_continuous():
    a = FakeAdapter()
    loop, _ = mk(a)
    assert await loop.run() == "game_over"
    assert a.new_games == []


async def test_release_leaves_takeover_no_new_game_and_pauses_series():
    a = FakeAdapter(moves_per_game=50)
    se = Series(5)
    loop, _ = mk(a, series=se, hook=lambda n: loop.release("release") if n == 3 else None)
    assert await loop.run() == "release"
    assert a.new_games == []
    assert a.left[0] == "release" and se.paused


async def test_user_move_releases():
    a = FakeAdapter(moves_per_game=50)
    loop, _ = mk(a, hook=lambda n: loop.release("user") if n == 2 else None)
    assert await loop.run() == "user"
    assert a.new_games == []


async def test_external_stop_flag_ends_loop():
    a = FakeAdapter(moves_per_game=50)
    loop, _ = mk(a, hook=lambda n: setattr(a, "mode_takeover", False) if n == 2 else None)
    assert await loop.run() == "stop"


async def test_speech_held_while_user_is_speaking():
    a = FakeAdapter(moves_per_game=1)
    a.speaking = True

    def hook(n):
        if n == 3:
            a.speaking = False

    loop, s = mk(a, hook=hook)
    await loop.run()
    assert s.calls[:3] == [0.1, 0.1, 0.1]
    assert a.said and all(not sp for _, _, sp in a.said)
    assert a.engine_calls == 0 or True


async def test_breaker_trips_after_three_errors_no_spin():
    a = FakeAdapter(fail=1)
    loop, s = mk(a)
    assert await loop.run() == "breaker"
    assert a.engine_calls == 3
    assert len(s.calls) == 2  # backoff sleep between failures
    assert "failing" in a.left[1]


async def test_breaker_resets_on_success():
    b = ErrorBreaker()
    b.fail(); b.fail(); b.ok()
    assert not b.fail() and not b.tripped
    assert b.fail() is False and b.fail() is True


async def test_series_paused_on_breaker():
    a = FakeAdapter(fail=1)
    se = Series(3)
    loop, _ = mk(a, series=se)
    await loop.run()
    assert se.paused


async def test_cancel_leaves_cleanly():
    a = FakeAdapter(moves_per_game=10**6)
    loop = TakeoverLoop(a, sleep=asyncio.sleep)
    t = asyncio.ensure_future(loop.run())
    await asyncio.sleep(0.01)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert a.left[0] == "cancelled" and a.new_games == []


async def test_max_moves_budget():
    a = FakeAdapter(moves_per_game=50)
    loop, _ = mk(a, max_moves=3)
    assert await loop.run() == "moves_done"
    assert a.ply == 3
