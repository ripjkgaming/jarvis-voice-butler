"""A completed media tool must not erase an app-launch handoff in its batch."""

import asyncio
from types import SimpleNamespace

import pytest

from handoff_recovery import install, remember
from jarvis_rt import (
    Agent,
    AgentSession,
    FunctionCall,
    FunctionToolsExecutedEvent,
    RunContext,
    function_tool,
)
from jarvis_rt.executor import make_result

pytestmark = pytest.mark.asyncio


def _call(name, call_id):
    return FunctionCall(name=name, call_id=call_id, arguments="{}")


def _context(session, call):
    return RunContext(
        session=session,
        speech_handle=SimpleNamespace(num_steps=1, interrupted=False),
        function_call=call,
    )


def _batch(results, interrupted=False):
    """The batch as the session emits it, from executor results (make_result).

    An interrupted turn answers a handoff as a failure and never replies.
    """
    outputs = []
    for result in results:
        out = result.output
        if interrupted:
            if result.agent is not None:
                out.output = "the agent handoff was interrupted and did not happen"
                out.is_error = True
            out.reply_required = False
        outputs.append(out)
    return FunctionToolsExecutedEvent(
        function_calls=[result.call for result in results],
        function_call_outputs=outputs,
    )


def _has_tool_reply(event):
    return any(out.reply_required for out in event.function_call_outputs)


def _session():
    session = AgentSession()
    session.update_agent(Agent(instructions="router"))
    install(session)
    return session


def _output(call, result):
    return make_result(call, result, None)


def _tool(name, returns):
    """A jarvis_rt tool that records a handoff the way the common wrapper does."""

    async def run(context: RunContext):
        result = returns()
        remember(context, result)
        return result

    return function_tool(run, name=name, description=name)


class NullBackend:
    """Model stand-in that produces no output: each generation ends silently."""

    session = None

    def __init__(self):
        self.instructions = []

    async def start(self, session, agent):
        self.session = session

    async def aclose(self):
        pass

    def push_audio(self, pcm):
        pass

    def push_video(self, frame, ts_ms):
        pass

    def push_text(self, text):
        pass

    async def say(self, text):
        pass

    async def generate_reply(self, instructions, tool_choice):
        self.instructions.append(instructions)
        self.session.backend_event("turn_complete")

    def interrupt(self):
        pass

    async def send_tool_results(self, results, *, reply):
        pass

    async def agent_changed(self, agent):
        pass

    async def sync_agent(self, agent):
        pass


async def _settle(session):
    """Wait until the session has no running tool, handoff or reply tasks."""

    async def idle():
        while session._tool_tasks:
            await asyncio.sleep(0)

    await asyncio.wait_for(idle(), 5)


@pytest.mark.asyncio
async def test_launch_handoff_survives_music_result_and_preserves_task():
    from agent import Assistant, _wrap_tools_with_timing

    # Real handoff implementation and the production tool wrapper, no model/API.
    assistant = Assistant.__new__(Assistant)
    assistant._system_agent = None
    assistant._llm = None

    async def transfer(context: RunContext):
        return await Assistant.transfer_to_system_control(
            assistant, context, task="launch Sober"
        )

    async def play_media(context: RunContext):
        return {"say": "Music is playing."}

    transfer_tool = function_tool(
        transfer, name="transfer_to_system_control", description="handoff"
    )
    play_tool = function_tool(play_media, name="play_media", description="media")
    _wrap_tools_with_timing([transfer_tool])
    router = Agent(instructions="router", tools=[transfer_tool, play_tool])
    backend = NullBackend()
    session = AgentSession(backend=backend)
    backend.session = session
    session.update_agent(router)
    install(session)
    session.backend_event(
        "tool_calls",
        calls=[
            _call("transfer_to_system_control", "launch"),
            _call("play_media", "music"),
        ],
    )
    await _settle(session)
    assert isinstance(session.current_agent, Agent)
    assert session.current_agent is not router
    # The specialist clears its task on entry; the task must reach its first turn.
    assert any("launch Sober" in (text or "") for text in backend.instructions)
    outputs = {
        item.call_id: item
        for item in session.history.items
        if item.type == "function_call_output"
    }
    assert outputs["music"].output == str({"say": "Music is playing."})


async def test_handoff_batch_sends_no_router_tool_reply():
    # Mirrors the specialist-owned response: the router sends no tool reply.
    replies = []

    class RecordingBackend(NullBackend):
        async def send_tool_results(self, results, *, reply):
            replies.append(reply)

    specialist = Agent(instructions="specialist")
    handoff = _tool("handoff", lambda: (specialist, "Handing off."))
    music = _tool("play_media", lambda: "Music is playing.")
    session = AgentSession(backend=RecordingBackend())
    session.update_agent(Agent(instructions="router", tools=[handoff, music]))
    install(session)
    session.backend_event(
        "tool_calls", calls=[_call("handoff", "launch"), _call("play_media", "music")]
    )
    await _settle(session)
    assert replies == [False]


async def test_sdk_handled_handoff_is_not_applied_twice(monkeypatch):
    session = _session()
    target = Agent(instructions="specialist")
    call = _call("handoff", "launch")
    result = _output(call, (target, "Handing off."))
    remember(_context(session, call), (target, "Handing off."))
    event = _batch([_output(_call("play_media", "music"), "done"), result])
    recovery = session._jarvis_handoff_recovery
    assert recovery._sdk_handoff(event)
    updates = []
    monkeypatch.setattr(session, "update_agent", updates.append)
    session.emit("function_tools_executed", event)
    assert updates == []
    # Consumed records cannot leak into a later event with the same ID.
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
        handoff.output.is_error = True
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
    assert _has_tool_reply(event) == (reason != "interrupted")


async def test_pending_handoffs_are_isolated_between_sessions():
    first, second = _session(), _session()
    target = Agent(instructions="specialist")
    call = _call("handoff", "launch")
    result = (target, "Handing off.")
    remember(_context(first, call), result)
    second_router = second.current_agent
    event = _batch(
        [
            _output(call, result),
            _output(_call("play_media", "music"), "done"),
        ]
    )
    second.emit("function_tools_executed", event)
    assert second.current_agent is second_router
    assert first._jarvis_handoff_recovery._sdk_handoff(event)
    assert second._jarvis_handoff_recovery.pending == {}


async def test_session_executes_launch_after_mixed_batch():
    """Drive the real session with a deterministic, scripted model backend.

    No model service, app launch or media playback is involved. The first
    response reproduces the production handoff+music batch; the specialist
    must enter and execute the remaining launch before the run completes.
    """
    from agent import _wrap_tools_with_timing

    actions = []

    class ScriptedBackend(NullBackend):
        """Each model generation emits the next scripted batch or text reply."""

        def __init__(self, script):
            self.script = list(script)
            self.turns = 0

        def push_text(self, text):
            self._respond()

        async def generate_reply(self, instructions, tool_choice):
            self._respond()

        async def send_tool_results(self, results, *, reply):
            if reply:
                self._respond()

        def _respond(self):
            self.turns += 1
            item = self.script.pop(0) if self.script else "Done."
            if isinstance(item, str):
                self.session.backend_event("agent_text", text=item)
                self.session.backend_event("turn_complete")
            else:
                self.session.backend_event("tool_calls", calls=item)

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

    backend = ScriptedBackend(
        [
            [
                FunctionCall(
                    name="transfer_to_system_control",
                    call_id="launch",
                    arguments="{}",
                ),
                FunctionCall(name="play_media", call_id="music", arguments="{}"),
            ],
            [
                FunctionCall(
                    name="open_app", call_id="open", arguments='{"app": "Sober"}'
                )
            ],
        ]
    )
    session = AgentSession(backend=backend)
    install(session)
    await session.start(Router())
    try:
        session.generate_reply(user_input="Launch Sober and play music.")
        await _settle(session)
        assert session.current_agent is specialist
        assert actions == [("play_media", "music"), ("open_app", "Sober")]
    finally:
        await session.aclose()
    return backend


async def test_handoff_scenario_takes_three_model_turns():
    backend = await test_session_executes_launch_after_mixed_batch()
    assert backend.turns == 3
