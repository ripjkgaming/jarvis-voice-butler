"""Always Aware voice wiring: the awareness tool, what_was_i_doing(at=), agent
fast route, prompt bullet and the bridge status word."""

import time

import pytest

from aware import spans
from aware import state as st


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.delenv("JARVIS_AWARE", raising=False)
    return tmp_path


@pytest.mark.asyncio
async def test_awareness_tool_toggles_and_rejects_unknown():
    from jarvis_rt import ToolError
    from system.aware_tools import AwareTools

    tools = AwareTools()
    out = await AwareTools.awareness(tools, None, action="on")  # type: ignore[arg-type]
    assert "on" in out["say"] and st.load()["enabled"] is True
    out = await AwareTools.awareness(tools, None, action="pause", arg="for 20 minutes")  # type: ignore[arg-type]
    assert out["say"].startswith("Paused until")
    with pytest.raises(ToolError):
        await AwareTools.awareness(tools, None, action="format_disk")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_awareness_tool_refuses_guest(monkeypatch):
    import phone_session
    from jarvis_rt import ToolError
    from system.aware_tools import AwareTools

    monkeypatch.setattr(phone_session, "guest_required", lambda: True)
    with pytest.raises(ToolError):
        await AwareTools.awareness(AwareTools(), None, action="on")  # type: ignore[arg-type]
    assert st.load()["enabled"] is False


@pytest.mark.asyncio
async def test_what_was_i_doing_at_reads_spans():
    from system.memory_tools import MemoryTools

    spans.update(
        {"outcome": "seen", "subject": "physics essay", "summary": "Writing the intro"},
        time.time() - 1800,
    )
    out = await MemoryTools.what_was_i_doing(MemoryTools(), None, at="half an hour ago")  # type: ignore[arg-type]
    assert "physics essay" in out["say"]
    none = await MemoryTools.what_was_i_doing(MemoryTools(), None, at="9 hours ago")  # type: ignore[arg-type]
    assert "no notes" in none["say"]


def test_agent_exposes_one_awareness_tool(monkeypatch):
    from types import SimpleNamespace

    import dotenv

    import memory
    import phone_session
    import watch_mode

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(memory, "persona_block", lambda: "")
    monkeypatch.setattr(watch_mode, "guest_required", lambda: False)
    monkeypatch.setattr(phone_session, "guest_required", lambda: False)
    import agent
    from jarvis_rt import tool_schema

    monkeypatch.setattr(agent, "_default_agent_llm", lambda: None)
    assistant = agent.Assistant(browser=SimpleNamespace())
    try:
        ids = [t.id for t in assistant.tools]
        assert ids.count("awareness") == 1
        assert not [i for i in ids if i.startswith("aware") and i != "awareness"]
        tool = next(t for t in assistant.tools if t.id == "awareness")
        assert set(tool_schema(tool)["parameters"]["properties"]) == {"action", "arg"}
        assert hasattr(assistant, "_aware_fast_route")
    finally:
        assistant.librarian_service.close()


@pytest.mark.asyncio
async def test_agent_fast_route_answers_without_model(monkeypatch):
    import agent
    import phone_session

    said = []

    class Sess:
        async def say(self, text, add_to_chat_ctx=True):
            said.append(text)

    monkeypatch.setattr(phone_session, "guest_required", lambda: False)
    fake = type("A", (), {"session": Sess()})()
    hit = await agent.Assistant._aware_fast_route(fake, "Jarvis, always aware on")
    assert hit and st.load()["enabled"] is True and "Always Aware is on" in said[0]
    miss = await agent.Assistant._aware_fast_route(fake, "what's the weather")
    assert miss is False
    monkeypatch.setattr(phone_session, "guest_required", lambda: True)
    assert await agent.Assistant._aware_fast_route(fake, "always aware off") is False
    assert st.load()["enabled"] is True


def test_prompt_routes_awareness():
    import prompts

    with open(prompts.__file__) as fh:
        text = fh.read()
    assert "= awareness:" in text and "what_was_i_doing with at" in text


def test_bridge_status_word(_home):
    import bridge

    assert bridge._aware_word() == "off"
    st.update(enabled=True)
    assert bridge._aware_word() == "on"
