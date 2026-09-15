import pytest
from livekit.agents import inference, llm

from agent import Assistant, ResearchAgent, SystemAgent
from prompts import AGENT_INSTRUCTIONS, RESEARCH_INSTRUCTIONS, SYSTEM_INSTRUCTIONS


def _agent_llm() -> llm.LLM:
    return inference.LLM(model="openai/gpt-4.1-mini")


def test_specialist_instructions_exist() -> None:
    assert "ISOLATED browser" in RESEARCH_INSTRUCTIONS
    assert "THIS Linux laptop" in SYSTEM_INSTRUCTIONS
    assert "shutdown and reboot" in SYSTEM_INSTRUCTIONS


def test_router_registers_handoff_tools() -> None:
    assistant = Assistant(llm=_agent_llm())
    ids = [tool.id for tool in assistant.tools]
    for expected in (
        "transfer_to_deep_research",
        "transfer_to_system_control",
        "transfer_back_to_main",
    ):
        assert expected in ids


def test_router_instructions_scope_narrow_handoff() -> None:
    assert "transfer_to_system_control, and only for" in AGENT_INSTRUCTIONS
    assert "shutdown/reboot" in AGENT_INSTRUCTIONS


def test_specialists_own_scoped_tools() -> None:
    research_ids = [tool.id for tool in ResearchAgent(llm=_agent_llm()).tools]
    assert "open_url" in research_ids
    assert "power_control" not in research_ids

    system_ids = [tool.id for tool in SystemAgent(llm=_agent_llm()).tools]
    assert "power_control" in system_ids
    assert "confirm_power_action" in system_ids
    assert "open_url" not in system_ids


@pytest.mark.asyncio
async def test_handoff_tools_return_specialist_agents() -> None:
    assistant = Assistant(llm=_agent_llm())
    research_agent, _ = await Assistant.transfer_to_deep_research(
        assistant,
        None,
        topic="compare laptops",  # type: ignore[arg-type]
    )
    assert isinstance(research_agent, ResearchAgent)

    system_agent, _ = await Assistant.transfer_to_system_control(
        assistant,
        None,
        task="shut down the laptop",  # type: ignore[arg-type]
    )
    assert isinstance(system_agent, SystemAgent)
    # Narrow handoff: dangerous tools in, common tools out.
    narrow_ids = [tool.id for tool in system_agent.tools]
    assert "power_control" in narrow_ids
    assert "tell_time" not in narrow_ids

    try:
        await research_agent.aclose()
    finally:
        pass
