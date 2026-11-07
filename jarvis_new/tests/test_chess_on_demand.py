"""Nothing happens unless asked, and stop means stop (§2.4 overrides everything)."""

import asyncio
import json
import random
import time

import chess
import httpx
import pytest

from games.chess.chat import Chat
from games.chess.commentary import Commentary, OpenRouter, Quota
from games.chess.engine import Analysis, Eval, MiniEngine
from games.chess.game import ChessGame
from games.chess.settings import ChessSettings


class CountingEngine(MiniEngine):
    def __init__(self):
        super().__init__(5)
        self.analyse_calls = 0

    async def analyse(self, board, depth=14):
        self.analyse_calls += 1
        return await super().analyse(board, depth)


class SlowEngine(MiniEngine):
    """analyse() hangs until released, like a deep Stockfish search."""

    def __init__(self):
        super().__init__(5)
        self.started = asyncio.Event()
        self.cancelled = 0

    async def analyse(self, board, depth=14):
        self.started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return Analysis(Eval(cp=10))


def make_game(tmp_path, engine, handler=None, key="sk-or-test-key-1", **settings):
    router = None
    commentary = chat = None
    if handler is not None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        router = OpenRouter(
            ["m/one:free"], client=client, quota=Quota(tmp_path / "q.json"), key=key
        )
        commentary = Commentary(router)
        chat = Chat(router)
    settings.setdefault("commentary_muted", False)
    s = ChessSettings(difficulty="novice", **settings)
    g = ChessGame(
        s,
        engine=engine,
        commentary=commentary,
        chat=chat,
        save_dir=tmp_path,
        autosave=False,
    )
    g.listening = True
    return g, router


async def _wait_idle(g):
    for _ in range(100):
        await g.drain(5)
        if not (g._reply_task and not g._reply_task.done()):
            return
        await asyncio.sleep(0.01)


async def test_twenty_moves_with_no_requests_stay_silent_and_offline(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={})

    eng = CountingEngine()
    g, router = make_game(tmp_path, eng, handler)
    await g.new_game()
    rng = random.Random(9)
    played = 0
    for _ in range(40):
        if g.state.is_over() or played >= 20:
            break
        if g.state.board.turn != g.state.human_colour:
            await _wait_idle(g)
            continue
        mv = rng.choice(list(g.state.board.legal_moves))
        r = await g.handle_text(mv.uci())
        assert r.ok, (mv.uci(), r.say)
        assert r.say is None  # no acknowledgement unless confirm-moves is on
        played += 1
        await _wait_idle(g)
    assert played >= 10
    assert eng.analyse_calls == 0
    assert router.calls == 0 and calls == []
    assert not g.panel and not g.badges and g.snapshot()["eval"] is None
    # the only speech is Jarvis's own one-line move announcements
    assert g.speech_log and all(
        s.endswith(".") and len(s.split()) <= 8 for s in g.speech_log
    ), g.speech_log
    flags = g.snapshot()["flags"]
    assert not any(flags.values())
    await g.close()


async def test_keep_analysing_then_stop_cancels_everything_under_300ms(tmp_path):
    release = asyncio.Event()
    hits = []

    async def handler(req):
        hits.append(1)
        await release.wait()
        payload = {"headline": "H", "spoken": "Late reply, Sir.", "detail": "d"}
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(payload)}}]}
        )

    eng = SlowEngine()
    g, router = make_game(tmp_path, eng, handler)
    await g.new_game()
    stopped = []
    g.on_stop_speech = lambda: stopped.append(time.monotonic())
    await g.handle_text("e4")
    await _wait_idle(g)
    r = await g.handle_text("keep analysing")
    assert "keep analysing" in r.say
    assert g.snapshot()["analysis_on"] and g.status() in ("ANALYSIS ON", "YOUR MOVE")
    await g.handle_text("commentary on")
    await asyncio.wait_for(eng.started.wait(), 2)

    # fire an explain as well so an HTTP request is in flight
    async def ask():
        content, _ = await router.complete([{"role": "user", "content": "why?"}])
        await g._say(content)

    g._spawn(ask())  # a Cancelled from the stop is swallowed by _spawn
    for _ in range(100):
        if hits:
            break
        await asyncio.sleep(0.01)
    assert hits, "an HTTP request should be in flight"
    spoken_before = len(g.speech_log)
    t0 = time.monotonic()
    r = await g.handle_text("stop")
    await asyncio.sleep(0)  # let cancellations land
    elapsed = time.monotonic() - t0
    assert elapsed < 0.3
    assert stopped and stopped[-1] - t0 < 0.3
    assert eng.cancelled >= 1
    assert not router._inflight
    flags = g.snapshot()["flags"]
    assert not any(flags.values()) and not g.snapshot()["analysis_on"]
    release.set()
    await asyncio.sleep(0.1)
    assert len(g.speech_log) == spoken_before  # the late reply is never spoken
    assert "Late reply" not in " ".join(g.speech_log)
    await g.close()


async def test_stop_never_restarts_anything(tmp_path):
    eng = CountingEngine()
    g, router = make_game(tmp_path, eng, lambda r: httpx.Response(500))
    await g.new_game()
    await g.handle_text("keep analysing")
    await g.handle_text("stop")
    n = eng.analyse_calls
    await g.handle_text("e4")
    await _wait_idle(g)
    await asyncio.sleep(0.1)
    assert eng.analyse_calls == n
    assert router.calls == 0
    await g.close()


async def test_new_game_starts_passive(tmp_path):
    g, _ = make_game(tmp_path, CountingEngine(), lambda r: httpx.Response(500))
    await g.new_game()
    for phrase in (
        "keep analysing",
        "commentary on",
        "keep the eval on",
        "hints as we go",
    ):
        await g.handle_text(phrase)
    assert all(
        g.snapshot()["flags"][k] for k in ("analysis", "commentary", "eval", "hints")
    )
    await g.new_game()
    assert not any(g.snapshot()["flags"].values())
    assert g.snapshot()["eval"] is None
    await g.close()


async def test_remember_analysis_settings_survive_a_new_game(tmp_path):
    g, _ = make_game(tmp_path, CountingEngine(), lambda r: httpx.Response(500))
    await g.new_game()
    await g.handle_text("keep the eval on")
    await g.handle_text("remember my analysis settings")
    await g.new_game()
    assert g.snapshot()["flags"]["eval"]
    await g.close()


async def test_narrow_stop_only_stops_its_own_thing(tmp_path):
    g, _ = make_game(tmp_path, CountingEngine(), lambda r: httpx.Response(500))
    await g.new_game()
    await g.handle_text("keep the eval on")
    await g.handle_text("hints as we go")
    await g.handle_text("stop the hints")
    f = g.snapshot()["flags"]
    assert f["eval"] and not f["hints"]
    await g.handle_text("hide the eval")
    assert not g.snapshot()["flags"]["eval"]
    await g.close()


async def test_stop_is_silent_when_the_setting_says_so(tmp_path):
    g, _ = make_game(
        tmp_path, CountingEngine(), lambda r: httpx.Response(500), stop_ack="silent"
    )
    await g.new_game()
    await g.handle_text("keep analysing")
    r = await g.handle_text("stop")
    assert r.say is None
    g2, _ = make_game(tmp_path, CountingEngine(), lambda r: httpx.Response(500))
    await g2.new_game()
    await g2.handle_text("keep analysing")
    assert (await g2.handle_text("stop")).say == "Very good, Sir."
    await g.close()
    await g2.close()


async def test_one_shot_requests_speak_once_then_go_quiet(tmp_path):
    eng = CountingEngine()
    g, _router = make_game(tmp_path, eng, lambda r: httpx.Response(500))
    await g.new_game()
    await g.handle_text("e4")
    await _wait_idle(g)
    before = len(g.speech_log)
    await g.handle_text("analyse")
    await g.drain()
    assert len(g.speech_log) == before + 1
    assert g.snapshot()["eval"] is not None
    n = eng.analyse_calls
    await g.handle_text("d4") if g.state.board.turn == chess.WHITE else None
    await _wait_idle(g)
    await asyncio.sleep(0.05)
    assert eng.analyse_calls == n  # no continuous mode was left running
    assert g.snapshot()["eval"] is None  # the ring blanks again
    await g.close()


async def test_explain_uses_llm_only_when_asked(tmp_path):
    hits = []

    def handler(req):
        hits.append(1)
        payload = {
            "headline": "Solid",
            "spoken": "A sound central pawn, Sir.",
            "detail": "e4 takes the centre.",
        }
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(payload)}}]}
        )

    g, _router = make_game(tmp_path, CountingEngine(), handler)
    await g.new_game()
    await g.handle_text("e4")
    await _wait_idle(g)
    assert not hits
    await g.handle_text("explain my move")
    await g.drain(15)
    assert len(hits) == 1
    assert g.panel and g.panel["source"] in ("llm", "template")
    await g.close()


async def test_hint_levels_escalate_and_are_one_shot(tmp_path):
    g, _ = make_game(tmp_path, CountingEngine(), lambda r: httpx.Response(500))
    await g.new_game()
    levels = []
    for _ in range(3):
        await g.handle_text("hint")
        await g.drain()
        levels.append(g.hint["level"])
    assert levels == [1, 2, 3]
    assert g.hint["to"]
    await g.close()


async def test_offline_chat_has_the_canned_line(tmp_path):
    g, _ = make_game(tmp_path, CountingEngine(), None)
    await g.new_game()
    import os

    old = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        r = await g.handle_text("why is my bishop bad here")
        if g.chat.online():
            pytest.skip("real key present")
        assert (
            r.say
            == "I'm afraid I can't discuss that offline, Sir. Moves and hints still work."
        )
    finally:
        if old:
            os.environ["OPENROUTER_API_KEY"] = old
    await g.close()


async def test_unrecognised_input_is_one_short_line_then_the_legend(tmp_path):
    g, _ = make_game(tmp_path, CountingEngine(), None)
    await g.new_game()
    r = await g.handle_text("blah")
    assert (
        r.say
        == "Sorry, Sir, I didn't catch a move. Try something like 'knight to f three'."
    )
    await g.handle_text("blah blah")
    r = await g.handle_text("blee")
    assert "alpha" in r.say and "hotel" in r.say
    r = await g.handle_text("bloo")
    assert r.say.startswith("Sorry, Sir")  # the legend is offered only once
    await g.close()
