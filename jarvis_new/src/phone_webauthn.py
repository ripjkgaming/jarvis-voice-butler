"""Minimal WebAuthn for the LOCKE//LINK phone gate.

Scope is deliberately small: platform passkeys only, ``attestation: "none"``
registration, and ES256 (ECDSA P-256 / SHA-256, COSE alg -7) assertions.
The CBOR decoder handles only what an ``attestationObject`` and a COSE key
need (major types 0-5 and the simple values of 7) and rejects indefinite
lengths, tags and floats. Everything raises ``WebAuthnError`` on bad input.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import threading
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

ES256 = -7
CHALLENGE_TTL_S = 60.0
RP_NAME = "LOCKE//LINK"

FLAG_UP = 0x01
FLAG_UV = 0x04
FLAG_AT = 0x40


class WebAuthnError(ValueError):
    """Any malformed or unverifiable WebAuthn response."""


class CBORError(WebAuthnError):
    """Malformed or unsupported CBOR."""


def b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64u_decode(text: str) -> bytes:
    if not isinstance(text, str) or len(text) > 32768:
        raise WebAuthnError("bad base64url")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as exc:
        raise WebAuthnError("bad base64url") from exc


# --- CBOR ------------------------------------------------------------------

_MAX_DEPTH = 16
_MAX_ITEMS = 256


def _cbor_item(data: bytes, pos: int, depth: int):
    if depth > _MAX_DEPTH:
        raise CBORError("nesting too deep")
    if pos >= len(data):
        raise CBORError("truncated")
    head = data[pos]
    major, info = head >> 5, head & 0x1F
    pos += 1
    if info < 24:
        arg = info
    elif info in (24, 25, 26, 27):
        size = 1 << (info - 24)
        if pos + size > len(data):
            raise CBORError("truncated")
        arg = int.from_bytes(data[pos : pos + size], "big")
        pos += size
    else:
        raise CBORError("indefinite or reserved length")
    if major == 0:
        return arg, pos
    if major == 1:
        return -1 - arg, pos
    if major in (2, 3):
        if pos + arg > len(data):
            raise CBORError("truncated")
        raw = data[pos : pos + arg]
        pos += arg
        if major == 2:
            return bytes(raw), pos
        try:
            return raw.decode(), pos
        except UnicodeDecodeError as exc:
            raise CBORError("bad text") from exc
    if major == 4:
        if arg > _MAX_ITEMS:
            raise CBORError("array too long")
        out = []
        for _ in range(arg):
            item, pos = _cbor_item(data, pos, depth + 1)
            out.append(item)
        return out, pos
    if major == 5:
        if arg > _MAX_ITEMS:
            raise CBORError("map too long")
        obj = {}
        for _ in range(arg):
            key, pos = _cbor_item(data, pos, depth + 1)
            if isinstance(key, (list, dict)):
                raise CBORError("unhashable key")
            obj[key], pos = _cbor_item(data, pos, depth + 1)
        return obj, pos
    if major == 7 and info < 24:
        simple = {20: False, 21: True, 22: None}
        if info in simple:
            return simple[info], pos
    raise CBORError("unsupported CBOR item")


def cbor_decode(data: bytes, pos: int = 0):
    """Decode one item; returns (value, end offset)."""
    return _cbor_item(bytes(data), pos, 0)


def cbor_loads(data: bytes):
    """Decode exactly one item spanning all of ``data``."""
    value, end = cbor_decode(data)
    if end != len(data):
        raise CBORError("trailing bytes")
    return value


# --- challenges --------------------------------------------------------------


class ChallengeStore:
    """Memory-only single-use challenges bound to (device, purpose), 60 s TTL."""

    MAX = 64

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._rows: dict[str, tuple[str, str, float]] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._rows)

    def issue(self, device_id: str, purpose: str) -> str:
        with self._lock:
            now = self.clock()
            self._rows = {k: v for k, v in self._rows.items() if v[2] > now}
            while len(self._rows) >= self.MAX:
                self._rows.pop(next(iter(self._rows)))
            challenge = b64u_encode(secrets.token_bytes(32))
            self._rows[challenge] = (device_id, purpose, now + CHALLENGE_TTL_S)
            return challenge

    def consume(self, device_id: str, purpose: str, challenge) -> bool:
        if not isinstance(challenge, str) or len(challenge) > 128:
            return False
        with self._lock:
            row = self._rows.get(challenge)
            if not row or row[0] != device_id or row[1] != purpose:
                return False
            del self._rows[challenge]
            return self.clock() < row[2]


# --- options ------------------------------------------------------------------


def registration_options(
    *, rp_id: str, device_id: str, name: str, challenge: str, exclude=()
) -> dict:
    """PublicKeyCredentialCreationOptions (JSON form, base64url buffers)."""
    return {
        "challenge": challenge,
        "rp": {"id": rp_id, "name": RP_NAME},
        "user": {
            "id": b64u_encode(device_id.encode()),
            "name": f"owner@{device_id}",
            "displayName": name or device_id,
        },
        "pubKeyCredParams": [{"type": "public-key", "alg": ES256}],
        "authenticatorSelection": {
            "authenticatorAttachment": "platform",
            "residentKey": "preferred",
            "userVerification": "required",
        },
        "attestation": "none",
        "timeout": 60000,
        "excludeCredentials": [{"type": "public-key", "id": cid} for cid in exclude],
    }


def request_options(*, rp_id: str, challenge: str, credentials) -> dict:
    """PublicKeyCredentialRequestOptions for /phone/v1/challenge."""
    return {
        "challenge": challenge,
        "rpId": rp_id,
        "allowCredentials": [
            {"type": "public-key", "id": c["id"], "transports": ["internal"]}
            for c in credentials
        ],
        "timeout": 60000,
        "userVerification": "required",
    }


# --- parsing helpers -----------------------------------------------------------


def _client_data(response: dict, kind: str, challenge: str, origin: str) -> bytes:
    raw = b64u_decode(response.get("clientDataJSON", ""))
    try:
        data = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise WebAuthnError("bad clientDataJSON") from exc
    if not isinstance(data, dict) or data.get("type") != kind:
        raise WebAuthnError("wrong ceremony type")
    got = data.get("challenge")
    if not isinstance(got, str) or not hmac.compare_digest(
        got.encode(), challenge.encode()
    ):
        raise WebAuthnError("challenge mismatch")
    if data.get("origin") != origin:
        raise WebAuthnError("origin mismatch")
    if data.get("crossOrigin") is True:
        raise WebAuthnError("cross-origin ceremony")
    return raw


def _auth_flags(auth_data: bytes, rp_id: str, need: int) -> tuple[int, int]:
    if len(auth_data) < 37:
        raise WebAuthnError("short authenticatorData")
    want = hashlib.sha256(rp_id.encode()).digest()
    if not hmac.compare_digest(auth_data[:32], want):
        raise WebAuthnError("rpIdHash mismatch")
    flags = auth_data[32]
    if flags & need != need:
        raise WebAuthnError("required flags missing")
    (count,) = struct.unpack(">I", auth_data[33:37])
    return flags, count


def _response(obj) -> dict:
    if not isinstance(obj, dict) or not isinstance(obj.get("response"), dict):
        raise WebAuthnError("missing response")
    if obj.get("type", "public-key") != "public-key":
        raise WebAuthnError("wrong credential type")
    return obj["response"]


def client_challenge(obj) -> str | None:
    """The challenge a response claims, for looking it up. None if absent."""
    try:
        raw = b64u_decode(obj["response"]["clientDataJSON"])
        value = json.loads(raw.decode()).get("challenge")
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, WebAuthnError):
        return None
    return value if isinstance(value, str) and len(value) <= 128 else None


# --- registration ---------------------------------------------------------------


def parse_registration(credential, *, challenge: str, origin: str, rp_id: str) -> dict:
    """Validate a registration response; returns {id, x, y, sign_count}."""
    response = _response(credential)
    _client_data(response, "webauthn.create", challenge, origin)
    att = cbor_loads(b64u_decode(response.get("attestationObject", "")))
    if not isinstance(att, dict) or att.get("fmt") != "none":
        raise WebAuthnError("only none attestation is accepted")
    auth_data = att.get("authData")
    if not isinstance(auth_data, bytes):
        raise WebAuthnError("missing authData")
    _, count = _auth_flags(auth_data, rp_id, FLAG_UP | FLAG_UV | FLAG_AT)
    pos = 37 + 16  # skip aaguid
    if len(auth_data) < pos + 2:
        raise WebAuthnError("short attested credential data")
    (id_len,) = struct.unpack(">H", auth_data[pos : pos + 2])
    pos += 2
    if id_len == 0 or id_len > 1023 or len(auth_data) < pos + id_len:
        raise WebAuthnError("bad credential id")
    cred_id = auth_data[pos : pos + id_len]
    pos += id_len
    key, end = cbor_decode(auth_data, pos)
    if end != len(auth_data):
        raise WebAuthnError("unexpected extensions")
    if not isinstance(key, dict):
        raise WebAuthnError("bad COSE key")
    if key.get(1) != 2 or key.get(3) != ES256 or key.get(-1) != 1:
        raise WebAuthnError("only ES256 P-256 keys are accepted")
    x, y = key.get(-2), key.get(-3)
    if not (isinstance(x, bytes) and isinstance(y, bytes)):
        raise WebAuthnError("bad COSE coordinates")
    if len(x) != 32 or len(y) != 32:
        raise WebAuthnError("bad COSE coordinates")
    try:
        _public_key(x, y)
    except ValueError as exc:
        raise WebAuthnError("point not on curve") from exc
    claimed = credential.get("id")
    if claimed is not None and claimed != b64u_encode(cred_id):
        raise WebAuthnError("credential id mismatch")
    return {
        "id": b64u_encode(cred_id),
        "x": b64u_encode(x),
        "y": b64u_encode(y),
        "sign_count": count,
    }


def _public_key(x: bytes, y: bytes) -> ec.EllipticCurvePublicKey:
    numbers = ec.EllipticCurvePublicNumbers(
        int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
    )
    return numbers.public_key()


# --- assertions -------------------------------------------------------------------


def verify_assertion(
    assertion, *, credentials, challenge: str, origin: str, rp_id: str
) -> tuple[str, int]:
    """Verify an ES256 assertion against a device's stored credentials.

    Returns (credential id, new sign count). The caller has already consumed
    ``challenge`` from its ChallengeStore and must persist the new count.
    """
    response = _response(assertion)
    cred_id = assertion.get("rawId") or assertion.get("id")
    cred = next((c for c in credentials if c.get("id") == cred_id), None)
    if cred is None:
        raise WebAuthnError("unknown credential")
    client = _client_data(response, "webauthn.get", challenge, origin)
    auth_data = b64u_decode(response.get("authenticatorData", ""))
    _, count = _auth_flags(auth_data, rp_id, FLAG_UP | FLAG_UV)
    stored = int(cred.get("sign_count") or 0)
    if (count or stored) and count <= stored:
        raise WebAuthnError("sign count regression")
    signature = b64u_decode(response.get("signature", ""))
    try:
        key = _public_key(b64u_decode(cred["x"]), b64u_decode(cred["y"]))
        key.verify(
            signature,
            auth_data + hashlib.sha256(client).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
    except (InvalidSignature, ValueError, KeyError) as exc:
        raise WebAuthnError("bad signature") from exc
    return cred_id, count
