"""Tests for the JarvisLink phone API (bridge routes + mic uplink)."""

import base64
import json
import sys
import time
import urllib.request

import pytest

sys.path.insert(0, "src")

import bridge
from bridge import _parse_volume_pct, resolve_bind, run_phone_tool, store_camera_frame


def _post(server, path, body, token=""):
    port = server.server_address[1]
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method="POST"
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.load(resp)
    except Exception as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return 0, {}


def test_resolve_bind_fails_closed_without_token(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_BRIDGE_BIND", "0.0.0.0")
    monkeypatch.delenv("JARVIS_BRIDGE_TOKEN", raising=False)
    host, required = resolve_bind()
    assert host == "0.0.0.0" and required is True
    monkeypatch.delenv("JARVIS_BRIDGE_BIND", raising=False)
    host, required = resolve_bind()
    assert (host, required) == ("127.0.0.1", False)


def test_volume_pct_parsing() -> None:
    assert _parse_volume_pct("Volume: front-left: 65536 / 100%") == 100
    assert (
        _parse_volume_pct("front-left: 32768 /  50%, front-right: 32768 /  50%") == 50
    )
    assert _parse_volume_pct("muted") is None


def test_unknown_tool_rejected() -> None:
    assert run_phone_tool("format_disk", {})["ok"] is False
    assert run_phone_tool("open_app", {"app": "evil"})["ok"] is False


def test_type_and_tool_routes_with_stubbed_runner(monkeypatch) -> None:
    calls = []

    def fake_run(argv, timeout=10.0):
        calls.append(argv)
        return 0, "", ""

    monkeypatch.setattr(bridge, "_run", fake_run)
    monkeypatch.setattr(bridge, "_which", lambda name: f"/usr/bin/{name}")
    server, _ = bridge._run_in_thread()
    try:
        code, body = _post(server, "/type", {"text": "hello"})
        assert code == 200 and body["ok"] is True
        assert calls[0][:2] == ["wtype", "--"]
        code, body = _post(server, "/type", {"text": ""})
        assert code == 400
        code, body = _post(server, "/tool", {"tool": "volume_up"})
        assert code == 200 and body["ok"] is True
        code, body = _post(server, "/tool", {"tool": "nope"})
        assert code == 400 and body["ok"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_camera_frame_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    png_1px = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()
    result = store_camera_frame(png_1px)
    assert result["ok"] is True
    assert (tmp_path / ".jarvis" / "phone_cam" / "latest.jpg").is_file()
    assert store_camera_frame("!!!")["ok"] is False
    server, _ = bridge._run_in_thread()
    try:
        port = server.server_address[1]
        req = urllib.request.Request(f"http://127.0.0.1:{port}/camera/latest")
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            assert resp.headers.get_content_type() == "image/jpeg"
            assert len(resp.read()) > 0
    finally:
        server.shutdown()
        server.server_close()


def test_talk_rejects_bad_bodies() -> None:
    server, _ = bridge._run_in_thread()
    try:
        code, _ = _post(server, "/talk", {})
        assert code == 400
        code, _ = _post(server, "/talk", {"audio_b64": "!!!", "rate": 16000})
        assert code == 400
        code, _ = _post(server, "/talk", {"audio_b64": "aGVsbG8=", "rate": 44100})
        assert code == 400
    finally:
        server.shutdown()
        server.server_close()


class _StubModel:
    """openWakeWord stand-in: fires once on the 3rd predict call."""

    def __init__(self) -> None:
        self.calls = 0

    def predict(self, frame):
        self.calls += 1
        return {"hey_jarvis": 0.99 if self.calls == 3 else 0.01}


def test_mic_handshake_validation() -> None:
    sys.path.insert(0, "src")
    from mic_uplink import parse_handshake

    assert parse_handshake(b'{"rate": 16000, "channels": 1}\n') == (16000, 1)
    with pytest.raises(ValueError):
        parse_handshake(b'{"rate": 44100, "channels": 1}')
    with pytest.raises(ValueError):
        parse_handshake(b"not json")


def test_mic_serve_client_wake_and_cooldown() -> None:
    import socket
    import threading

    sys.path.insert(0, "src")
    from mic_uplink import serve_client

    server_sock, client_sock = socket.socketpair()
    model = _StubModel()
    thread = threading.Thread(
        target=serve_client,
        args=(server_sock, ("127.0.0.1", 9), model, 0.5),
        daemon=True,
    )
    thread.start()
    client_sock.settimeout(5)
    client_sock.sendall(b'{"rate": 16000, "channels": 1}\n')
    assert json.loads(client_sock.recv(256).decode()) == {"ok": True}
    pcm = (b"\x00\x00" * 1280) * 8  # 8 native frames of silence
    client_sock.sendall(pcm)
    client_sock.sendall(pcm)  # 16 frames total -> fires on 3rd, cooldown eats rest
    time.sleep(0.5)
    client_sock.setblocking(False)
    got = b""
    try:
        while True:
            chunk = client_sock.recv(256)
            if not chunk:
                break
            got += chunk
    except BlockingIOError:
        pass
    wake_lines = [ln for ln in got.decode().splitlines() if ln.startswith("WAKE")]
    assert len(wake_lines) == 1  # exactly one: cooldown suppresses the rest
    assert model.calls >= 3
    client_sock.close()
