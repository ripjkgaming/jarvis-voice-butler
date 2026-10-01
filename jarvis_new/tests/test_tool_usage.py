import asyncio
import time

import pytest

import tool_usage


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_ADAPTIVE_TOOLS", raising=False)
    tool_usage.reset()
    yield
    tool_usage.reset()


def _seed(**counts):
    now = time.time()
    for tool, n in counts.items():
        for _ in range(n):
            tool_usage.record(tool, now)


def test_nothing_demoted_without_history():
    _seed(a=3)
    assert tool_usage.plan_cold(["a", "b", "c"]) == set()


def test_unused_tools_go_cold_once_history_exists():
    _seed(a=70, b=1)  # one recent call keeps b on the router
    assert tool_usage.plan_cold(["a", "b", "c", "d"]) == {"c", "d"}
    assert tool_usage.plan_cold(["a", "b", "c", "d"], protected=["c"]) == {"d"}


def test_usage_decays_so_stale_tools_go_cold():
    old = time.time() - 60 * 86400
    for _ in range(80):
        tool_usage.record("a", time.time())
    for _ in range(10):
        tool_usage.record("b", old)
    assert "b" in tool_usage.plan_cold(["a", "b"])


def test_promote_and_kill_switch(monkeypatch):
    _seed(open_app=5)
    assert tool_usage.promote(["open_app", "media_control"]) == {"open_app"}
    monkeypatch.setenv("JARVIS_ADAPTIVE_TOOLS", "0")
    assert tool_usage.promote(["open_app"]) == set()
    assert tool_usage.plan_cold(["x"]) == set()


def test_persists_across_restart():
    _seed(a=4)
    tool_usage.flush()
    tool_usage.reset()
    assert tool_usage.score("a") > 3.5


def test_assistant_router_shrinks_and_handoff_reaches_cold_tools(monkeypatch):
    for k in ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.setenv(k, "x" * 32)
    monkeypatch.setenv("LIVEKIT_URL", "ws://localhost:7880")
    from agent import Assistant
    from livekit.agents import inference

    def mk():
        return Assistant(llm=inference.LLM(model="openai/gpt-4.1-mini"))

    base = mk()
    base_ids = {t.id for t in base.tools}
    assert "transfer_to_more_tools" not in base_ids  # no history -> unchanged
    hot = {"end_call"} & base_ids
    always = {t.id for t in (*base.browser_tools.tools, *base.notify_tools.tools)}
    cands = [
        i
        for i in sorted(base_ids)
        if i not in hot
        and i not in always
        and "confirm" not in i
        and not i.startswith("transfer_")
    ]
    drop = cands[0]
    dropped = set(cands[:10])
    tool_usage.reset()
    now = time.time()
    for i in base_ids - dropped:
        for _ in range(3):
            tool_usage.record(i, now)
    a = mk()
    ids = {t.id for t in a.tools}
    assert not dropped & ids
    assert "transfer_to_more_tools" in ids
    assert len(ids) < len(base_ids)
    assert drop in {t.id for t in a._cold_extras}
    tool = next(t for t in a.tools if t.id == "transfer_to_more_tools")
    assert drop in tool.info.description
    agent, _msg = asyncio.run(tool._func(None, task="do the thing"))
    assert drop in {t.id for t in agent.tools}
    assert agent.task == "do the thing"
