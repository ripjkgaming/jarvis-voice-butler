"""Coordinator behavior; no desktop, network, or calendar mutations."""

import threading
import time

import pytest

from aware import state


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    state.update(enabled=True)


def test_failure_does_not_block_other_components():
    from aware.coordinator import Coordinator

    def broken():
        raise RuntimeError("offline")

    c = Coordinator(
        {
            "whatsapp": broken,
            "diagnostics": lambda: {"cpu": 4},
            "screen": lambda: {"outcome": "skip", "reason": "idle"},
        },
        timeouts={"whatsapp": 0.1, "diagnostics": 0.1, "screen": 0.1},
    )
    result = c.poll()
    assert result["components"]["whatsapp"]["status"] == "error"
    assert result["components"]["diagnostics"]["status"] == "ok"
    assert result["components"]["screen"]["result"]["reason"] == "idle"


def test_scan_timeout_is_single_flight_and_eventually_collected():
    from aware.coordinator import Coordinator

    release = threading.Event()
    calls = []

    def scan():
        calls.append(1)
        release.wait(1)
        return {"chats": [1]}

    c = Coordinator({"whatsapp": scan}, timeouts={"whatsapp": 0.01})
    try:
        assert c.poll()["components"]["whatsapp"]["status"] == "running"
        assert c.poll()["components"]["whatsapp"]["status"] == "running"
        assert calls == [1]
        release.set()
        for _ in range(100):
            if c.jobs["whatsapp"].done.is_set():
                break
            time.sleep(0.001)
        assert c.poll()["components"]["whatsapp"]["result"] == {"chats": [1]}
        assert calls == [1]
    finally:
        release.set()


@pytest.mark.parametrize(
    "updates", [{"enabled": False}, {"paused_until": time.time() + 300}]
)
def test_global_controls_suppress_all_components(updates):
    from aware.coordinator import Coordinator

    calls = []
    c = Coordinator({"whatsapp": lambda: calls.append(1)})
    state.update(**updates)
    assert c.poll()["outcome"] == "skip"
    assert calls == []


def test_timeouts_are_independent_and_parallel():
    from aware.coordinator import Coordinator

    release = threading.Event()
    c = Coordinator(
        {
            "whatsapp": lambda: release.wait(1),
            "screen": lambda: release.wait(1),
            "diagnostics": lambda: 42,
        },
        timeouts={"whatsapp": 0.03, "screen": 0.03, "diagnostics": 0.03},
    )
    try:
        start = time.monotonic()
        result = c.poll()
        assert time.monotonic() - start < 0.15
        assert result["components"]["diagnostics"]["result"] == 42
        assert result["components"]["screen"]["status"] == "running"
    finally:
        release.set()


def test_sidecar_coordinator_runs_before_screen_guards(monkeypatch):
    from aware.sidecar import Runner

    calls = []
    runner = Runner()
    runner.coordinator = type(
        "FakeCoordinator",
        (),
        {"poll": lambda self: calls.append("poll") or {"outcome": "poll"}},
    )()
    runner.sched.blocked_until = time.time() + 500
    runner.sched.block_reason = "quota"
    assert runner.tick()["outcome"] == "poll"
    assert calls == ["poll"]
    state.update(paused_until=time.time() + 300)
    assert runner.tick()["outcome"] == "skip"
    assert calls == ["poll"]


def test_enable_discloses_read_receipts_and_exercise_mode():
    from aware.control import handle

    line = handle("on")
    assert "marks" in line.lower() and "read" in line.lower()
    assert "observation" in line.lower()


def test_status_reports_poll_components_without_message_text():
    from aware.coordinator import Coordinator
    from aware.sidecar import status

    Coordinator({"whatsapp": lambda: {"messages": [{"text": "secret"}]}}).poll()
    report = status()
    assert report["poll"]["components"]["whatsapp"]["status"] == "ok"
    assert "secret" not in str(report)
    assert report["exercise_mode"] == "observation_only"


def test_poll_audit_is_not_a_screen_observation():
    from aware import log
    from aware.coordinator import Coordinator

    Coordinator({"diagnostics": lambda: {}}).poll()
    assert log.recent(outcome="seen") == []
    assert log.recent(outcome="poll")[0]["event"] == "poll"


def test_live_components_wire_activity_and_independent_consumers(monkeypatch):
    import sys
    from types import SimpleNamespace

    from aware.coordinator import live

    called = []
    monkeypatch.setitem(
        sys.modules, "aware.whatsapp_scan", SimpleNamespace(scan=lambda: None)
    )
    monkeypatch.setitem(
        sys.modules,
        "aware.diagnostics",
        SimpleNamespace(sample=lambda: {"incidents": []}),
    )
    monkeypatch.setitem(
        sys.modules,
        "aware.calendar_stream",
        SimpleNamespace(process=lambda: called.append("calendar") or {"staged": 1}),
    )
    monkeypatch.setitem(
        sys.modules,
        "aware.expansion_memory",
        SimpleNamespace(
            process_pending=lambda: called.append("memory") or {},
            retry_pending=lambda: 0,
            status=lambda: {"retries": 0},
        ),
    )
    c = live(lambda: {"outcome": "skip", "reason": "idle"})
    result = c.poll()
    assert result["components"]["whatsapp"]["status"] == "error"
    assert result["components"]["calendar"]["result"]["staged"] == 1
    assert (
        result["components"]["screen"]["result"]["exercise"]["mode"]
        == "observation_only"
    )
    assert set(called) == {"calendar", "memory"}


def test_whatsapp_status_reports_each_chat_outcome_without_identifiers():
    from aware.coordinator import Coordinator, status

    Coordinator(
        {
            "whatsapp": lambda: {
                "outcome": "partial",
                "selected": 2,
                "chats": [
                    {
                        "name": "Private Person",
                        "chat_id": "secret-jid",
                        "status": "complete",
                        "messages": 8,
                    },
                    {"status": "partial", "messages": 3},
                ],
            }
        }
    ).poll()
    report = status()["components"]["whatsapp"]
    assert report["outcome"] == "partial"
    assert report["selected"] == 2
    assert report["chats"] == [
        {"position": 1, "status": "complete", "messages": 8},
        {"position": 2, "status": "partial", "messages": 3},
    ]
    assert "Private Person" not in str(report) and "secret-jid" not in str(report)


def test_voice_status_explains_scan_coverage_and_calendar_progress():
    from aware.control import handle
    from aware.coordinator import Coordinator

    Coordinator(
        {
            "whatsapp": lambda: {
                "selected": 2,
                "chats": [
                    {"status": "complete", "messages": 4},
                    {"status": "partial", "messages": 1},
                ],
            },
            "calendar": lambda: {"staged": 5},
        }
    ).poll()
    line = handle("status").lower()
    assert "1 of 2" in line
    assert "partial" in line
    assert "5" in line and "calendar" in line


def test_status_includes_durable_scan_progress_during_long_poll():
    import json

    from aware.coordinator import status

    state.write_private(
        state.aware_dir() / "whatsapp-scan.json",
        json.dumps(
            {
                "scan_id": "scan1",
                "started_at": 1000,
                "selected": 2,
                "chats": [
                    {"slot": 1, "status": "complete", "messages": 8, "name": "private"},
                    {"slot": 2, "status": "pending", "messages": 0},
                ],
            }
        ),
    )
    receipt = status()["whatsapp_scan"]
    assert receipt["selected"] == 2
    assert receipt["chats"][1]["status"] == "pending"
    assert "private" not in str(receipt)


def test_component_deadline_is_recorded_even_when_another_job_waits_longer():
    from aware.coordinator import Coordinator

    def slow_diagnostics():
        time.sleep(0.025)
        return {"readings": {}}

    def slow_scan():
        time.sleep(0.04)
        return {"chats": []}

    result = Coordinator(
        {"whatsapp": slow_scan, "diagnostics": slow_diagnostics},
        timeouts={"whatsapp": 0.1, "diagnostics": 0.005},
    ).poll()
    diagnostics = result["components"]["diagnostics"]
    assert diagnostics["deadline_exceeded"] is True
    assert diagnostics["status"] == "ok"
