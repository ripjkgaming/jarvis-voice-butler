"""Fleet listener: zero-setup linking by Tailscale identity (no tokens).

Every device runs this listener on TCP port 4318 (``JARVIS_FLEET_PORT``),
bound ONLY to its tailnet IP (``tailscale ip -4``, or ``JARVIS_FLEET_BIND``).
The main bridge on 4317 is not touched.

Callers are identified by socket peer address only (forwarded headers are never
read). The peer IP must be in 100.64.0.0/10 or fd7a:115c:a1e0::/48, and
``tailscale whois --json <ip>`` must name its login. A login is TRUSTED when:

- it equals this device's own Tailscale login (same account, automatic);
- it is in partner accounts (``fleet/config.json`` key ``partner_accounts`` or
  env ``JARVIS_FLEET_PARTNERS``); config presence is pre-approved consent; or
- the owner approved it earlier (``fleet/trust.json`` ``accounts``).

Any other caller is recorded as PENDING (``fleet/trust.json`` ``pending``, max
20 entries) and gets HTTP 403 ``{"ok": false, "pending": true, ...}``. The owner
approves per account with ``fleet.py approve NAME``, voice, or the bridge.

Routes (JSON, every reply has ``ok``):
    GET  /fleet/hello   any tailnet caller; records pending when untrusted
    GET  /fleet/tools   trusted
    POST /fleet/chat    trusted  {"text", "from"} -> {"ok", "reply"}
    POST /fleet/tool    trusted  {"tool", "args"} -> fleet_tools.run result

Stdlib only. Runs in the bridge process (``start_in_thread``) or standalone
(``python src/fleet_server.py``, same as ``python src/fleet.py serve``).
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import fleet

WHOIS_TTL = 60.0
PENDING_MAX = 20
MAX_BODY = 8192
MAX_TEXT = 2000
SOCKET_TIMEOUT = 30.0
RETRY_SECONDS = 15.0
BRIDGE_PORT_DEFAULT = 4317

_LOCK = threading.RLock()
# Test seam: callable(ip) -> `tailscale whois --json` style dict, or None.
_whois_fn = None
_whois_cache: dict[str, tuple[float, dict | None]] = {}
_own: tuple[float, str] | None = None


def _log(message: str) -> None:
    print(f"[fleet] {message}", file=sys.stderr, flush=True)


# ------------------------------------------------------------ identity
def is_tailnet_ip(ip: str) -> bool:
    """True when ``ip`` is inside the Tailscale IPv4 or IPv6 range."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr.version == 4:
        return addr in fleet._TS_V4
    return addr in fleet._TS_V6


def _whois_raw(ip: str) -> dict | None:
    """Run `tailscale whois --json <ip>` (argv list, no shell). None if unavailable."""
    for binary in fleet._TAILSCALE_BINS:
        try:
            out = subprocess.run(
                [binary, "whois", "--json", ip],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode != 0:
            continue
        try:
            data = json.loads(out.stdout)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None
    return None


def _parse_whois(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    profile = raw.get("UserProfile") if isinstance(raw.get("UserProfile"), dict) else {}
    node = raw.get("Node") if isinstance(raw.get("Node"), dict) else {}
    login = str(profile.get("LoginName") or "").strip().lower()
    if not login:
        return None
    name = str(node.get("ComputedName") or node.get("Name") or "").strip()
    hostinfo = node.get("Hostinfo") if isinstance(node.get("Hostinfo"), dict) else {}
    # ShareeNode: the caller's device is in our netmap only because the owner
    # of THIS device shared it with the caller's account (Tailscale node sharing).
    return {
        "login": login,
        "node": name[:80],
        "sharee": hostinfo.get("ShareeNode") is True,
    }


def whois(ip: str) -> dict | None:
    """``{"login", "node"}`` for a tailnet source IP, None when unknown. Cached 60 s."""
    now = time.monotonic()
    with _LOCK:
        hit = _whois_cache.get(ip)
        if hit is not None and now - hit[0] < WHOIS_TTL:
            return hit[1]
    try:
        info = _parse_whois((_whois_fn or _whois_raw)(ip))
    except Exception:
        info = None
    with _LOCK:
        _whois_cache[ip] = (now, info)
    return info


def _own_login() -> str:
    """This device's Tailscale login, cached 60 s. '' when unknown."""
    global _own
    now = time.monotonic()
    with _LOCK:
        if _own is not None and now - _own[0] < WHOIS_TTL:
            return _own[1]
    try:
        login = fleet.own_account()
    except Exception:
        login = ""
    with _LOCK:
        _own = (now, login)
    return login


def trust_shared() -> bool:
    """Sharing this device with an account counts as consent (default on).

    Disable with JARVIS_FLEET_TRUST_SHARED=0 or {"trust_shared": false} in
    fleet/config.json to require an explicit approval instead.
    """
    env = os.environ.get("JARVIS_FLEET_TRUST_SHARED", "").strip().lower()
    if env:
        return env not in ("0", "false", "no", "off")
    return fleet.config().get("trust_shared", True) is not False


def trusted(login: str, sharee: bool = False) -> bool:
    """Own account, shared-to account, partner account, or approved account.

    The device owner's Tailscale share is the consent, so only the controlling
    device is ever approved by its owner; the shared device asks nothing.
    """
    key = (login or "").strip().lower()
    if not key:
        return False
    if key == _own_login():
        return True
    if sharee and trust_shared():
        return True
    if key in fleet.partner_accounts():
        return True
    with _LOCK:
        return key in _load()["accounts"]


# ------------------------------------------------------------ trust store
def _store_path() -> Path:
    return fleet._home() / "fleet" / "trust.json"


def _load() -> dict:
    """{"accounts": [...], "pending": [...]}; fail-soft to empty."""
    try:
        data = json.loads(_store_path().read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    accounts: list[str] = []
    raw_accounts = data.get("accounts")
    for item in raw_accounts if isinstance(raw_accounts, list) else []:
        if isinstance(item, str) and item.strip():
            key = item.strip().lower()
            if key not in accounts:
                accounts.append(key)
    raw_pending = data.get("pending")
    pending = [
        p
        for p in (raw_pending if isinstance(raw_pending, list) else [])
        if isinstance(p, dict) and isinstance(p.get("account"), str) and p["account"]
    ]
    return {"accounts": accounts, "pending": pending[-PENDING_MAX:]}


def _save(store: dict) -> None:
    """Atomic write; 0600 where POSIX modes apply (Windows uses ACLs)."""
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".trust-")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(store, fh, indent=2)
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _public(entry: dict) -> dict:
    return {
        "account": entry["account"],
        "node": str(entry.get("node") or ""),
        "ip": str(entry.get("ip") or ""),
        "first_seen": int(entry.get("first_seen") or 0),
        "last_seen": int(entry.get("last_seen") or 0),
    }


def _note_pending(login: str, node: str, ip: str) -> None:
    """Record an untrusted caller. Oldest entries drop past PENDING_MAX."""
    now = int(time.time())
    with _LOCK:
        store = _load()
        old = next(
            (
                p
                for p in store["pending"]
                if p["account"] == login and str(p.get("node") or "") == node
            ),
            None,
        )
        if (
            old is not None
            and old.get("ip") == ip
            and now - int(old.get("last_seen") or 0) < 60
        ):
            return  # same caller seen a moment ago: skip the disk write
        rest = [p for p in store["pending"] if p is not old]
        rest.append(
            {
                "account": login,
                "node": node,
                "ip": ip,
                "first_seen": int((old or {}).get("first_seen") or now),
                "last_seen": now,
            }
        )
        store["pending"] = rest[-PENDING_MAX:]
        _save(store)


def pending() -> list[dict]:
    """Untrusted callers waiting for approval, oldest first."""
    with _LOCK:
        return [_public(p) for p in _load()["pending"]]


def find_pending(target: str) -> list[dict]:
    """Pending entries matching an account (contains @) or a node name.

    Exact node slug first; a prefix of 3+ characters only when no exact match.
    """
    key = (target or "").strip().lower()
    if not key:
        return []
    entries = pending()
    if "@" in key:
        return [p for p in entries if p["account"] == key]
    slugged = fleet.slug(key)
    exact = [
        p
        for p in entries
        if p["account"] == key or (slugged and fleet.slug(p["node"]) == slugged)
    ]
    if exact or len(slugged) < 3:
        return exact
    return [p for p in entries if fleet.slug(p["node"]).startswith(slugged)]


def _resolve_one(target: str) -> tuple[dict | None, str]:
    """(matches, error). Ambiguous = more than one account matches."""
    if not (target or "").strip():
        return None, "name or account required"
    matches = find_pending(target)
    if not matches:
        return None, f"no pending device named {target.strip()}"
    if len({m["account"] for m in matches}) > 1:
        return (
            None,
            f"more than one pending account matches {target.strip()}; say the account",
        )
    return matches, ""


def approve(target: str) -> dict:
    """Trust a pending account (per account). Clears its pending entries."""
    matches, error = _resolve_one(target)
    if matches is None:
        return {"ok": False, "error": error}
    account = matches[0]["account"]
    name = next((m["node"] for m in matches if m["node"]), account)
    with _LOCK:
        store = _load()
        if account not in store["accounts"]:
            store["accounts"].append(account)
        store["pending"] = [p for p in store["pending"] if p["account"] != account]
        _save(store)
    return {"ok": True, "account": account, "name": name}


def deny(target: str) -> dict:
    """Drop pending entries for an account. Does not revoke an approved account."""
    matches, error = _resolve_one(target)
    if matches is None:
        return {"ok": False, "error": error}
    account = matches[0]["account"]
    name = next((m["node"] for m in matches if m["node"]), account)
    with _LOCK:
        store = _load()
        store["pending"] = [p for p in store["pending"] if p["account"] != account]
        _save(store)
    return {"ok": True, "account": account, "name": name}


# ------------------------------------------------------------ bind
def _os_family() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def tailscale_ip4() -> str | None:
    """First line of `tailscale ip -4` when it is a tailnet IPv4. None if absent."""
    for binary in fleet._TAILSCALE_BINS:
        try:
            out = subprocess.run(
                [binary, "ip", "-4"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode != 0:
            continue
        parts = out.stdout.split()
        return parts[0] if parts and is_tailnet_ip(parts[0]) else None
    return None


def bind_host() -> str | None:
    """JARVIS_FLEET_BIND (specific IP only) or the tailnet IP. None while Tailscale is down."""
    raw = os.environ.get("JARVIS_FLEET_BIND", "").strip()
    if raw:
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise RuntimeError("JARVIS_FLEET_BIND must be an IP address") from exc
        if addr.is_unspecified:
            raise RuntimeError("JARVIS_FLEET_BIND must not be a wildcard address")
        return raw
    return tailscale_ip4()


class _Listener(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        # Skip HTTPServer's reverse-DNS lookup of the bind address.
        socketserver.TCPServer.server_bind(self)
        host, port_no = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = port_no


def make_server(chat_fn, tool_fn, host: str, port_no: int) -> ThreadingHTTPServer:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    cls = type("FleetListener", (_Listener,), {"address_family": family})
    server = cls((host, port_no), FleetHandler)
    server.chat_fn = chat_fn
    server.tool_fn = tool_fn
    return server


# ------------------------------------------------------------ HTTP
class FleetHandler(BaseHTTPRequestHandler):
    server_version = "JarvisFleet/1"
    protocol_version = "HTTP/1.0"
    timeout = SOCKET_TIMEOUT

    def log_message(self, format, *args) -> None:  # noqa: A002 - stdlib signature
        return

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def _route(self, method: str) -> None:
        path = urlparse(self.path).path
        ip = self.client_address[0]  # socket peer only; never forwarded headers
        who = whois(ip) if is_tailnet_ip(ip) else None
        if who is None:
            return self._send(
                403, {"ok": False, "error": "caller is not a known tailnet device"}
            )
        body: dict = {}
        if method == "POST":
            parsed = self._read_body()
            if parsed is None:
                return None
            body = parsed
        login, node = who["login"], who["node"]
        allowed = trusted(login, bool(who.get("sharee")))
        if method == "GET" and path == "/fleet/hello":
            if not allowed:
                _note_pending(login, node, ip)
            return self._send(
                200,
                {
                    "ok": True,
                    "name": fleet.self_name(),
                    "os": _os_family(),
                    "account": _own_login(),
                    "version": 1,
                    "trusted": allowed,
                },
            )
        if not allowed:
            _note_pending(login, node, ip)
            return self._send(
                403, {"ok": False, "pending": True, "error": "awaiting approval"}
            )
        if method == "GET" and path == "/fleet/tools":
            import fleet_tools

            return self._send(200, {"ok": True, "tools": fleet_tools.TOOLS})
        if method == "POST" and path == "/fleet/chat":
            return self._chat(body, node)
        if method == "POST" and path == "/fleet/tool":
            return self._tool(body)
        return self._send(404, {"ok": False, "error": "unknown fleet route"})

    def _read_body(self) -> dict | None:
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = -1
        if size < 0 or size > MAX_BODY:
            # Drain a bounded amount so the client reads the 413 instead of a reset.
            self.rfile.read(min(max(size, 0), MAX_BODY * 8))
            self._send(413, {"ok": False, "error": "body must be 0..8192 bytes"})
            return None
        raw = self.rfile.read(size) if size else b""
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._send(400, {"ok": False, "error": "body must be a JSON object"})
            return None
        return data

    def _chat(self, body: dict, node: str) -> None:
        text = body.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            return self._send(
                400, {"ok": False, "error": "body needs text 1..2000 chars"}
            )
        sender = fleet.slug(str(body.get("from") or "")) or fleet.slug(node) or "peer"
        try:
            reply = self.server.chat_fn(text.strip(), sender)
        except Exception:
            return self._send(502, {"ok": False, "error": "chat failed"})
        return self._send(200, {"ok": True, "reply": str(reply or "")})

    def _tool(self, body: dict) -> None:
        tool = body.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            return self._send(400, {"ok": False, "error": "tool name required"})
        try:
            res = self.server.tool_fn(tool.strip(), body.get("args"))
        except Exception:
            return self._send(502, {"ok": False, "error": "tool failed"})
        if not isinstance(res, dict):
            res = {"ok": False, "error": "bad tool result"}
        return self._send(200 if res.get("ok") else 400, res)

    def _send(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


# ------------------------------------------------------------ run
def serve(chat_fn, tool_fn, bind: str | None = None) -> None:
    """Bind to the tailnet IP and serve until shutdown. Raises if not bindable."""
    host = bind or bind_host()
    if not host:
        raise RuntimeError("no tailnet IP yet (is Tailscale up?)")
    server = make_server(chat_fn, tool_fn, host, fleet.listener_port())
    _log(f"listening on {host}:{server.server_port} as {fleet.self_name()}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _run_forever(chat_fn, tool_fn) -> None:
    last = ""
    while True:
        try:
            serve(chat_fn, tool_fn)
            return
        except (RuntimeError, OSError) as exc:
            reason = str(exc) or "bind failed"
            if reason != last:
                _log(f"not listening: {reason}; retry every {int(RETRY_SECONDS)} s")
                last = reason
        time.sleep(RETRY_SECONDS)


def start_in_thread(chat_fn, tool_fn) -> threading.Thread | None:
    """Start the listener in a daemon thread. JARVIS_FLEET=0 disables it."""
    if os.environ.get("JARVIS_FLEET", "").strip() == "0":
        _log("disabled (JARVIS_FLEET=0)")
        return None
    thread = threading.Thread(
        target=_run_forever, args=(chat_fn, tool_fn), name="fleet-listener", daemon=True
    )
    thread.start()
    return thread


def _bridge_token() -> str:
    env = os.environ.get("JARVIS_BRIDGE_TOKEN", "").strip()
    if env:
        return env
    try:
        return (fleet._home() / "bridge_token").read_text().strip()
    except OSError:
        return ""


def _bridge_chat(text: str, sender: str) -> str:
    """Standalone chat: forward to the local bridge as a fleet client (loop guard)."""
    port = os.environ.get("JARVIS_BRIDGE_PORT", "").strip() or str(BRIDGE_PORT_DEFAULT)
    headers = {"Content-Type": "application/json", "X-Jarvis-Fleet-Hop": "1"}
    token = _bridge_token()
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/chat",
        data=json.dumps({"text": text, "client_id": "fleet:" + sender}).encode(),
        method="POST",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.loads(resp.read() or b"{}")
    if not isinstance(payload, dict):
        raise RuntimeError("bridge reply is not an object")
    return str(payload.get("reply") or payload.get("say") or payload.get("error") or "")


def main() -> int:
    """Standalone listener: bridge chat plus local fleet tools. Blocks."""
    import fleet_tools

    _run_forever(_bridge_chat, fleet_tools.run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
