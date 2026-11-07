"""Scheduling voice tool contracts; no live delivery or account access."""

import pytest
from jarvis_rt import ToolError

import scheduled_tasks as tasks
from system.schedule_tools import ScheduleTools


@pytest.fixture(autouse=True)
def local(monkeypatch):
    import phone_session

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_SCHEDULED_TASKS", "1")
    monkeypatch.setattr(phone_session, "guest_required", lambda: False)


async def test_schedule_list_cancel_tool_roundtrip(monkeypatch):
    monkeypatch.setattr(tasks.time, "time", lambda: 1792242000)
    tools = ScheduleTools()
    created = await tools.schedule_task(
        None, kind="reminder", delay_minutes=10, text="Drink water"
    )
    assert "scheduled" in created["say"].lower()
    assert "Asia/Singapore" in created["say"]
    assert created["task_id"].startswith("sched-")
    listed = await tools.scheduled_tasks(None)
    assert "Drink water" in listed["say"]
    cancelled = await tools.scheduled_tasks(
        None, action="cancel", task_id=created["task_id"]
    )
    assert "Cancelled" in cancelled["say"]


async def test_creation_disabled_and_listing_does_not_create_store(monkeypatch):
    monkeypatch.setenv("JARVIS_SCHEDULED_TASKS", "0")
    tools = ScheduleTools()
    with pytest.raises(ToolError, match="disabled"):
        await tools.schedule_task(None, kind="reminder", delay_minutes=10, text="Water")
    await tools.scheduled_tasks(None)
    assert not tasks.store_path().exists()


async def test_guest_refused(monkeypatch):
    import phone_session

    monkeypatch.setattr(phone_session, "guest_required", lambda: True)
    with pytest.raises(ToolError, match="owner"):
        await ScheduleTools().schedule_task(
            None, kind="reminder", delay_minutes=10, text="Water"
        )
    assert not tasks.store_path().exists()


async def test_private_owner_can_schedule_but_expired_private_owner_cannot(monkeypatch):
    import phone_session

    monkeypatch.setenv("JARVIS_LOCAL", "0")
    monkeypatch.setattr(phone_session, "private", lambda: True)
    monkeypatch.setattr(phone_session, "identity", lambda: "owner")
    result = await ScheduleTools().schedule_task(
        None, kind="reminder", delay_minutes=10, text="Water"
    )
    assert result["task_id"]
    monkeypatch.setattr(phone_session, "identity", lambda: "")
    with pytest.raises(ToolError):
        await ScheduleTools().scheduled_tasks(None)


async def test_invalid_schedule_becomes_tool_error():
    with pytest.raises(ToolError):
        await ScheduleTools().schedule_task(
            None, kind="reminder", when="last week", text="Water"
        )


async def test_list_rechecks_owner_after_background_read(monkeypatch):
    import phone_session
    import system.schedule_tools as tools_module

    allowed = [True]
    monkeypatch.setattr(phone_session, "guest_required", lambda: not allowed[0])

    def revoked():
        allowed[0] = False
        return [{"text": "SECRET_CANARY"}]

    monkeypatch.setattr(tools_module, "_read_tasks", revoked)
    with pytest.raises(ToolError, match="owner"):
        await ScheduleTools().scheduled_tasks(None)
