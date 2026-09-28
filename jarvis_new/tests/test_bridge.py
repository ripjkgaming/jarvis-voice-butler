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
    finally:
        server.shutdown()
        server.server_close()


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
            "b": {"OS": "android", "HostName": "pixel", "Online": True,
                  "TailscaleIPs": ["100.2.2.2"]},
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
    ["what is the capital of australia", "what time is it", "2 to the 99999",
     "who wrote romeo and juliet", "open project two"],
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
