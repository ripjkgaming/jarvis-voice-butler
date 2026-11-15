"""Fleet: pairing store, tailnet-only URLs, voice parsing, peer round trip."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import fleet  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_FLEET_NAME", "main")
    monkeypatch.setattr(fleet, "tailnet_peers", lambda: [])


def test_valid_url_tailnet_only(monkeypatch):
    assert fleet.valid_url("http://100.101.2.3:4317") == "http://100.101.2.3:4317"
    assert fleet.valid_url("http://100.101.2.3") == "http://100.101.2.3:4317"
    assert fleet.valid_url("https://box.tail1234.ts.net") == "https://box.tail1234.ts.net:4317"
    for bad in ("http://192.168.1.5:4317", "http://example.com", "ftp://100.1.1.1", "http://127.0.0.1:4317"):
        assert fleet.valid_url(bad) is None


def test_add_remove_and_no_token_leak():
    assert fleet.add("Office PC", "http://100.64.0.9:4317", "tok123")["name"] == "office-pc"
    assert not fleet.add("x", "http://8.8.8.8:4317", "t")["ok"]
    assert not fleet.add("x", "http://100.64.0.9", "has space")["ok"]
    devs = fleet.devices()
    assert devs[0]["name"] == "office-pc" and "token" not in json.dumps(devs)
    assert fleet.remove("office-pc")["ok"] and not fleet.remove("office-pc")["ok"]


def test_handle_requires_paired_device():
    assert fleet.handle("ask office to open spotify") is None
    assert fleet.handle("list my devices") == "No other devices are paired yet, Sir."


class _Peer(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply(200, {"ok": True})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        _Peer.seen.append((self.headers.get("Authorization"), json.loads(self.rfile.read(n))))
        if self.headers.get("Authorization") != "Bearer good":
            return self._reply(401, {"ok": False})
        self._reply(200, {"ok": True, "reply": "Spotify is open."})


@pytest.fixture
def peer(monkeypatch):
    monkeypatch.setenv("JARVIS_FLEET_ALLOW_LOCAL", "1")
    server = HTTPServer(("127.0.0.1", 0), _Peer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Peer.seen.clear()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_round_trip_voice_and_loop_marker(peer):
    assert fleet.add("office", peer, "good")["ok"]
    assert fleet.handle("Ask the office laptop to open spotify.") == "Spotify is open."
    auth, body = _Peer.seen[-1]
    assert auth == "Bearer good"
    assert body == {"text": "open spotify", "client_id": "fleet:main"}
    assert fleet.handle("is office online?") == "office is online."
    assert "Your devices: office" in fleet.handle("list my devices")


def test_bad_token_and_unreachable(peer):
    fleet.add("office", peer, "wrong")
    assert "re-pair" in fleet.ask("office", "hi")["error"]
    fleet.add("gone", "http://100.64.0.77:1", "x")
    assert "unreachable" in fleet.ask("gone", "hi", timeout=0.2)["error"]


def test_bridge_routes_owner_only():
    import bridge

    code, res = bridge._fleet_post("/fleet/add", {"name": "a", "url": "http://100.64.0.2", "token": "t", "guest": True})
    assert code == 403
    code, res = bridge._fleet_post("/fleet/add", {"name": "a", "url": "http://100.64.0.2", "token": "t"})
    assert code == 200
    code, res = bridge._fleet_get("/fleet/devices")
    assert code == 200 and res["devices"][0]["name"] == "a" and "token" not in json.dumps(res)
    assert bridge._fleet_post("/fleet/nope", {})[0] == 404
