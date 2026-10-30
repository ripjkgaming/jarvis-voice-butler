"""HMAC call tokens for the voice WebSocket transport.

Token: ``<b64url(json claims)>.<b64url(hmac_sha256(secret, claims_b64))>``.
Claims: room, identity, mode, issued, expires, nonce, attrs. No livekit imports.
Semantics follow src/mint_token.py (room allowlist) and src/phone_session.py
(HMAC authority, single-use ``.claimed`` ledger, TTL 900).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_ROOM_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_NONCE_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")
MODES = frozenset({"speaker", "observer"})
MAX_TTL = 900
CLOCK_SKEW = 5
_MAX_TOKEN_CHARS = 2048
_CLAIM_KEYS = frozenset(
    {"room", "identity", "mode", "issued", "expires", "nonce", "attrs"}
)
_SECRET_BYTES = 32


class TokenError(Exception):
    """Token rejected. ``code`` is the WebSocket close code to send."""

    code = 4001


class BadTokenError(TokenError):
    code = 4001


class ReplayedTokenError(TokenError):
    code = 4002


@dataclass(frozen=True)
class CallClaims:
    room: str
    identity: str
    mode: str
    issued: int
    expires: int
    nonce: str
    attrs: dict[str, str]


def valid_room_name(name: object) -> bool:
    """Room names are wake-generated ``jarvis-<epoch>``; strict allowlist. Pure."""
    return isinstance(name, str) and _ROOM_RE.fullmatch(name) is not None


def jarvis_home() -> Path:
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def load_secret(home: Path | None = None) -> bytes:
    """Return the 32-byte signing secret, creating ``voice_secret`` (0600) once."""
    base = jarvis_home() if home is None else Path(home)
    final = base / "voice_secret"
    if final.exists():
        return _read_secret(final)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = secrets.token_hex(_SECRET_BYTES)
    tmp = base / f".voice_secret.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(raw + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, final)  # Atomic create-if-absent.
        except FileExistsError:
            return _read_secret(final)
    finally:
        tmp.unlink(missing_ok=True)
    return bytes.fromhex(raw)


def _read_secret(path: Path) -> bytes:
    if path.stat().st_mode & 0o077:
        os.chmod(path, 0o600)
    text = path.read_text().strip()
    try:
        secret = bytes.fromhex(text)
    except ValueError as exc:
        raise ValueError("voice_secret is malformed") from exc
    if len(secret) != _SECRET_BYTES:
        raise ValueError("voice_secret has the wrong length")
    return secret


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: bytes, claims_b64: str) -> str:
    mac = hmac.new(secret, claims_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64e(mac)


def mint_call_token(
    room: str,
    identity: str,
    mode: str,
    ttl: int,
    attrs: dict[str, str] | None,
    secret: bytes,
    *,
    now: float | None = None,
) -> str:
    if not valid_room_name(room):
        raise ValueError("invalid room name")
    if not isinstance(identity, str) or not 0 < len(identity) <= 128:
        raise ValueError("invalid identity")
    if mode not in MODES:
        raise ValueError("invalid mode")
    if type(ttl) is not int or not 0 < ttl <= MAX_TTL:
        raise ValueError("invalid ttl")
    attrs = {} if attrs is None else attrs
    if (
        not isinstance(attrs, dict)
        or len(attrs) > 16
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in attrs.items())
        or len(json.dumps(attrs).encode()) > 1024
    ):
        raise ValueError("invalid attrs")
    if not secret:
        raise ValueError("missing secret")
    issued = int(time.time() if now is None else now)
    claims = {
        "room": room,
        "identity": identity,
        "mode": mode,
        "issued": issued,
        "expires": issued + ttl,
        "nonce": secrets.token_urlsafe(18),
        "attrs": attrs,
    }
    claims_b64 = _b64e(
        json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    )
    return f"{claims_b64}.{_sign(secret, claims_b64)}"


def verify_call_token(
    token: object,
    secret: bytes,
    *,
    now: float | None = None,
    ledger: NonceLedger | None = None,
) -> CallClaims:
    now = time.time() if now is None else now
    if not isinstance(token, str) or len(token) > _MAX_TOKEN_CHARS:
        raise BadTokenError("malformed token")
    parts = token.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise BadTokenError("malformed token")
    claims_b64, signature = parts
    # Signature first: nothing from an unauthenticated body is parsed.
    if not (claims_b64.isascii() and signature.isascii()) or not hmac.compare_digest(
        _sign(secret, claims_b64), signature
    ):
        raise BadTokenError("bad signature")
    try:
        data = json.loads(_b64d(claims_b64))
    except (ValueError, UnicodeDecodeError):
        raise BadTokenError("malformed claims") from None
    if not isinstance(data, dict) or set(data) != _CLAIM_KEYS:
        raise BadTokenError("malformed claims")
    room, identity, mode = data["room"], data["identity"], data["mode"]
    issued, expires = data["issued"], data["expires"]
    nonce, attrs = data["nonce"], data["attrs"]
    if (
        not valid_room_name(room)
        or not isinstance(identity, str)
        or not 0 < len(identity) <= 128
        or mode not in MODES
        or type(issued) is not int
        or type(expires) is not int
        or not 0 < expires - issued <= MAX_TTL
        or issued > now + CLOCK_SKEW
        or not isinstance(nonce, str)
        or not isinstance(attrs, dict)
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in attrs.items())
    ):
        raise BadTokenError("malformed claims")
    if expires <= now:
        raise BadTokenError("expired")
    if ledger is not None and not ledger.claim(nonce, expires, now):
        raise ReplayedTokenError("token already used")
    return CallClaims(room, identity, mode, issued, expires, nonce, attrs)


class NonceLedger(Protocol):
    def claim(self, nonce: str, expires: float, now: float) -> bool:
        """Return True only the first time ``nonce`` is seen."""


class MemoryNonceLedger:
    """In-process one-shot ledger; pruned of entries past their expiry."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    def claim(self, nonce: str, expires: float, now: float) -> bool:
        self._seen = {n: e for n, e in self._seen.items() if e > now}
        if nonce in self._seen:
            return False
        self._seen[nonce] = expires
        return True


class FileNonceLedger:
    """Cross-process one-shot ledger: O_EXCL ``<nonce>.claimed`` files."""

    def __init__(self, directory: Path, retain_seconds: float = MAX_TTL + 60) -> None:
        self._dir = Path(directory)
        self._retain = retain_seconds

    def claim(self, nonce: str, expires: float, now: float) -> bool:
        if not isinstance(nonce, str) or _NONCE_RE.fullmatch(nonce) is None:
            return False
        if expires <= now:
            return False
        self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for old in self._dir.glob("*.claimed"):
            try:
                if time.time() - old.stat().st_mtime > self._retain:
                    old.unlink()
            except OSError:
                pass
        try:
            fd = os.open(
                self._dir / f"{nonce}.claimed",
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            return False
        os.close(fd)
        return True
