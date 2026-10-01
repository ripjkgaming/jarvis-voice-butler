"""No model calls or desktop input: the controller runs only injected fakes."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from system.computer_use import ComputerUseService, ComputerUseTools, Limits


def decision(obs="obs-0", *, action=None, status="action", **changes):
    return {
        "status": status,
        "observation_id": obs,
        "summary": "The requested view is visible."
        if status == "done"
        else "Click the visible search box.",
        "evidence": "Search box is visible near the top left.",
        "within_scope": True,
        "risk": "ordinary",
        "action": action
        if action is not None
        else (
            {
                "type": "click",
                "x": 20,
                "y": 30,
                "button": "left",
                "region": {"x": 0, "y": 0, "width": 100, "height": 100},
            }
            if status == "action"
            else None
        ),
        **changes,
    }


def test_every_action_schema_requires_bounded_region_and_scroll_point():
    from system.computer_use import DECISION_SCHEMA

    actions = DECISION_SCHEMA["properties"]["action"]["anyOf"][1:]
    for action in actions:
        assert "region" in action["required"]
        region = action["properties"]["region"]
        assert set(region["required"]) == {"x", "y", "width", "height"}
        assert region["properties"]["width"]["minimum"] == 1
        if action["properties"]["type"]["enum"] == ["scroll"]:
            assert {"x", "y"} <= set(action["required"])


class Surface:
    def __init__(self, workspace):
        self.capabilities = {"target": "sandbox::99", "grid": "0-1000"}
        self.workspace = workspace
        self.actions = []
        self.closed = False
        self.n = 0
        self.same = False

    async def observe(self):
        return SimpleNamespace(
            id=f"obs-{self.n}",
            path=str(self.workspace / "screen.png"),
            width=100,
            height=100,
            sha256="same" if self.same else f"hash-{self.n}",
            target="sandbox::99",
        )

    async def act(self, action, observation_id):
        assert observation_id == f"obs-{self.n}"
        self.actions.append(action)
        self.n += 1
        return await self.observe()

    async def close(self):
        self.closed = True


class Worker:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.started = False
        self.closed = False
        self.waiting = asyncio.Event()

    async def start(self, workspace):
        self.started = True
        self.workspace = workspace

    async def propose(self, prompt, image_path, schema):
        self.prompts.append(prompt)
        if not self.replies:
            await self.waiting.wait()
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def close(self):
        self.closed = True


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_COMPUTER_USE", "1")

    def build(replies, *, limits=None):
        worker = Worker(replies)
        surfaces = []

        def surface_factory(workspace):
            surfaces.append(Surface(workspace))
            return surfaces[-1]

        service = ComputerUseService(
            worker_factory=lambda: worker,
            surface_factory=surface_factory,
            limits=limits or Limits(),
        )
        return service, worker, surfaces

    return build


async def finished(service):
    await service.wait()
    return service.status()


async def test_grounded_action_then_visually_verified_result(setup):
    service, worker, surfaces = setup([decision(), decision("obs-1", status="done")])
    started = service.start("Find the search box and show the result")
    assert started["status"] == "running"
    result = await finished(service)
    assert result["status"] == "done"
    assert result["ok"] is True and result["steps"] == 1
    assert result["verification"] == "model_visual"
    assert len(result["actions"]) == 1
    assert result["actions"][0]["before"] == "obs-0"
    assert result["actions"][0]["after"] == "obs-1"
    assert len(surfaces[0].actions) == 1 and surfaces[0].closed
    assert worker.started and worker.closed
    assert not Path(worker.workspace).exists()
    assert "Find the search box" in worker.prompts[0]
    assert "untrusted" in worker.prompts[0].lower()
    assert "Find the search box" not in worker.prompts[1]


@pytest.mark.parametrize(
    "changes",
    [
        {"observation_id": "old"},
        {"within_scope": False},
        {"risk": "consequential"},
        {"action": {"type": "shell", "command": "rm -rf /"}},
        {"action": {"type": "click", "x": True, "y": 10}},
        {"evidence": ""},
        {"unexpected": True},
    ],
)
async def test_bad_or_unauthorized_proposal_never_reaches_input(setup, changes):
    service, worker, surfaces = setup([decision(**changes)])
    service.start("Inspect the current screen")
    result = await finished(service)
    assert result["status"] in {"failed", "blocked"}
    assert not surfaces[0].actions
    assert worker.closed and surfaces[0].closed


async def test_step_limit_does_not_claim_success(setup):
    service, _, surfaces = setup(
        [decision(), decision("obs-1")], limits=Limits(max_steps=1)
    )
    service.start("Inspect and navigate")
    result = await finished(service)
    assert result["status"] == "step_limit" and not result["ok"]
    assert len(surfaces[0].actions) == 1


async def test_no_progress_stops_repeated_clicks(setup):
    service, _, surfaces = setup([decision(), decision("obs-1"), decision("obs-2")])
    original = service.surface_factory

    def unchanged_surface(workspace):
        surface = original(workspace)
        surface.same = True
        return surface

    service.surface_factory = unchanged_surface
    service.start("Inspect and navigate")
    result = await finished(service)
    assert result["status"] == "stalled"
    assert len(surfaces[0].actions) <= 2


async def test_concurrent_services_cannot_steal_ownership(setup):
    first, _, _ = setup([])
    second, _, _ = setup([])
    first.start("Inspect and navigate")
    assert second.start("Some other task")["status"] == "busy"
    first.cancel()
    assert (await finished(first))["status"] == "cancelled"
    assert second.start("Some other task")["status"] == "running"
    second.cancel()
    await finished(second)


async def test_cancel_waiting_worker_releases_input_ownership(setup):
    service, worker, surfaces = setup([])
    service.start("Inspect and navigate")
    while not worker.prompts:
        await asyncio.sleep(0)
    assert service.cancel()["status"] == "cancelling"
    result = await finished(service)
    assert result["status"] == "cancelled" and not surfaces[0].actions
    assert worker.closed and surfaces[0].closed


async def test_timeout_and_worker_error_fail_cleanly(setup):
    service, worker, surfaces = setup(
        [], limits=Limits(timeout=0.02, turn_timeout=0.01)
    )
    service.start("Inspect and navigate")
    result = await finished(service)
    assert result["status"] == "timed_out"
    assert worker.closed and surfaces[0].closed
    service, _, _ = setup([RuntimeError("secret-token-must-not-escape")])
    service.start("Inspect and navigate")
    result = await finished(service)
    assert result["status"] == "failed"
    assert "secret-token" not in str(result)


async def test_disabled_and_cloud_do_not_construct_dependencies(setup, monkeypatch):
    service, worker, surfaces = setup([])
    monkeypatch.setenv("JARVIS_COMPUTER_USE", "0")
    assert service.start("Inspect screen")["status"] == "disabled"
    monkeypatch.setenv("JARVIS_COMPUTER_USE", "1")
    monkeypatch.delenv("JARVIS_LOCAL")
    assert service.start("Inspect screen")["status"] == "unavailable"
    assert not worker.started and not surfaces


async def test_tools_return_status_and_cancel_without_input(setup):
    service, _worker, surfaces = setup([])
    tools = ComputerUseTools(service)
    assert {t.id for t in tools.tools} == {
        "computer_use",
        "computer_use_status",
        "computer_use_cancel",
    }
    result = await tools.computer_use(None, "Inspect and navigate")
    assert result["status"] == "running"
    assert (await tools.computer_use_status(None))["status"] == "running"
    await tools.computer_use_cancel(None)
    await finished(service)
    assert not surfaces or not surfaces[0].actions


async def test_changed_scene_requires_new_proposal_before_input(setup):
    from system.computer_surface import SceneChangedError

    service, worker, surfaces = setup(
        [decision(), decision("obs-1"), decision("obs-2", status="done")]
    )
    original = service.surface_factory

    def changed_once(workspace):
        surface = original(workspace)
        act = surface.act

        async def changed(action, observation_id):
            if surface.n == 0:
                surface.n += 1
                raise SceneChangedError("The screen changed; observe again.")
            return await act(action, observation_id)

        surface.act = changed
        return surface

    service.surface_factory = changed_once
    service.start("Inspect and navigate")
    result = await finished(service)
    assert result["status"] == "done" and result["steps"] == 1
    assert len(surfaces[0].actions) == 1
    assert len(worker.prompts) == 3
    assert "input_completed" in worker.prompts[1]
    assert "false" in worker.prompts[1]


async def test_new_run_clears_previous_result_evidence(setup):
    service, worker, _ = setup([decision(status="done")])
    service.start("Inspect and navigate")
    assert "evidence" in await finished(service)
    worker.replies = []
    assert "evidence" not in service.start("Inspect another app")
    service.cancel()
    await finished(service)


@pytest.mark.parametrize("resource", ["worker", "surface"])
async def test_late_cancel_cannot_interrupt_cleanup_or_leave_lease(setup, resource):
    from system.computer_use_ownership import ControlBusyError, acquire_control

    service, worker, surfaces = setup([decision(status="done")])
    closing, finish_close = asyncio.Event(), asyncio.Event()

    async def close():
        closing.set()
        await finish_close.wait()
        if resource == "worker":
            worker.closed = True
        else:
            surfaces[0].closed = True

    if resource == "worker":
        worker.close = close
    else:
        original = service.surface_factory

        def surface_with_slow_close(workspace):
            surface = original(workspace)
            surface.close = close
            return surface

        service.surface_factory = surface_with_slow_close
    service.start("Inspect and navigate")
    await closing.wait()
    assert service.status()["status"] == "done"
    assert service.cancel()["status"] == "done"
    # Even an external shutdown cancellation must drain, unlike the public
    # cancel method which correctly leaves an already-terminal job alone.
    service._task.cancel()
    await asyncio.sleep(0)
    with pytest.raises(ControlBusyError):
        acquire_control()
    finish_close.set()
    await finished(service)
    assert worker.closed and surfaces[0].closed
    assert not Path(worker.workspace).exists()
    acquire_control().release()


def test_worker_tools_registered_on_system_specialist_only(monkeypatch):
    import agent

    monkeypatch.setattr(agent, "_default_agent_llm", lambda: None)
    ids = {"computer_use", "computer_use_status", "computer_use_cancel"}
    assert ids <= agent.RARE_SYSTEM_TOOL_IDS
    specialist = agent.SystemAgent(llm=None, only_ids=agent.RARE_SYSTEM_TOOL_IDS)
    assert ids <= {tool.id for tool in specialist.tools}
    direct = agent.SystemAgent(llm=None, only_ids=set())
    assert not ids & {tool.id for tool in direct.tools}


async def test_background_completion_uses_existing_session_without_another_model(setup):
    service, _, _ = setup([decision(status="done")])
    spoken = []

    async def say(text):
        spoken.append(text)

    tools = ComputerUseTools(service)
    await tools.computer_use(
        SimpleNamespace(session=SimpleNamespace(say=say)), "Inspect the current app"
    )
    notifications = list(tools._notifications)
    await asyncio.gather(*notifications)
    assert spoken == [service.status()["say"]]
