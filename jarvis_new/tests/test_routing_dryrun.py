"""Dry-run routing tests: which tool the model picks, never executing it.

Every tool's implementation is stubbed after Assistant construction, so
schemas and descriptions stay intact (the model still sees the real
surface) while NO action can run: no files, no browser I/O, no settings,
no power. Assertions read FunctionCallEvent names only.
"""

import pytest
from _free_llm import free_eval_llm, patient_run, throttle
from livekit.agents import AgentSession, llm

from agent import Assistant


def _agent_llm() -> llm.LLM:
    # Free routing judge (AI Studio key, $0). inference.LLM bills Cloud
    # credits per call; the fallback chain spreads load across per-model
    # free quotas just like production.
    return free_eval_llm()


async def _dry_stub(*args, **kwargs):  # type: ignore[no-untyped-def]
    return {"say": "dry run, not executed"}


def _dry_assistant(agent_llm: llm.LLM) -> Assistant:
    assistant = Assistant(browser=None, llm=agent_llm)
    for tool in assistant.tools:
        tool._func = _dry_stub  # type: ignore[attr-defined]
    return assistant


async def _calls(session: AgentSession, assistant: Assistant, prompt: str) -> list[str]:
    # Pace every prompt (not just test starts): multi-prompt tests burst
    # through the ~10 RPM combined free quota otherwise.
    await throttle()
    result = await patient_run(session.run, user_input=prompt)
    return [
        event.item.name
        for event in result.events
        if type(event).__name__ == "FunctionCallEvent"
    ]


@pytest.mark.asyncio
async def test_routing_shutdown_uses_narrow_handoff() -> None:
    await throttle()
    async with _agent_llm() as agent_llm, AgentSession() as session:
        assistant = _dry_assistant(agent_llm)
        await session.start(assistant)
        calls = await _calls(session, assistant, "Shut down the laptop now.")
        assert "transfer_to_system_control" in calls
        assert "power_control" not in calls


@pytest.mark.asyncio
async def test_routing_open_app_uses_narrow_handoff() -> None:
    await throttle()
    async with _agent_llm() as agent_llm, AgentSession() as session:
        assistant = _dry_assistant(agent_llm)
        await session.start(assistant)
        calls = await _calls(session, assistant, "Open the Files app on my laptop.")
        assert "transfer_to_system_control" in calls
        assert "open_app" not in calls


@pytest.mark.asyncio
async def test_routing_gmail_never_touches_browser() -> None:
    await throttle()
    async with _agent_llm() as agent_llm, AgentSession() as session:
        assistant = _dry_assistant(agent_llm)
        await session.start(assistant)
        calls = await _calls(session, assistant, "Check my gmail inbox please.")
        assert "gmail_inbox" in calls
        assert "open_url" not in calls


@pytest.mark.asyncio
async def test_routing_common_tools_stay_direct() -> None:
    await throttle()
    async with _agent_llm() as agent_llm, AgentSession() as session:
        assistant = _dry_assistant(agent_llm)
        await session.start(assistant)
        assert await _calls(session, assistant, "What time is it?") == ["tell_time"]
        assert "transfer_to_system_control" not in await _calls(
            session, assistant, "What's the weather in London?"
        )
        volume_calls = await _calls(session, assistant, "Turn the volume down a bit.")
        assert "set_volume" in volume_calls
        assert "transfer_to_system_control" not in volume_calls


@pytest.mark.asyncio
async def test_routing_search_uses_search_tool() -> None:
    await throttle()
    async with _agent_llm() as agent_llm, AgentSession() as session:
        assistant = _dry_assistant(agent_llm)
        await session.start(assistant)
        calls = await _calls(
            session, assistant, "Search the web for the capital of France."
        )
        assert "search_the_web" in calls
