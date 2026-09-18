"""Tests for the desktop-shell bridge (stdlib HTTP, no LiveKit needed)."""

import json
import os
import sys
import urllib.request

sys.path.insert(0, "src")

import bridge


def _get(server, path, token=""):
    port = server.server_address[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.load(resp)
    except Exception as exc:
        status = getattr(exc, "code", None) or 0
        try:
            body = json.loads(exc.read().decode())
        except Exception:
            body = {}
        return status, body


def test_health_and_status_shape():
    server, _ = bridge._run_in_thread()
    try:
        code, health = _get(server, "/health")
        assert code == 200 and health["ok"] is True
        assert health["version"] == bridge.VERSION
        code, status = _get(server, "/status")
        assert code == 200 and status["ok"] is True
        for key in (
            "pipeline",
            "local_unlocked",
            "livekit_configured",
            "voice_model_present",
            "whatsapp_reachable",
            "log",
        ):
            assert key in status, key
        # Presence flags only: no secret values leak.
        assert "LIVEKIT_API_SECRET" not in json.dumps(status)
        assert os.environ.get("LIVEKIT_API_SECRET", "") not in json.dumps(status) or (
            not os.environ.get("LIVEKIT_API_SECRET")
        )
    finally:
        server.shutdown()
        server.server_close()


def test_actions_tail_and_limit_clamp(tmp_path, monkeypatch):
    log = tmp_path / "actions.log"
    log.write_text("\n".join(f"2026-01-01T00:00:0{i} hud:tool t{i}" for i in range(10)))
    monkeypatch.setenv("JARVIS_ACTIONS_LOG", str(log))
    server, _ = bridge._run_in_thread()
    try:
        code, body = _get(server, "/actions?limit=3")
        assert code == 200 and len(body["actions"]) == 3
        assert body["actions"][-1].endswith("t9")
        code, body = _get(server, "/actions?limit=9999")
        assert code == 200 and len(body["actions"]) == 10
        code, body = _get(server, "/nope")
        assert code == 404 and body["ok"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_bearer_token_enforced():
    server, _ = bridge._run_in_thread(token="s3cret")
    try:
        code, _ = _get(server, "/health")
        assert code == 401
        code, body = _get(server, "/health", token="s3cret")
        assert code == 200 and body["ok"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_sys_never_crashes():
    server, _ = bridge._run_in_thread()
    try:
        code, body = _get(server, "/sys")
        assert code == 200 and body["ok"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_cors_headers_for_webview_fetch():
    import http.client

    server, _ = bridge._run_in_thread()
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/status")
        resp = conn.getresponse()
        assert resp.getheader("Access-Control-Allow-Origin") == "*"
        resp.read()
        conn.request("OPTIONS", "/status")
        resp = conn.getresponse()
        assert resp.status == 204
        assert resp.getheader("Access-Control-Allow-Headers") == "Authorization"
        resp.read()
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
