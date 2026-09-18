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
        assert "Authorization" in (resp.getheader("Access-Control-Allow-Headers") or "")
        resp.read()
        conn.close()
    finally:
        server.shutdown()
        server.server_close()


# --- Phase 4.2: POST /mic + GET /mic proxy to wake.sock (stub, no mic) ---


def _post(server, path, payload, token="", raw=None):
    import urllib.error

    port = server.server_address[1]
    body = raw if raw is not None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {}


class _WakeStub:
    """Fake wake_client mic-control listener on a tmp AF_UNIX socket."""

    def __init__(self, sock_path, reply):
        import socket
        import threading

        self.requests = []
        self._reply = reply
        self._stop = threading.Event()
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(str(sock_path))
        self._srv.listen(8)
        self._srv.settimeout(0.2)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        import contextlib
        import json as _json

        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(2.0)
                try:
                    raw = conn.recv(4096)
                    with contextlib.suppress(Exception):
                        self.requests.append(_json.loads(raw.decode()))
                    conn.sendall(_json.dumps(self._reply).encode())
                except OSError:
                    pass

    def close(self):
        self._stop.set()
        self._thread.join(timeout=3)
        self._srv.close()


def _mic_home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    return tmp_path / "wake.sock"


def test_mic_post_proxies_mute_to_wake_socket(monkeypatch, tmp_path):
    sock_path = _mic_home(monkeypatch, tmp_path)
    stub = _WakeStub(sock_path, {"ok": True, "muted": True})
    server, _ = bridge._run_in_thread()
    try:
        code, body = _post(server, "/mic", {"muted": True})
        assert code == 200
        assert body == {"ok": True, "muted": True}
        assert stub.requests == [{"mute": True}]
    finally:
        server.shutdown()
        server.server_close()
        stub.close()


def test_mic_post_fail_soft_without_listener(monkeypatch, tmp_path):
    _mic_home(monkeypatch, tmp_path)  # no stub: socket absent
    server, _ = bridge._run_in_thread()
    try:
        code, body = _post(server, "/mic", {"muted": True})
        assert code == 503
        assert body["ok"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_mic_get_status_passthrough(monkeypatch, tmp_path):
    sock_path = _mic_home(monkeypatch, tmp_path)
    status = {"ok": True, "muted": False, "threshold": 0.5, "in_call": True}
    stub = _WakeStub(sock_path, status)
    server, _ = bridge._run_in_thread()
    try:
        code, body = _get(server, "/mic")
        assert code == 200
        assert body == status
        assert stub.requests == [{"status": True}]
    finally:
        server.shutdown()
        server.server_close()
        stub.close()


def test_mic_get_fail_soft_without_listener(monkeypatch, tmp_path):
    _mic_home(monkeypatch, tmp_path)
    server, _ = bridge._run_in_thread()
    try:
        code, body = _get(server, "/mic")
        assert code == 200
        assert body["ok"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_mic_post_validates_body(monkeypatch, tmp_path):
    _mic_home(monkeypatch, tmp_path)
    server, _ = bridge._run_in_thread()
    try:
        for bad in ({"muted": "yes"}, {}, {"muted": 1}):
            code, body = _post(server, "/mic", bad)
            assert code == 400 and body["ok"] is False
        code, body = _post(server, "/mic", {}, raw=b"not json")
        assert code == 400 and body["ok"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_mic_post_uses_same_bearer_gate(monkeypatch, tmp_path):
    sock_path = _mic_home(monkeypatch, tmp_path)
    stub = _WakeStub(sock_path, {"ok": True, "muted": True})
    server, _ = bridge._run_in_thread(token="s3cret")
    try:
        code, _ = _post(server, "/mic", {"muted": True})
        assert code == 401
        assert stub.requests == []
        code, body = _post(server, "/mic", {"muted": True}, token="s3cret")
        assert code == 200 and body["ok"] is True
        code, _ = _get(server, "/mic")
        assert code == 401
    finally:
        server.shutdown()
        server.server_close()
        stub.close()


def test_options_allows_mic_post():
    import http.client

    server, _ = bridge._run_in_thread()
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("OPTIONS", "/mic")
        resp = conn.getresponse()
        assert resp.status == 204
        assert "POST" in (resp.getheader("Access-Control-Allow-Methods") or "")
        resp.read()
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
