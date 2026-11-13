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


@pytest.mark.parametrize("confidence", [0.0, 0.49, 0.5])
def test_automatic_routing_consults_at_or_below_half(monkeypatch, confidence):
    calls = []
    monkeypatch.setattr(
        request_router,
        "decide",
        lambda request: (
            calls.append(request)
            or {"route": "research", "provider": "auto", "source": "decider"}
        ),
    )
    result = request_router.automatic(
        "Compare these options thoroughly",
        intent_confidence=confidence,
        actionable=True,
    )
    assert calls == ["Compare these options thoroughly"]
    assert result["route"] == "research" and result["routing_skipped"] is False


@pytest.mark.parametrize(
    ("confidence", "actionable", "reason"),
    [
        (0.5001, True, "confident"),
        (0.9, True, "confident"),
        (0.0, False, "conversation"),
        (0.5, False, "conversation"),
        (True, True, "invalid-confidence"),
        (float("nan"), True, "invalid-confidence"),
        (float("inf"), True, "invalid-confidence"),
        (-0.1, True, "invalid-confidence"),
        (1.1, True, "invalid-confidence"),
        (None, True, "invalid-confidence"),
    ],
)
def test_automatic_routing_skips_confident_conversation_and_invalid_inputs(
    monkeypatch, confidence, actionable, reason
):
    monkeypatch.setattr(
        request_router,
        "decide",
        lambda *_: pytest.fail("unexpected decision-model routing"),
    )
    result = request_router.automatic(
        "Hello there", intent_confidence=confidence, actionable=actionable
    )
    assert result["routing_skipped"] is True and result["reason"] == reason


@pytest.mark.asyncio
async def test_route_tool_passes_confidence_gate_without_starting_work(monkeypatch):
    monkeypatch.setattr(
        request_router,
        "decide",
        lambda *_: pytest.fail("confident turn must not reach decider"),
    )
    tools = WorkspaceTools()
    result = await WorkspaceTools.route_request(
        tools, None, request="Prepare a report", intent_confidence=0.9, actionable=True
    )
    assert result["routing_skipped"] is True
    assert result["backend_started"] is False and tools._tasks == set()
    assert "selected workflow" in result["guidance"]


def test_route_tool_schema_exposes_confidence_and_actionability():
    from jarvis_rt import tool_schema

    schema = tool_schema(WorkspaceTools().route_request)
    properties = schema["parameters"]["properties"]
    assert properties["intent_confidence"]["type"] == "number"
    assert properties["actionable"]["type"] == "boolean"


def test_automatic_trigger_does_not_lower_decider_acceptance(monkeypatch):
    calls = []

    def decide(*args, **kwargs):
        calls.append(args)
        return answer(route="backend", p=0.55), None

    monkeypatch.setattr(mercury_decide, "decide", decide)
    result = request_router.automatic(
        "Work out what should handle this task", intent_confidence=0.5, actionable=True
    )
    assert len(calls) == 1
    assert result["routing_skipped"] is False
    assert result["route"] == "direct" and result["reason"] == "uncertain"


def test_automatic_routing_respects_existing_kill_switch(monkeypatch):
    monkeypatch.setenv("JARVIS_REQUEST_DECIDER", "0")
    monkeypatch.setattr(
        mercury_decide,
        "decide",
        lambda *_args, **_kwargs: pytest.fail("disabled route invoked decision API"),
    )
    result = request_router.automatic(
        "Research or write a plan", intent_confidence=0.5, actionable=True
    )
    assert result["routing_skipped"] is True and result["reason"] == "disabled"
