import contextvars
from types import SimpleNamespace

import pytest
from livekit.agents.llm import ToolError

import agent_tasks as at
import phone_session
import system.task_tools as tt


@pytest.fixture
def tools_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(at, "_hud", lambda *a: None)
    monkeypatch.setattr(at, "_tell", lambda *a, **k: None)
    monkeypatch.setattr(tt, "log_action", lambda *a: None)
    monkeypatch.setattr(phone_session, "private", lambda: False)
    monkeypatch.setattr(phone_session, "guest_required", lambda: False)

    async def mocked_dispatch(function, *args, **kwargs):
        return contextvars.copy_context().run(function, *args, **kwargs)

    monkeypatch.setattr(tt.asyncio, "to_thread", mocked_dispatch)
    return tt.TaskTools()


def seed(identifier="task-one", status="waiting"):
    task = {
        "id": identifier,
        "title": "Synthetic",
        "goal": "fixture",
        "status": status,
        "steps": [],
        "created": 1,
    }
    at.save(task)


@pytest.mark.asyncio
async def test_ambiguous_empty_target_does_not_cancel(tools_home):
    seed()
    seed("task-two")
    receipt = await tt.TaskTools.cancel_task(tools_home, None)
    assert not receipt["accepted"]
    assert all(task["status"] == "waiting" for task in at.all_tasks())


@pytest.mark.asyncio
async def test_explicit_native_task_receipt(tools_home):
    seed()
    receipt = await tt.TaskTools.cancel_task(tools_home, None, "task-one")
    assert receipt["settled"] and receipt["state"] == "cancelled"
    assert "undone" in receipt["say"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["private", "guest"])
async def test_private_and_guest_cannot_enumerate_or_cancel(
    tools_home, monkeypatch, kind
):
    monkeypatch.setattr(
        phone_session,
        "private" if kind == "private" else "guest_required",
        lambda: True,
    )
    monkeypatch.setattr(at, "all_tasks", lambda: pytest.fail("private enumeration"))
    monkeypatch.setattr(at, "cancel", lambda *a: pytest.fail("private cancellation"))
    with pytest.raises(ToolError):
        await tt.TaskTools.task_status(tools_home, None)
    with pytest.raises(ToolError):
        await tt.TaskTools.cancel_task(tools_home, None, "task-one")


@pytest.mark.asyncio
async def test_redirect_without_input_identity_cancels_nothing(tools_home, monkeypatch):
    seed()
    monkeypatch.setattr(
        at, "cancel", lambda *a: pytest.fail("unauthenticated redirect")
    )
    with pytest.raises(ToolError, match="identity"):
        await tt.TaskTools.redirect_task(
            tools_home, None, "task-one", "replacement goal"
        )


@pytest.mark.asyncio
async def test_redirect_new_request_preserves_old_cancellation(tools_home, monkeypatch):
    seed()
    monkeypatch.setattr(at, "advance", lambda task_id, *a, **k: at.get(task_id))
    context = SimpleNamespace(speech_handle=SimpleNamespace(id="fresh-user-turn"))
    result = await tt.TaskTools.redirect_task(
        tools_home, context, "task-one", "prepare replacement report"
    )
    assert result["replacement_started"]
    assert result["task_id"] != "task-one"
    assert at.get("task-one")["status"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["build-example", "job-" + "a" * 32, "123"])
async def test_unregistered_job_and_pid_never_cancel(
    tools_home, monkeypatch, identifier
):
    monkeypatch.setattr(
        at, "cancel", lambda *a: pytest.fail("unsupported cancellation")
    )
    result = await tt.TaskTools.cancel_task(tools_home, None, identifier)
    assert not result["accepted"]
