"""IntentRouter: offline fuzzy pass + guarded LLM tool-call pass (no sockets)."""

import asyncio
import json
import socket
import time

import pytest

from games.chess import commands, router
from games.chess.commentary import Cancelled, OpenRouter, Quota
from games.chess.router import IntentRouter, RouteCtx


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network touched")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("games.chess.commentary.api_key", lambda: None)


def run(coro):
    return asyncio.run(coro)


class FakeLLM:
    """Duck-typed OpenRouter: records calls, replies with canned content."""

    def __init__(self, reply="", available=True, delay=0.0, exc=None):
        self.reply = reply
        self._avail = available
        self.delay = delay
        self.exc = exc
        self.calls = []
        self.cancelled = 0

    def available(self):
        return self._avail

    def cancel(self):
        self.cancelled += 1

    async def complete(self, messages, *, max_tokens=0, json_mode=False):
        self.calls.append((messages, json_mode))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        r = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return r, "fake"


def llm_router(reply, **kw):
    f = FakeLLM(reply, **{k: kw.pop(k) for k in ("available", "delay", "exc") if k in kw})
    return IntentRouter(f, **kw), f


def tool(name, conf=0.95, **args):
    return {"intent": name, "args": args, "confidence": conf}


MISS = "please could you rearrange whatever happens over there"


# ------------------------------------------------------------------ fuzzy
@pytest.mark.parametrize(
    "text,name",
    [
        ("could you make it a bit easier", "level_adjust"),
        ("I'd like to see how I did", "analyse"),
        ("let's keep going for a few more games", "series_start"),
        ("hand it back", "handback"),
        ("go back a couple of moves", "undo"),
        ("show me the last game", "open_game"),
    ],
)
def test_paraphrases_offline(text, name):
    f = FakeLLM()
    r = run(IntentRouter(f).route(text, RouteCtx()))
    assert r and r.intent and r.intent.name == name and r.source == "fuzzy"
    assert f.calls == []


def test_paraphrase_args():
    rt = IntentRouter(None)
    r = run(rt.route("could you make it a bit easier", RouteCtx()))
    assert r.intent.args == {"direction": "down"}
    r = run(rt.route("make it a little harder", RouteCtx()))
    assert r.intent.args == {"direction": "up"}
    r = run(rt.route("go back a couple of moves", RouteCtx()))
    assert r.intent.args["n"] == 2
    r = run(rt.route("show me the last game", RouteCtx()))
    assert r.intent.args == {"ref": "1"}


def test_take_it_from_here_depends_on_takeover():
    rt = IntentRouter(None)
    a = run(rt.route("take it from here", RouteCtx(takeover=True)))
    b = run(rt.route("take it from here", RouteCtx(takeover=False)))
    assert a.intent.name == "handback"
    assert b.intent.name == "takeover"


def test_continue_with_paused_series():
    rt = IntentRouter(None)
    r = run(rt.route("let's keep going with the series", RouteCtx(series="paused")))
    assert r.intent.name == "series_resume"


def test_close_match_of_catalogue_phrase():
    rt = IntentRouter(None)
    r = run(rt.route("pause the serries", RouteCtx(series="running")))
    assert r and r.intent.name == "series_pause" and r.source == "fuzzy"


def test_fuzzy_is_fast():
    rt = IntentRouter(None)
    run(rt.route("pause the serries", RouteCtx()))  # warm the catalogue
    t = time.perf_counter()
    for _ in range(5):
        run(rt.route("pause the serries", RouteCtx()))
    assert (time.perf_counter() - t) / 5 < 0.02  # 5 ms budget + loop overhead


def test_fuzzy_never_returns_destructive():
    rt = IntentRouter(None)
    for t in ("i resign the game", "quit the gaem", "start a new gam please"):
        r = run(rt.route(t, RouteCtx()))
        assert r is None or r.intent is None or r.intent.name not in router.DESTRUCTIVE


# ------------------------------------------------------------------ moves
@pytest.mark.parametrize(
    "text",
    [
        "knight f three",
        "night f free",
        "knight eff three",
        "pawn e four",
        "bishop takes e five",
        "castle kingside",
        "e four",
        "queen to d five please",
    ],
)
def test_moves_never_hijacked(text):
    f = FakeLLM(tool("set_level", n=3))
    r = run(IntentRouter(f).route(text, RouteCtx()))
    assert r is None or (r.intent is None and r.clarify is None)
    assert f.calls == []


def test_move_probe_blocks():
    f = FakeLLM(tool("set_level", n=3))
    rt = IntentRouter(f)
    ctx = RouteCtx(is_move=lambda t: True)
    assert run(rt.route("do the thing now please", ctx)) is None
    assert f.calls == []


# ------------------------------------------------------------------ LLM
def test_no_key_zero_calls_and_sockets():
    rt = IntentRouter(OpenRouter(["m"], quota=Quota(path=None) if False else None))
    assert run(rt.route(MISS, RouteCtx())) is None  # socket guard would raise


def test_unavailable_or_disabled_or_short_no_call():
    f = FakeLLM(tool("set_level", n=3), available=False)
    assert run(IntentRouter(f).route(MISS, RouteCtx())) is None
    g = FakeLLM(tool("set_level", n=3))
    assert run(IntentRouter(g, enabled=False).route(MISS, RouteCtx())) is None
    assert run(IntentRouter(g).route("do thing", RouteCtx())) is None
    assert f.calls == [] and g.calls == []


def test_llm_hit():
    rt, f = llm_router(tool("set_level", n=7))
    r = run(rt.route("I fancy a proper challenge, around seven", RouteCtx()))
    assert r.source == "llm" and r.intent.name == "set_level"
    assert r.intent.args == {"n": 7} and r.clarify is None
    assert len(f.calls) == 1 and f.calls[0][1] is True  # json mode


def test_prompt_has_no_fen_or_keys():
    rt, f = llm_router(tool("none"))
    run(rt.route(MISS, RouteCtx(series="running")))
    blob = json.dumps(f.calls[0][0])
    assert "rnbqkbnr" not in blob and "sk-or" not in blob
    assert "set_level" in blob and MISS in blob


def test_none_and_unknown_and_destructive_rejected():
    for rep in (
        tool("none"),
        tool("quit"),
        tool("resign"),
        tool("new_game"),
        tool("delete_game"),
        tool("rm_rf"),
    ):
        rt, _ = llm_router(rep)
        assert run(rt.route(MISS, RouteCtx())) is None


@pytest.mark.parametrize(
    "bad",
    [
        "not json at all",
        "[]",
        "{",
        '{"intent": 5}',
        {"intent": "set_level"},
        {"intent": "set_level", "args": "x", "confidence": 1},
        {"intent": "set_level", "args": {"n": 7}},  # no confidence
    ],
)
def test_malformed_ignored(bad):
    rt, _ = llm_router(bad)
    assert run(rt.route(MISS, RouteCtx())) is None


@pytest.mark.parametrize(
    "rep",
    [
        tool("set_level", n=0),
        tool("set_level", n=11),
        tool("set_level", n="seven"),
        tool("set_level", n=True),
        tool("set_level", n=3.5),
        tool("level_adjust", direction="sideways"),
        tool("series_start", total=0),
        tool("series_start", total=101),
        tool("series_start", total=3, colour="green"),
        tool("series_start", total=3, level=12),
        tool("hist_step", delta=0),
        tool("hist_step", delta=999),
        tool("hist_goto", move=0),
        tool("hist_goto", move=-4),
        tool("hist_branch", move="e4"),
        tool("open_game", ref="../../etc/passwd"),
        tool("explain", which="number"),
        tool("undo", n=50),
        tool("set_level", conf=0.9, n=5, **{"__proto__": 1}),
    ],
)
def test_out_of_range_rejected(rep):
    if rep["args"].pop("conf", None) is not None:  # unknown extra keys are dropped
        pass
    rt, _ = llm_router(rep)
    r = run(rt.route(MISS, RouteCtx()))
    if rep["intent"] == "set_level" and rep["args"].get("n") == 5:
        assert r.intent.args == {"n": 5}  # extras stripped
    else:
        assert r is None


def test_valid_variants():
    cases = [
        (tool("series_start", total=None, colour="white", level=4),
         "series_start", {"total": None, "colour": "white", "level": 4}),
        (tool("series_start", total=5), "series_start", {"total": 5, "colour": "alternate"}),
        (tool("series_pause"), "series_pause", {}),
        (tool("series_resume"), "series_resume", {}),
        (tool("series_cancel"), "series_cancel", {}),
        (tool("series_left"), "series_left", {}),
        (tool("series_score"), "series_score", {}),
        (tool("takeover"), "takeover", {}),
        (tool("release_control"), "handback", {}),
        (tool("list_games"), "list_games", {}),
        (tool("open_game", ref="3"), "open_game", {"ref": "3"}),
        (tool("open_game", ref="latest"), "open_game", {"ref": "latest"}),
        (tool("hist_step", delta=-2), "hist_step", {"delta": -2}),
        (tool("hist_goto", move=12), "hist_goto", {"n": 12}),
        (tool("hist_branch", move=9), "hist_branch", {"n": 9}),
        (tool("hist_branch"), "hist_branch", {}),
        (tool("undo", n=2), "undo", {"n": 2}),
        (tool("undo"), "undo", {"n": 1}),
        (tool("analyse_board"), "analyse_board", {}),
        (tool("explain", which="number", move=14), "explain", {"which": "number", "n": 14}),
        (tool("difficulty"), "difficulty", {}),
        (tool("level_adjust", direction="up"), "level_adjust", {"direction": "up"}),
    ]
    for rep, name, args in cases:
        rt, _ = llm_router(rep)
        r = run(rt.route(MISS, RouteCtx(series="running", history=True)))
        assert r and r.intent.name == name, rep
        assert r.intent.args == args, rep


def test_json_in_code_fence_and_string_numbers_tolerated():
    rt, _ = llm_router("```json\n" + json.dumps(tool("set_level", n=6)) + "\n```")
    r = run(rt.route(MISS, RouteCtx()))
    assert r.intent.args == {"n": 6}


def test_low_confidence_asks_to_clarify():
    rt, _ = llm_router(tool("set_level", conf=0.4, n=7))
    r = run(rt.route(MISS, RouteCtx()))
    assert r.intent is None and r.source == "llm"
    assert r.clarify == "Did you mean level 7, Sir?"
    assert r.candidate.name == "set_level" and r.candidate.args == {"n": 7}


def test_confidence_boundary():
    rt, _ = llm_router(tool("set_level", conf=0.7, n=7))
    assert run(rt.route(MISS, RouteCtx())).intent.name == "set_level"
    rt, _ = llm_router(tool("takeover", conf=0.69))
    assert run(rt.route(MISS, RouteCtx())).clarify


def test_timeout_never_blocks_past_budget():
    rt, _f = llm_router(tool("set_level", n=3), delay=5, timeout=0.05)
    t = time.perf_counter()
    assert run(rt.route(MISS, RouteCtx())) is None
    assert time.perf_counter() - t < 1.0


def test_cancelled_and_errors_swallowed():
    for exc in (Cancelled(), RuntimeError("boom")):
        rt, _ = llm_router("", exc=exc)
        assert run(rt.route(MISS, RouteCtx())) is None


def test_cancel_forwards_and_aborts_inflight():
    f = FakeLLM(tool("set_level", n=3), delay=5)
    rt = IntentRouter(f, timeout=3)

    async def go():
        t = asyncio.ensure_future(rt.route(MISS, RouteCtx()))
        await asyncio.sleep(0.02)
        rt.cancel()
        return await asyncio.wait_for(t, 1)

    assert run(go()) is None
    assert f.cancelled == 1


def test_rules_hit_means_router_not_needed():
    """The game consults the router only on a classify miss; verify the contract."""
    assert commands.classify("undo last move") is not None
    f = FakeLLM(tool("set_level", n=3))
    # a literal catalogue phrase is still resolved offline, never via LLM
    r = run(IntentRouter(f).route("list my games", RouteCtx()))
    assert f.calls == [] and (r is None or r.source == "fuzzy")


# ------------------------------------------------- real client, fake HTTP
class FakeHTTP:
    def __init__(self, content):
        self.content = content
        self.posts = []

    async def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append(json)

        class R:
            status_code = 200

            def json(_s):  # noqa: N805
                return {"model": "m", "choices": [{"message": {"content": self.content}}]}

        return R()


def test_with_real_openrouter_client(tmp_path):
    http = FakeHTTP(json.dumps(tool("set_level", n=8)))
    client = OpenRouter(["m"], client=http, key="sk-or-test", quota=Quota(path=tmp_path / "q.json"))
    r = run(IntentRouter(client).route(MISS, RouteCtx()))
    assert r.intent.args == {"n": 8}
    body = http.posts[0]
    assert body["response_format"] == {"type": "json_object"}
    assert "sk-or" not in json.dumps(body["messages"])
