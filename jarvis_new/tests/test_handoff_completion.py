"""Offline regression for action + return in a silent realtime generation."""

import asyncio
from types import SimpleNamespace

import pytest

from handoff_recovery import begin_specialist, install, remember, resume_return
from jarvis_rt import (
    Agent,
    AgentSession,
    FunctionCall,
    FunctionCallOutput,
    FunctionToolsExecutedEvent,
    RunContext,
)


def scenario(*, error=False, interrupted=False, reported=False, mixed=True):
    session = AgentSession()
    router = Agent(instructions="router")
    source = Agent(instructions="system specialist")
    session.update_agent(source)
    install(session)
    begin_specialist(source)
    action = FunctionCall(
        name="open_app", call_id="action", arguments='{"app":"calculator"}'
    )
    result = FunctionCallOutput(
        name="open_app",
        call_id="action",
        output="blocked by policy" if error else "{'say': 'Opening calculator.'}",
        is_error=error,
    )
    source._chat_ctx.insert([action, result])
    if reported:
        source._chat_ctx.add_message(role="assistant", content="Opening calculator.")
    call = FunctionCall(name="transfer_back_to_main", call_id="return", arguments="{}")
    output = FunctionCallOutput(
        name=call.name,
        call_id=call.call_id,
        output="Returning to Jarvis.",
        is_error=False,
    )
    source._chat_ctx.insert([call, output])
    speech = SimpleNamespace(num_steps=1, interrupted=interrupted)
    context = RunContext(session=session, speech_handle=speech, function_call=call)
    remember(context, (router, "Returning to Jarvis."))
    event = FunctionToolsExecutedEvent(
        function_calls=[action, call] if mixed else [call],
        function_call_outputs=[result, output] if mixed else [output],
    )
    session.emit("function_tools_executed", event)
    session.update_agent(router)
    replies = []

    async def generate_reply(**kwargs):
        # A fake provider emits audio only for an explicitly owned generation.
        # It cannot execute the completed action again on this continuation.
        assert kwargs["tool_choice"] == "none"
        replies.append(kwargs)

    session.generate_reply = generate_reply
    return router, source, speech, replies, event


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [True, False])
async def test_successful_action_has_one_owned_spoken_completion(mixed):
    from agent import Assistant

    router, _, _, replies, event = scenario(mixed=mixed)
    assert not any(out.reply_required for out in event.function_call_outputs)
    await Assistant.on_enter(router)
    await Assistant.on_enter(router)
    assert len(replies) == 1
    assert "Opening calculator." in replies[0]["instructions"]
    assert "Do not execute or retry" in replies[0]["instructions"]


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before", "after"])
async def test_interruption_abandons_return_without_repeating_action(when):
    router, _, speech, replies, _ = scenario(interrupted=when == "before")
    speech.interrupted = True
    await resume_return(router)
    assert replies == []
    assert getattr(router, "_jarvis_return_completion", None) is None


@pytest.mark.asyncio
async def test_already_reported_result_is_not_spoken_twice():
    router, _, _, replies, _ = scenario(reported=True, mixed=False)
    await resume_return(router)
    assert replies == []


@pytest.mark.asyncio
async def test_failure_is_reported_without_claiming_success():
    router, _, _, replies, _ = scenario(error=True)
    await resume_return(router)
    assert "blocked by policy" in replies[0]["instructions"]
    assert "Opening calculator" not in replies[0]["instructions"]


@pytest.mark.asyncio
async def test_changed_owner_discards_stale_completion():
    router, _, _, replies, _ = scenario()
    router.session._agent = Agent(instructions="new task")
    await resume_return(router)
    assert replies == []


@pytest.mark.asyncio
async def test_reused_specialist_baseline_excludes_previous_action():
    _, source, _, _, _ = scenario()
    begin_specialist(source)
    assert all(
        item.id in source._jarvis_result_baseline for item in source.chat_ctx.items
    )


@pytest.mark.asyncio
async def test_cancelled_generation_is_consumed_without_retry():

    router, _, _, replies, _ = scenario()

    async def cancelled(**kwargs):
        raise asyncio.CancelledError

    router.session.generate_reply = cancelled
    with pytest.raises(asyncio.CancelledError):
        await resume_return(router)
    await resume_return(router)
    assert replies == []
