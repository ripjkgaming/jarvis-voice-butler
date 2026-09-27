"""Regression: the handoff task must reach the specialist.

LiveKit commits a handoff tool's call+output to the OLD agent's chat_ctx,
so a specialist built without context never saw "open spotify" and fell
back to the only concrete app in its instructions (Calculator -> kcalc).
Every "open X" launched KCalc. The task must ride into on_enter itself.
"""

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
