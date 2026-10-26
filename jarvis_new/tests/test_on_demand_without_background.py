import asyncio
from types import SimpleNamespace
from urllib.parse import urlparse

import build_monitor as bm
import health_recorder as health
import health_recorder_bridge as bridge
import health_recorder_client as client
import pytest


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_BUILD_MONITOR", "0")
    monkeypatch.setenv("JARVIS_HEALTH_RECORDER", "0")
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_BUILD_TOOLS", raising=False)
    monkeypatch.delenv("JARVIS_HEALTH_TOOLS", raising=False)
    monkeypatch.setattr(bm, "owner_allowed", lambda peer: peer == "127.0.0.1")
    return tmp_path


def kernel(name):
    return {
        "stat": "cpu 1 0 1 1 0 0 0 0\n",
        "meminfo": "MemTotal: 1024 kB\nMemAvailable: 512 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n",
    }.get(name, "some avg10=0 avg60=0 avg300=0 total=0\n")


def test_jobs_status_works_without_observer(desktop):
    assert not bm.enabled()
    assert bm.tools_enabled()
    code, result = bm.endpoint(
        "GET", "/build-monitor/latest", {}, None, authorized=True, peer="127.0.0.1"
    )
    assert code == 200 and result == {"ok": True, "job": None}
    assert not (desktop / "job_registry/build-monitor.owner.lock").exists()


def test_background_start_refuses_before_registry_or_thread(desktop, monkeypatch):
    monkeypatch.setattr(
        bm, "service", lambda: pytest.fail("no registry/observer activation")
    )
    with pytest.raises(bm.MonitorError, match="disabled"):
        bm.start_thread()


def test_jobs_tools_can_be_disabled_separately(desktop, monkeypatch):
    monkeypatch.setenv("JARVIS_BUILD_TOOLS", "0")
    assert not bm.tools_enabled()
    assert (
        bm.endpoint(
            "GET", "/build-monitor/latest", {}, None, authorized=True, peer="127.0.0.1"
        )[0]
        == 503
    )


def test_remote_and_unauth_status_stays_refused(desktop):
    assert (
        bm.endpoint(
            "GET", "/build-monitor/latest", {}, None, authorized=False, peer="127.0.0.1"
        )[0]
        == 403
    )
    assert (
        bm.endpoint(
            "GET", "/build-monitor/latest", {}, None, authorized=True, peer="192.0.2.1"
        )[0]
        == 403
    )


def test_snapshot_reads_only_kernel_once_no_history(monkeypatch):
    seen = []

    def reader(name):
        seen.append(name)
        return kernel(name)

    report = health.current_snapshot(reader=reader)
    assert seen == list(health.ProcReader.PATHS)
    assert report["mode"] == "current_snapshot"
    assert report["recording_enabled"] is False
    assert report["retained_history"] is False
    assert report["coverage"]["event_samples"] == 0
    assert report["kernel_snapshot"]["cpu_busy_pct"] is None
    assert report["kernel_snapshot"]["mem_available_bytes"] == 512 * 1024


def test_read_only_incident_works_but_event_ingest_stays_off(desktop, monkeypatch):
    recorder = health.HealthRecorder()
    monkeypatch.setattr(
        bridge, "current_snapshot", lambda: health.current_snapshot(reader=kernel)
    )
    handler = SimpleNamespace(
        token="synthetic", _send=lambda code, result: outputs.append((code, result))
    )
    outputs = []
    assert bridge.handle(
        handler,
        "GET",
        urlparse("/health-recorder/incident"),
        recorder=recorder,
        allowed=lambda: True,
    )
    assert outputs[-1][0] == 200 and outputs[-1][1]["recording_enabled"] is False
    assert recorder._worker is None and not recorder._events and not recorder._system
    assert bridge.handle(
        handler,
        "POST",
        urlparse("/health-recorder/events"),
        recorder=recorder,
        allowed=lambda: True,
    )
    assert outputs[-1][0] == 503


async def test_owner_tool_reports_snapshot_without_bridge_or_sender(
    desktop, monkeypatch
):
    monkeypatch.setattr(
        client, "current_snapshot", lambda: health.current_snapshot(reader=kernel)
    )
    monkeypatch.setattr(
        client, "BridgeClient", lambda: pytest.fail("no historical transport")
    )

    async def direct(fn, *args):
        return fn(*args)

    monkeypatch.setattr(asyncio, "to_thread", direct)
    report = await client.incident_report(allowed=lambda: True)
    assert "Current" in report["say"] and "off" in report["say"]
    session = SimpleNamespace()
    assert client.install_session_recorder(session) is None


async def test_snapshot_rechecks_owner_after_sampling(desktop, monkeypatch):
    accesses = iter([True, False])
    monkeypatch.setattr(
        client, "current_snapshot", lambda: health.current_snapshot(reader=kernel)
    )

    async def direct(fn, *args):
        return fn(*args)

    monkeypatch.setattr(asyncio, "to_thread", direct)
    assert (
        "unavailable"
        in (await client.incident_report(allowed=lambda: next(accesses)))["say"]
    )


async def test_diagnostics_disable_does_not_sample(desktop, monkeypatch):
    monkeypatch.setenv("JARVIS_HEALTH_TOOLS", "0")
    monkeypatch.setenv("JARVIS_HEALTH_RECORDER", "1")
    monkeypatch.setattr(client, "current_snapshot", lambda: pytest.fail("no sample"))
    monkeypatch.setattr(
        client, "BridgeClient", lambda: pytest.fail("no report transport")
    )
    assert "disabled" in (await client.incident_report(allowed=lambda: True))["say"]
