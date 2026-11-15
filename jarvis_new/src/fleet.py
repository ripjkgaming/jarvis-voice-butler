"""Jarvis Fleet: talk to the Jarvis running on your other computers.

Every device runs its own full Jarvis with its own API keys, so usage is
never shared or pooled. This module only sends plain text commands from one
Jarvis to another over the Tailscale tailnet. The remote device answers with
its own models and its own keys. No keys ever cross the wire.

Pairing (once per target device):
    1. On the target: bind the bridge to its tailnet IP and read its token
       (see ``python src/fleet.py pair-info``).
    2. On the controller: ``python src/fleet.py add <name> <url>`` and paste
       the target's bridge token when asked.

Voice / chat: "list my devices", "ping office", "ask office to open
spotify", "tell laptop to lock the screen".

Stdlib only. Peer URLs must be tailnet addresses (100.64.0.0/10, the
Tailscale IPv6 range, or *.ts.net) so a stored URL can never be used to
reach arbitrary hosts. Peers cannot forward to further peers (loop guard).
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_PORT = 4317
FLEET_CLIENT_PREFIX = "fleet:"
_LOCK = threading.Lock()
_NAME_RE = re.compile(r"[^a-z0-9]+")
_TS_V4 = ipaddress.ip_network("100.64.0.0/10")
_TS_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
_TAILSCALE_BINS = (
    "tailscale",
    r"C:\Program Files\Tailscale\tailscale.exe",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
)


def _home() -> Path:
    return Path(os.environ.get("JARVIS_HOME", "").strip() or Path.home() / ".jarvis")


def registry_path() -> Path:
    return _home() / "fleet" / "devices.json"


def slug(name: str) -> str:
    """Stable device key: lowercase alphanumerics and single dashes."""
    return _NAME_RE.sub("-", str(name).lower()).strip("-")[:40]


def self_name() -> str:
    return slug(os.environ.get("JARVIS_FLEET_NAME", "") or socket.gethostname()) or "jarvis"


# --------------------------------------------------------------------- store
def load() -> dict[str, dict]:
    try:
        data = json.loads(registry_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, dict]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".devices-")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
        with_perms = Path(tmp)
        try:
            with_perms.chmod(0o600)
        except OSError:
            pass  # Windows: ACLs, not POSIX modes
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def valid_url(url: str) -> str | None:
    """Normalised base URL if it points inside the tailnet, else None."""
    try:
        parsed = urllib.parse.urlparse(url.strip())
        host = parsed.hostname or ""
        port = parsed.port or DEFAULT_PORT
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not host:
        return None
    ok = host.lower().endswith(".ts.net")
    try:
        ip = ipaddress.ip_address(host)
        ok = ip in _TS_V4 or ip in _TS_V6
    except ValueError:
        pass
    if not ok and os.environ.get("JARVIS_FLEET_ALLOW_LOCAL") == "1":
        ok = host in ("127.0.0.1", "localhost")  # tests only
    if not ok:
        return None
    shown = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme}://{shown}:{port}"


def add(name: str, url: str, token: str) -> dict:
    key, base = slug(name), valid_url(url)
    token = token.strip()
    if not key:
        return {"ok": False, "error": "device needs a name"}
    if base is None:
        return {"ok": False, "error": "url must be a tailnet address (100.x.y.z or *.ts.net)"}
    if not token or any(c.isspace() for c in token):
        return {"ok": False, "error": "token missing or contains whitespace"}
    with _LOCK:
        data = load()
        data[key] = {"url": base, "token": token}
        _save(data)
    return {"ok": True, "name": key, "url": base}


def remove(name: str) -> dict:
    key = slug(name)
    with _LOCK:
        data = load()
        if key not in data:
            return {"ok": False, "error": f"no device named {key}"}
        del data[key]
        _save(data)
    return {"ok": True, "name": key}


# ----------------------------------------------------------------- tailnet
def tailnet_peers() -> list[dict]:
    """Peers from `tailscale status --json` (fail-soft: [] if unavailable)."""
    for binary in _TAILSCALE_BINS:
        try:
            out = subprocess.run(
                [binary, "status", "--json"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            if out.returncode != 0:
                continue
            status = json.loads(out.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
        peers = []
        for p in (status.get("Peer") or {}).values():
            ips = p.get("TailscaleIPs") or []
            peers.append(
                {
                    "name": slug(p.get("HostName", "")),
                    "host": p.get("HostName", ""),
                    "os": str(p.get("OS", "")).lower(),
                    "online": p.get("Online") is True,
                    "ip": next((i for i in ips if ":" not in i), ips[0] if ips else ""),
                }
            )
        return peers
    return []


def devices() -> list[dict]:
    """Paired devices merged with tailnet presence. Never includes tokens."""
    peers = {p["name"]: p for p in tailnet_peers()}
    out = []
    for key, rec in sorted(load().items()):
        peer = peers.get(key) or next(
            (p for p in peers.values() if p["ip"] and p["ip"] in rec.get("url", "")),
            {},
        )
        out.append(
            {
                "name": key,
                "url": rec.get("url", ""),
                "os": peer.get("os", ""),
                "online": peer.get("online"),
                "paired": True,
            }
        )
    known = {d["name"] for d in out}
    for key, p in sorted(peers.items()):
        if key not in known and p["os"] in ("linux", "windows", "macos", "darwin"):
            out.append(
                {
                    "name": key,
                    "url": f"http://{p['ip']}:{DEFAULT_PORT}" if p["ip"] else "",
                    "os": p["os"],
                    "online": p["online"],
                    "paired": False,
                }
            )
    return out


def self_info() -> dict:
    """What a controller needs to pair with THIS device (token excluded)."""
    ip = ""
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=4, check=False
        )
        ip = out.stdout.split()[0] if out.returncode == 0 and out.stdout.split() else ""
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "ok": True,
        "name": self_name(),
        "url": f"http://{ip}:{DEFAULT_PORT}" if ip else "",
        "platform": sys.platform,
    }


# ------------------------------------------------------------------ remote
def _call(name: str, method: str, path: str, body: dict | None, timeout: float) -> dict:
    rec = load().get(slug(name))
    if rec is None:
        return {"ok": False, "error": f"unknown device {name}; pair it first"}
    base = valid_url(rec.get("url", ""))
    if base is None:
        return {"ok": False, "error": "stored url is not a tailnet address"}
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": "Bearer " + rec.get("token", ""),
            "Content-Type": "application/json",
            "X-Jarvis-Fleet-Hop": "1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return {"ok": False, "error": f"{name} rejected the token; re-pair it"}
        return {"ok": False, "error": f"{name} answered HTTP {exc.code}"}
    except (OSError, ValueError):
        return {"ok": False, "error": f"{name} is unreachable (asleep, offline, or Jarvis not running)"}
    return payload if isinstance(payload, dict) else {"ok": False, "error": "bad reply"}


def ping(name: str) -> dict:
    res = _call(name, "GET", "/health", None, 5.0)
    return {**res, "name": slug(name)}


def ask(name: str, text: str, timeout: float = 90.0) -> dict:
    """Send a text command to a peer's Jarvis. It runs there with its own keys."""
    text = (text or "").strip()
    if not text or len(text) > 2000:
        return {"ok": False, "error": "command needs 1..2000 characters"}
    res = _call(
        name,
        "POST",
        "/chat",
        {"text": text, "client_id": FLEET_CLIENT_PREFIX + self_name()},
        timeout,
    )
    reply = res.get("reply") or res.get("say") or ""
    if res.get("ok") is False and not reply:
        return res
    return {"ok": True, "name": slug(name), "reply": str(reply) or "Done."}


# ------------------------------------------------------------------- voice
_LIST_RE = re.compile(
    r"^(?:list|show|what(?:'s| is| are)|which)\s+(?:are\s+)?(?:my\s+|the\s+)?"
    r"(?:connected\s+|linked\s+|other\s+)?(?:devices|laptops|computers|machines|fleet)\b"
)
_PING_RE = re.compile(r"^(?:ping|is)\s+(?:my\s+|the\s+)?(?P<n>.+?)(?:\s+(?:online|up|awake|alive))?\s*\??$")
_ASK_RE = re.compile(
    r"^(?:ask|tell|have|get|send(?:\s+to)?|on)\s+(?:my\s+|the\s+)?(?P<n>.+?)"
    r"(?:\s+(?:laptop|pc|computer|machine))?\s*(?:,|:|\bto\b|\bthat\b)\s*(?P<c>.+)$"
)


def _resolve(spoken: str, known: list[str]) -> str | None:
    key = slug(spoken)
    if key in known:
        return key
    for k in known:  # "office laptop" -> office; "mac" -> macbook-pro
        if (k.startswith(key) and len(key) >= 3) or (key.startswith(k) and len(k) >= 3):
            return k
    return None


def handle(text: str) -> str | None:
    """Fleet voice/chat command -> spoken reply, or None if not a fleet command."""
    lowered = re.sub(r"\s+", " ", text.lower()).strip(" .!?")
    if _LIST_RE.match(lowered):
        devs = devices()
        if not devs:
            return "No other devices are paired yet, Sir."
        parts = [
            f"{d['name']} {'online' if d['online'] else 'offline'}"
            + ("" if d["paired"] else " (not paired)")
            for d in devs
        ]
        return "Your devices: " + ", ".join(parts) + "."
    known = list(load())
    if not known:
        return None
    m = _ASK_RE.match(lowered)
    if m:
        target = _resolve(m["n"], known)
        if target:
            res = ask(target, m["c"])
            return res["reply"] if res["ok"] else res["error"]
    m = _PING_RE.match(lowered)
    if m:
        target = _resolve(m["n"], known)
        if target:
            res = ping(target)
            return f"{target} is online." if res.get("ok") else res.get("error", "No reply.")
    return None


# --------------------------------------------------------------------- cli
def _cli(argv: list[str]) -> int:
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        for d in devices():
            state = "online" if d["online"] else "offline"
            print(f"{d['name']:20} {state:8} {'paired' if d['paired'] else 'unpaired':9} {d['url']}")
        return 0
    if cmd == "pair-info":
        info = self_info()
        print(json.dumps(info, indent=2))
        print(
            "\nBridge token file: "
            f"{_home() / 'bridge_token'}\nThe bridge must listen on the tailnet: set "
            "JARVIS_BRIDGE_BIND to the url's IP before starting it."
        )
        return 0
    if cmd == "add" and len(argv) >= 3:
        import getpass

        token = argv[3] if len(argv) > 3 else getpass.getpass("Target bridge token: ")
        res = add(argv[1], argv[2], token)
        print(json.dumps(res))
        return 0 if res["ok"] else 1
    if cmd == "remove" and len(argv) == 2:
        res = remove(argv[1])
        print(json.dumps(res))
        return 0 if res["ok"] else 1
    if cmd == "ping" and len(argv) == 2:
        res = ping(argv[1])
        print(json.dumps(res))
        return 0 if res.get("ok") else 1
    if cmd == "ask" and len(argv) >= 3:
        res = ask(argv[1], " ".join(argv[2:]))
        print(res.get("reply") or res.get("error"))
        return 0 if res["ok"] else 1
    print("usage: fleet.py list | pair-info | add NAME URL [TOKEN] | remove NAME | ping NAME | ask NAME TEXT...")
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
