import json
from types import SimpleNamespace

import pytest

from health_recorder import HealthRecorder, KernelSampler, validate_event
from latency import install_session_timing


def event(at=0, **changes):
    return {
        "at": at,
        "metric": "vad_end_to_agent_speaking_s",
        "value": 2,
        "scope": "state_events_not_acoustic_e2e",
        "error": None,
        **changes,
    }


class Clock:
    now = 0

    def __call__(self):
        return self.now


def kernel(cpu="cpu 10 0 0 90 0 0 0 0 0 0", pressure=0):
    def read(path):
        if path == "stat":
            return cpu
        if path == "meminfo":
            return "MemTotal: 1000 kB\nMemAvailable: 500 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"
        return f"some avg10=0 avg60=0 avg300=0 total={pressure}\n"

    return read


def test_disabled_does_no_io_or_worker_and_clear():
    def forbidden(_):
        raise AssertionError("kernel I/O")

    recorder = HealthRecorder(reader=forbidden)
    recorder.start()
    assert recorder._worker is None
    assert recorder.sample() is False
    assert recorder.record_batch([event()]) == 0
    assert recorder.report()["recording_enabled"] is False
    recorder.close()
    assert not recorder._system and not recorder._events


def test_cpu_memory_pressure_reset_and_gap():
    sampler = KernelSampler(kernel())
    first = sampler.sample(0)
    assert first["cpu_busy_pct"] is None
    assert first["mem_available_bytes"] == 500 * 1024
    # Guest counters are not counted twice, iowait is separate.
    sampler.reader = kernel("cpu 100 0 0 95 5 0 0 0 999 999", 500000)
    row = sampler.sample(5)
    assert row["cpu_busy_pct"] == 90
    assert row["iowait_pct"] == 5
    assert row["memory_psi_some_pct"] == 10
    sampler.reader = kernel()
    assert sampler.sample(10)["cpu_busy_pct"] is None
    assert sampler.sample(50)["cpu_psi_some_pct"] is None


def test_unavailable_kernel_never_becomes_zero():
    def unavailable(_):
        raise PermissionError("private canary content")

    assert all(x is None for x in KernelSampler(unavailable).sample(0).values())


@pytest.mark.parametrize(
    "changes",
    [
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": True},
        {"value": -1},
        {"value": "personal message"},
        {"scope": "personal message"},
        {"metric": "private tool name"},
        {"error": "private stack trace"},
        {"content": "secret"},
        {"at": -1},
        {"at": 2},
        {"scope": []},
    ],
)
def test_strict_privacy_validation(changes):
    with pytest.raises(ValueError):
        validate_event(event(**changes), 0)


def test_atomic_invalid_batch_and_event_ring_bounds():
    clock = Clock()
    recorder = HealthRecorder(enabled=True, clock=clock, reader=kernel())
    with pytest.raises(ValueError):
        recorder.record_batch([event(), event(error="SECRET_CANARY")])
    assert not recorder._events
    for _ in range(20):
        recorder.record_batch([event()] * 32)
    report = recorder.report()
    assert report["coverage"]["event_samples"] == 512
    assert report["coverage"]["dropped_events"] == 128
    assert "SECRET_CANARY" not in json.dumps(report)
    clock.now = 1801
    assert recorder.report()["coverage"]["event_samples"] == 0


def test_system_bounds_sample_cadence_and_clear_reset():
    clock = Clock()
    recorder = HealthRecorder(enabled=True, clock=clock, reader=kernel())
    for index in range(400):
        clock.now = index * 5
        assert recorder.sample()
        assert not recorder.sample()
    assert len(recorder._system) == 360
    recorder.clear()
    assert recorder._sampler.cpu is None
    assert recorder.report()["coverage"]["system_samples"] == 0


def test_conservative_incident_and_sampling_gaps():
    clock = Clock()
    recorder = HealthRecorder(enabled=True, clock=clock)
    recorder._system.extend((at, {"cpu_busy_pct": 95}) for at in (0, 5, 10))
    clock.now = 10
    report = recorder.report()
    assert "Sustained high host CPU" in report["say"]
    assert "cause" in report["say"]
    assert "No process or decoder attribution." in report["limitations"]
    recorder.clear()
    recorder._system.extend((at, {"cpu_busy_pct": 95}) for at in (0, 5, 100))
    clock.now = 100
    assert not recorder.report()["evidence"]
    recorder.record_batch([event(100, error="quota")])
    assert "quota" in recorder.report()["say"]


@pytest.mark.parametrize("window", [True, 29, 1801, 30.0, "300"])
def test_report_window_validation(window):
    with pytest.raises(ValueError):
        HealthRecorder().report(window)


class Session:
    def __init__(self):
        self.handlers = {}

    def on(self, name, callback):
        self.handlers.setdefault(name, []).append(callback)


def test_structured_latency_sink_content_free_and_idempotent():
    session, logs, numeric = Session(), [], []
    observer = install_session_timing(
        session,
        lambda *args: logs.append(args),
        sink=lambda *args: numeric.append(args),
    )
    assert (
        install_session_timing(session, lambda *args: None, sink=lambda *args: None)
        is observer
    )
    observer.user_state_changed("speaking", now=1)
    observer.user_state_changed("listening", now=2)
    observer.agent_state_changed("speaking", now=4)
    assert numeric == [
        ("vad_end_to_agent_speaking_s", 2, "state_events_not_acoustic_e2e")
    ]
    metric = SimpleNamespace(
        type="realtime_model_metrics", ttft=0.2, text="SECRET_CANARY"
    )
    session.handlers["metrics_collected"][0](SimpleNamespace(metrics=metric))
    assert numeric[-1] == (
        "provider_generation_to_first_audio_s",
        0.2,
        "provider_generation_not_user_e2e",
    )
    assert "SECRET_CANARY" not in repr(numeric)
    assert len(session.handlers["metrics_collected"]) == 1


def test_broken_sink_never_breaks_observer():
    def broken(*args):
        raise RuntimeError("fail")

    observer = install_session_timing(Session(), lambda *args: None, sink=broken)
    observer.user_state_changed("speaking", now=1)
    observer.user_state_changed("listening", now=2)
    assert observer.agent_state_changed("speaking", now=4) is not None


def test_wrong_scope_rejected_and_memory_pressure_reported():
    with pytest.raises(ValueError):
        validate_event(event(scope="request_to_first_audio"), 0)
    clock = Clock()
    recorder = HealthRecorder(enabled=True, clock=clock)
    recorder._system.extend(
        (at, {"mem_total_bytes": 1000, "mem_available_bytes": 10}) for at in (0, 5, 10)
    )
    clock.now = 10
    assert "persistently low" in recorder.report()["say"]
