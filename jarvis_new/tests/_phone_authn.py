"""Software WebAuthn authenticator for the phone gate tests.

Builds real ES256 (P-256) registration and assertion responses, encoded the
way Chrome sends them from navigator.credentials (base64url JSON), so the
bridge's verifier is exercised against genuine signatures.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

ORIGIN = "https://ripjk.tailea4e7f.ts.net"
RP_ID = "ripjk.tailea4e7f.ts.net"


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _head(major: int, n: int) -> bytes:
    if n < 24:
        return bytes([(major << 5) | n])
    if n < 256:
        return bytes([(major << 5) | 24, n])
    if n < 65536:
        return bytes([(major << 5) | 25]) + struct.pack(">H", n)
    return bytes([(major << 5) | 26]) + struct.pack(">I", n)


def cbor(obj) -> bytes:
    """Tiny CBOR encoder (definite lengths only) for test fixtures."""
    if isinstance(obj, bool):
        return b"\xf5" if obj else b"\xf4"
    if isinstance(obj, int):
        return _head(0, obj) if obj >= 0 else _head(1, -1 - obj)
    if isinstance(obj, bytes):
        return _head(2, len(obj)) + obj
    if isinstance(obj, str):
        raw = obj.encode()
        return _head(3, len(raw)) + raw
    if isinstance(obj, list):
        return _head(4, len(obj)) + b"".join(cbor(x) for x in obj)
    if isinstance(obj, dict):
        return _head(5, len(obj)) + b"".join(cbor(k) + cbor(v) for k, v in obj.items())
    raise TypeError(type(obj))


class Authenticator:
    """One platform credential. Mutate fields to forge bad responses."""

    def __init__(self, *, rp_id: str = RP_ID, origin: str = ORIGIN, alg: int = -7):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = hashlib.sha256(str(id(self)).encode()).digest()[:16]
        self.rp_id = rp_id
        self.origin = origin
        self.alg = alg
        self.sign_count = 0

    def _cose(self) -> bytes:
        nums = self.key.public_key().public_numbers()
        return cbor(
            {
                1: 2,
                3: self.alg,
                -1: 1,
                -2: nums.x.to_bytes(32, "big"),
                -3: nums.y.to_bytes(32, "big"),
            }
        )

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": challenge,
                "origin": origin or self.origin,
                "crossOrigin": False,
            }
        ).encode()

    def register(
        self, challenge: str, *, fmt: str = "none", flags: int = 0x45, origin=None
    ) -> dict:
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        attested = (
            b"\x00" * 16 + struct.pack(">H", len(self.cred_id)) + self.cred_id
        ) + self._cose()
        auth_data = rp_hash + bytes([flags]) + struct.pack(">I", 0) + attested
        att = cbor({"fmt": fmt, "attStmt": {}, "authData": auth_data})
        return {
            "id": b64u(self.cred_id),
            "rawId": b64u(self.cred_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64u(
                    self._client_data("webauthn.create", challenge, origin)
                ),
                "attestationObject": b64u(att),
            },
        }

    def assert_(
        self,
        challenge: str,
        *,
        flags: int = 0x05,
        origin: str | None = None,
        rp_id: str | None = None,
        kind: str = "webauthn.get",
        count: int | None = None,
        tamper: bool = False,
    ) -> dict:
        if count is None:
            self.sign_count += 1
            count = self.sign_count
        rp_hash = hashlib.sha256((rp_id or self.rp_id).encode()).digest()
        auth_data = rp_hash + bytes([flags]) + struct.pack(">I", count)
        client = self._client_data(kind, challenge, origin)
        sig = self.key.sign(
            auth_data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256())
        )
        if tamper:
            sig = sig[:-1] + bytes([sig[-1] ^ 1])
        return {
            "id": b64u(self.cred_id),
            "rawId": b64u(self.cred_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64u(client),
                "authenticatorData": b64u(auth_data),
                "signature": b64u(sig),
                "userHandle": None,
            },
        }
