import json

from aware import diagnostics


def test_sample_reports_unavailable_separately_from_healthy(tmp_path):
    report = diagnostics.sample(
        now=1000,
        state_path=tmp_path / "diagnostics.json",
        probes={
            "calendar": lambda: {"status": "healthy", "detail": "connected"},
            "camera": lambda: {"status": "unavailable", "detail": "camera absent"},
            "whatsapp": lambda: {"status": "healthy", "detail": "connected"},
            "jarvis": lambda: {"status": "healthy", "detail": "running"},
        },
        network_connect=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()),
    )

    assert report["availability"]["calendar"]["status"] == "healthy"
    assert report["availability"]["camera"]["status"] == "unavailable"
    assert (
        report["availability"]["camera"]["status"]
        != report["availability"]["calendar"]["status"]
    )


def test_incidents_are_deduplicated_persisted_and_recovery_is_reported(tmp_path):
    path = tmp_path / "diagnostics.json"

    def fail():
        return {
            "status": "critical",
            "detail": "disk nearly full",
            "speech": "Disk space is critically low.",
        }

    def healthy():
        return {"status": "healthy", "detail": "disk recovered"}

    def service_healthy():
        return {"status": "healthy", "detail": "running"}

    quiet = dict.fromkeys(
        (
            "cpu",
            "temperature",
            "memory",
            "battery",
            "network",
            "jarvis",
            "whatsapp",
            "calendar",
            "camera",
            "input",
        ),
        service_healthy,
    )
    first = diagnostics.sample(
        now=1000, state_path=path, probes={**quiet, "disk": fail}
    )
    repeated = diagnostics.sample(
        now=1010, state_path=path, probes={**quiet, "disk": fail}
    )
    recovered = diagnostics.sample(
        now=1020, state_path=path, probes={**quiet, "disk": healthy}
    )

    assert len(first["incidents"]) == 1
    assert first["incidents"][0]["kind"] == "started"
    assert first["incidents"][0]["speech"] == "Disk space is critically low."
    assert repeated["incidents"] == []
    assert recovered["incidents"][0]["kind"] == "recovered"
    assert recovered["incidents"][0]["key"] == first["incidents"][0]["key"]
    assert json.loads(path.read_text())["active_incidents"] == {}


def test_slow_checks_are_throttled_and_cached(tmp_path):
    count = 0

    def probe():
        nonlocal count
        count += 1
        return {"status": "healthy", "detail": f"check {count}"}

    def healthy():
        return {"status": "healthy", "detail": "test stub"}

    path = tmp_path / "diagnostics.json"
    stubs = dict.fromkeys(
        (
            "cpu",
            "temperature",
            "memory",
            "disk",
            "battery",
            "jarvis",
            "whatsapp",
            "calendar",
            "camera",
            "input",
        ),
        healthy,
    )
    diagnostics.sample(
        now=1000,
        state_path=path,
        probes={**stubs, "network": probe},
        slow_probes={"network"},
        slow_interval_s=60,
    )
    cached = diagnostics.sample(
        now=1030,
        state_path=path,
        probes={**stubs, "network": probe},
        slow_probes={"network"},
        slow_interval_s=60,
    )

    assert count == 1
    assert cached["readings"]["network"]["detail"] == "check 1"
    assert cached["readings"]["network"]["cached"] is True


def test_service_incident_needs_consecutive_failures_and_resets_on_recovery(tmp_path):
    path = tmp_path / "diagnostics.json"

    def service_down():
        return {
            "status": "unavailable",
            "detail": "service offline",
            "speech": "Service offline.",
        }

    def service_ok():
        return {"status": "healthy", "detail": "service online"}

    stubs = dict.fromkeys(
        (
            "cpu",
            "temperature",
            "memory",
            "disk",
            "battery",
            "network",
            "jarvis",
            "whatsapp",
            "calendar",
            "camera",
            "input",
        ),
        service_ok,
    )

    first = diagnostics.sample(
        now=1000,
        state_path=path,
        probes={**stubs, "whatsapp": service_down},
        slow_probes=set(),
    )
    second = diagnostics.sample(
        now=1010,
        state_path=path,
        probes={**stubs, "whatsapp": service_down},
        slow_probes=set(),
    )
    healthy = diagnostics.sample(
        now=1020, state_path=path, probes=stubs, slow_probes=set()
    )

    assert first["incidents"] == []
    assert second["incidents"][0]["kind"] == "started"
    assert second["incidents"][0]["name"] == "whatsapp"
    assert healthy["incidents"][0]["kind"] == "recovered"


def test_cached_service_failure_does_not_count_as_a_second_failure(tmp_path):
    path = tmp_path / "diagnostics.json"
    calls = 0

    def service_down():
        nonlocal calls
        calls += 1
        return {"status": "unavailable", "detail": "service offline"}

    def healthy():
        return {"status": "healthy", "detail": "ok"}

    stubs = dict.fromkeys(
        (
            "cpu",
            "temperature",
            "memory",
            "disk",
            "battery",
            "network",
            "jarvis",
            "calendar",
            "camera",
            "input",
        ),
        healthy,
    )
    first = diagnostics.sample(
        now=1000,
        state_path=path,
        probes={**stubs, "whatsapp": service_down},
        slow_probes={"whatsapp"},
        slow_interval_s=60,
    )
    cached = diagnostics.sample(
        now=1030,
        state_path=path,
        probes={**stubs, "whatsapp": service_down},
        slow_probes={"whatsapp"},
        slow_interval_s=60,
    )
    repeated = diagnostics.sample(
        now=1061,
        state_path=path,
        probes={**stubs, "whatsapp": service_down},
        slow_probes={"whatsapp"},
        slow_interval_s=60,
    )

    assert calls == 2
    assert first["incidents"] == []
    assert cached["readings"]["whatsapp"]["cached"] is True
    assert cached["incidents"] == []
    assert repeated["incidents"][0]["kind"] == "started"


def test_critical_hardware_incident_is_immediate_and_escalation_speaks_once(tmp_path):
    path = tmp_path / "diagnostics.json"

    def healthy():
        return {"status": "healthy", "detail": "ok"}

    stubs = dict.fromkeys(
        (
            "cpu",
            "temperature",
            "memory",
            "disk",
            "battery",
            "network",
            "jarvis",
            "whatsapp",
            "calendar",
            "camera",
            "input",
        ),
        healthy,
    )

    warning = diagnostics.sample(
        now=1000,
        state_path=path,
        probes={
            **stubs,
            "disk": lambda: {"status": "degraded", "detail": "disk warning"},
        },
        slow_probes=set(),
    )
    escalated = diagnostics.sample(
        now=1010,
        state_path=path,
        probes={
            **stubs,
            "disk": lambda: {"status": "critical", "detail": "disk nearly full"},
        },
        slow_probes=set(),
    )
    repeated_critical = diagnostics.sample(
        now=1020,
        state_path=path,
        probes={
            **stubs,
            "disk": lambda: {"status": "critical", "detail": "disk still nearly full"},
        },
        slow_probes=set(),
    )

    assert warning["incidents"][0]["kind"] == "started"
    assert escalated["incidents"][0]["kind"] == "escalated"
    assert escalated["incidents"][0]["status"] == "critical"
    assert repeated_critical["incidents"] == []


def test_critical_hardware_problem_is_announced_on_first_sample(tmp_path):
    def healthy():
        return {"status": "healthy", "detail": "ok"}

    stubs = dict.fromkeys(
        (
            "cpu",
            "temperature",
            "memory",
            "disk",
            "battery",
            "network",
            "jarvis",
            "whatsapp",
            "calendar",
            "camera",
            "input",
        ),
        healthy,
    )
    report = diagnostics.sample(
        now=1000,
        state_path=tmp_path / "diagnostics.json",
        probes={
            **stubs,
            "memory": lambda: {
                "status": "critical",
                "detail": "memory pressure critical",
                "speech": "Available memory is critically low.",
            },
        },
        slow_probes=set(),
    )

    assert report["incidents"][0]["kind"] == "started"
    assert report["incidents"][0]["status"] == "critical"


def test_unavailable_unknown_reading_does_not_resolve_active_incident(tmp_path):
    path = tmp_path / "diagnostics.json"

    def healthy():
        return {"status": "healthy", "detail": "ok"}

    def down():
        return {
            "status": "unavailable",
            "detail": "service offline",
            "actionable": True,
        }

    def unknown():
        return {
            "status": "unavailable",
            "detail": "probe unsupported",
            "actionable": False,
        }

    stubs = dict.fromkeys(
        (
            "cpu",
            "temperature",
            "memory",
            "disk",
            "battery",
            "network",
            "jarvis",
            "calendar",
            "camera",
            "input",
        ),
        healthy,
    )

    diagnostics.sample(
        now=1000, state_path=path, probes={**stubs, "whatsapp": down}, slow_probes=set()
    )
    active = diagnostics.sample(
        now=1010, state_path=path, probes={**stubs, "whatsapp": down}, slow_probes=set()
    )
    unresolved = diagnostics.sample(
        now=1020,
        state_path=path,
        probes={**stubs, "whatsapp": unknown},
        slow_probes=set(),
    )

    assert active["active_incidents"]
    assert unresolved["incidents"] == []
    assert unresolved["active_incidents"]


def test_platform_readings_are_fail_soft_and_have_clear_statuses(tmp_path, monkeypatch):
    monkeypatch.setattr(
        diagnostics.os, "getloadavg", lambda: (_ for _ in ()).throw(OSError())
    )
    report = diagnostics.sample(
        now=1000,
        state_path=tmp_path / "diagnostics.json",
        proc_root=tmp_path / "proc",
        sys_root=tmp_path / "sys",
        root_path=tmp_path,
        probes={
            "jarvis": lambda: {"status": "healthy", "detail": "running"},
            "whatsapp": lambda: {"status": "unavailable", "actionable": False},
            "calendar": lambda: {"status": "unavailable", "actionable": False},
            "camera": lambda: {"status": "unavailable", "actionable": False},
            "input": lambda: {"status": "unavailable", "actionable": False},
        },
        network_connect=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()),
    )

    assert {"cpu", "temperature", "memory", "disk", "battery", "network"}.issubset(
        report["readings"]
    )
    assert report["readings"]["cpu"]["status"] == "unavailable"
    assert report["readings"]["battery"]["status"] == "unavailable"
    assert report["availability"]["whatsapp"]["status"] == "unavailable"
    assert report["availability"]["calendar"]["status"] == "unavailable"
    assert report["availability"]["camera"]["status"] == "unavailable"
    assert report["availability"]["input"]["status"] == "unavailable"
    assert report["availability"]["jarvis"]["status"] == "healthy"
