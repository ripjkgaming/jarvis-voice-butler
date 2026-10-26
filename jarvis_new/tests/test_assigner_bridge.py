"""Bridge request-ID forwarding with fake handlers; no server or live job."""

from types import SimpleNamespace

import pytest

import bridge
import projects
import watch_mode


@pytest.mark.parametrize(
    "authorized,guest,code",
    [(False, False, 401), (True, True, 403), (True, False, 200)],
)
def test_bridge_preserves_auth_guest_gate_and_forwards_request_id(
    monkeypatch, authorized, guest, code
):
    calls, responses = [], []
    body = {
        "kind": "code",
        "task": "return hello",
        "directory": "/task",
        "variant": "high",
        "request_id": "coding-fixed",
    }
    monkeypatch.setattr(bridge, "_read_json_body", lambda *a: body)
    monkeypatch.setattr(bridge, "_effective_guest", lambda _: guest)
    monkeypatch.setattr(watch_mode, "guest_required", lambda: guest)

    def start(task, **kwargs):
        calls.append((task, kwargs))
        return {"id": "fake", "status": "pending"}

    monkeypatch.setattr(projects, "start_code", start)
    handler = SimpleNamespace(
        path="/projects",
        _authorized=lambda: authorized,
        _send=lambda *a: responses.append(a),
    )
    bridge._Handler.do_POST(handler)
    assert responses[0][0] == code
    if code == 200:
        assert calls == [
            (
                "return hello",
                {"directory": "/task", "variant": "high", "request_id": "coding-fixed"},
            )
        ]
    else:
        assert not calls
