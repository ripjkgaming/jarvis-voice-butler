"""Tests for the desktop-shell bridge (stdlib HTTP, no LiveKit needed)."""

import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest

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


# --- PTT: POST /summon proxies {"talk": true} to wake.sock (stub, no mic) ---


def test_summon_post_proxies_talk_to_wake_socket(monkeypatch, tmp_path):
    sock_path = _mic_home(monkeypatch, tmp_path)
    stub = _WakeStub(sock_path, {"ok": True, "talk": "requested", "muted": False})
    server, _ = bridge._run_in_thread()
    try:
        code, body = _post(server, "/summon", {})
        assert code == 200
        assert body == {"ok": True, "talk": "requested", "muted": False}
        assert stub.requests == [{"talk": True}]
    finally:
        server.shutdown()
        server.server_close()
        stub.close()


def test_summon_post_fail_soft_without_listener(monkeypatch, tmp_path):
    _mic_home(monkeypatch, tmp_path)  # no stub: socket absent
    server, _ = bridge._run_in_thread()
    try:
        code, body = _post(server, "/summon", {})
        assert code == 503
        assert body["ok"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_summon_post_uses_same_bearer_gate(monkeypatch, tmp_path):
    sock_path = _mic_home(monkeypatch, tmp_path)
    stub = _WakeStub(sock_path, {"ok": True, "talk": "requested", "muted": False})
    server, _ = bridge._run_in_thread(token="s3cret")
    try:
        code, _ = _post(server, "/summon", {})
        assert code == 401
        assert stub.requests == []
        code, body = _post(server, "/summon", {}, token="s3cret")
        assert code == 200 and body["ok"] is True
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


def test_room_null_without_call(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    server, _ = bridge._run_in_thread()
    try:
        code, body = _get(server, "/room")
        assert code == 200 and body["ok"] is True
        assert body["room"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_room_reports_live_wake_room(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "hud_room").write_text("jarvis-123")
    server, _ = bridge._run_in_thread()
    try:
        code, body = _get(server, "/room")
        assert code == 200 and body["room"] == "jarvis-123"
        assert body["waking"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_room_reports_waking_before_room_exists(monkeypatch, tmp_path):
    """The wake stamp turns the HUD purple before the call room is up."""
    import os
    import time

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "hud_waking").write_text("1")
    server, _ = bridge._run_in_thread()
    try:
        code, body = _get(server, "/room")
        assert code == 200 and body["room"] is None and body["waking"] is True
        old = time.time() - bridge.HUD_WAKING_MAX_AGE_S - 5
        os.utime(tmp_path / "hud_waking", (old, old))
        assert _get(server, "/room")[1]["waking"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_room_serves_the_boot_log(monkeypatch, tmp_path):
    """The wake client's setup steps ride along on /room for the HUD."""
    import json

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    log = [["wake", 1.0], ["connect", 1.5]]
    (tmp_path / "hud_waking").write_text(json.dumps({"log": log}))
    server, _ = bridge._run_in_thread()
    try:
        body = _get(server, "/room")[1]
        assert body["waking"] is True and body["boot"] == log
        (tmp_path / "hud_waking").write_text("1790000000.0")  # old format
        assert _get(server, "/room")[1]["boot"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_pending_boot_remains_visible_through_agent_join_timeout(monkeypatch, tmp_path):
    import json
    import os
    import time

    from wake_client import AGENT_JOIN_TIMEOUT

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    age = AGENT_JOIN_TIMEOUT - 1
    then = time.time() - age
    log = [["connect", then - 5], ["dispatch", then]]
    path = tmp_path / "hud_waking"
    path.write_text(json.dumps({"log": log}))
    os.utime(path, (then, then))
    assert bridge.read_hud_waking() is True
    assert bridge.read_hud_boot() == log
    # A crashed writer still expires; no background freshness heartbeat.
    stale = time.time() - bridge.HUD_WAKING_MAX_AGE_S - 1
    os.utime(path, (stale, stale))
    assert bridge.read_hud_waking() is False
    assert bridge.read_hud_boot() is None


def test_room_rejects_stale_file(monkeypatch, tmp_path):
    import time

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    path = tmp_path / "hud_room"
    path.write_text("jarvis-old")
    old = time.time() - 20 * 60
    os.utime(path, (old, old))
    server, _ = bridge._run_in_thread()
    try:
        code, body = _get(server, "/room")
        assert code == 200 and body["room"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_captions_tail(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "captions.log").write_text(
        "1700000000\tsir\thello\n1700000001\tjarvis\tAt once.\n"
    )
    server, _ = bridge._run_in_thread()
    try:
        code, body = _get(server, "/captions?limit=5")
        assert code == 200 and body["ok"] is True
        assert [(c["role"], c["text"]) for c in body["captions"]] == [
            ("sir", "hello"),
            ("jarvis", "At once."),
        ]
        code, body = _get(server, "/captions")
        assert code == 200 and body["captions"]
    finally:
        server.shutdown()
        server.server_close()


def test_captions_empty_without_file(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    server, _ = bridge._run_in_thread()
    try:
        code, body = _get(server, "/captions")
        assert code == 200 and body["captions"] == []
    finally:
        server.shutdown()
        server.server_close()


# --- Text→voice: POST /summon carries an optional seed, /chat takes voice:true ---


def test_summon_post_forwards_seed_text(monkeypatch, tmp_path):
    sock_path = _mic_home(monkeypatch, tmp_path)
    stub = _WakeStub(
        sock_path, {"ok": True, "talk": "requested", "muted": False, "text": "seeded"}
    )
    server, _ = bridge._run_in_thread()
    try:
        code, body = _post(server, "/summon", {"text": "tell me the news"})
        assert code == 200 and body["text"] == "seeded"
        assert stub.requests == [{"talk": True, "text": "tell me the news"}]
    finally:
        server.shutdown()
        server.server_close()
        stub.close()


def test_summon_post_blank_text_is_plain_talk(monkeypatch, tmp_path):
    sock_path = _mic_home(monkeypatch, tmp_path)
    stub = _WakeStub(sock_path, {"ok": True, "talk": "requested", "muted": False})
    server, _ = bridge._run_in_thread()
    try:
        for payload in ({}, {"text": ""}, {"text": "   "}, {"text": None}):
            code, _ = _post(server, "/summon", payload)
            assert code == 200
        assert stub.requests == [{"talk": True}] * 4
    finally:
        server.shutdown()
        server.server_close()
        stub.close()


def test_summon_post_rejects_bad_text(monkeypatch, tmp_path):
    _mic_home(monkeypatch, tmp_path)
    server, _ = bridge._run_in_thread()
    try:
        for bad in ({"text": 42}, {"text": ["hi"]}, {"text": "x" * 501}):
            code, body = _post(server, "/summon", bad)
            assert code == 400 and body["ok"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_chat_voice_true_summons_without_text_llm(monkeypatch, tmp_path):
    sock_path = _mic_home(monkeypatch, tmp_path)
    stub = _WakeStub(sock_path, {"ok": True, "talk": "requested", "muted": False})
    server, _ = bridge._run_in_thread()
    try:

        def _boom(prompt):
            raise AssertionError("text LLM must not run on the voice path")

        monkeypatch.setattr(bridge, "_gemini_reply", _boom)
        code, body = _post(server, "/chat", {"text": "hello", "voice": True})
        assert code == 200
        assert body == {"ok": True, "voice": "summoned"}
        assert stub.requests == [{"talk": True, "text": "hello"}]
    finally:
        server.shutdown()
        server.server_close()
        stub.close()


def test_chat_voice_true_falls_back_to_text_without_listener(monkeypatch, tmp_path):
    _mic_home(monkeypatch, tmp_path)  # no stub: listener absent
    server, _ = bridge._run_in_thread()
    try:
        monkeypatch.setattr(bridge, "_gemini_reply", lambda prompt: ("fallback", None))
        code, body = _post(server, "/chat", {"text": "hello", "voice": True})
        assert code == 200 and body.get("reply") == "fallback"
    finally:
        server.shutdown()
        server.server_close()


def test_chat_voice_true_rejects_long_text(monkeypatch, tmp_path):
    sock_path = _mic_home(monkeypatch, tmp_path)
    stub = _WakeStub(sock_path, {"ok": True})
    server, _ = bridge._run_in_thread()
    try:
        code, body = _post(server, "/chat", {"text": "x" * 501, "voice": True})
        assert code == 400 and body["ok"] is False
        assert stub.requests == []
    finally:
        server.shutdown()
        server.server_close()
        stub.close()


# --- Text LLM high-usage backups: chain + fail-fast rules ---


class _UsageError(Exception):
    def __init__(self, code=429):
        super().__init__(f"{code} RESOURCE_EXHAUSTED quota")
        self.code = code


class _AuthError(Exception):
    def __init__(self):
        super().__init__("400 API key not valid")
        self.code = 400


def _fake_genai(monkeypatch, script):
    """Stub `google.genai` with a scripted Client. Returns call log."""
    import sys
    import types as _types

    calls = []

    class _Resp:
        def __init__(self, text):
            self.text = text

    class _Models:
        def generate_content(self, model, contents):
            calls.append(model)
            action = script[min(len(calls) - 1, len(script) - 1)]
            if isinstance(action, Exception):
                raise action
            return _Resp(action)

    class _Client:
        def __init__(self, api_key=None):
            self.models = _Models()

    pkg = _types.ModuleType("google")
    pkg.genai = _types.SimpleNamespace(Client=_Client)
    monkeypatch.setitem(sys.modules, "google", pkg)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    return calls


def test_gemini_reply_uses_primary_first(monkeypatch):
    calls = _fake_genai(monkeypatch, ["primary says hi"])
    reply, warning = bridge._gemini_reply("hello")
    assert (reply, warning) == ("primary says hi", None)
    assert calls == [bridge.GEMINI_TEXT_MODEL]


def test_gemini_reply_falls_back_on_429(monkeypatch):
    calls = _fake_genai(monkeypatch, [_UsageError(429), "backup answers"])
    reply, warning = bridge._gemini_reply("hello")
    assert (reply, warning) == ("backup answers", None)
    assert calls == [bridge.GEMINI_TEXT_MODEL, bridge.GEMINI_TEXT_FALLBACKS[0]]


def test_gemini_reply_fails_fast_on_auth_error(monkeypatch):
    calls = _fake_genai(monkeypatch, [_AuthError()])
    reply, warning = bridge._gemini_reply("hello")
    assert reply == "" and "LLM unavailable" in (warning or "")
    assert calls == [bridge.GEMINI_TEXT_MODEL]


def test_gemini_reply_reports_last_error_when_all_saturated(monkeypatch):
    calls = _fake_genai(monkeypatch, [_UsageError(429)])
    # Fallback down too: hermetic (no live OpenRouter call), keeps the
    # Gemini-side warning.
    monkeypatch.setattr("openrouter_chat.chat_reply", lambda *a, **k: ("", "down"))
    reply, warning = bridge._gemini_reply("hello")
    assert reply == "" and "LLM unavailable" in (warning or "")
    assert len(calls) == 1 + len(bridge.GEMINI_TEXT_FALLBACKS)


def test_fallback_chat_takes_over_when_gemini_is_saturated(monkeypatch):
    _fake_genai(monkeypatch, [_UsageError(429)])
    seen = {}

    def fake_chat(prompt, **kw):
        seen.update(kw, prompt=prompt)
        return "Ling here, Sir.", None

    monkeypatch.setattr("openrouter_chat.chat_reply", fake_chat)
    reply, warning = bridge._gemini_reply("hello", guest=True)
    assert (reply, warning) == ("Ling here, Sir.", None)
    assert seen["timeout"] == 45.0
    assert "guest mode" in seen["system"]  # persona survives the fallback


def test_fallback_chat_not_used_for_non_usage_errors(monkeypatch):
    _fake_genai(monkeypatch, [_AuthError()])
    monkeypatch.setattr(
        "openrouter_chat.chat_reply",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no fallback chat on auth errors")
        ),
    )
    reply, warning = bridge._gemini_reply("hello")
    assert reply == "" and "LLM unavailable" in warning


def test_fallback_chat_failure_keeps_gemini_warning(monkeypatch):
    _fake_genai(monkeypatch, [_UsageError(429)])
    monkeypatch.setattr("openrouter_chat.chat_reply", lambda *a, **k: ("", "down"))
    reply, warning = bridge._gemini_reply("hello")
    assert reply == "" and "LLM unavailable" in warning


def test_projects_voice_route_beats_app_launcher(monkeypatch):
    import projects

    monkeypatch.setattr(projects, "BUS", projects.UiBus())
    hit = bridge._match_voice_tool("Jarvis, open research projects.")
    assert hit is not None and hit[0] == "projects_ui"
    assert hit[1]["commands"][0] == {"action": "show"}
    hit = bridge._match_voice_tool("open research projects and open project two")
    assert [c["action"] for c in hit[1]["commands"]] == ["show", "filter", "select"]
    # Websites and searches keep their own routes.
    assert bridge._match_voice_tool("open youtube")[0] == "open_app"
    assert bridge._match_voice_tool("search for batteries") is None or (
        bridge._match_voice_tool("search for batteries")[0] != "projects_ui"
    )


def test_projects_http_routes(monkeypatch, tmp_path):
    import projects

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(projects, "BUS", projects.UiBus())
    started = {}
    monkeypatch.setattr(
        projects,
        "start_research",
        lambda topic: started.setdefault("m", {"id": "x", "topic": topic}),
    )
    server = _bridge_server()
    try:
        status, body = _post(
            server,
            "/projects",
            {"kind": "research", "topic": "solid state"},
            token="test-token",
        )
        assert status == 200 and started["m"]["topic"] == "solid state"
        status, body = _post(
            server, "/projects", {"kind": "research", "topic": "x"}, token="test-token"
        )
        assert status == 400
        status, body = _post(
            server,
            "/projects",
            {"kind": "code", "task": "rm", "guest": True},
            token="test-token",
        )
        assert status == 403
        status, body = _get(server, "/projects", token="test-token")
        assert status == 200 and body["projects"] == []
        projects.BUS.push("show")
        status, body = _get(server, "/projects/ui?since=0", token="test-token")
        assert status == 200 and body["commands"][0]["action"] == "show"
        status, body = _get(server, "/projects/../../etc", token="test-token")
        assert status == 404
    finally:
        server.server_close()


def test_is_usage_error_classifies(monkeypatch):
    assert bridge._is_usage_error(_UsageError(429)) is True
    assert bridge._is_usage_error(_UsageError(503)) is True
    assert bridge._is_usage_error(TimeoutError("timed out")) is True
    assert bridge._is_usage_error(_AuthError()) is False
    assert bridge._is_usage_error(ValueError("bad request")) is False


def _bridge_server(token="test-token"):
    server, _thread = bridge._run_in_thread(port=0, token=token)
    return server


def test_token_post_mints_join_shape():
    pytest.importorskip("livekit.api")
    server = _bridge_server()
    try:
        status, body = _post(server, "/token", {}, token="test-token")
    finally:
        server.server_close()
    assert status == 200, body
    assert body["ok"] is True
    assert body["roomName"].startswith("voice_assistant_room_")
    assert body["participantName"] == "user"
    assert body["participantToken"].count(".") == 2


def test_token_post_named_room_no_dispatch():
    pytest.importorskip("livekit.api")
    import base64

    server = _bridge_server()
    try:
        status, body = _post(
            server,
            "/token",
            {"room": "jarvis-4242", "dispatch": False},
            token="test-token",
        )
    finally:
        server.server_close()
    assert status == 200, body
    assert body["roomName"] == "jarvis-4242"
    payload = body["participantToken"].split(".")[1]
    assert "roomConfig" not in json.loads(base64.urlsafe_b64decode(payload + "=="))


def test_token_post_rejects_bad_room_and_strangers():
    server = _bridge_server()
    try:
        status, _ = _post(server, "/token", {"room": "../../etc"}, token="test-token")
        assert status == 400
        status, _body = _post(server, "/token", {}, token="wrong")
        assert status == 401
        status, _ = _post(server, "/token", {})
        assert status == 401
    finally:
        server.server_close()


def test_voice_tool_routing_and_honest_failure(monkeypatch):
    assert bridge._match_voice_tool("Lock the computer")[0] == "lock"
    assert bridge._match_voice_tool("please unlock my laptop")[0] == "unlock"
    assert bridge._match_voice_tool("what's the volume right now")[0] == "volume_get"
    assert bridge._match_voice_tool("tell me about black holes") is None
    assert bridge._match_voice_tool("") is None
    monkeypatch.setattr(
        bridge,
        "run_phone_tool",
        lambda tool, args: {"ok": True, "volume": 42, "muted": False},
    )
    code, payload = bridge.handle_chat({"text": "what's the volume"})
    assert code == 200
    assert payload["reply"] == "Volume 42 percent, live, Sir."
    assert payload["action"] == {"tool": "volume_get", "ok": True}
    monkeypatch.setattr(
        bridge,
        "run_phone_tool",
        lambda tool, args: {"ok": False, "error": "pactl failed"},
    )
    code, payload = bridge.handle_chat({"text": "lock the computer"})
    assert code == 200
    assert "couldn't" in payload["reply"] and "pactl failed" in payload["reply"]
    assert payload["action"] == {"tool": "lock", "ok": False, "error": "pactl failed"}


def test_route_regex_executes_instantly(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "run_phone_tool",
        lambda tool, args: {"ok": True, "volume": 30, "muted": False},
    )
    code, payload = bridge.handle_route({"text": "lock the computer"})
    assert code == 200 and payload["reply"] == "Locked, Sir."
    assert payload["action"] == {"tool": "lock", "ok": True}
    code, payload = bridge.handle_route({"text": "what is the volume"})
    assert code == 200
    assert payload["reply"] == "Volume 30 percent, live, Sir."


def test_route_resolver_paraphrase_guest_and_tiers(monkeypatch):
    from intent.resolver import IntentResult

    calls = []
    monkeypatch.setattr(
        bridge,
        "run_phone_tool",
        lambda tool, args: calls.append(tool) or {"ok": True},
    )
    monkeypatch.setattr(
        bridge,
        "_resolve_intent",
        lambda text: IntentResult(
            action="set_volume", params={"action": "up"}, confidence=0.95
        ),
    )
    code, payload = bridge.handle_route({"text": "crank it"})
    assert code == 200 and calls == ["volume_up"]
    assert payload["action"] == {"tool": "volume_up", "ok": True}
    monkeypatch.setattr(
        bridge,
        "_resolve_intent",
        lambda text: IntentResult(
            action="set_volume",
            params={"action": "up"},
            confidence=0.6,
            clarification="Louder, Sir — correct?",
        ),
    )
    code, payload = bridge.handle_route({"text": "crank it"})
    assert code == 200 and "action" not in payload
    assert payload["reply"] == "Louder, Sir — correct?"
    assert calls == ["volume_up"]  # confirm tier never acts
    monkeypatch.setattr(
        bridge,
        "_resolve_intent",
        lambda text: IntentResult(action="lock_pc", params={}, confidence=1.0),
    )
    code, payload = bridge.handle_route({"text": "secure my laptop", "guest": True})
    assert code == 200 and payload["reply"] == bridge._GUEST_REFUSAL
    monkeypatch.setattr(
        bridge,
        "_resolve_intent",
        lambda text: IntentResult(action="tell_time", params={}, confidence=1.0),
    )
    code, payload = bridge.handle_route({"text": "what time is it"})
    assert code == 200 and payload["reply"].startswith("It's ")
    monkeypatch.setattr(
        bridge,
        "_resolve_intent",
        lambda text: IntentResult(
            action="do_math", params={"expr": "2*3+4"}, confidence=1.0
        ),
    )
    code, payload = bridge.handle_route({"text": "two times three plus four"})
    assert code == 200 and payload["reply"] == "That's 10, Sir."
    monkeypatch.setattr(
        bridge, "_resolve_intent", lambda text: IntentResult(action="unknown")
    )
    code, payload = bridge.handle_route({"text": "ramble on about nothing"})
    assert code == 404 and payload["ok"] is False
    code, payload = bridge.handle_route({"text": "   "})
    assert code == 400


def test_route_live_volume_over_http():
    # Read-only end to end: regex -> pactl -> reply, no mocks.
    server, _ = bridge._run_in_thread()
    try:
        code, body = _post(server, "/route", {"text": "what is the volume"})
        assert code == 200 and body["action"]["tool"] == "volume_get"
        code, body = _post(server, "/route", {"text": "ramble on about nothing"})
        # Real resolver: no-route (404) or confirm-tier clarification
        # (200, reply, never an action). Either way nothing executes.
        assert (code == 404 and body["ok"] is False) or (
            code == 200 and "action" not in body and body.get("reply")
        )
    finally:
        server.shutdown()
        server.server_close()


def test_absolute_volume_routing_and_validation(monkeypatch):
    assert bridge._match_voice_tool("set volume to 80") == (
        "volume_set",
        {"level": 80},
        None,
    )
    assert bridge._match_voice_tool("volume 120")[1] == {"level": 120}
    assert bridge._match_voice_tool("max volume")[1] == {"level": 100}
    monkeypatch.setattr(bridge, "_which", lambda name: "/usr/bin/pactl")
    assert bridge.run_phone_tool("volume_set", {"level": 200})["ok"] is False
    assert bridge.run_phone_tool("volume_set", {"level": "x"})["ok"] is False

    def fake_run(argv, timeout=10.0):
        if "get-sink-volume" in argv:
            return 0, "Volume: front-left: 50000 /  76% / -4.16 dB", ""
        if "get-sink-mute" in argv:
            return 0, "Mute: no", ""
        assert "set-sink-volume" in argv and argv[-1] == "76%"
        return 0, "", ""

    monkeypatch.setattr(bridge, "_run", fake_run)
    result = bridge.run_phone_tool("volume_set", {"level": 76})
    assert result["ok"] is True and result["volume"] == 76
    # Over-100 levels report honestly (regression: clamp lied at 100).
    assert bridge._parse_volume_pct("Volume: front-left: 78643 / 120% / x") == 120
    code, payload = bridge.handle_route({"text": "set volume to 76"})
    assert code == 200 and payload["action"] == {"tool": "volume_set", "ok": True}
    assert payload["reply"] == "Volume 76 percent, live, Sir."


def test_screenshot_crops_to_output(monkeypatch, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    full = tmp_path / "full.png"
    Image.new("RGB", (100, 60), "red").save(full)

    def fake_run(argv, timeout=10.0):
        if argv[0] == "kscreen-doctor":
            return (
                0,
                "Output: 1 HDMI-A-2 uuid\n enabled\n Geometry: 10,5 40x20\n",
                "",
            )
        dest = Path(argv[argv.index("-o") + 1])
        dest.write_bytes(full.read_bytes())
        return 0, "", ""

    monkeypatch.setattr(bridge, "_run", fake_run)
    monkeypatch.setattr(
        bridge,
        "_which",
        lambda name: "/usr/bin/spectacle" if name == "spectacle" else None,
    )
    import base64
    import io

    res = bridge.run_phone_tool("screenshot", {"output": "HDMI-A-2"})
    assert res["ok"] is True and res["output"] == "HDMI-A-2"
    img = Image.open(io.BytesIO(base64.b64decode(res["image_b64"])))
    assert img.size == (40, 20)
    bad = bridge.run_phone_tool("screenshot", {"output": "NOPE"})
    assert bad["ok"] is False and "no such output" in bad["error"]
    outs = bridge._parse_kscreen_outputs(
        "Output: 2 DP-1 uuid\n enabled\n Geometry: 1920,0 1920x1080\n"
    )
    assert outs[0]["geometry"] == {"x": 1920, "y": 0, "w": 1920, "h": 1080}


def test_guest_mode_refuses_control_but_allows_media(monkeypatch):
    calls = []
    monkeypatch.setattr(
        bridge,
        "run_phone_tool",
        lambda tool, args: calls.append(tool) or {"ok": True},
    )
    code, payload = bridge.handle_chat({"text": "lock the computer", "guest": True})
    assert code == 200 and payload["reply"] == bridge._GUEST_REFUSAL
    assert payload["action"] == {"tool": "lock", "ok": False, "denied": "guest mode"}
    assert calls == []
    code, payload = bridge.handle_chat({"text": "volume up", "guest": True})
    assert code == 200 and payload["reply"] == "Turned it up, Sir."
    assert calls == ["volume_up"]
    server, _ = bridge._run_in_thread()
    try:
        code, body = _post(server, "/tool", {"tool": "lock", "guest": True})
        assert code == 403 and body["ok"] is False
        code, body = _post(server, "/tool", {"tool": "volume_up", "guest": True})
        assert code in (200, 400) and "guest" not in body.get("error", "")
        code, body = _post(server, "/type", {"text": "hi", "guest": True})
        assert code == 403 and body["ok"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_phone_telemetry_roundtrip_and_staleness() -> None:
    import bridge

    bridge._PHONE_TELEMETRY.clear()
    assert bridge._phone_stats() is None
    assert bridge.record_phone_telemetry({"battery": 150}) is None
    assert bridge.record_phone_telemetry({"battery": True}) is None
    stored = bridge.record_phone_telemetry({"battery": 64, "charging": True})
    assert stored and stored["battery"] == 64
    stats = bridge._phone_stats(now=stored["ts"] + 5)
    assert stats == {"battery": 64, "charging": True, "age_s": 5}
    assert bridge._phone_stats(now=stored["ts"] + 3600) is None
    bridge._PHONE_TELEMETRY.clear()


def test_phone_peer_picks_mobile_or_pinned_ip(monkeypatch) -> None:
    import bridge

    status = {
        "Peer": {
            "a": {"OS": "linux", "HostName": "box", "TailscaleIPs": ["100.1.1.1"]},
            "b": {
                "OS": "android",
                "HostName": "pixel",
                "Online": True,
                "TailscaleIPs": ["100.2.2.2"],
            },
        }
    }
    monkeypatch.delenv("JARVIS_PHONE_TAILNET_IP", raising=False)
    assert bridge._phone_peer(status)["HostName"] == "pixel"
    monkeypatch.setenv("JARVIS_PHONE_TAILNET_IP", "100.1.1.1")
    assert bridge._phone_peer(status)["HostName"] == "box"
    monkeypatch.setenv("JARVIS_PHONE_TAILNET_IP", "100.9.9.9")
    assert bridge._phone_peer(status) is None
    assert bridge._phone_peer({}) is None


def test_laptop_power_and_cpu_temp_from_sysfs(tmp_path) -> None:
    import bridge

    bat = tmp_path / "ps" / "BAT1"
    bat.mkdir(parents=True)
    (bat / "capacity").write_text("81\n")
    (bat / "status").write_text("Discharging\n")
    (bat / "current_now").write_text("1000000\n")
    (bat / "voltage_now").write_text("15000000\n")
    ac = tmp_path / "ps" / "ACAD"
    ac.mkdir()
    (ac / "online").write_text("0\n")
    power = bridge._laptop_power(tmp_path / "ps")
    assert power == {"battery": 81, "status": "Discharging", "watts": 15.0, "ac": False}

    th = tmp_path / "th"
    th.mkdir()
    for i, (kind, temp) in enumerate((("acpitz", "50000"), ("TCPU", "71500"))):
        z = th / f"thermal_zone{i}"
        z.mkdir()
        (z / "type").write_text(kind)
        (z / "temp").write_text(temp)
    assert bridge._cpu_temp_c(th) == 71.5


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Can you take 2,401 and subtract 1,605?", 796),
        ("Can you tell me what 7 to the power of 4 is?", 2401),
        ("What is 7 ^ 4?", 2401),
        ("what is 12 times 12", 144),
        ("what is 15% of 80", 12),
        ("what's 144 divided by 12", 12),
        ("subtract 5 from 20", 15),
        ("what is the square root of 144", 12),
        ("Jarvis, what's 3 plus 4 times 2", 11),
    ],
)
def test_spoken_math_is_instant(text, value):
    hit = bridge._match_voice_tool(text)
    assert hit is not None and hit[0] == "do_math"
    assert bridge._safe_math(hit[1]["expr"]) == value


@pytest.mark.parametrize(
    "text",
    [
        "what is the capital of australia",
        "what time is it",
        "2 to the 99999",
        "who wrote romeo and juliet",
        "open project two",
    ],
)
def test_spoken_math_leaves_other_turns_alone(text):
    hit = bridge._match_voice_tool(text)
    assert hit is None or hit[0] != "do_math"


def test_brave_orb_debounce() -> None:
    from bridge import is_brave_window, orb_step

    assert is_brave_window({"app": "brave-browser", "title": "x"})
    assert not is_brave_window({"app": "org.kde.konsole", "title": "brave new world"})
    assert not is_brave_window(None)
    st: dict = {"orb": False}
    assert orb_step(st, True, 0.0, False) is None  # just focused
    assert orb_step(st, True, 0.7, False) == "orbon"
    assert orb_step(st, True, 1.0, False) is None  # already an orb
    assert orb_step(st, False, 2.0, False) is None  # alt-tab flick
    assert orb_step(st, True, 2.5, False) is None  # back before exit hold
    assert orb_step(st, False, 3.0, False) is None
    assert orb_step(st, False, 4.6, False) == "orboff"
    st = {"orb": False}
    assert orb_step(st, True, 0.0, True) is None  # school mode wins
    assert orb_step(st, True, 5.0, True) is None


def test_orb_holds_while_jarvis_window_is_active() -> None:
    from bridge import is_jarvis_window, orb_step

    assert is_jarvis_window({"app": "jarvis-shell"})
    st: dict = {"orb": False}
    orb_step(st, True, 0.0, False)
    assert orb_step(st, True, 0.7, False) == "orbon"
    # The orb window itself grabs activation: must not flap off.
    assert orb_step(st, None, 1.0, False) is None
    assert orb_step(st, None, 5.0, False) is None
    assert st["orb"] is True


# --- /sys school-mode taskbar fields: mode_since / net / volume ---


def _cp(rc=0, stdout="", stderr=""):
    import types

    return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


def _fake_clock(start=100.0):
    now = [start]
    return now, lambda: now[0]


def test_mode_since_reads_since_key(monkeypatch, tmp_path):
    import json as _json

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "mode.json").write_text(
        _json.dumps({"mode": "school", "since": 1700000000.5})
    )
    assert bridge._mode_since() == 1700000000.5


def test_mode_since_missing_corrupt_or_badsince(monkeypatch, tmp_path):
    import json as _json

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert bridge._mode_since() is None  # missing file
    (tmp_path / "mode.json").write_text("not json{")
    assert bridge._mode_since() is None  # corrupt
    (tmp_path / "mode.json").write_text(_json.dumps({"mode": "school"}))
    assert bridge._mode_since() is None  # no since key
    (tmp_path / "mode.json").write_text(_json.dumps({"since": "soon"}))
    assert bridge._mode_since() is None  # non-numeric


def test_net_wifi_with_signal():
    bridge._NET_CACHE.clear()
    _, clock = _fake_clock()
    calls = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        assert kw.get("timeout") == 2
        if "dev" in argv:
            return _cp(0, "no:Other:70\nyes:HomeNet:80\n")
        return _cp(0, "wifi:connected:HomeNet\nethernet:disconnected:--\n")

    assert bridge._net_status(run=fake_run, clock=clock) == {
        "kind": "wifi",
        "name": "HomeNet",
        "signal": 80,
    }
    assert len(calls) == 2


def test_net_escaped_colon_ssid():
    bridge._NET_CACHE.clear()
    _, clock = _fake_clock()

    def fake_run(argv, **kw):
        if "dev" in argv:
            return _cp(0, "yes:My\\:Home\\:Net:62\n")
        return _cp(0, "wifi:connected:MyHome\n")

    assert bridge._net_status(run=fake_run, clock=clock) == {
        "kind": "wifi",
        "name": "My:Home:Net",
        "signal": 62,
    }


def test_net_ethernet_and_disconnected():
    bridge._NET_CACHE.clear()
    _, clock = _fake_clock()

    def fake_eth(argv, **kw):
        return _cp(0, "ethernet:connected:Wired connection 1\nwifi:disconnected:--\n")

    assert bridge._net_status(run=fake_eth, clock=clock) == {
        "kind": "ethernet",
        "name": "Wired connection 1",
        "signal": None,
    }
    bridge._NET_CACHE.clear()

    def fake_none(argv, **kw):
        return _cp(0, "wifi:disconnected:--\nethernet:disconnected:--\n")

    assert bridge._net_status(run=fake_none, clock=clock) == {
        "kind": "none",
        "name": "",
        "signal": None,
    }


def test_net_missing_or_failing_is_none():
    bridge._NET_CACHE.clear()
    _, clock = _fake_clock()

    def fake_missing(argv, **kw):
        raise FileNotFoundError("no nmcli")

    assert bridge._net_status(run=fake_missing, clock=clock) is None
    bridge._NET_CACHE.clear()

    def fake_rc(argv, **kw):
        return _cp(1, "", "err")

    assert bridge._net_status(run=fake_rc, clock=clock) is None


def test_net_cache_avoids_rerun_within_5s():
    bridge._NET_CACHE.clear()
    now, clock = _fake_clock()
    calls = []

    def fake_run(argv, **kw):
        calls.append(1)
        return _cp(0, "ethernet:disconnected:--\nwifi:disconnected:--\n")

    first = bridge._net_status(run=fake_run, clock=clock)
    second = bridge._net_status(run=fake_run, clock=clock)
    assert first == second and len(calls) == 1
    now[0] += 6.0  # past the 5s TTL
    bridge._net_status(run=fake_run, clock=clock)
    assert len(calls) == 2


def test_volume_wpctl_normal_and_muted():
    bridge._VOL_CACHE.clear()
    _, clock = _fake_clock()
    assert bridge._volume_status(
        run=lambda argv, **kw: _cp(0, "Volume: 0.45\n"), clock=clock
    ) == {"pct": 45, "muted": False}
    bridge._VOL_CACHE.clear()
    assert bridge._volume_status(
        run=lambda argv, **kw: _cp(0, "Volume: 0.45 [MUTED]\n"), clock=clock
    ) == {"pct": 45, "muted": True}


def test_volume_pactl_fallback_and_all_fail():
    bridge._VOL_CACHE.clear()
    _, clock = _fake_clock()

    def fake_pactl(argv, **kw):
        if "get-sink-volume" in argv:
            return _cp(0, "Volume: front-left: 50000 /  76% / -4.16 dB")
        if "get-sink-mute" in argv:
            return _cp(0, "Mute: yes")
        raise AssertionError(f"wpctl should fail first: {argv}")

    def fake_run(argv, **kw):
        if argv[0] == "wpctl":
            return _cp(1, "", "nope")
        return fake_pactl(argv, **kw)

    assert bridge._volume_status(run=fake_run, clock=clock) == {
        "pct": 76,
        "muted": True,
    }
    bridge._VOL_CACHE.clear()
    assert (
        bridge._volume_status(run=lambda argv, **kw: _cp(1, "", "down"), clock=clock)
        is None
    )


def test_volume_cache_avoids_rerun_within_5s():
    bridge._VOL_CACHE.clear()
    now, clock = _fake_clock()
    calls = []

    def fake_run(argv, **kw):
        calls.append(1)
        return _cp(0, "Volume: 0.50\n")

    assert bridge._volume_status(run=fake_run, clock=clock) == {
        "pct": 50,
        "muted": False,
    }
    assert bridge._volume_status(run=fake_run, clock=clock) == {
        "pct": 50,
        "muted": False,
    }
    assert len(calls) == 1
    now[0] += 6.0
    bridge._volume_status(run=fake_run, clock=clock)
    assert len(calls) == 2


def test_sys_stats_carries_new_taskbar_fields(monkeypatch):
    bridge._NET_CACHE.clear()
    bridge._VOL_CACHE.clear()
    monkeypatch.setattr(bridge, "_phone_tailnet", lambda: None)
    monkeypatch.setattr(
        bridge, "_net_status", lambda **kw: {"kind": "none", "name": "", "signal": None}
    )
    monkeypatch.setattr(
        bridge, "_volume_status", lambda **kw: {"pct": 10, "muted": False}
    )
    monkeypatch.setattr(bridge, "_mode_since", lambda: 123.0)
    stats = bridge._sys_stats()
    assert stats["mode_since"] == 123.0
    assert stats["net"] == {"kind": "none", "name": "", "signal": None}
    assert stats["volume"] == {"pct": 10, "muted": False}


# --- /appicon + windows list for the school-mode taskbar ---


def _fake_xdg(tmp_path):
    apps = tmp_path / "applications"
    icons = tmp_path / "icons"
    pixmaps = tmp_path / "pixmaps"
    scalable = icons / "breeze-dark" / "scalable" / "apps"
    small = icons / "breeze-dark" / "48x48" / "apps"
    scalable.mkdir(parents=True)
    small.mkdir(parents=True)
    pixmaps.mkdir(parents=True)
    apps.mkdir(parents=True)
    (apps / "foo.desktop").write_text("[Desktop Entry]\nName=Foo\nIcon=fooicon\n")
    (apps / "other.desktop").write_text(
        "[Desktop Entry]\nName=Other\nIcon=myicon\nStartupWMClass=MyApp\n"
    )
    (scalable / "fooicon.svg").write_bytes(b"<svg/>")
    (small / "myicon.png").write_bytes(bytes.fromhex("89504e470d0a1a0a") + b"1234")
    kdeglobals = tmp_path / "kdeglobals"
    kdeglobals.write_text("[Icons]\nTheme=breeze-dark\n")
    return apps, icons, pixmaps, kdeglobals


def test_app_icon_from_themed_svg(tmp_path):
    apps, icons, pixmaps, kdeglobals = _fake_xdg(tmp_path)
    url = bridge.app_icon_data_url(
        "foo",
        app_dirs=[apps],
        icon_dirs=[icons],
        pixmap_dirs=[pixmaps],
        kdeglobals=kdeglobals,
    )
    assert url is not None and url.startswith("data:image/svg+xml;base64,")


def test_app_icon_startupwmclass_match(tmp_path):
    apps, icons, pixmaps, kdeglobals = _fake_xdg(tmp_path)
    url = bridge.app_icon_data_url(
        "myapp",
        app_dirs=[apps],
        icon_dirs=[icons],
        pixmap_dirs=[pixmaps],
        kdeglobals=kdeglobals,
    )
    assert url is not None and url.startswith("data:image/png;base64,")


def test_app_icon_case_insensitive_desktop(tmp_path):
    apps, icons, pixmaps, kdeglobals = _fake_xdg(tmp_path)
    url = bridge.app_icon_data_url(
        "FOO",
        app_dirs=[apps],
        icon_dirs=[icons],
        pixmap_dirs=[pixmaps],
        kdeglobals=kdeglobals,
    )
    assert url is not None and url.startswith("data:image/svg+xml;base64,")


def test_app_icon_absolute_path(tmp_path):
    apps, icons, pixmaps, kdeglobals = _fake_xdg(tmp_path)
    raw = tmp_path / "raw.png"
    raw.write_bytes(bytes.fromhex("89504e470d0a1a0a") + b"zz")
    (apps / "abs.desktop").write_text(f"[Desktop Entry]\nName=Abs\nIcon={raw}\n")
    url = bridge.app_icon_data_url(
        "abs",
        app_dirs=[apps],
        icon_dirs=[icons],
        pixmap_dirs=[pixmaps],
        kdeglobals=kdeglobals,
    )
    assert url is not None and url.startswith("data:image/png;base64,")


def test_app_icon_miss_and_size_cap(tmp_path):
    apps, icons, pixmaps, kdeglobals = _fake_xdg(tmp_path)
    assert (
        bridge.app_icon_data_url(
            "nope",
            app_dirs=[apps],
            icon_dirs=[icons],
            pixmap_dirs=[pixmaps],
            kdeglobals=kdeglobals,
        )
        is None
    )
    big = icons / "breeze-dark" / "scalable" / "apps" / "bigicon.svg"
    big.write_bytes(b"x" * (bridge._APPICON_MAX_BYTES + 1))
    (apps / "big.desktop").write_text("[Desktop Entry]\nName=Big\nIcon=bigicon\n")
    assert (
        bridge.app_icon_data_url(
            "big",
            app_dirs=[apps],
            icon_dirs=[icons],
            pixmap_dirs=[pixmaps],
            kdeglobals=kdeglobals,
        )
        is None
    )


def test_app_icon_shrinks_big_raster_pixmap(tmp_path):
    """A 1024px pixmap over the cap (ChatGPT's) is shrunk, not dropped."""
    import base64
    import io

    Image = pytest.importorskip("PIL.Image")
    apps, icons, pixmaps, kdeglobals = _fake_xdg(tmp_path)
    buf = io.BytesIO()
    Image.frombytes("RGBA", (1024, 1024), os.urandom(1024 * 1024 * 4)).save(
        buf, format="PNG"
    )
    assert len(buf.getvalue()) > bridge._APPICON_MAX_BYTES
    (pixmaps / "hugeicon.png").write_bytes(buf.getvalue())
    (apps / "huge.desktop").write_text("[Desktop Entry]\nName=Huge\nIcon=hugeicon\n")
    url = bridge.app_icon_data_url(
        "huge",
        app_dirs=[apps],
        icon_dirs=[icons],
        pixmap_dirs=[pixmaps],
        kdeglobals=kdeglobals,
    )
    assert url is not None and url.startswith("data:image/png;base64,")
    with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as im:
        assert max(im.size) == bridge._APPICON_SHRINK_PX


def test_app_icon_caches_misses(monkeypatch):
    bridge._APPICON_CACHE.clear()
    calls = []
    monkeypatch.setattr(bridge, "find_desktop_file", lambda app, app_dirs=None: None)
    monkeypatch.setattr(
        bridge, "find_icon_path", lambda *a, **k: calls.append(1) or None
    )
    try:
        assert bridge.app_icon_data_url("missing-app-xyz") is None
        assert bridge.app_icon_data_url("missing-app-xyz") is None
        assert len(calls) == 1
    finally:
        bridge._APPICON_CACHE.clear()


def test_appicon_route(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "app_icon_data_url",
        lambda app: "data:image/png;base64,AAA" if app == "foo" else None,
    )
    server, _ = bridge._run_in_thread()
    try:
        code, body = _get(server, "/appicon?app=foo")
        assert code == 200 and body == {"ok": True, "data": "data:image/png;base64,AAA"}
        code, body = _get(server, "/appicon?app=nope")
        assert code == 200 and body == {"ok": False}
        code, body = _get(server, "/appicon")
        assert code == 400 and body["ok"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_appicon_route_uses_same_bearer_gate(monkeypatch):
    monkeypatch.setattr(bridge, "app_icon_data_url", lambda app: "data:x")
    server, _ = bridge._run_in_thread(token="s3cret")
    try:
        code, _ = _get(server, "/appicon?app=foo")
        assert code == 401
        code, body = _get(server, "/appicon?app=foo", token="s3cret")
        assert code == 200 and body["ok"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_sys_stats_carries_windows(monkeypatch):
    import active_window

    bridge._NET_CACHE.clear()
    bridge._VOL_CACHE.clear()
    monkeypatch.setattr(active_window, "ensure_listener", lambda: True)
    fake_windows = [
        {
            "id": "a",
            "title": "t",
            "app": "brave",
            "desktop": "brave.desktop",
            "active": True,
            "minimized": False,
            "pid": 1,
        }
    ]
    monkeypatch.setattr(active_window, "windows", lambda: fake_windows)
    monkeypatch.setattr(bridge, "_phone_tailnet", lambda: None)
    monkeypatch.setattr(
        bridge, "_net_status", lambda **kw: {"kind": "none", "name": "", "signal": None}
    )
    monkeypatch.setattr(
        bridge, "_volume_status", lambda **kw: {"pct": 10, "muted": False}
    )
    stats = bridge._sys_stats()
    assert stats["windows"] == fake_windows


def test_sys_stats_windows_fail_soft(monkeypatch):
    import active_window

    bridge._NET_CACHE.clear()
    bridge._VOL_CACHE.clear()

    def _boom():
        raise RuntimeError("dbus down")

    monkeypatch.setattr(active_window, "ensure_listener", _boom)
    monkeypatch.setattr(active_window, "windows", _boom)
    monkeypatch.setattr(bridge, "_phone_tailnet", lambda: None)
    monkeypatch.setattr(
        bridge, "_net_status", lambda **kw: {"kind": "none", "name": "", "signal": None}
    )
    monkeypatch.setattr(
        bridge, "_volume_status", lambda **kw: {"pct": 10, "muted": False}
    )
    assert bridge._sys_stats()["windows"] == []


def test_sys_stats_carries_launchers(monkeypatch):
    import taskbar

    bridge._NET_CACHE.clear()
    bridge._VOL_CACHE.clear()
    monkeypatch.setattr(bridge, "_phone_tailnet", lambda: None)
    monkeypatch.setattr(
        bridge, "_net_status", lambda **kw: {"kind": "none", "name": "", "signal": None}
    )
    monkeypatch.setattr(
        bridge, "_volume_status", lambda **kw: {"pct": 10, "muted": False}
    )
    fake = [{"desktop": "brave-browser.desktop", "name": "Brave"}]
    monkeypatch.setattr(taskbar, "launchers", lambda: fake)
    assert bridge._sys_stats()["launchers"] == fake


def test_sys_stats_launchers_fail_soft(monkeypatch):
    import taskbar

    bridge._NET_CACHE.clear()
    bridge._VOL_CACHE.clear()
    monkeypatch.setattr(bridge, "_phone_tailnet", lambda: None)
    monkeypatch.setattr(
        bridge, "_net_status", lambda **kw: {"kind": "none", "name": "", "signal": None}
    )
    monkeypatch.setattr(
        bridge, "_volume_status", lambda **kw: {"pct": 10, "muted": False}
    )

    def _boom():
        raise RuntimeError("appletsrc unreadable")

    monkeypatch.setattr(taskbar, "launchers", _boom)
    assert bridge._sys_stats()["launchers"] == []


def test_set_school_mode_toggles_meta_shortcut(monkeypatch, tmp_path):
    import school as _school

    # Never touch the live ~/.jarvis/mode.json from tests.
    real_set = _school.set_mode
    monkeypatch.setattr(
        _school, "set_mode", lambda mode, **kw: real_set(mode, home=tmp_path)
    )
    calls = []
    monkeypatch.setattr(
        _school, "meta_enter_school", lambda **kw: calls.append("enter") or True
    )
    monkeypatch.setattr(
        _school, "meta_exit_school", lambda **kw: calls.append("exit") or True
    )
    import projects as _projects

    monkeypatch.setattr(_projects, "shell_verb", lambda *a, **k: True)
    import system

    monkeypatch.setattr(system, "log_action", lambda *a, **k: None)
    assert bridge.set_school_mode("school") == {"mode": "school"}
    assert bridge.set_school_mode("normal") == {"mode": "normal"}
    assert calls == ["enter", "exit"]


def test_sys_stats_never_starts_real_listener(monkeypatch):
    """_sys_stats must not RequestName org.jarvis.Focus under pytest."""
    import active_window

    bridge._NET_CACHE.clear()
    bridge._VOL_CACHE.clear()

    def _boom():
        raise AssertionError("real D-Bus listener must not start in tests")

    monkeypatch.setattr(active_window, "ensure_listener", _boom)
    monkeypatch.setattr(active_window, "windows", lambda: [])
    monkeypatch.setattr(bridge, "_phone_tailnet", lambda: None)
    monkeypatch.setattr(
        bridge, "_net_status", lambda **kw: {"kind": "none", "name": "", "signal": None}
    )
    monkeypatch.setattr(
        bridge, "_volume_status", lambda **kw: {"pct": 10, "muted": False}
    )
    assert bridge._sys_stats()["windows"] == []


def test_watch_mode_voice_routing(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_WATCH_MODE", "1")
    assert bridge._match_voice_tool("Jarvis, I'm leaving") == (
        "watch_mode",
        {},
        "Watching the laptop, Sir.",
    )
    assert bridge._match_voice_tool("jarvis watch the laptop") == (
        "watch_mode",
        {},
        "Watching the laptop, Sir.",
    )
    hit = bridge._match_voice_tool("jarvis i'm leaving for school at eight remind me")
    assert hit is None or hit[0] != "watch_mode"


def test_watch_mode_voice_routing_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_WATCH_MODE", "0")
    hit = bridge._match_voice_tool("jarvis watch the laptop")
    assert hit is None or hit[0] != "watch_mode"


# --- Drafts window: voice route + GET /drafts + POST /drafts/close ---


def _draft_row(did="m1", status="pending"):
    return {
        "id": did,
        "to": "t@school.edu",
        "subject": "Re: Homework",
        "body": "Done, Sir.",
        "summary": "Teacher asks about homework.",
        "sender": "Teacher <t@school.edu>",
        "created": 1700000000.0,
        "status": status,
        "priority": "normal",
    }


def test_drafts_voice_route_open_and_close(monkeypatch):
    import drafts_ui

    monkeypatch.setattr(drafts_ui, "pending_drafts", lambda: [_draft_row()])
    hit = bridge._match_voice_tool("Jarvis, open my drafts.")
    assert hit is not None and hit[0] == "drafts_ui"
    assert hit[1]["op"] == "open"
    hit = bridge._match_voice_tool("hide my drafts")
    assert hit is not None and hit[0] == "drafts_ui"
    assert hit[1]["op"] == "close"
    # Projects and singular-draft phrases stay off this route.
    assert bridge._match_voice_tool("open research projects")[0] == "projects_ui"
    assert bridge._match_voice_tool("draft a whatsapp") is None or (
        bridge._match_voice_tool("draft a whatsapp")[0] != "drafts_ui"
    )
    assert bridge._match_voice_tool("draft an email") is None or (
        bridge._match_voice_tool("draft an email")[0] != "drafts_ui"
    )


def test_drafts_tool_open_empty_never_shells(monkeypatch):
    import drafts_ui
    import projects

    monkeypatch.setattr(drafts_ui, "pending_drafts", lambda: [])

    def _boom(*a, **k):
        raise AssertionError("shell must not run when empty")

    monkeypatch.setattr(projects, "shell_verb", _boom)
    code, payload = bridge.handle_route({"text": "open my drafts"})
    assert code == 200
    assert payload["reply"] == "No drafts, Sir."
    assert payload["action"]["tool"] == "drafts_ui"
    assert payload["action"]["ok"] is True


def test_drafts_tool_open_nonempty_shells_draftsshow(monkeypatch):
    import drafts_ui
    import projects

    monkeypatch.setattr(drafts_ui, "pending_drafts", lambda: [_draft_row()])
    calls: list = []
    monkeypatch.setattr(
        projects, "shell_verb", lambda verb, *a, **k: calls.append(verb) or True
    )
    code, payload = bridge.handle_route({"text": "show my email drafts"})
    assert code == 200
    assert calls == ["draftsshow"]
    assert payload["action"] == {"tool": "drafts_ui", "ok": True}
    assert payload["reply"]


def test_drafts_http_routes(monkeypatch):
    import drafts_ui

    rows = [_draft_row("m1"), _draft_row("m2", "announced")]
    monkeypatch.setattr(drafts_ui, "pending_drafts", lambda: rows)
    server = _bridge_server()
    try:
        status, body = _get(server, "/drafts", token="test-token")
        assert status == 200 and body["ok"] is True
        assert body["count"] == 2
        assert {d["id"] for d in body["drafts"]} == {"m1", "m2"}
        for key in (
            "id",
            "to",
            "subject",
            "body",
            "summary",
            "sender",
            "created",
            "status",
            "priority",
        ):
            assert key in body["drafts"][0], key
        monkeypatch.setattr(drafts_ui, "pending_drafts", lambda: [])
        sync_calls: list = []
        monkeypatch.setattr(
            drafts_ui,
            "sync_close",
            lambda run=None: sync_calls.append("sync") or True,
        )
        import projects

        monkeypatch.setattr(projects, "shell_verb", lambda *a, **k: True)
        status, body = _post(server, "/drafts/close", {}, token="test-token")
        assert status == 200 and body == {"ok": True, "closed": True}
        assert sync_calls == ["sync"]
        monkeypatch.setattr(drafts_ui, "pending_drafts", lambda: rows)
        monkeypatch.setattr(drafts_ui, "sync_close", lambda run=None: False)
        status, body = _post(server, "/drafts/close", {}, token="test-token")
        assert status == 200 and body == {"ok": True, "closed": False}
    finally:
        server.server_close()


def test_drafts_http_routes_use_same_bearer_gate(monkeypatch):
    import drafts_ui

    monkeypatch.setattr(drafts_ui, "pending_drafts", lambda: [])
    monkeypatch.setattr(drafts_ui, "sync_close", lambda run=None: True)
    server, _ = bridge._run_in_thread(token="s3cret")
    try:
        code, _ = _get(server, "/drafts")
        assert code == 401
        code, body = _get(server, "/drafts", token="s3cret")
        assert code == 200 and body["ok"] is True
        code, _ = _post(server, "/drafts/close", {})
        assert code == 401
        code, body = _post(server, "/drafts/close", {}, token="s3cret")
        assert code == 200 and body["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
