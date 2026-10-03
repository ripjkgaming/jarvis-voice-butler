"""A completed media tool must not erase an app-launch handoff in its batch."""

from types import SimpleNamespace

import pytest
from livekit.agents import Agent, AgentSession, RunContext, function_tool, llm
from livekit.agents.voice.events import FunctionToolsExecutedEvent
from livekit.agents.voice.generation import _interrupted_tool_output, make_tool_output

from handoff_recovery import install, remember

pytestmark = pytest.mark.asyncio


def _call(name, call_id):
    return llm.FunctionCall(name=name, call_id=call_id, arguments="{}")


def _context(session, call):
    return RunContext(
        session=session,
        speech_handle=SimpleNamespace(num_steps=1, interrupted=False),
        function_call=call,
    )


def _batch(outputs, interrupted=False):
    # LiveKit 1.8.2's real tool-output sanitizer and handoff aggregation.
    target = None
    multiple = False
    for out in outputs:
        if target is not None and out.agent_task is not None:
            multiple = True
        target = out.agent_task
    event = FunctionToolsExecutedEvent(
        function_calls=[out.fnc_call for out in outputs],
        function_call_outputs=[
            _interrupted_tool_output(out) if interrupted else out.fnc_call_out
            for out in outputs
        ],
    )
    event._handoff_required = bool(target and not multiple and not interrupted)
    return event


def _session():
    session = AgentSession()
    session.update_agent(Agent(instructions="router"))
    install(session)
    return session


def _output(call, result):
    return make_tool_output(fnc_call=call, output=result, exception=None)


@pytest.mark.asyncio
async def test_launch_handoff_survives_music_result_and_preserves_task():
    from agent import Assistant

    session = _session()
    # Real handoff implementation and the production tool wrapper, no model/API.
    assistant = Assistant.__new__(Assistant)
    assistant._system_agent = None
    assistant._llm = None
    session.update_agent(assistant)
    call = _call("transfer_to_system_control", "launch")
    context = _context(session, call)
    from agent import _wrap_tools_with_timing

    async def transfer(context: RunContext):
        return await Assistant.transfer_to_system_control(
            assistant, context, task="launch Sober"
        )

    tool = function_tool(transfer)
    _wrap_tools_with_timing([tool])
    result = await tool._func(context)
    music = _output(_call("play_media", "music"), {"say": "Music is playing."})
    event = _batch([_output(call, result), music])
    assert not event.has_agent_handoff  # reproduces the SDK loss
    session.emit("function_tools_executed", event)
    assert session.current_agent is result[0]
    assert session.current_agent.task == "launch Sober"
    assert not event.has_tool_reply  # specialist owns the response now
    assert music.fnc_call_out.output == str({"say": "Music is playing."})


async def test_sdk_handled_handoff_is_not_applied_twice(monkeypatch):
    session = _session()
    target = Agent(instructions="specialist")
    call = _call("handoff", "launch")
    result = (target, "Handing off.")
    remember(_context(session, call), result)
    event = _batch(
        [_output(_call("play_media", "music"), "done"), _output(call, result)]
    )
    assert event.has_agent_handoff
    updates = []
    monkeypatch.setattr(session, "update_agent", updates.append)
    session.emit("function_tools_executed", event)
    assert updates == []
    # Consumed records cannot leak into a later event with the same ID.
    event._handoff_required = False
    session.emit("function_tools_executed", event)
    assert updates == []


@pytest.mark.parametrize(
    "reason", ["interrupted", "failed", "source_changed", "two_handoffs"]
)
async def test_cancelled_failed_or_ambiguous_batches_never_switch(reason, monkeypatch):
    session = _session()
    target = Agent(instructions="specialist")
    call = _call("handoff", "launch")
    context = _context(session, call)
    result = (target, "Handing off.")
    remember(context, result)
    handoff = _output(call, result)
    outputs = [handoff]
    if reason == "failed":
        handoff.fnc_call_out.is_error = True
    elif reason == "source_changed":
        session.update_agent(Agent(instructions="someone else"))
    elif reason == "two_handoffs":
        second = _call("handoff", "second")
        second_result = (Agent(instructions="other specialist"), "Handing off.")
        remember(_context(session, second), second_result)
        outputs.append(_output(second, second_result))
    outputs.append(_output(_call("play_media", "music"), "done"))
    event = _batch(outputs, interrupted=reason == "interrupted")
    updates = []
    monkeypatch.setattr(session, "update_agent", updates.append)
    session.emit("function_tools_executed", event)
    assert updates == []
    assert event.has_tool_reply == (reason != "interrupted")


async def test_pending_handoffs_are_isolated_between_sessions():
    first, second = _session(), _session()
    target = Agent(instructions="specialist")
    call = _call("handoff", "launch")
    result = (target, "Handing off.")
    remember(_context(first, call), result)
    second_router = second.current_agent
    event = _batch(
        [_output(call, result), _output(_call("play_media", "music"), "done")]
    )
    second.emit("function_tools_executed", event)
    assert second.current_agent is second_router
    first.emit("function_tools_executed", event)
    assert first.current_agent is target


async def test_livekit_session_executes_launch_after_mixed_batch():
    """Exercise the actual SDK activity/transition, with a deterministic LLM.

    No model service, app launch or media playback is involved. The first
    response reproduces the production handoff+music batch; the specialist
    must enter and execute the remaining launch before the run completes.
    """
    import asyncio

    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from agent import _wrap_tools_with_timing

    actions = []

    class ScriptedStream(llm.LLMStream):
        def __init__(self, model, *, chat_ctx, tools, conn_options, delta):
            self.delta = delta
            super().__init__(
                model, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options
            )

        async def _run(self):
            self._event_ch.send_nowait(llm.ChatChunk(id="scripted", delta=self.delta))

    class ScriptedLLM(llm.LLM):
        def __init__(self):
            super().__init__()
            self.turns = 0

        def chat(
            self,
            *,
            chat_ctx,
            tools=None,
            conn_options=DEFAULT_API_CONNECT_OPTIONS,
            **kwargs,
        ):
            self.turns += 1
            batches = {
                1: [
                    llm.FunctionToolCall(
                        name="transfer_to_system_control",
                        call_id="launch",
                        arguments="{}",
                    ),
                    llm.FunctionToolCall(
                        name="play_media", call_id="music", arguments="{}"
                    ),
                ],
                2: [
                    llm.FunctionToolCall(
                        name="open_app", call_id="open", arguments='{"app": "Sober"}'
                    )
                ],
            }
            delta = llm.ChoiceDelta(
                role="assistant",
                tool_calls=batches.get(self.turns, []),
                content=None if self.turns in batches else "Done.",
            )
            return ScriptedStream(
                self,
                chat_ctx=chat_ctx,
                tools=tools or [],
                conn_options=conn_options,
                delta=delta,
            )

    class Specialist(Agent):
        def __init__(self):
            super().__init__(instructions="Launch the requested app.")
            _wrap_tools_with_timing(self.tools)

        async def on_enter(self):
            await self.session.generate_reply()

        @function_tool()
        async def open_app(self, context: RunContext, app: str):
            actions.append(("open_app", app))
            return "Sober is launched."

    specialist = Specialist()

    class Router(Agent):
        def __init__(self):
            super().__init__(instructions="Launch Sober and play music.")
            _wrap_tools_with_timing(self.tools)

        @function_tool()
        async def transfer_to_system_control(self, context: RunContext):
            return specialist, "Handing off to launch Sober."

        @function_tool()
        async def play_media(self, context: RunContext):
            # Keep the same result ordering as the real failure.
            await asyncio.sleep(0.02)
            actions.append(("play_media", "music"))
            return "Music is playing."

    async with ScriptedLLM() as model, AgentSession(llm=model) as session:
        install(session)
        await session.start(Router())
        await asyncio.wait_for(
            session.run(user_input="Launch Sober and play music."), 10
        )
        assert session.current_agent is specialist
        assert actions == [("play_media", "music"), ("open_app", "Sober")]
        assert model.turns == 3
