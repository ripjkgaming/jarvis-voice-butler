import io
import json
import os

import build_monitor as bm
import jobs
import pytest


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_BUILD_MONITOR", "1")
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    import phone_session
    import watch_mode

    monkeypatch.setattr(phone_session, "private", lambda: False)
    monkeypatch.setattr(phone_session, "guest_required", lambda: False)
    monkeypatch.setattr(phone_session, "has_private_calls", lambda: False)
    monkeypatch.setattr(watch_mode, "guest_required", lambda: False)
    mon = bm.Monitor(
        jobs.Registry(tmp_path / "jobs"),
        proc_reader=lambda pid: bm.Identity("boot", os.getuid(), pid, 10, "S"),
    )
    monkeypatch.setattr(bm, "service", lambda: mon)
    return mon


def call(method, path, body=None, query=None, auth=True, peer="127.0.0.1"):
    return bm.endpoint(method, path, query or {}, body, authorized=auth, peer=peer)


def test_routes_register_replay_stop_and_tail(setup):
    body = {"request_id": "r", "pid": 42, "label": "Build"}
    code, result = call("POST", "/build-monitor/register", body)
    assert code == 201
    job_id = result["job"]["id"]
    assert call("POST", "/build-monitor/register", body)[0] == 200
    assert call("GET", "/build-monitor/latest")[1]["job"]["id"] == job_id
    assert call("GET", "/build-monitor/" + job_id)[1]["job"]["status"] == "running"
    assert not call("GET", "/build-monitor/" + job_id + "/tail")[1]["available"]
    code, result = call("POST", "/build-monitor/" + job_id + "/stop", {})
    assert code == 200 and result["job"]["outcome"] == "monitoring_stopped"
    assert result["job"]["work_stopped"] is False


@pytest.mark.parametrize("which", ["private", "guest_required", "has_private_calls"])
def test_private_guest_and_revocation_refused(setup, monkeypatch, which):
    import phone_session

    monkeypatch.setattr(phone_session, which, lambda: True)
    assert call("GET", "/build-monitor/latest")[0] == 403
    assert (
        call(
            "POST",
            "/build-monitor/register",
            {"request_id": "r", "pid": 42, "label": "build"},
        )[0]
        == 403
    )
    assert setup.latest() is None


def test_auth_remote_disabled_and_unknown(setup, monkeypatch):
    assert call("GET", "/build-monitor/latest", auth=False)[0] == 403
    assert call("GET", "/build-monitor/latest", peer="192.0.2.1")[0] == 403
    assert call("GET", "/build-monitor/latest", peer="invalid")[0] == 403
    assert call("GET", "/build-monitor/unknown")[0] == 404
    assert (
        call(
            "POST",
            "/build-monitor/register",
            {"request_id": "r", "pid": 42, "label": "build", "command": "shell"},
        )[0]
        == 400
    )
    monkeypatch.setenv("JARVIS_BUILD_TOOLS", "0")
    assert call("GET", "/build-monitor/latest")[0] == 503


def test_tail_query_bounds_and_path_substitution(setup):
    row = setup.register("r", 42, "build")
    path = "/build-monitor/" + row["id"] + "/tail"
    for query in (
        {"lines": ["0"]},
        {"lines": ["81"]},
        {"lines": ["1", "2"]},
        {"path": ["other"]},
        {"lines": [""]},
    ):
        assert call("GET", path, query=query)[0] == 400


def handler(method, path, body, token="test", authorization="Bearer test"):
    import bridge

    obj = object.__new__(bridge._Handler)
    obj.path = path
    obj.token = token
    obj.client_address = ("127.0.0.1", 1234)
    raw = json.dumps(body).encode() if body is not None else b""
    obj.headers = {"Authorization": authorization, "Content-Length": str(len(raw))}
    obj.rfile = io.BytesIO(raw)
    captured = []
    obj._send_bytes = lambda code, content_type, payload, **kw: captured.append(
        (code, json.loads(payload), kw)
    )
    obj._send = lambda code, payload: captured.append((code, payload, {}))
    getattr(obj, "do_" + method)()
    return captured[0]


def test_bridge_no_store_and_explicit_token(setup):
    got = handler(
        "POST",
        "/build-monitor/register",
        {"request_id": "r", "pid": 42, "label": "build"},
    )
    assert got[0] == 201 and got[2]["no_store"] is True
    assert handler("GET", "/build-monitor/latest", None)[2]["no_store"]
    assert handler("GET", "/build-monitor/latest", None, token="")[0] == 403
    assert (
        handler("GET", "/build-monitor/latest", None, authorization="Bearer wrong")[0]
        == 401
    )


def test_bridge_body_limit(setup):
    got = handler("POST", "/build-monitor/register", {"label": "x" * 5000})
    assert got[0] == 400 and setup.latest() is None


def test_no_process_control_or_notification(setup, monkeypatch):
    import subprocess

    def forbidden(*a, **kw):
        pytest.fail("Unexpected effect")

    for name in ("kill", "killpg"):
        monkeypatch.setattr(os, name, forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    row = setup.register("r", 42, "build")
    setup.poll_once()
    setup.stop_monitoring(row["id"])


def test_audience_change_during_read_suppresses_result(setup, monkeypatch):
    flags = iter([True, False])
    monkeypatch.setattr(bm, "owner_allowed", lambda peer: next(flags))
    code, result = call("GET", "/build-monitor/latest")
    assert code == 403 and "job" not in result


def test_unknown_payload_types_are_bounded_errors(setup):
    for body in (
        None,
        [],
        {},
        {"request_id": None, "pid": 42, "label": "build"},
        {"request_id": "r", "pid": True, "label": "build"},
    ):
        assert call("POST", "/build-monitor/register", body)[0] == 400
