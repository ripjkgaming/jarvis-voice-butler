"""Foundation contracts: offline tests, no live GUI/send/model calls."""

import json

import pytest

from power_foundation.connectors import ConnectorRegistry, basic_connectors
from power_foundation.engine import FoundationStore
from power_foundation.workflow import ProposalPool, StepPlan


def test_safe_registry_read_probe_and_no_action(tmp_path):
    db = FoundationStore(tmp_path / "foundation.sqlite3")
    reg = ConnectorRegistry(db, enabled=True)
    reg.register("desktop", lambda: {"ready": True, "displays": 2})
    assert reg.probe("desktop")["ready"] is True
    assert reg.invoke_read("desktop")["displays"] == 2
    with pytest.raises(PermissionError):
        reg.execute_action("desktop", "click", {"x": 1, "y": 2})
    assert db.events(limit=10)[-1]["kind"] == "action_blocked"


def test_registry_off_by_default(tmp_path):
    reg = ConnectorRegistry(FoundationStore(tmp_path / "x.db"))
    reg.register("test", lambda: {"ready": True})
    with pytest.raises(PermissionError):
        reg.invoke_read("test")
    assert reg.probe("test")["ready"] is True


def test_connector_failures_are_reported_not_hidden(tmp_path):
    reg = ConnectorRegistry(FoundationStore(tmp_path / "x.db"), enabled=True)

    def bad():
        raise RuntimeError("secret: do not disclose")

    reg.register("broken", bad)
    result = reg.probe("broken")
    assert result["ready"] is False
    assert result["status"] == "error"
    assert "secret" not in json.dumps(result)


def test_connectors_are_registered_without_side_effects(tmp_path):
    reg = basic_connectors(FoundationStore(tmp_path / "x.db"), enabled=False)
    keys = set(reg.names)
    assert {
        "desktop",
        "whatsie",
        "browser",
        "mobile",
        "memory",
        "tasks",
        "voice",
        "mcp",
        "workers",
        "downloads",
    } <= keys


def test_tasks_idempotency_persistence_and_safe_recovery(tmp_path):
    path = tmp_path / "x.db"
    db = FoundationStore(path)
    a = db.create_task("Make a draft", ["read", "write"], key="dedupe")
    b = db.create_task("Make a draft", ["read", "write"], key="dedupe")
    assert a["id"] == b["id"]
    db.transition(a["id"], "running")
    reopened = FoundationStore(path)
    assert reopened.get_task(a["id"])["status"] == "running"
    assert reopened.pause_interrupted() == 1
    assert reopened.get_task(a["id"])["status"] == "paused"
    assert reopened.pause_interrupted() == 0


def test_task_state_machine_rejects_invalid_progress(tmp_path):
    db = FoundationStore(tmp_path / "x.db")
    task = db.create_task("Read research", ["read"])
    with pytest.raises(ValueError):
        db.transition(task["id"], "done")
    db.transition(task["id"], "running")
    db.transition(task["id"], "done")
    with pytest.raises(ValueError):
        db.transition(task["id"], "running")


def test_events_do_not_store_inputs_of_blocked_actions(tmp_path):
    db = FoundationStore(tmp_path / "x.db")
    reg = ConnectorRegistry(db, enabled=True)
    reg.register("whatsie", lambda: {"ready": True})
    with pytest.raises(PermissionError):
        reg.execute_action("whatsie", "send", {"password": "supersecret"})
    assert "supersecret" not in json.dumps(db.events())


def test_evidence_requires_source_and_is_searchable(tmp_path):
    db = FoundationStore(tmp_path / "x.db")
    with pytest.raises(ValueError):
        db.remember("fact", "")
    evidence = db.remember("a project note about windows", "file:project.md")
    hits = db.search_evidence("windows")
    assert hits[0]["id"] == evidence["id"]
    assert hits[0]["source"] == "file:project.md"


def test_evidence_confidence_is_not_invented(tmp_path):
    db = FoundationStore(tmp_path / "x.db")
    entry = db.remember("some text", "tool:observer")
    assert "confidence" not in entry
    assert entry["observed_at"]


def test_plan_validation_and_no_automatic_execution():
    p = StepPlan.build(
        "Get image to Whatsie",
        [
            {"name": "locate image", "effect": "read"},
            {"name": "send image", "effect": "external"},
        ],
    )
    assert p.next_step()["name"] == "locate image"
    assert p.steps[1]["requires_approval"] is True
    with pytest.raises(ValueError):
        StepPlan.build("unsafe", [{"name": "run", "effect": "arbitrary"}])


def test_proposal_workers_only_return_proposals():
    pool = ProposalPool(max_workers=2)
    result = pool.gather(
        {"a": lambda: {"suggestion": "read"}, "b": lambda: {"suggestion": "draft"}}
    )
    assert list(result) == ["a", "b"]
    assert result["a"]["suggestion"] == "read"


def test_worker_failures_dont_execute_or_crash_batch():
    out = ProposalPool(max_workers=2).gather(
        {
            "broken": lambda: 1 / 0,
            "ok": lambda: {"status": "proposed"},
        }
    )
    assert out["broken"]["status"] == "error"
    assert out["ok"]["status"] == "proposed"


def test_snapshot_contains_tasks_events_connectors(tmp_path):
    from power_foundation.workspace import snapshot

    db = FoundationStore(tmp_path / "x.db")
    reg = ConnectorRegistry(db)
    reg.register("voice", lambda: {"ready": False, "status": "unavailable"})
    db.create_task("Check state", ["read"])
    state = snapshot(db, reg)
    assert len(state["tasks"]) == 1
    assert "voice" in state["connectors"]
    assert state["connectors"]["voice"]["ready"] is False


def test_policy_requires_consent_and_exposes_no_raw_arguments():
    from power_foundation.policy import preview_action

    proposal = preview_action("whatsie", "send_image", "external", {"token": "secret"})
    assert proposal["requires_approval"] is True
    assert proposal["executable"] is False
    assert "secret" not in json.dumps(proposal)
    assert proposal["effects"] == "external"


def test_policy_previews_every_effect_without_auto_authorizing():
    from power_foundation.policy import preview_action

    for effect in ("read", "draft", "external", "system"):
        proposal = preview_action("desktop", "action", effect, {})
        assert proposal["executable"] is False
    with pytest.raises(ValueError):
        preview_action("desktop", "action", "unsafe_effect", {})


def test_verification_must_have_observation_and_source():
    from power_foundation.verification import result_receipt

    no_proof = result_receipt("window_move", "attempted", None)
    assert no_proof["verified"] is False
    assert no_proof["status"] == "unverified"
    proof = result_receipt("window_move", "observed", "screen:after")
    assert proof["verified"] is True
    assert proof["evidence"] == "screen:after"
    with pytest.raises(ValueError):
        result_receipt("window_move", "made_up", "screen:after")


def test_catalog_cannot_execute_unreviewed_mcp_tool():
    from power_foundation.tool_catalog import ToolCatalog, starter_catalog

    catalog = starter_catalog()
    assert len(catalog.describe()) >= 10
    assert all(item["executable"] is False for item in catalog.describe())
    with pytest.raises(PermissionError):
        catalog.execute("desktop:screen_observation", {})
    with pytest.raises(TypeError):
        ToolCatalog().add({"connector": "fake", "name": "tool"})


def test_live_probes_are_read_only_and_separate_from_imports(monkeypatch, tmp_path):
    from power_foundation import connectors

    reg = connectors.basic_connectors(FoundationStore(tmp_path / "db"), live=True)
    assert "installed" in reg.probe("whatsie")
    assert "ready" in reg.probe("desktop")
    static = connectors.basic_connectors(FoundationStore(tmp_path / "db"), live=False)
    assert static.probe("desktop")["status"] == "scaffold_only"
