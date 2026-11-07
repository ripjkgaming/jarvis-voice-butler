"""Bridge request-ID forwarding with fake handlers; no server or live job."""

from types import SimpleNamespace

import pytest

import assigner_headless
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


def test_bridge_code_route_reaches_start_code_without_env(tmp_path, monkeypatch):
    """Default-on: the real start_code runs with JARVIS_ASSIGNER_ROUTING unset."""
    monkeypatch.delenv("JARVIS_ASSIGNER_ROUTING", raising=False)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(projects, "_announce", lambda _: None)
    monkeypatch.setattr(projects, "resolve_directory", lambda *a: (tmp_path, None))
    service = SimpleNamespace(
        jobs=assigner_headless.Jobs(tmp_path / "ledger"), kick=lambda: None
    )
    service.submit = lambda *a, **k: service.jobs.submit(*a, **k)
    monkeypatch.setattr(projects, "_assigner_service", lambda: service)
    body = {"kind": "code", "task": "return hello", "request_id": "coding-env-unset"}
    monkeypatch.setattr(bridge, "_read_json_body", lambda *a: body)
    monkeypatch.setattr(bridge, "_effective_guest", lambda _: False)
    monkeypatch.setattr(watch_mode, "guest_required", lambda: False)
    responses = []
    handler = SimpleNamespace(
        path="/projects",
        _authorized=lambda: True,
        _send=lambda *a: responses.append(a),
    )
    bridge._Handler.do_POST(handler)
    code, payload = responses[0][0], responses[0][1]
    assert code == 200
    assert payload["project"]["status"] != "blocked"
    assert service.jobs.get("coding-env-unset") is not None
