"""Regression: the handoff task must reach the specialist.

LiveKit commits a handoff tool's call+output to the OLD agent's chat_ctx,
so a specialist built without context never saw "open spotify" and fell
back to the only concrete app in its instructions (Calculator -> kcalc).
Every "open X" launched KCalc. The task must ride into on_enter itself.
"""

import asyncio
from types import SimpleNamespace

import pytest

from agent import Assistant


class _FakeSession:
    def __init__(self) -> None:
        self.instructions: list[str] = []

    async def generate_reply(self, instructions: str = "", **_: object) -> None:
        self.instructions.append(instructions)

    async def say(self, *_: object, **__: object) -> None:
        pass


def _assistant() -> Assistant:
    # Bypass __init__: only the handoff plumbing is under test here.
    assistant = Assistant.__new__(Assistant)
    assistant._system_agent = None
    assistant._research_agent = None
    return assistant


async def _enter(agent) -> list[str]:  # type: ignore[no-untyped-def]
    session = _FakeSession()
    type(agent).session = property(lambda self: session)  # type: ignore[assignment]
    try:
        await agent.on_enter()
    finally:
        await agent.on_exit()
        del type(agent).session
    return session.instructions


@pytest.mark.asyncio
@pytest.mark.parametrize("task", ["open spotify", "launch konsole", "open steam"])
async def test_system_handoff_carries_task(task: str) -> None:
    assistant = _assistant()
    specialist, _ = await Assistant.transfer_to_system_control(
        assistant,
        None,  # type: ignore[arg-type]
        task=task,
    )
    instructions = await _enter(specialist)
    assert instructions and task in instructions[0]


@pytest.mark.asyncio
async def test_reused_system_specialist_gets_the_new_task() -> None:
    assistant = _assistant()
    first, _ = await Assistant.transfer_to_system_control(
        assistant,
        None,  # type: ignore[arg-type]
        task="open calculator",
    )
    second, _ = await Assistant.transfer_to_system_control(
        assistant,
        None,  # type: ignore[arg-type]
        task="open discord",
    )
    assert first is second
    instructions = await _enter(second)
    assert "open discord" in instructions[0]
    assert "open calculator" not in instructions[0]


@pytest.mark.asyncio
async def test_research_handoff_carries_topic(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import agent as agent_mod

    monkeypatch.setattr(
        agent_mod.BrowserManager,
        "create_research_manager",
        staticmethod(lambda: SimpleNamespace()),
    )
    monkeypatch.setattr(agent_mod, "BrowserTools", lambda _b: SimpleNamespace(tools=[]))
    assistant = _assistant()
    specialist, _ = await Assistant.transfer_to_deep_research(
        assistant,
        None,  # type: ignore[arg-type]
        topic="best budget e-readers",
    )
    instructions = await _enter(specialist)
    assert "best budget e-readers" in instructions[0]


@pytest.mark.asyncio
async def test_system_entry_without_task_does_not_invent_a_launch() -> None:
    from agent import SystemAgent

    specialist = SystemAgent.__new__(SystemAgent)
    specialist.task = ""
    assert await _enter(specialist) == []


@pytest.mark.asyncio
async def test_system_handoff_task_is_consumed_once() -> None:
    assistant = _assistant()
    specialist, _ = await Assistant.transfer_to_system_control(
        assistant, None, task="open calculator"
    )
    first = await _enter(specialist)
    assert "open calculator" in first[0]
    assert specialist.task == ""
    assert await _enter(specialist) == []


@pytest.mark.asyncio
async def test_watchdog_is_armed_while_reply_is_pending_and_cancelled_on_exit(
    monkeypatch,
):
    import agent as agent_mod

    started = asyncio.Event()
    stopped = asyncio.Event()

    async def watchdog(session, entered):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    class SlowSession(_FakeSession):
        async def generate_reply(self, **kwargs):
            # The watchdog must be running even if generation never finishes.
            await asyncio.wait_for(started.wait(), 0.2)

    monkeypatch.setattr(agent_mod, "_specialist_watchdog", watchdog)
    specialist = agent_mod.SystemAgent.__new__(agent_mod.SystemAgent)
    specialist.task = "open spotify"
    session = SlowSession()
    monkeypatch.setattr(agent_mod.SystemAgent, "session", property(lambda _: session))
    try:
        await specialist.on_enter()
        assert started.is_set()
    finally:
        await specialist.on_exit()
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_research_handoff_task_is_consumed_once():
    from agent import ResearchAgent

    specialist = ResearchAgent.__new__(ResearchAgent)
    specialist.task = "compare e-readers"
    assert "compare e-readers" in (await _enter(specialist))[0]
    assert specialist.task == ""
    assert await _enter(specialist) == []
