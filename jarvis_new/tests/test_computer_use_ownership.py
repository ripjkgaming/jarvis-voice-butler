import asyncio

import pytest
from livekit.agents.llm import ToolError

from system.computer_use_ownership import (
    ControlBusyError,
    acquire_control,
    serialized_desktop_action,
)


def test_exclusive_lease_releases_and_reacquires():
    lease = acquire_control()
    with pytest.raises(ControlBusyError):
        acquire_control()
    lease.release()
    lease.release()
    acquire_control().release()


async def test_direct_desktop_input_is_blocked_by_lease():
    calls = []

    @serialized_desktop_action
    async def action():
        calls.append(True)

    lease = acquire_control()
    try:
        with pytest.raises(ToolError, match="owns"):
            await action()
        assert not calls
    finally:
        lease.release()
    await action()
    assert calls == [True]


@pytest.mark.parametrize(
    "name,args",
    [
        ("desktop_click", (100, 100)),
        ("desktop_type", ("hi",)),
        ("desktop_key", ("Tab",)),
        ("desktop_scroll", ("down",)),
    ],
)
async def test_registered_desktop_actions_respect_task_owner(name, args, monkeypatch):
    from system.desktop import DesktopTools

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    desktop = DesktopTools()
    lease = acquire_control()
    try:
        with pytest.raises(ToolError, match="owns"):
            await getattr(desktop, name)(None, *args)
    finally:
        lease.release()


async def test_cancelled_direct_action_keeps_lease_until_input_settles():
    started, finish = asyncio.Event(), asyncio.Event()

    @serialized_desktop_action
    async def action():
        started.set()
        await finish.wait()

    task = asyncio.create_task(action())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    with pytest.raises(ControlBusyError):
        acquire_control()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    acquire_control().release()


async def test_shared_command_cancel_kills_and_reaps_child(monkeypatch):
    import system

    waiting = asyncio.Event()

    class Process:
        returncode = None
        killed = False
        reaped = False

        async def communicate(self):
            waiting.set()
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True

        async def wait(self):
            self.reaped = True
            self.returncode = -9

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(system.run_cmd("fake-screenshot"))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed and process.reaped
