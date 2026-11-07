import pytest

import mercury_decide
import request_router
from system.workspace_tools import WorkspaceTools


def answer(route="backend", provider="auto", p=0.98):
    return {
        "destination": {
            "choice": route,
            "probabilities": {
                name: p if name == route else (1 - p) / 3
                for name in ("direct", "backend", "research", "code")
            },
        },
        "provider": {
            "choice": provider,
            "probabilities": {
                name: p if name == provider else (1 - p) / 2
                for name in ("auto", "chatgpt", "claude")
            },
        },
    }


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("JARVIS_MERCURY", "1")
    monkeypatch.setenv("JARVIS_LOCAL", "1")


@pytest.mark.parametrize("route", ["direct", "backend", "research", "code"])
def test_decision_routes_are_typed(monkeypatch, route):
    monkeypatch.setattr(mercury_decide, "decide", lambda *a, **k: (answer(route), None))
    assert request_router.decide("A complete owner request")["route"] == route


def test_provider_preference_and_uncertainty(monkeypatch):
    monkeypatch.setattr(
        mercury_decide, "decide", lambda *a, **k: (answer(provider="claude"), None)
    )
    assert (
        request_router.decide("Ask Claude to plan my revision")["provider"] == "claude"
    )
    monkeypatch.setattr(
        mercury_decide, "decide", lambda *a, **k: (answer(p=0.55), None)
    )
    assert request_router.decide("This is ambiguous")["route"] == "direct"


def test_outage_preserves_explicit_handoff_only(monkeypatch):
    monkeypatch.setattr(mercury_decide, "decide", lambda *a, **k: (None, "offline"))
    assert request_router.decide("What is photosynthesis?")["route"] == "direct"
    got = request_router.decide("Ask Claude to write a detailed revision plan")
    assert got["route"] == "backend" and got["provider"] == "claude"
    assert (
        request_router.decide("Don't ask Claude, just explain it yourself")["route"]
        == "direct"
    )


@pytest.mark.asyncio
async def test_route_tool_never_dispatches(monkeypatch):
    monkeypatch.setattr(
        request_router,
        "decide",
        lambda task: {"route": "backend", "provider": "auto", "source": "decider"},
    )
    tools = WorkspaceTools()
    got = await WorkspaceTools.route_request(tools, None, request="Make a careful plan")
    assert got["route"] == "backend" and got["next_tool"] == "ask_backend"
    assert tools._tasks == set()


@pytest.mark.asyncio
async def test_backend_tool_returns_direct_without_starting_job(monkeypatch):
    monkeypatch.setattr(
        request_router,
        "decide",
        lambda task: {"route": "direct", "provider": "auto", "source": "decider"},
    )
    tools = WorkspaceTools()
    monkeypatch.setattr(
        tools, "_in_background", lambda *a, **k: pytest.fail("unexpected dispatch")
    )
    got = await WorkspaceTools.ask_backend(tools, None, task="What is 2 plus 2?")
    assert got["route"] == "direct" and got["backend_started"] is False


@pytest.mark.asyncio
async def test_backend_tool_keeps_provider_preference(monkeypatch):
    import backend_model

    monkeypatch.setattr(
        request_router,
        "decide",
        lambda task: {"route": "backend", "provider": "claude", "source": "decider"},
    )
    seen, jobs = [], []
    monkeypatch.setattr(
        backend_model,
        "backend_think",
        lambda task, **kw: (seen.append(kw) or "The plan", None),
    )
    tools = WorkspaceTools()
    monkeypatch.setattr(
        tools, "_in_background", lambda context, job, **kw: jobs.append(job)
    )
    got = await WorkspaceTools.ask_backend(
        tools, None, task="Ask Claude to plan revision"
    )
    assert got["backend_started"] is True
    await jobs[0]()
    assert seen[0]["preferred"] == "claude"
