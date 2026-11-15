"""Fleet: pairing store, tailnet-only URLs, voice parsing, peer round trip, cross-account."""

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import fleet  # noqa: E402

OWNER = "owner@example.com"
PARTNER = "partner@example.com"

# Fake `tailscale status --json`: owner's own node, one peer on the same account,
# one shared peer on the partner account, one peer with an unknown user.
STATUS = {
    "Self": {"UserID": 1, "HostName": "main"},
    "User": {
        "1": {"LoginName": OWNER},
        "2": {"LoginName": PARTNER.upper()},
    },
    "Peer": {
        "nodeA": {
            "HostName": "Office",
            "OS": "linux",
            "Online": True,
            "TailscaleIPs": ["100.64.0.5", "fd7a:115c:a1e0::5"],
            "UserID": 1,
        },
        "nodeB": {
            "HostName": "Partner-PC",
            "OS": "Windows",
            "Online": False,
            "TailscaleIPs": ["100.64.0.6"],
            "UserID": 2,
        },
        "nodeC": {
            "HostName": "Loose-Box",
            "OS": "macOS",
            "Online": True,
            "TailscaleIPs": ["100.64.0.7"],
            "UserID": 9,
        },
    },
}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_FLEET_NAME", "main")
    monkeypatch.delenv("JARVIS_FLEET_PARTNERS", raising=False)
    monkeypatch.setattr(fleet, "_TAILSCALE_BINS", ())  # no tailscale binary: fail-soft


def _fake_tailscale(monkeypatch, status=STATUS, ip="100.64.0.1"):
    """Make `tailscale status --json` and `tailscale ip -4` answer from fixtures."""
    monkeypatch.setattr(fleet, "_TAILSCALE_BINS", ("tailscale",))

    def run(args, **_kwargs):
        if args[1:3] == ["ip", "-4"]:
            return subprocess.CompletedProcess(args, 0, stdout=ip + "\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(status), stderr="")

    monkeypatch.setattr(fleet.subprocess, "run", run)


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


def test_partner_accounts_from_config_and_env(tmp_path, monkeypatch):
    (tmp_path / "fleet").mkdir()
    (tmp_path / "fleet" / "config.json").write_text(
        json.dumps({"partner_accounts": [" Partner@Example.com ", PARTNER, "not-an-email", 5, ""]})
    )
    monkeypatch.setenv("JARVIS_FLEET_PARTNERS", "Other@Example.com, ,broken, other@example.com")
    assert fleet.partner_accounts() == [PARTNER, "other@example.com"]


def test_partner_accounts_empty_and_corrupt_config(tmp_path):
    assert fleet.partner_accounts() == []
    (tmp_path / "fleet").mkdir()
    (tmp_path / "fleet" / "config.json").write_text("[not json")
    assert fleet.config() == {}
    assert fleet.partner_accounts() == []


def test_tailnet_peers_account_and_shared(monkeypatch):
    _fake_tailscale(monkeypatch)
    peers = {p["name"]: p for p in fleet.tailnet_peers()}
    assert peers["office"]["account"] == OWNER and peers["office"]["shared"] is False
    assert peers["partner-pc"]["account"] == PARTNER and peers["partner-pc"]["shared"] is True
    assert peers["partner-pc"]["online"] is False and peers["partner-pc"]["ip"] == "100.64.0.6"
    assert peers["loose-box"]["account"] == "" and peers["loose-box"]["shared"] is True


def test_devices_flags_partner_shared_and_account(monkeypatch):
    _fake_tailscale(monkeypatch)
    monkeypatch.setenv("JARVIS_FLEET_PARTNERS", PARTNER)
    assert fleet.add("partner-pc", "http://100.64.0.6:4317", "tok")["ok"]
    devs = {d["name"]: d for d in fleet.devices()}
    pc = devs["partner-pc"]
    assert pc["paired"] and pc["account"] == PARTNER and pc["shared"] and pc["partner"]
    office = devs["office"]
    assert not office["paired"] and office["account"] == OWNER
    assert office["shared"] is False and office["partner"] is False
    assert "token" not in json.dumps(list(devs.values()))


def test_self_info_reports_own_account(monkeypatch):
    _fake_tailscale(monkeypatch, ip="100.64.0.1")
    info = fleet.self_info()
    assert info["account"] == OWNER
    assert info["url"] == "http://100.64.0.1:4317"
    assert fleet.own_account() == OWNER
    assert "token" not in json.dumps(info)


def test_own_account_unknown_without_tailscale():
    assert fleet.own_account() == ""
    assert fleet.self_info()["account"] == ""


def test_share_info_steps_with_and_without_partner(monkeypatch):
    res = fleet.share_info()
    assert res["ok"] and res["partners"] == []
    assert any("<partner account>" in step for step in res["steps"])
    assert "login.tailscale.com/admin/machines" in res["steps"][0]

    monkeypatch.setenv("JARVIS_FLEET_PARTNERS", PARTNER)
    res = fleet.share_info()
    assert res["partners"] == [PARTNER]
    assert any(PARTNER in step for step in res["steps"])
    assert not any("<partner account>" in step for step in res["steps"])


class _Peer(BaseHTTPRequestHandler):
    seen: list = []
    last_path = ""

    def log_message(self, *a):
        pass

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _Peer.last_path = self.path
        if self.path == "/fleet/tools":
            if self.headers.get("Authorization") != "Bearer good":
                return self._reply(401, {"ok": False})
            return self._reply(200, {"ok": True, "tools": ["lock_screen"]})
        self._reply(200, {"ok": True})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        _Peer.last_path = self.path
        _Peer.seen.append((self.headers.get("Authorization"), body))
        if self.headers.get("Authorization") != "Bearer good":
            return self._reply(401, {"ok": False})
        if self.path == "/fleet/tool":
            if body.get("tool") != "lock_screen":
                return self._reply(404, {"ok": False, "error": "unknown tool"})
            return self._reply(
                200, {"ok": True, "tool": body["tool"], "args": body["args"], "result": "locked"}
            )
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


def test_call_tool_and_list_tools_round_trip(peer):
    assert fleet.add("office", peer, "good")["ok"]
    res = fleet.call_tool("office", "lock_screen", {"delay": 5})
    assert res == {"ok": True, "tool": "lock_screen", "args": {"delay": 5}, "result": "locked"}
    assert _Peer.last_path == "/fleet/tool"
    assert _Peer.seen[-1] == ("Bearer good", {"tool": "lock_screen", "args": {"delay": 5}})
    assert fleet.call_tool("office", "lock_screen")["args"] == {}
    assert fleet.call_tool("office", "nuke")["ok"] is False
    assert fleet.call_tool("office", "")["ok"] is False
    assert fleet.call_tool("office", "lock_screen", ["not", "a", "dict"])["ok"] is False
    assert fleet.list_tools("office")["tools"] == ["lock_screen"]
    assert _Peer.last_path == "/fleet/tools"
    assert fleet.call_tool("nobody", "lock_screen")["ok"] is False


def test_cli_usage_and_bad_json_args(capsys):
    assert fleet._cli(["tool", "office", "lock_screen", "{not json"]) == 2
    assert fleet._cli(["tool", "office"]) == 2
    assert fleet._cli(["tools"]) == 2
    capsys.readouterr()
    assert fleet._cli(["share-info"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["ok"] and info["account"] == "" and info["steps"]


def test_bridge_routes_owner_only():
    import bridge

    code, res = bridge._fleet_post("/fleet/add", {"name": "a", "url": "http://100.64.0.2", "token": "t", "guest": True})
    assert code == 403
    code, res = bridge._fleet_post("/fleet/add", {"name": "a", "url": "http://100.64.0.2", "token": "t"})
    assert code == 200
    code, res = bridge._fleet_get("/fleet/devices")
    assert code == 200 and res["devices"][0]["name"] == "a" and "token" not in json.dumps(res)
    assert res["partners"] == 0 and "account" in res
    assert bridge._fleet_post("/fleet/nope", {})[0] == 404


def test_bridge_share_and_partner_count(monkeypatch):
    import bridge

    monkeypatch.setenv("JARVIS_FLEET_PARTNERS", PARTNER)
    code, res = bridge._fleet_get("/fleet/share")
    assert code == 200 and res["ok"] and res["partners"] == [PARTNER] and res["steps"]
    code, res = bridge._fleet_get("/fleet/devices")
    assert code == 200 and res["partners"] == 1 and res["account"] == ""
    assert PARTNER not in json.dumps(bridge._fleet_get("/fleet/devices")[1])


def test_voice_open_devices_window(monkeypatch):
    import projects

    seen = []
    monkeypatch.setattr(projects, "shell_verb", lambda v, *a, **k: seen.append(v) or True)
    assert fleet.handle("open connected devices") == "Connected devices on screen."
    assert fleet.handle("close devices") == "Devices closed."
    assert seen == ["devicesshow", "deviceshide"]


def test_bridge_tool_routes():
    import bridge

    code, res = bridge._fleet_get("/fleet/tools")
    assert code == 200 and "screenshot" in res["tools"]
    assert bridge._fleet_post("/fleet/tool", {"tool": "nope"})[0] == 400
    assert bridge._fleet_post("/fleet/tool", {"tool": "sysinfo", "guest": True})[0] == 403
