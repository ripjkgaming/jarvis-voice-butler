"""Fleet listener: Tailscale identity, trust store, pending queue, routes, discovery."""

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import fleet
import fleet_server

OWNER = "owner@example.com"
PARTNER = "partner@example.com"
STRANGER = "stranger@example.com"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_FLEET_NAME", "main")
    monkeypatch.delenv("JARVIS_FLEET_PARTNERS", raising=False)
    monkeypatch.delenv("JARVIS_FLEET_BIND", raising=False)
    monkeypatch.delenv("JARVIS_FLEET", raising=False)
    monkeypatch.delenv("JARVIS_FLEET_ALLOW_LOCAL", raising=False)
    monkeypatch.setattr(fleet, "_TAILSCALE_BINS", ())
    monkeypatch.setattr(fleet, "own_account", lambda: OWNER)
    monkeypatch.setattr(fleet, "_discover_cache", None)
    fleet_server._whois_cache.clear()
    monkeypatch.setattr(fleet_server, "_own", None)


WHO: dict[str, tuple[str, str]] = {}  # client ip -> (login, node)


@pytest.fixture
def who(monkeypatch):
    """Fake `tailscale whois`: WHO maps a source IP to (login, node)."""

    def fake(ip):
        entry = WHO.get(ip)
        if entry is None:
            return None
        return {
            "UserProfile": {"LoginName": entry[0]},
            "Node": {"ComputedName": entry[1]},
        }

    WHO.clear()
    monkeypatch.setattr(fleet_server, "_whois_fn", fake)
    return WHO


def _serve(monkeypatch, chat_reply="Spotify is open.", tailnet=True):
    """Listener on 127.0.0.1. Loopback counts as tailnet only when `tailnet` is set."""
    calls = {"chat": [], "tool": []}

    def chat(text, sender):
        calls["chat"].append((text, sender))
        return chat_reply

    def tool(name, args):
        calls["tool"].append((name, args))
        if name == "lock":
            return {"ok": True, "tool": name, "result": "locked"}
        return {"ok": False, "error": f"unknown tool: {name}"}

    if tailnet:
        monkeypatch.setattr(fleet_server, "is_tailnet_ip", lambda ip: True)
    server = fleet_server.make_server(chat, tool, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    return base, calls, server


@pytest.fixture
def listener(monkeypatch, who):
    base, calls, server = _serve(monkeypatch)
    yield base, calls
    server.shutdown()
    server.server_close()


def _req(base, method, path, body=None, raw=None):
    data = (
        raw
        if raw is not None
        else (json.dumps(body).encode() if body is not None else None)
    )
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_tailnet_ip_ranges():
    for ok in (
        "100.64.0.1",
        "100.127.255.254",
        "fd7a:115c:a1e0::1",
        "::ffff:100.64.0.1",
    ):
        assert fleet_server.is_tailnet_ip(ok), ok
    for bad in (
        "100.128.0.1",
        "192.168.1.5",
        "127.0.0.1",
        "fd7a:115c:a1e1::1",
        "example.com",
        "",
    ):
        assert not fleet_server.is_tailnet_ip(bad), bad


def test_whois_parsed_lowercased_and_cached(monkeypatch):
    calls = []

    def fake(ip):
        calls.append(ip)
        return {
            "UserProfile": {"LoginName": "Owner@Example.com"},
            "Node": {"ComputedName": "Laptop"},
        }

    monkeypatch.setattr(fleet_server, "_whois_fn", fake)
    assert fleet_server.whois("100.64.0.9") == {"login": OWNER, "node": "Laptop", "sharee": False}
    assert fleet_server.whois("100.64.0.9")["login"] == OWNER
    assert calls == ["100.64.0.9"]  # second lookup served from the 60 s cache


def test_whois_unknown_and_missing_login(monkeypatch):
    monkeypatch.setattr(fleet_server, "_whois_fn", lambda ip: {"UserProfile": {}})
    assert fleet_server.whois("100.64.0.8") is None
    monkeypatch.setattr(fleet_server, "_whois_fn", lambda ip: None)
    fleet_server._whois_cache.clear()
    assert fleet_server.whois("100.64.0.7") is None


def test_non_tailnet_socket_rejected_even_with_forwarded_header(monkeypatch, who):
    who["127.0.0.1"] = (OWNER, "main")  # identity exists, but the IP is not tailnet
    base, _calls, server = _serve(monkeypatch, tailnet=False)
    try:
        req = urllib.request.Request(
            base + "/fleet/hello", headers={"X-Forwarded-For": "100.64.0.5"}
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 403
        assert fleet_server.pending() == []
    finally:
        server.shutdown()
        server.server_close()


def test_own_account_trusted_and_hello_reports_identity(listener, who):
    base, _calls = listener
    who["127.0.0.1"] = (OWNER, "laptop")
    code, body = _req(base, "GET", "/fleet/hello")
    assert code == 200 and body["ok"] is True
    assert body["trusted"] is True and body["account"] == OWNER
    assert body["name"] == "main" and body["version"] == 1
    assert body["os"] in ("linux", "windows", "macos", sys.platform)
    assert "token" not in json.dumps(body).lower()
    code, body = _req(base, "GET", "/fleet/tools")
    assert code == 200 and "screenshot" in body["tools"]


def test_untrusted_gets_pending_403_and_hello_still_works(listener, who):
    base, calls = listener
    who["127.0.0.1"] = (STRANGER, "Stranger-Box")
    code, body = _req(base, "GET", "/fleet/hello")
    assert code == 200 and body["trusted"] is False and body["account"] == OWNER
    code, body = _req(base, "GET", "/fleet/tools")
    assert code == 403 and body == {
        "ok": False,
        "pending": True,
        "error": "awaiting approval",
    }
    code, body = _req(base, "POST", "/fleet/chat", {"text": "hi", "from": "x"})
    assert code == 403 and body["pending"] is True
    code, body = _req(base, "POST", "/fleet/tool", {"tool": "lock"})
    assert code == 403 and calls["chat"] == [] and calls["tool"] == []
    pend = fleet_server.pending()
    assert len(pend) == 1
    assert pend[0]["account"] == STRANGER and pend[0]["node"] == "Stranger-Box"
    assert (
        pend[0]["ip"] == "127.0.0.1" and pend[0]["first_seen"] <= pend[0]["last_seen"]
    )
    assert set(pend[0]) == {"account", "node", "ip", "first_seen", "last_seen"}


def test_partner_from_config_is_trusted(listener, who, tmp_path):
    base, calls = listener
    (tmp_path / "fleet").mkdir()
    (tmp_path / "fleet" / "config.json").write_text(
        json.dumps({"partner_accounts": [PARTNER]})
    )
    who["127.0.0.1"] = (PARTNER, "partner-pc")
    code, body = _req(base, "GET", "/fleet/hello")
    assert body["trusted"] is True
    code, body = _req(
        base, "POST", "/fleet/chat", {"text": "open spotify", "from": "Office PC"}
    )
    assert code == 200 and body == {"ok": True, "reply": "Spotify is open."}
    assert calls["chat"] == [("open spotify", "office-pc")]
    assert fleet_server.pending() == []


def test_pending_capped_at_twenty_and_keeps_newest(monkeypatch):
    for i in range(25):
        fleet_server._note_pending(f"user{i}@example.com", f"box-{i}", "100.64.0.9")
    pend = fleet_server.pending()
    assert len(pend) == 20
    accounts = [p["account"] for p in pend]
    assert accounts[0] == "user5@example.com" and accounts[-1] == "user24@example.com"


def test_repeat_pending_updates_one_entry(monkeypatch):
    fleet_server._note_pending(STRANGER, "box", "100.64.0.9")
    fleet_server._note_pending(STRANGER, "box", "100.64.0.10")
    pend = fleet_server.pending()
    assert len(pend) == 1 and pend[0]["ip"] == "100.64.0.10"


def test_approve_then_trusted_then_pending_cleared(who):
    fleet_server._note_pending(STRANGER, "laptop", "100.64.0.9")
    assert not fleet_server.trusted(STRANGER)
    res = fleet_server.approve("laptop")
    assert res == {"ok": True, "account": STRANGER, "name": "laptop"}
    assert fleet_server.trusted(STRANGER)
    assert fleet_server.pending() == []


def test_approve_by_account_and_prefix(who):
    fleet_server._note_pending(STRANGER, "office-pc", "100.64.0.9")
    assert fleet_server.approve(STRANGER)["ok"]
    assert fleet_server.pending() == []
    fleet_server._note_pending("other@example.com", "office-laptop", "100.64.0.9")
    res = fleet_server.approve("office")  # no exact node match: unique 3+ char prefix
    assert res["ok"] and res["account"] == "other@example.com"
    fleet_server._note_pending("a@example.com", "ab-box", "100.64.0.9")
    assert (
        fleet_server.approve("ab")["ok"] is False
    )  # prefix under 3 chars never matches


def test_deny_removes_pending_but_does_not_trust(who):
    fleet_server._note_pending(STRANGER, "laptop", "100.64.0.9")
    res = fleet_server.deny("laptop")
    assert res["ok"] and res["account"] == STRANGER
    assert fleet_server.pending() == []
    assert not fleet_server.trusted(STRANGER)
    assert fleet_server.deny("laptop")["ok"] is False


def test_approve_unknown_and_ambiguous():
    assert fleet_server.approve("")["ok"] is False
    assert "no pending" in fleet_server.approve("nobody")["error"]
    fleet_server._note_pending(STRANGER, "laptop", "100.64.0.9")
    fleet_server._note_pending(PARTNER, "laptop", "100.64.0.9")
    res = fleet_server.approve("laptop")
    assert res["ok"] is False and "more than one" in res["error"]
    assert len(fleet_server.pending()) == 2


def test_chat_and_tool_routes_for_trusted(listener, who):
    base, calls = listener
    who["127.0.0.1"] = (OWNER, "laptop")
    code, body = _req(
        base, "POST", "/fleet/chat", {"text": "open spotify", "from": "Office PC"}
    )
    assert code == 200 and body == {"ok": True, "reply": "Spotify is open."}
    assert calls["chat"] == [("open spotify", "office-pc")]
    code, body = _req(
        base, "POST", "/fleet/tool", {"tool": "lock", "args": {"delay": 5}}
    )
    assert code == 200 and body["result"] == "locked"
    assert calls["tool"] == [("lock", {"delay": 5})]
    code, body = _req(base, "POST", "/fleet/tool", {"tool": "nuke"})
    assert code == 400 and body["ok"] is False


def test_bad_requests_rejected(listener, who):
    base, _calls = listener
    who["127.0.0.1"] = (OWNER, "laptop")
    assert _req(base, "POST", "/fleet/chat", {"text": "  "})[0] == 400
    assert _req(base, "POST", "/fleet/chat", {"text": "x" * 2001})[0] == 400
    assert _req(base, "POST", "/fleet/chat", raw=b"[1,2]")[0] == 400
    assert _req(base, "POST", "/fleet/chat", raw=b"{not json")[0] == 400
    assert _req(base, "POST", "/fleet/tool", {"args": {}})[0] == 400
    assert _req(base, "POST", "/fleet/chat", raw=b"{" + b" " * 9000 + b"}")[0] == 413
    assert _req(base, "GET", "/fleet/unknown")[0] == 404


def test_chat_failure_hides_detail(monkeypatch, who):
    who["127.0.0.1"] = (OWNER, "laptop")

    def boom(text, sender):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(fleet_server, "is_tailnet_ip", lambda ip: True)
    server = fleet_server.make_server(boom, lambda n, a: {"ok": True}, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        code, body = _req(
            f"http://127.0.0.1:{server.server_port}",
            "POST",
            "/fleet/chat",
            {"text": "hi"},
        )
        assert code == 502 and body == {"ok": False, "error": "chat failed"}
    finally:
        server.shutdown()
        server.server_close()


def test_bind_host_rules(monkeypatch):
    monkeypatch.setattr(fleet, "_TAILSCALE_BINS", ())
    assert fleet_server.bind_host() is None
    monkeypatch.setenv("JARVIS_FLEET_BIND", "100.64.0.9")
    assert fleet_server.bind_host() == "100.64.0.9"
    monkeypatch.setenv("JARVIS_FLEET_BIND", "0.0.0.0")
    with pytest.raises(RuntimeError):
        fleet_server.bind_host()
    monkeypatch.setenv("JARVIS_FLEET_BIND", "::")
    with pytest.raises(RuntimeError):
        fleet_server.bind_host()


def test_tailscale_ip_from_cli(monkeypatch):
    monkeypatch.setattr(fleet, "_TAILSCALE_BINS", ("tailscale",))

    def run(args, **_kw):
        return subprocess.CompletedProcess(
            args, 0, stdout="100.90.1.2\nfd7a::1\n", stderr=""
        )

    monkeypatch.setattr(fleet_server.subprocess, "run", run)
    assert fleet_server.tailscale_ip4() == "100.90.1.2"
    monkeypatch.setattr(
        fleet_server.subprocess,
        "run",
        lambda args, **_kw: subprocess.CompletedProcess(
            args, 0, stdout="192.168.1.9\n", stderr=""
        ),
    )
    assert fleet_server.tailscale_ip4() is None


def test_start_in_thread_disabled_by_env(monkeypatch):
    monkeypatch.setenv("JARVIS_FLEET", "0")
    assert fleet_server.start_in_thread(lambda t, s: "", lambda n, a: {}) is None


def test_start_in_thread_listens_on_configured_bind(monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        free = sock.getsockname()[1]
    monkeypatch.setenv("JARVIS_FLEET_BIND", "127.0.0.1")
    monkeypatch.setenv("JARVIS_FLEET_PORT", str(free))
    thread = fleet_server.start_in_thread(lambda t, s: "", lambda n, a: {})
    assert thread is not None
    deadline = time.monotonic() + 5
    while True:
        try:
            _req(f"http://127.0.0.1:{free}", "GET", "/fleet/hello")
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)
    # Loopback is not a tailnet address, so the live listener refuses it.
    assert _req(f"http://127.0.0.1:{free}", "GET", "/fleet/hello")[0] == 403


def test_discovery_and_token_less_ask_round_trip(monkeypatch, listener, who):
    base, calls = listener
    port = base.rsplit(":", 1)[1]
    who["127.0.0.1"] = (OWNER, "main")
    monkeypatch.setenv("JARVIS_FLEET_ALLOW_LOCAL", "1")
    monkeypatch.setenv("JARVIS_FLEET_PORT", port)
    status = {
        "Self": {"UserID": 1, "HostName": "laptop"},
        "User": {"1": {"LoginName": OWNER}},
        "Peer": {
            "n1": {
                "HostName": "Main",
                "OS": "linux",
                "Online": True,
                "TailscaleIPs": ["127.0.0.1"],
                "UserID": 1,
            }
        },
    }
    monkeypatch.setattr(fleet, "_TAILSCALE_BINS", ("tailscale",))
    monkeypatch.setattr(
        fleet.subprocess,
        "run",
        lambda args, **_kw: subprocess.CompletedProcess(
            args, 0, stdout=json.dumps(status), stderr=""
        ),
    )
    found = fleet.discover()
    assert found["main"]["trusted"] is True and found["main"]["url"] == base
    assert fleet.ping("main")["ok"] is True
    assert fleet.ask("main", "open spotify") == {
        "ok": True,
        "name": "main",
        "reply": "Spotify is open.",
    }
    assert calls["chat"] == [("open spotify", "main")]
    devs = {d["name"]: d for d in fleet.devices()}
    assert devs["main"]["jarvis"] is True and devs["main"]["trusted"] is True
    assert devs["main"]["paired"] is False
    assert "token" not in json.dumps(list(devs.values())).lower()


def test_discovered_peer_pending_reports_clear_error(monkeypatch, listener, who):
    base, _calls = listener
    port = base.rsplit(":", 1)[1]
    who["127.0.0.1"] = (STRANGER, "laptop")  # listener does not trust this caller
    monkeypatch.setenv("JARVIS_FLEET_ALLOW_LOCAL", "1")
    monkeypatch.setenv("JARVIS_FLEET_PORT", port)
    monkeypatch.setattr(fleet, "_TAILSCALE_BINS", ("tailscale",))
    status = {
        "Self": {"UserID": 1},
        "User": {"1": {"LoginName": OWNER}},
        "Peer": {
            "n1": {
                "HostName": "main",
                "OS": "linux",
                "Online": True,
                "TailscaleIPs": ["127.0.0.1"],
                "UserID": 1,
            }
        },
    }
    monkeypatch.setattr(
        fleet.subprocess,
        "run",
        lambda args, **_kw: subprocess.CompletedProcess(
            args, 0, stdout=json.dumps(status), stderr=""
        ),
    )
    res = fleet.ask("main", "hi")
    assert res["ok"] is False and res["pending"] is True
    assert (
        res["error"]
        == "main has not approved this device yet; approve it on that machine"
    )
    assert fleet_server.pending()[0]["account"] == STRANGER


def test_shared_to_account_trusted_without_approval(monkeypatch, tmp_path):
    import fleet_server as fs

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(fs, "_own_login", lambda: "me@example.com")
    monkeypatch.delenv("JARVIS_FLEET_TRUST_SHARED", raising=False)
    parsed = fs._parse_whois(
        {
            "UserProfile": {"LoginName": "Partner@Example.com"},
            "Node": {"ComputedName": "laptop", "Hostinfo": {"ShareeNode": True}},
        }
    )
    assert parsed["sharee"] is True
    assert fs.trusted(parsed["login"], parsed["sharee"]) is True
    assert fs.trusted("stranger@example.com", False) is False
    monkeypatch.setenv("JARVIS_FLEET_TRUST_SHARED", "0")
    assert fs.trusted(parsed["login"], parsed["sharee"]) is False
    plain = fs._parse_whois({"UserProfile": {"LoginName": "a@example.com"}, "Node": {}})
    assert plain["sharee"] is False
