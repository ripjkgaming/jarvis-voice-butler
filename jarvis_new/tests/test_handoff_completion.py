"""Offline regression for action + return in a silent realtime generation."""

import asyncio
from types import MethodType, SimpleNamespace

import pytest
from livekit.agents import Agent, AgentSession, RunContext, llm
from livekit.agents.voice.events import FunctionToolsExecutedEvent

from handoff_recovery import begin_specialist, install, remember, resume_return


def scenario(*, error=False, interrupted=False, reported=False, mixed=True):
    session = AgentSession()
    router = Agent(instructions="router")
    source = Agent(instructions="system specialist")
    session.update_agent(source)
    install(session)
    begin_specialist(source)
    action = llm.FunctionCall(
        name="open_app", call_id="action", arguments='{"app":"calculator"}'
    )
    result = llm.FunctionCallOutput(
        name="open_app",
        call_id="action",
        output="blocked by policy" if error else "{'say': 'Opening calculator.'}",
        is_error=error,
    )
    source._chat_ctx.insert([action, result])
    if reported:
        source._chat_ctx.add_message(role="assistant", content="Opening calculator.")
    call = llm.FunctionCall(
        name="transfer_back_to_main", call_id="return", arguments="{}"
    )
    output = llm.FunctionCallOutput(
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
    event._handoff_required = True
    session.emit("function_tools_executed", event)
    session.update_agent(router)
    replies = []

    async def generate_reply(**kwargs):
        # A fake provider emits audio only for an explicitly owned generation.
        # It cannot execute the completed action again on this continuation.
        assert kwargs["tool_choice"] == "none"
        replies.append(kwargs)

    router._activity = SimpleNamespace(
        session=SimpleNamespace(current_agent=router, generate_reply=generate_reply)
    )
    return router, source, speech, replies, event


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [True, False])
async def test_successful_action_has_one_owned_spoken_completion(mixed):
    from agent import Assistant

    router, _, _, replies, event = scenario(mixed=mixed)
    assert not event.has_tool_reply
    await Assistant.on_enter(router)
    await Assistant.on_enter(router)
    assert len(replies) == 1
    assert "Opening calculator." in replies[0]["instructions"]
    assert "Do not execute or retry" in replies[0]["instructions"]


@pytest.mark.asyncio
async def test_completion_reaches_google_audio_stream_without_another_action():
    from google.genai import types
    from livekit.plugins.google.realtime import realtime_api as google_rt

    router, source, _, replies, _ = scenario()
    actions = [
        item
        for item in source.chat_ctx.items
        if item.type == "function_call" and item.name == "open_app"
    ]
    events = []
    rt = SimpleNamespace(
        _current_generation=None,
        _rejected_tool_calls=0,
        _pending_generation_fut=asyncio.Future(),
        _realtime_model=SimpleNamespace(
            capabilities=SimpleNamespace(audio_output=True)
        ),
        _opts=SimpleNamespace(
            output_audio_transcription=types.AudioTranscriptionConfig()
        ),
        _chat_ctx=llm.ChatContext.empty(),
        emit=lambda *args: events.append(args),
    )
    for name in (
        "_close_output_streams",
        "_mark_current_generation_done",
        "_handle_input_speech_stopped",
    ):
        setattr(rt, name, MethodType(getattr(google_rt.RealtimeSession, name), rt))

    async def provider(**kwargs):
        assert kwargs["tool_choice"] == "none"
        replies.append(kwargs)
        google_rt.RealtimeSession._start_new_generation(rt)
        content = types.LiveServerContent(
            model_turn=types.Content(
                parts=[
                    types.Part(
                        inline_data=types.Blob(
                            mime_type="audio/pcm", data=b"\x00\x00" * 240
                        )
                    )
                ]
            ),
            output_transcription=types.Transcription(text="Opening calculator."),
            generation_complete=True,
            turn_complete=True,
        )
        google_rt.RealtimeSession._handle_server_content(rt, content)

    router._activity.session.generate_reply = provider
    await resume_return(router)
    await resume_return(router)
    frames = [frame async for frame in rt._current_generation.audio_ch]
    assert len(frames) == 1 and frames[0].samples_per_channel == 240
    assert rt._current_generation.output_text == "Opening calculator."
    assert events[0][1].user_initiated
    assert len(replies) == 1
    assert [
        item
        for item in source.chat_ctx.items
        if item.type == "function_call" and item.name == "open_app"
    ] == actions


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
    router._activity.session.current_agent = Agent(instructions="new task")
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
    import asyncio

    router, _, _, replies, _ = scenario()

    async def cancelled(**kwargs):
        raise asyncio.CancelledError

    router._activity.session.generate_reply = cancelled
    with pytest.raises(asyncio.CancelledError):
        await resume_return(router)
    await resume_return(router)
    assert replies == []
