"""OpenRouter commentary with a mocked transport: validation, fallback, quota, cache."""

import json

import chess
import httpx
import pytest

from games.chess import commentary as cm
from games.chess import templates
from games.chess.chat import Chat

FEN = chess.STARTING_FEN
MODELS = ["m/one:free", "m/two:free", "openrouter/free"]


def facts(move="e2e4", best="e2e4", label="Best"):
    b = chess.Board(FEN)
    mv = chess.Move.from_uci(move)
    bm = chess.Move.from_uci(best)
    return cm.build_facts(
        fen_before=FEN,
        move=mv,
        eval_before={"cp": 20, "mate": None},
        eval_after={"cp": 25, "mate": None},
        cp_loss=0,
        classification=label,
        best=bm,
        pv=[bm, chess.Move.from_uci("e7e5"), chess.Move.from_uci("g1f3")],
        preset="club",
        phase="opening",
        material={"white": 39, "black": 39},
    ) | {"_b": b.fen()}


GOOD = {
    "headline": "A principled opening",
    "spoken": "Pawn to e four claims the centre, Sir. Quite the classic.",
    "detail": "e4 stakes a claim in the centre and frees the bishop and queen. Nf3 would follow.",
}


def make(handler, tmp_path, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    quota = cm.Quota(tmp_path / "q.json")
    router = cm.OpenRouter(
        MODELS, client=client, quota=quota, key="sk-or-test-key-1234", sleep=_nosleep
    )
    return cm.Commentary(router, **kw), router, quota


async def _nosleep(_s):
    return None


def ok_response(payload, model="m/one:free"):
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": {"content": json.dumps(payload)}}],
        },
    )


async def test_valid_reply_is_accepted_and_headers_are_right(tmp_path):
    seen = {}

    def handler(req: httpx.Request):
        seen["h"] = req.headers
        seen["body"] = json.loads(req.content)
        return ok_response(GOOD)

    c, router, _ = make(handler, tmp_path)
    exp = await c.explain(facts())
    assert (
        exp.source == "llm"
        and exp.headline == GOOD["headline"]
        and exp.model == "m/one:free"
    )
    assert seen["h"]["authorization"] == "Bearer sk-or-test-key-1234"
    assert (
        seen["h"]["http-referer"]
        == "https://github.com/ripjkgaming/jarvis-voice-butler"
    )
    assert seen["h"]["x-title"] == "Jarvis Chess"
    b = seen["body"]
    assert b["max_tokens"] == 220 and b["temperature"] == 0.4 and b["models"] == MODELS
    assert "Use ONLY the engine facts" in b["messages"][0]["content"]
    assert "club player" in b["messages"][0]["content"]
    assert router.calls == 1


async def test_invented_move_is_rejected_for_the_template(tmp_path):
    bad = dict(GOOD, detail="Qxf7 is crushing here, Sir.")
    c, _, _ = make(lambda r: ok_response(bad), tmp_path)
    exp = await c.explain(facts())
    assert exp.source == "template"


async def test_invented_square_is_rejected(tmp_path):
    bad = dict(GOOD, spoken="The knight lands on h six, Sir.")
    c, _, _ = make(lambda r: ok_response(bad), tmp_path)
    assert (await c.explain(facts())).source == "template"


async def test_moves_from_the_facts_and_legal_moves_are_fine(tmp_path):
    good = dict(GOOD, detail="Nf3 and d4 are natural follow-ups to e4.")
    c, _, _ = make(lambda r: ok_response(good), tmp_path)
    assert (await c.explain(facts())).source == "llm"


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        "{}",
        '{"headline": "x"}',
        json.dumps({"headline": "", "spoken": "", "detail": ""}),
    ],
)
async def test_malformed_json_falls_back(content, tmp_path):
    def handler(r):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    c, _, _ = make(handler, tmp_path)
    assert (await c.explain(facts())).source == "template"


async def test_code_fenced_json_is_accepted(tmp_path):
    def handler(r):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "```json\n" + json.dumps(GOOD) + "\n```"}}
                ]
            },
        )

    c, _, _ = make(handler, tmp_path)
    assert (await c.explain(facts())).source == "llm"


async def test_429_moves_to_the_next_model_with_backoff_not_a_loop(tmp_path):
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append(body["model"])
        if body["model"] != "m/two:free":
            return httpx.Response(429, json={"error": "slow down"})
        return ok_response(GOOD, "m/two:free")

    c, router, _ = make(handler, tmp_path)
    exp = await c.explain(facts())
    assert exp.source == "llm" and exp.model == "m/two:free"
    assert calls == ["m/one:free", "m/two:free"]
    assert router.calls == 2


async def test_500_then_all_fail_gives_template_after_one_pass(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(500, text="boom")

    c, _, _ = make(handler, tmp_path)
    assert (await c.explain(facts())).source == "template"
    assert len(calls) == len(MODELS)  # one pass through the list, never a loop


async def test_timeout_falls_back(tmp_path):
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    c, _, _ = make(handler, tmp_path)
    assert (await c.explain(facts())).source == "template"


async def test_cache_hit_makes_no_second_request(tmp_path):
    n = []

    def handler(req):
        n.append(1)
        return ok_response(GOOD)

    c, _, _ = make(handler, tmp_path)
    a = await c.explain(facts())
    b = await c.explain(facts())
    assert a is b and len(n) == 1


async def test_quota_switches_to_templates_with_one_notice(tmp_path):
    n = []

    def handler(req):
        n.append(1)
        return ok_response(GOOD)

    c, _router, quota = make(handler, tmp_path)
    quota.count = cm.PER_DAY
    a = await c.explain(facts())
    assert a.source == "template" and not n
    assert c.last_notice and "quota" in c.last_notice
    c.last_notice = None
    await c.explain(facts("d2d4", "d2d4"))
    assert c.last_notice is None  # a single notice only


async def test_per_minute_limit():
    q = cm.Quota(
        path=None.__class__ and __import__("pathlib").Path("/tmp/_q_test.json"),
        now=lambda: 100.0,
    )
    q.count = 0
    for _ in range(cm.PER_MINUTE):
        assert q.allow()
        q.record()
    assert not q.allow()
    assert q.left_today() == cm.PER_DAY - cm.PER_MINUTE


def test_quota_persists_and_rolls_over(tmp_path):
    p = tmp_path / "q.json"
    q = cm.Quota(p, today=lambda: "2026-10-01")
    q.record()
    q.record()
    assert cm.Quota(p, today=lambda: "2026-10-01").count == 2
    assert cm.Quota(p, today=lambda: "2026-10-02").count == 0


async def test_no_key_means_templates_and_no_http(tmp_path):
    n = []

    def handler(req):
        n.append(1)
        return ok_response(GOOD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    router = cm.OpenRouter(
        MODELS, client=client, quota=cm.Quota(tmp_path / "q.json"), key=None
    )
    import os

    old = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        router._key = None
        c = cm.Commentary(router)
        if router.key:  # a real key on this machine: skip the env-dependent part
            pytest.skip("real key present")
        assert (await c.explain(facts())).source == "template"
        assert not n
    finally:
        if old:
            os.environ["OPENROUTER_API_KEY"] = old


async def test_cancel_discards_late_reply(tmp_path):
    import asyncio

    release = asyncio.Event()

    async def slow(req):
        await release.wait()
        return ok_response(GOOD)

    c, router, _ = make(slow, tmp_path)
    task = asyncio.ensure_future(c.explain(facts()))
    await asyncio.sleep(0.05)
    assert router._inflight
    c.cancel()
    with pytest.raises(cm.Cancelled):
        await task
    release.set()
    assert not router._inflight


def test_classification_thresholds():
    assert cm.classify(0) == "Best"
    assert cm.classify(30) == "Good" and cm.classify(50) == "Good"
    assert cm.classify(51) == "Inaccuracy" and cm.classify(100) == "Inaccuracy"
    assert cm.classify(101) == "Mistake" and cm.classify(250) == "Mistake"
    assert cm.classify(251) == "Blunder"
    assert cm.classify(10, mate_missed_or_allowed=True) == "Blunder"


def test_redaction():
    assert "sk-or-abcdef…" in cm.redact("key sk-or-abcdef123456789")
    assert "123456789" not in cm.redact("key sk-or-abcdef123456789")


def test_template_explanation_uses_only_facts():
    t = templates.template_explanation(facts("g1f3", "e2e4", "Good"))
    assert "Nf3" in t["spoken"] and "e4" in t["spoken"].replace("e4", "e4")
    assert t["source"] == "template"


def test_validate_requires_grounding():
    assert cm.validate(GOOD, facts()) is not None
    assert (
        cm.validate(
            {"headline": "a", "spoken": "Play h five, Sir.", "detail": "ok"}, facts()
        )
        is None
    )
    assert cm.validate("nope", facts()) is None


async def test_chat_offline_when_no_key_then_online_reply(tmp_path):
    def handler(req):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Because it is cramped, Sir."}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    router = cm.OpenRouter(
        MODELS, client=client, quota=cm.Quota(tmp_path / "q.json"), key=None
    )
    chat = Chat(router)
    router._key = None
    import os

    if not os.environ.get("OPENROUTER_API_KEY") and not router.key:
        assert await chat.ask("why?", fen=FEN, last_moves=[]) == templates.OFFLINE_CHAT
    router._key = "sk-or-test"
    assert (
        await chat.ask("why is my bishop bad", fen=FEN, last_moves=["e4"])
    ).startswith("Because")
