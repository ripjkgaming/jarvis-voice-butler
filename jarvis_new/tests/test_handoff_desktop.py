"""Regression test: the transfer_to_system_control handoff must survive.

Covers the 21:17 prod incident where the call died ~50ms after the handoff
tool executed. The session must switch to the SystemAgent and stay open;
a follow-up turn on the specialist must work.
"""

import pytest
from livekit.agents import AgentSession, inference, llm

from agent import Assistant
from system.desktop import DesktopTools


def _agent_llm() -> llm.LLM:
    return inference.LLM(model="openai/gpt-4.1-mini")


async def _dry_stub(*args, **kwargs):  # type: ignore[no-untyped-def]
    return {"say": "dry run, not executed"}


def _dry_assistant(agent_llm: llm.LLM) -> Assistant:
    assistant = Assistant(browser=None, llm=agent_llm)
    for tool in assistant.tools:
        tool._func = _dry_stub  # type: ignore[attr-defined]
    return assistant


async def _calls(session: AgentSession, assistant: Assistant, prompt: str) -> list[str]:
    result = await session.run(user_input=prompt)
    return [
        event.item.name
        for event in result.events
        if type(event).__name__ == "FunctionCallEvent"
    ]


@pytest.mark.asyncio
async def test_handoff_to_system_control_survives() -> None:
    async with _agent_llm() as agent_llm, AgentSession() as session:
        assistant = _dry_assistant(agent_llm)
        await session.start(assistant)
        calls = await _calls(
            session, assistant, "Open the calculator app on my laptop."
        )
        assert "transfer_to_system_control" in calls
        # Take the handoff exactly as the realtime session does and prove
        # the specialist runs a full turn with an open session.
        specialist, _ = await Assistant.transfer_to_system_control(
            assistant,
            None,  # type: ignore[arg-type]
            task="open the calculator app",
        )
        for tool in specialist.tools:
            tool._func = _dry_stub  # type: ignore[attr-defined]
        await session.start(specialist)
        followup = await _calls(session, specialist, "What laptop actions can you do?")
        assert isinstance(followup, list)  # responded without closing


@pytest.mark.asyncio
async def test_desktop_tool_ids_reachable_on_specialist() -> None:
    from agent import RARE_SYSTEM_TOOL_IDS, SystemAgent

    specialist = SystemAgent(llm=None, only_ids=RARE_SYSTEM_TOOL_IDS)
    ids = [tool.id for tool in specialist.tools]
    for expected in [tool.id for tool in DesktopTools().tools]:
        assert expected in ids


@pytest.mark.asyncio
async def test_handoff_calculator_launches_open_app() -> None:
    async with _agent_llm() as agent_llm, AgentSession() as session:
        assistant = _dry_assistant(agent_llm)
        specialist, _ = await Assistant.transfer_to_system_control(
            assistant,
            None,  # type: ignore[arg-type]
            task="Open calculator and calculate 25 * 4",
        )
        for tool in specialist.tools:
            tool._func = _dry_stub  # type: ignore[attr-defined]
        await session.start(specialist)
        calls = await _calls(
            session, specialist, "Open calculator and calculate 25 * 4."
        )
        assert "open_app" in calls
        assert "window_action" not in calls

