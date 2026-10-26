import asyncio
from types import SimpleNamespace

import pytest
from test_health_recorder import Clock, Session

from health_recorder_client import (
    BridgeClient,
    HealthEventSender,
    incident_report,
    install_session_recorder,
)


class Client:
    def __init__(self, unavailable=False):
        self.calls = []
        self.unavailable = unavailable

    def request(self, route, payload=None):
        self.calls.append((route, payload))
        if self.unavailable:
            raise TimeoutError("SECRET_CANARY")
        return {
            "ok": True,
            "accepted": len(payload["events"]) if payload else 0,
            "say": "Known numeric report",
            "text": "Known numeric report",
        }


def test_sender_disabled_no_client_call_no_thread():
    client = Client()
    sender = HealthEventSender(client=client)
    sender.start()
    assert sender._worker is None
    assert not sender.offer("tool_duration_s", 1, "tool_call")
    assert not sender.flush()
    sender.close()
    assert not client.calls


def test_sender_bounds_backpressure_batch_rate_outage_cleanup():
    clock, client = Clock(), Client(unavailable=True)
    sender = HealthEventSender(enabled=True, client=client, clock=clock)
    for _ in range(128):
        assert sender.offer("tool_duration_s", 1, "tool_call")
    assert not sender.offer("tool_duration_s", 1, "tool_call")
    assert sender.dropped == 1
    assert not sender.flush()
    assert len(client.calls[0][1]["events"]) == 32
    assert sender.dropped == 33
    assert len(sender._queue) == 96
    sender.flush()
    assert len(client.calls) == 1
    sender.close()
    assert not sender._queue
    assert len(client.calls) == 1


def test_sender_invalid_event_and_lock_contention_are_nonblocking():
    sender = HealthEventSender(enabled=True, clock=Clock(), client=Client())
    assert not sender.offer("SECRET_CANARY", 1, "tool_call")
    sender._lock.acquire()
    try:
        assert not sender.offer("tool_duration_s", 1, "tool_call")
    finally:
        sender._lock.release()
    assert not sender._queue


@pytest.mark.parametrize(
    "env", [{"JARVIS_BRIDGE_BIND": "10.0.0.1"}, {"JARVIS_BRIDGE_PORT": "0"}]
)
def test_nonlocal_and_invalid_bridge_rejected(env):
    with pytest.raises(ValueError):
        BridgeClient(environ=env)


def test_client_missing_credentials_does_not_open():
    def forbidden(*args, **kwargs):
        raise AssertionError("network")

    client = BridgeClient(opener=SimpleNamespace(open=forbidden), environ={})
    with pytest.raises(RuntimeError):
        client.request("/health-recorder/incident")


def test_session_private_guard_rechecked_callbacks_cleanup():
    session, client, clock = Session(), Client(), Clock()
    sender = HealthEventSender(enabled=True, clock=clock, client=client)
    access = [True]
    health = install_session_recorder(session, sender=sender, allowed=lambda: access[0])
    assert install_session_recorder(session, sender=sender) is health
    health[1]("tool_duration_s", 1, "tool_call")
    access[0] = False
    health[1]("tool_duration_s", 1, "tool_call")
    session.handlers["error"][0](SimpleNamespace(error=TimeoutError("SECRET_CANARY")))
    assert len(sender._queue) == 1
    access[0] = True
    session.handlers["error"][0](SimpleNamespace(error=TimeoutError("SECRET_CANARY")))
    assert sender._queue[-1]["error"] == "timeout"
    assert "SECRET_CANARY" not in repr(sender._queue)
    session.handlers["close"][0](None)
    assert not sender.enabled and not sender._queue


def test_session_install_default_off(monkeypatch):
    monkeypatch.delenv("JARVIS_HEALTH_RECORDER", raising=False)
    session = Session()
    assert install_session_recorder(session) is None
    assert not session.handlers


@pytest.mark.asyncio
async def test_report_access_disabled_unavailable_and_revocation(monkeypatch):
    monkeypatch.delenv("JARVIS_HEALTH_RECORDER", raising=False)
    monkeypatch.setenv("JARVIS_HEALTH_TOOLS", "0")
    assert "disabled" in (await incident_report(allowed=lambda: True))["say"]
    monkeypatch.setenv("JARVIS_HEALTH_TOOLS", "1")
    client = Client()
    assert (
        "session"
        in (await incident_report(client=client, allowed=lambda: False))["say"]
    )
    assert not client.calls

    # Mock off-thread I/O deterministically: no executor/network in offline tests.
    async def fake_thread(func, *args):
        return func(*args)

    monkeypatch.setattr(asyncio, "to_thread", fake_thread)
    assert (
        "Known numeric"
        in (await incident_report(client=client, allowed=lambda: True))["say"]
    )
    unavailable = await incident_report(client=Client(True), allowed=lambda: True)
    assert "unavailable" in unavailable["say"] and "SECRET_CANARY" not in repr(
        unavailable
    )
    access = iter([True, False])
    assert (
        "unavailable"
        in (await incident_report(client=client, allowed=lambda: next(access)))["say"]
    )


def test_error_classification_does_not_stringify_content():
    from health_recorder_client import classify_error

    class SecretError(Exception):
        status_code = 429

        def __str__(self):
            raise AssertionError("content capture")

    assert classify_error(SecretError()) == "quota"
    assert classify_error(asyncio.CancelledError()) == "cancelled"
    assert classify_error(RuntimeError("SECRET_CANARY"), "tool_failed") == "tool_failed"


def test_bad_provider_error_property_is_contained():
    from health_recorder_client import classify_error

    class BadProvider:
        @property
        def status_code(self):
            raise RuntimeError("SECRET_CANARY")

    assert classify_error(BadProvider()) == "session_error"
    session = Session()
    sender = HealthEventSender(enabled=True, clock=Clock())
    install_session_recorder(session, sender=sender, allowed=lambda: True)

    class BadEvent:
        @property
        def error(self):
            raise RuntimeError("SECRET_CANARY")

    session.handlers["error"][0](BadEvent())
    assert not sender._queue
