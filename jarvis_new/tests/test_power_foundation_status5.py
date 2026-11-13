import json

import agent_tasks
from power_foundation.integration import status
from power_foundation.operations import OutboundLedger, fingerprint


def test_status_redacted_and_explicit_uncertainty(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_POWER_FOUNDATION", "1")
    monkeypatch.setenv("JARVIS_POWER_TASK_CHECKPOINTS", "1")
    monkeypatch.setenv("JARVIS_POWER_APPROVAL_TICKETS", "1")
    record = {
        "id": "task-redacted",
        "status": "recovery_required",
        "revision": 8,
        "goal": "SECRET",
        "created": 1,
        "steps": [{"kind": "research", "status": "running", "output": "PRIVATE"}],
        "recovery": {"review_id": "HIDDEN"},
    }
    assert agent_tasks.save(record)
    ledger = OutboundLedger(tmp_path / "foundation" / "outbound.sqlite3")
    assert ledger.claim("request-123456", "gmail_send", fingerprint({"body": "SECRET"}))
    report = status()
    assert report["safety"]["recovery_required"] == 1
    assert report["outbound"]["uncertain"] == 1
    assert report["native_tasks"]["items"][0]["checkpoints"] >= 1
    assert report["mcp"]["status"] == "not_inspected"
    assert not any(x in json.dumps(report) for x in ("SECRET", "PRIVATE", "HIDDEN"))
