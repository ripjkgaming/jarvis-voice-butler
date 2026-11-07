"""Calls for tools of an agent this session has just left must still resolve.

After a handoff the live model can keep calling tools from the declarations it
was connected with (the router's ``transfer_to_system_control`` after the
switch to SystemAgent, and the reverse for ``list_windows``). Those calls must
not fail as "Unknown function" when the tool exists on an agent of this session.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jarvis_rt import Agent, AgentSession, FunctionCall, function_tool


class RecordingBackend:
    """Backend stand-in: records tool results, ignores everything else."""

    def __init__(self):
        self.batches = []

    async def send_tool_results(self, results, *, reply):
        self.batches.append((list(results), reply))

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        async def _noop(*args, **kwargs):
            return None

        return lambda *a, **k: _noop(*a, **k)


class Router(Agent):
    def __init__(self):
        super().__init__(instructions="router")
        self.system = None

    @function_tool()
    async def transfer_to_system_control(self):
        """Hand off to system control."""
        return self.system, "Handing off to system control."

    @function_tool()
    async def status(self) -> str:
        """Router status."""
        return "router-status"


class SystemAgent(Agent):
    def __init__(self, router):
        super().__init__(instructions="system")
        self.router = router

    @function_tool()
    async def transfer_back_to_main(self):
        """Return to the router."""
        return self.router, "Returning to Jarvis."

    @function_tool()
    async def list_windows(self) -> str:
        """List windows."""
        return "windows-listed"

    @function_tool()
    async def status(self) -> str:
        """System status."""
        return "system-status"


def _call(name, call_id):
    return FunctionCall(call_id=call_id, name=name, arguments="{}")


async def _settle(session):
    async def idle():
        while session._tool_tasks:
            await asyncio.sleep(0)

    await asyncio.wait_for(idle(), 5)


async def _run_calls(session, backend, names):
    before = len(backend.batches)
    session.backend_event(
        "tool_calls", calls=[_call(n, f"c{i}") for i, n in enumerate(names)]
    )
    await _settle(session)
    assert len(backend.batches) == before + 1
    return backend.batches[-1][0]


def _started():
    router = Router()
    system = SystemAgent(router)
    router.system = system
    backend = RecordingBackend()
    session = AgentSession(backend=backend)
    session.update_agent(router)
    return session, backend, router, system


def test_router_tool_called_after_handoff_to_system_still_resolves():
    async def run():
        session, backend, _router, _system = _started()
        await _run_calls(session, backend, ["transfer_to_system_control"])
        assert session.current_agent is _system
        # The live model still calls the router's handoff tool on SystemAgent.
        return await _run_calls(session, backend, ["transfer_to_system_control"])

    (out,) = asyncio.run(run())
    assert not out.is_error, out.output
    assert out.output == "Handing off to system control."


def test_system_tool_called_after_return_to_router_still_resolves():
    async def run():
        session, backend, _router, _system = _started()
        await _run_calls(session, backend, ["transfer_to_system_control"])
        await _run_calls(session, backend, ["transfer_back_to_main"])
        assert session.current_agent is _router
        # The live model still calls SystemAgent's tool on the router.
        return await _run_calls(session, backend, ["list_windows"])

    (out,) = asyncio.run(run())
    assert not out.is_error, out.output
    assert out.output == "windows-listed"


def test_current_agent_tool_wins_over_a_previous_agent_of_the_same_name():
    async def run():
        session, backend, _router, _system = _started()
        await _run_calls(session, backend, ["transfer_to_system_control"])
        return await _run_calls(session, backend, ["status"])

    (out,) = asyncio.run(run())
    assert out.output == "system-status"


def test_name_on_no_agent_of_the_session_is_still_unknown():
    async def run():
        session, backend, _router, _system = _started()
        await _run_calls(session, backend, ["transfer_to_system_control"])
        return await _run_calls(session, backend, ["no_such_tool"])

    (out,) = asyncio.run(run())
    assert out.is_error
    assert "Unknown function: no_such_tool" in out.output
