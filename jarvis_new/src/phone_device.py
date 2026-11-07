"""LOCKE//LINK device auth: pairing, device credentials, the gate, lockout.

The PWA never sees the bridge bearer token. A phone pairs once with a short
code printed on the laptop (``scripts/phone_pair.py``) and gets an HttpOnly
device cookie (``jl_dev``). Each launch then opens a *gate* (``jl_gate``, a
session cookie) with the deck passphrase; a passkey (fingerprint) reopens a
gate that went soft after 10 idle minutes. Gate records live in bridge memory
only, so a bridge restart makes every gate cold.

``PhoneAuth.handle`` is a pure request -> (status, payload, set-cookie list)
function so it is tested without HTTP; ``bridge.py`` only adapts it.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import phone_webauthn as wa

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PAIR_TTL_S = 600.0
MAX_PENDING = 3
MAX_DEVICES = 3
MAX_CREDENTIALS = 4
PASSKEY_WINDOW_S = 600.0
GATE_IDLE_S = 600.0
GATE_MAX_S = 43200.0
LOCKOUT_TRIES = 5
LOCKOUT_BASE_S = 60.0
LOCKOUT_MAX_S = 3600.0
PAIR_RATE = (5, 60.0)
DEV_MAX_AGE = 15552000

STEPUP_ACTIONS = frozenset({"guest_off", "unlock", "restart", "shutdown"})
CONTROL_ACTIONS = frozenset(
    {
        "guest_on",
        "guest_off",
        "lock",
        "unlock",
        "screens_off",
        "screens_on",
        "sleep",
        "restart",
        "shutdown",
        "mic_mute",
        "mic_unmute",
    }
)

# Routes this module answers itself (everything else only authorizes).
AUTH_ROUTES = frozenset(
    {
        "/phone/v1/pair",
        "/phone/v1/passkey",
        "/phone/v1/challenge",
        "/phone/v1/gate",
        "/phone/v1/gate/close",
    }
)
# Device-cookie routes and whether they need an open gate.
DEVICE_ROUTES = {
    "/phone/v1/state": True,
    "/phone/v1/control": True,
    "/phone/v1/command": True,
    "/phone/v1/session": True,
    "/phone/telemetry": True,
    "/phone/v1/capabilities": False,
}

_DEVICE_ID = re.compile(r"^jl-[0-9a-f]{8}$")
_SECRET = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_GATE_ID = re.compile(r"^[A-Za-z0-9_-]{32,64}$")


def _log(detail_category: str, detail: str) -> None:
    with contextlib.suppress(Exception):
        from system import log_action

        log_action(detail_category, detail)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def phone_dir() -> Path:
    home = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".jarvis") / "phone"


def gate_file() -> Path:
    override = os.environ.get("JARVIS_PHONE_GATE", "").strip()
    return Path(override) if override else Path.home() / ".config" / "locke" / "gate"


def phone_origin() -> str:
    return os.environ.get("JARVIS_PHONE_ORIGIN", "").strip().rstrip("/")


class GateNotConfiguredError(RuntimeError):
    pass


def verify_passphrase(passphrase: str, path: Path) -> bool:
    """Check against the deck gate file ``salt_hex:hash_hex`` (scrypt)."""
    try:
        raw = path.read_text().strip()
    except FileNotFoundError as exc:
        raise GateNotConfiguredError(str(path)) from exc
    except OSError:
        return False
    try:
        salt_hex, hash_hex = raw.split(":", 1)
        salt, want = bytes.fromhex(salt_hex), bytes.fromhex(hash_hex)
    except ValueError:
        return False
    if not salt or not 16 <= len(want) <= 128:
        return False
    if not isinstance(passphrase, str) or not 0 < len(passphrase) <= 1024:
        return False
    got = hashlib.scrypt(
        passphrase.encode(), salt=salt, n=16384, r=8, p=1, dklen=len(want)
    )
    return hmac.compare_digest(got, want)


# --- persistent store -----------------------------------------------------------


class PairError(RuntimeError):
    pass


class DeviceLimitError(RuntimeError):
    pass


def _normalise_code(code) -> str:
    if not isinstance(code, str) or len(code) > 40:
        return ""
    text = re.sub(r"[\s-]", "", code.upper())
    return text.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))


class DeviceStore:
    """``pair.json`` and ``devices.json`` under ~/.jarvis/phone (0600, atomic).

    Re-read on every call so the laptop CLI can revoke without a restart.
    """

    def __init__(self, root: Path | None = None, clock=time.time):
        self.root = Path(root) if root else phone_dir()
        self.clock = clock
        self._lock = threading.RLock()

    # files
    def _read(self, name: str, default):
        try:
            data = json.loads((self.root / name).read_text())
        except (OSError, ValueError):
            return default
        return data if isinstance(data, type(default)) else default

    def _write(self, name: str, data) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.root, 0o700)
        tmp = self.root / (name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, self.root / name)

    # pairing codes
    def _pending(self) -> dict[str, float]:
        now = self.clock()
        rows = self._read("pair.json", {})
        return {
            k: float(v)
            for k, v in rows.items()
            if isinstance(v, (int, float)) and float(v) > now
        }

    def issue_code(self) -> str:
        with self._lock:
            pending = self._pending()
            if len(pending) >= MAX_PENDING:
                raise PairError(f"{MAX_PENDING} pairing codes already pending")
            code = "".join(secrets.choice(CROCKFORD) for _ in range(10))
            pending[_sha(code)] = self.clock() + PAIR_TTL_S
            self._write("pair.json", pending)
            return code

    def consume_code(self, code) -> bool:
        want = _sha(_normalise_code(code)).encode()
        with self._lock:
            pending = self._pending()
            hit = None
            for key in list(pending):  # compare all, constant time each
                if hmac.compare_digest(key.encode(), want):
                    hit = key
            if hit is None:
                if pending != self._read("pair.json", {}):
                    self._write("pair.json", pending)
                return False
            del pending[hit]
            self._write("pair.json", pending)
            return True

    # devices
    def list(self) -> list[dict]:
        rows = self._read("devices.json", [])
        return [r for r in rows if isinstance(r, dict) and "id" in r]

    def get(self, device_id: str) -> dict | None:
        return next((d for d in self.list() if d["id"] == device_id), None)

    def create(self, name: str) -> tuple[dict, str]:
        with self._lock:
            devices = self.list()
            if len(devices) >= MAX_DEVICES:
                raise DeviceLimitError(f"at most {MAX_DEVICES} devices")
            taken = {d["id"] for d in devices}
            device_id = "jl-" + secrets.token_hex(4)
            while device_id in taken:
                device_id = "jl-" + secrets.token_hex(4)
            secret = secrets.token_urlsafe(32)
            now = self.clock()
            device = {
                "id": device_id,
                "name": (name or "phone")[:40],
                "secret_sha256": _sha(secret),
                "created": now,
                "last_seen": now,
                "credentials": [],
            }
            self._write("devices.json", [*devices, device])
            return device, secret

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            devices = self.list()
            kept = [d for d in devices if d["id"] != device_id]
            if len(kept) == len(devices):
                return False
            self._write("devices.json", kept)
        _log("phone_revoke", device_id)
        return True

    def check_secret(self, device_id: str, secret: str) -> dict | None:
        device = self.get(device_id)
        if device is None:
            hmac.compare_digest(_sha(secret), "0" * 64)  # same work either way
            return None
        ok = hmac.compare_digest(_sha(secret), str(device.get("secret_sha256", "")))
        return device if ok else None

    def _update(self, device_id: str, fn) -> bool:
        with self._lock:
            devices = self.list()
            for d in devices:
                if d["id"] == device_id:
                    fn(d)
                    self._write("devices.json", devices)
                    return True
            return False

    def add_credential(self, device_id: str, cred: dict) -> bool:
        def add(d):
            creds = [c for c in d.get("credentials", []) if c.get("id") != cred["id"]]
            d["credentials"] = [*creds, {**cred, "created": self.clock()}][
                -MAX_CREDENTIALS:
            ]

        return self._update(device_id, add)

    def set_sign_count(self, device_id: str, cred_id: str, count: int) -> None:
        def bump(d):
            for c in d.get("credentials", []):
                if c.get("id") == cred_id:
                    c["sign_count"] = count

        self._update(device_id, bump)

    def touch(self, device_id: str) -> None:
        def seen(d):
            d["last_seen"] = self.clock()

        self._update(device_id, seen)


# --- memory-only state ----------------------------------------------------------------


class RateLimit:
    def __init__(self, limit: int, window: float, clock):
        self.limit, self.window, self.clock = limit, window, clock
        self._hits: list[float] = []
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = self.clock()
            self._hits = [t for t in self._hits if now - t < self.window]
            if len(self._hits) >= self.limit:
                return False
            self._hits.append(now)
            return True


class GateBook:
    """{gate_id: {device_id, passphrase_at, last_seen}} in memory."""

    MAX = 32

    def __init__(self, clock):
        self.clock = clock
        self._rows: dict[str, dict] = {}
        self._lock = threading.Lock()

    def open(self, device_id: str) -> str:
        with self._lock:
            now = self.clock()
            self._rows = {
                k: v
                for k, v in self._rows.items()
                if now - v["passphrase_at"] < GATE_MAX_S
            }
            while len(self._rows) >= self.MAX:
                self._rows.pop(next(iter(self._rows)))
            gate_id = secrets.token_urlsafe(32)
            self._rows[gate_id] = {
                "device_id": device_id,
                "passphrase_at": now,
                "last_seen": now,
            }
            return gate_id

    def _state(self, row: dict | None, device_id: str) -> str:
        if not row or not hmac.compare_digest(row["device_id"], device_id):
            return "cold"
        now = self.clock()
        if now - row["passphrase_at"] >= GATE_MAX_S:
            return "cold"
        if now - row["last_seen"] >= GATE_IDLE_S:
            return "soft"
        return "open"

    def state(self, gate_id: str | None, device_id: str, *, touch=False) -> str:
        with self._lock:
            row = self._rows.get(gate_id or "")
            state = self._state(row, device_id)
            if state == "cold" and row and row["device_id"] == device_id:
                self._rows.pop(gate_id, None)
            if state == "open" and touch:
                row["last_seen"] = self.clock()
            return state

    def reopen(self, gate_id: str, device_id: str) -> bool:
        with self._lock:
            row = self._rows.get(gate_id or "")
            if self._state(row, device_id) == "cold":
                return False
            row["last_seen"] = self.clock()
            return True

    def info(self, gate_id: str | None, device_id: str) -> dict:
        with self._lock:
            row = self._rows.get(gate_id or "")
            state = self._state(row, device_id)
            if state == "cold":
                return {"state": "cold", "expires_in_s": 0, "soft_in_s": 0}
            now = self.clock()
            return {
                "state": state,
                "expires_in_s": round(GATE_MAX_S - (now - row["passphrase_at"]), 1),
                "soft_in_s": round(max(0.0, GATE_IDLE_S - (now - row["last_seen"])), 1),
            }

    def close(self, gate_id: str | None) -> None:
        with self._lock:
            self._rows.pop(gate_id or "", None)


class Lockout:
    """5 consecutive failures -> 60 s, then each failure doubles, max 1 h."""

    def __init__(self, clock):
        self.clock = clock
        self._rows: dict[str, list[float]] = {}  # device -> [fails, until]
        self._lock = threading.Lock()

    def retry_after(self, device_id: str) -> float:
        with self._lock:
            row = self._rows.get(device_id)
            return max(0.0, row[1] - self.clock()) if row else 0.0

    def fail(self, device_id: str) -> tuple[int, int]:
        with self._lock:
            row = self._rows.setdefault(device_id, [0, 0.0])
            row[0] += 1
            fails = int(row[0])
            wait = 0.0
            if fails >= LOCKOUT_TRIES:
                wait = min(LOCKOUT_MAX_S, LOCKOUT_BASE_S * 2 ** (fails - LOCKOUT_TRIES))
                row[1] = self.clock() + wait
            return max(0, LOCKOUT_TRIES - fails), int(wait)

    def tries_left(self, device_id: str) -> int:
        with self._lock:
            row = self._rows.get(device_id)
            return max(0, LOCKOUT_TRIES - int(row[0])) if row else LOCKOUT_TRIES

    def success(self, device_id: str) -> None:
        with self._lock:
            self._rows.pop(device_id, None)


# --- request auth -----------------------------------------------------------------------


def parse_cookies(header: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (header or "")[:8192].split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            out.setdefault(key.strip(), value.strip())
    return out


def parse_device_cookie(value: str | None) -> tuple[str, str] | None:
    if not value or len(value) > 200 or "." not in value:
        return None
    device_id, secret = value.split(".", 1)
    if not _DEVICE_ID.match(device_id) or not _SECRET.match(secret):
        return None
    return device_id, secret


def _cookie(name: str, value: str, max_age: int | None) -> str:
    row = f"{name}={value}; HttpOnly; Secure; SameSite=Strict; Path=/"
    return row + (f"; Max-Age={max_age}" if max_age is not None else "")


@dataclass
class Principal:
    kind: str  # "bearer" | "device"
    gate: str  # "open" | "soft" | "cold"
    device: dict | None = None
    gate_id: str | None = None

    @property
    def device_id(self) -> str | None:
        return self.device["id"] if self.device else None


def _header(headers, name: str) -> str:
    getter = getattr(headers, "get", None)
    value = getter(name) if getter else None
    if value is None and isinstance(headers, dict):
        lowered = {k.lower(): v for k, v in headers.items()}
        value = lowered.get(name.lower())
    return value or ""


class PhoneAuth:
    """All device-auth state for one bridge process."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        origin: str | None = None,
        bearer: str = "",
        clock=None,
    ):
        wall = clock or time.time
        mono = clock or time.monotonic
        self.store = DeviceStore(root, clock=wall)
        self.origin = phone_origin() if origin is None else origin.rstrip("/")
        self.bearer = bearer
        self.gates = GateBook(mono)
        self.lockout = Lockout(mono)
        self.challenges = wa.ChallengeStore(clock=mono)
        self.pair_rate = RateLimit(*PAIR_RATE, clock=mono)
        self.clock = wall

    @property
    def rp_id(self) -> str:
        return urlparse(self.origin).hostname or ""

    # credential checks
    def _bearer_ok(self, headers) -> bool:
        if not self.bearer:
            return False
        got = _header(headers, "Authorization")
        return hmac.compare_digest(got.encode(), ("Bearer " + self.bearer).encode())

    def _same_origin(self, method: str, headers) -> bool:
        if not self.origin or _header(headers, "X-Jarvis-Phone") != "1":
            return False
        origin = _header(headers, "Origin")
        if not origin:
            return method in ("GET", "HEAD")
        return hmac.compare_digest(origin.rstrip("/").encode(), self.origin.encode())

    def authorize(self, method: str, headers, *, touch: bool = True):
        """Principal for a bearer or device request, else None."""
        if self._bearer_ok(headers):
            return Principal(kind="bearer", gate="open")
        if not self._same_origin(method, headers):
            return None
        cookies = parse_cookies(_header(headers, "Cookie"))
        parsed = parse_device_cookie(cookies.get("jl_dev"))
        if parsed is None:
            return None
        device = self.store.check_secret(*parsed)
        if device is None:
            return None
        gate_id = cookies.get("jl_gate") or None
        if gate_id and not _GATE_ID.match(gate_id):
            gate_id = None
        gate = self.gates.state(gate_id, device["id"], touch=touch)
        return Principal(kind="device", gate=gate, device=device, gate_id=gate_id)

    def guard(self, method: str, route: str, headers):
        """Auth for a non-auth phone route: (principal, None) or (None, error)."""
        principal = self.authorize(method, headers)
        if principal is None:
            return None, (401, {"ok": False, "error": "unauthorized", "auth": "pair"})
        if DEVICE_ROUTES.get(route, True) and principal.gate != "open":
            return None, (401, {"ok": False, "error": "gate", "gate": principal.gate})
        return principal, None

    # route handlers
    def handle(self, method: str, route: str, headers, body):
        """(status, payload, set_cookies) for any /phone/v1 route."""
        if route in AUTH_ROUTES:
            if method != "POST":
                return 405, {"ok": False, "error": "POST only"}, []
            if not isinstance(body, dict):
                return 400, {"ok": False, "error": "JSON object body required"}, []
            return getattr(self, "_" + route.rsplit("/v1/", 1)[1].replace("/", "_"))(
                headers, body
            )
        principal, error = self.guard(method, route, headers)
        if error:
            return (*error, [])
        return 200, {"ok": True, "principal": principal.kind}, []

    def _device_principal(self, headers):
        principal = self.authorize("POST", headers, touch=False)
        if principal is None or principal.kind != "device":
            return None, (
                401,
                {"ok": False, "error": "unauthorized", "auth": "pair"},
                [],
            )
        return principal, None

    def _registration(self, device: dict) -> dict:
        challenge = self.challenges.issue(device["id"], "register")
        return wa.registration_options(
            rp_id=self.rp_id,
            device_id=device["id"],
            name=device.get("name", ""),
            challenge=challenge,
            exclude=[c["id"] for c in device.get("credentials", [])],
        )

    def _may_register(self, principal: Principal) -> bool:
        created = float(principal.device.get("created") or 0)
        return (
            self.clock() - created < PASSKEY_WINDOW_S
            or self.gates.state(principal.gate_id, principal.device_id) == "open"
        )

    def _pair(self, headers, body):
        if not self.origin:
            return 503, {"ok": False, "error": "JARVIS_PHONE_ORIGIN is not set"}, []
        if not self._same_origin("POST", headers):
            return 403, {"ok": False, "error": "same-origin phone request required"}, []
        if not self.pair_rate.allow():
            return 429, {"ok": False, "error": "too many pairing attempts"}, []
        if not self.store.consume_code(body.get("code")):
            _log("phone_pair", "fail bad code")
            return 401, {"ok": False, "error": "pairing code invalid or expired"}, []
        name = body.get("name") if isinstance(body.get("name"), str) else "phone"
        try:
            device, secret = self.store.create(re.sub(r"[^\w .-]", "", name)[:40])
        except DeviceLimitError as exc:
            return 409, {"ok": False, "error": str(exc)}, []
        _log("phone_pair", f"{device['id']} {device['name']}")
        cookie = _cookie("jl_dev", f"{device['id']}.{secret}", DEV_MAX_AGE)
        return (
            200,
            {
                "ok": True,
                "device_id": device["id"],
                "registration": self._registration(device),
            },
            [cookie],
        )

    def _passkey(self, headers, body):
        principal, error = self._device_principal(headers)
        if error:
            return error
        if not self._may_register(principal):
            return 401, {"ok": False, "error": "gate", "gate": principal.gate}, []
        credential = body.get("credential")
        challenge = wa.client_challenge(credential)
        if not self.challenges.consume(principal.device_id, "register", challenge):
            return 400, {"ok": False, "error": "registration challenge invalid"}, []
        try:
            cred = wa.parse_registration(
                credential, challenge=challenge, origin=self.origin, rp_id=self.rp_id
            )
        except wa.WebAuthnError as exc:
            _log("phone_passkey", f"fail {principal.device_id}")
            return 400, {"ok": False, "error": f"passkey rejected: {exc}"}, []
        self.store.add_credential(principal.device_id, cred)
        _log("phone_passkey", f"registered {principal.device_id}")
        return 200, {"ok": True}, []

    def _challenge(self, headers, body):
        principal, error = self._device_principal(headers)
        if error:
            return error
        purpose = body.get("purpose")
        if purpose == "register":
            if not self._may_register(principal):
                return 401, {"ok": False, "error": "gate", "gate": principal.gate}, []
            return (
                200,
                {"ok": True, "registration": self._registration(principal.device)},
                [],
            )
        if purpose != "gate" and purpose not in STEPUP_ACTIONS:
            return 400, {"ok": False, "error": "unknown purpose"}, []
        if purpose != "gate":
            state = self.gates.state(principal.gate_id, principal.device_id)
            if state != "open":
                return 401, {"ok": False, "error": "gate", "gate": state}, []
        creds = principal.device.get("credentials", [])
        if not creds:
            return 409, {"ok": False, "error": "no passkey registered"}, []
        challenge = self.challenges.issue(principal.device_id, purpose)
        return (
            200,
            {
                "ok": True,
                **wa.request_options(
                    rp_id=self.rp_id, challenge=challenge, credentials=creds
                ),
            },
            [],
        )

    def verify_stepup(self, principal: Principal, purpose: str, assertion) -> bool:
        """Single-use passkey check for a challenge issued with ``purpose``."""
        if principal.kind != "device":
            return principal.kind == "bearer"
        challenge = wa.client_challenge(assertion)
        if not self.challenges.consume(principal.device_id, purpose, challenge):
            return False
        try:
            cred_id, count = wa.verify_assertion(
                assertion,
                credentials=principal.device.get("credentials", []),
                challenge=challenge,
                origin=self.origin,
                rp_id=self.rp_id,
            )
        except wa.WebAuthnError:
            return False
        self.store.set_sign_count(principal.device_id, cred_id, count)
        return True

    def _denied(self, device_id: str, error: str):
        tries, wait = self.lockout.fail(device_id)
        _log("phone_gate", f"fail {device_id}")
        return (
            401,
            {"ok": False, "error": error, "tries_left": tries, "retry_after": wait},
            [],
        )

    def _opened(self, gate_id: str, device_id: str, fresh: bool):
        self.lockout.success(device_id)
        self.store.touch(device_id)
        info = self.gates.info(gate_id, device_id)
        _log("phone_gate", f"open {device_id}")
        return (
            200,
            {
                "ok": True,
                "gate": "open",
                "expires_in_s": info["expires_in_s"],
                "soft_after_s": int(GATE_IDLE_S),
            },
            [_cookie("jl_gate", gate_id, None)] if fresh else [],
        )

    def _gate(self, headers, body):
        principal, error = self._device_principal(headers)
        if error:
            return error
        device_id = principal.device_id
        wait = self.lockout.retry_after(device_id)
        if wait > 0:
            return (
                429,
                {
                    "ok": False,
                    "error": "lockout",
                    "tries_left": 0,
                    "retry_after": math.ceil(wait),
                },
                [],
            )
        if "assertion" in body:
            state = self.gates.state(principal.gate_id, device_id)
            if state == "cold":
                return 401, {"ok": False, "error": "gate", "gate": "cold"}, []
            if not self.verify_stepup(principal, "gate", body.get("assertion")):
                return self._denied(device_id, "passkey rejected")
            self.gates.reopen(principal.gate_id, device_id)
            return self._opened(principal.gate_id, device_id, fresh=False)
        try:
            ok = verify_passphrase(body.get("passphrase"), gate_file())
        except GateNotConfiguredError:
            return (
                503,
                {"ok": False, "error": "gate not configured; run deck passwd"},
                [],
            )
        if not ok:
            return self._denied(device_id, "denied")
        self.gates.close(principal.gate_id)
        return self._opened(self.gates.open(device_id), device_id, fresh=True)

    def _gate_close(self, headers, body):
        principal, error = self._device_principal(headers)
        if error:
            return error
        self.gates.close(principal.gate_id)
        _log("phone_gate", f"close {principal.device_id}")
        return 200, {"ok": True}, [_cookie("jl_gate", "", 0)]


_SERVICE: PhoneAuth | None = None
_SERVICE_LOCK = threading.Lock()


def service(bearer: str = "") -> PhoneAuth:
    """Process-wide PhoneAuth (gate records must survive between requests)."""
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None or _SERVICE.bearer != bearer:
            _SERVICE = PhoneAuth(bearer=bearer)
        return _SERVICE
