import io
import json
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from health_recorder import HealthRecorder
from health_recorder_bridge import _GATE, handle
from test_health_recorder import Clock, event


class Handler:
    def __init__(self, payload=None, token="fake-token", headers=None):
        raw = json.dumps(payload or {}).encode()
        self.token = token
        self.headers = {"Content-Length": str(len(raw)), **(headers or {})}
        self.rfile = io.BytesIO(raw)
        self.timeout = None
        self.connection = SimpleNamespace(
            settimeout=lambda value: setattr(self, "timeout", value)
        )
        self.result = None

    def _send(self, code, body):
        self.result = code, body


def request(path="/health-recorder/incident", method="GET", payload=None, **kwargs):
    handler = Handler(payload)
    recorder = kwargs.pop("recorder", HealthRecorder(enabled=True, clock=Clock()))
    assert handle(
        handler,
        method,
        urlparse(path),
        recorder=recorder,
        allowed=kwargs.pop("allowed", lambda: True),
        **kwargs,
    )
    return handler


@pytest.fixture(autouse=True)
def desktop_tools(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_HEALTH_TOOLS", "1")


def test_disabled_auth_owner_and_recheck(monkeypatch):
    handler = Handler(token="")
    handle(handler, "GET", urlparse("/health-recorder/incident"), allowed=lambda: True)
    assert handler.result[0] == 403
    assert request(allowed=lambda: False).result[0] == 403
    monkeypatch.setenv("JARVIS_HEALTH_TOOLS", "0")
    assert request(recorder=HealthRecorder()).result[0] == 503
    monkeypatch.setenv("JARVIS_HEALTH_TOOLS", "1")
    access = iter([True, False])
    assert request(allowed=lambda: next(access)).result[0] == 403


def test_numeric_event_report_and_clear():
    recorder = HealthRecorder(enabled=True, clock=Clock())
    assert request(
        "/health-recorder/events",
        "POST",
        {"version": 1, "events": [event()]},
        recorder=recorder,
    ).result == (200, {"ok": True, "accepted": 1})
    report = request(recorder=recorder).result[1]
    assert report["coverage"]["event_samples"] == 1
    assert (
        request("/health-recorder/clear", "POST", {}, recorder=recorder).result[0]
        == 200
    )
    assert not recorder._events


@pytest.mark.parametrize(
    "path",
    [
        "/health-recorder/incident?window_s=29",
        "/health-recorder/incident?window_s=30&window_s=60",
        "/health-recorder/incident?secret=canary",
        "/health-recorder/incident?window_s=",
        "/health-recorder/incident?a=1&b=2&c=3",
    ],
)
def test_query_bounds(path):
    assert request(path).result[0] == 400


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Length": "16385"},
        {"Content-Length": "-1"},
        {"Transfer-Encoding": "chunked"},
        {"Content-Length": "nonsense"},
        {"Content-Length": "100"},
    ],
)
def test_request_frame_bounds(headers):
    handler = Handler({}, headers=headers)
    handle(
        handler,
        "POST",
        urlparse("/health-recorder/events"),
        recorder=HealthRecorder(enabled=True),
        allowed=lambda: True,
    )
    assert handler.result[0] == 400


def test_bounded_concurrency_and_privacy():
    _GATE.acquire()
    try:
        assert request().result[0] == 429
    finally:
        _GATE.release()
    result = request(
        "/health-recorder/events",
        "POST",
        {"version": 1, "events": [event(error="SECRET_CANARY")]},
    )
    assert result.result[0] == 400
    assert "SECRET_CANARY" not in repr(result.result)


def test_ingestion_rate_limit_and_clear_baseline():
    clock = Clock()
    recorder = HealthRecorder(enabled=True, clock=clock)
    payload = {"version": 1, "events": [event()]}
    assert (
        request("/health-recorder/events", "POST", payload, recorder=recorder).result[0]
        == 200
    )
    assert (
        request("/health-recorder/events", "POST", payload, recorder=recorder).result[0]
        == 429
    )
    clock.now = 5
    assert (
        request("/health-recorder/events", "POST", payload, recorder=recorder).result[0]
        == 200
    )
    recorder.clear()
    assert recorder._last_ingest is None
