import base64
import hashlib
import hmac
import json
import os
import stat
import time

import pytest

from jarvis_rt.transport.tokens import (
    BadTokenError,
    FileNonceLedger,
    MemoryNonceLedger,
    ReplayedTokenError,
    load_secret,
    mint_call_token,
    valid_room_name,
    verify_call_token,
)

SECRET = b"s" * 32


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _resign(claims: dict, secret: bytes = SECRET) -> str:
    body = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def test_round_trip_claims():
    now = time.time()
    token = mint_call_token(
        "jarvis-1", "user", "speaker", 120, {"k": "v"}, SECRET, now=now
    )
    claims = verify_call_token(token, SECRET, now=now)
    assert (claims.room, claims.identity, claims.mode) == (
        "jarvis-1",
        "user",
        "speaker",
    )
    assert claims.attrs == {"k": "v"}
    assert claims.expires - claims.issued == 120


def test_wrong_secret_is_bad_signature():
    token = mint_call_token("jarvis-1", "u", "observer", 60, None, SECRET)
    with pytest.raises(BadTokenError) as exc:
        verify_call_token(token, b"x" * 32)
    assert exc.value.code == 4001


def test_tampered_claims_rejected_without_resigning():
    token = mint_call_token("jarvis-1", "u", "observer", 60, None, SECRET)
    body, sig = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["mode"] = "speaker"
    forged = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    with pytest.raises(BadTokenError):
        verify_call_token(f"{forged}.{sig}", SECRET)


@pytest.mark.parametrize(
    "token",
    ["", "nodot", "a.b.c", ".", 12, "x" * 2049, "é.é", None],
)
def test_garbage_tokens_rejected(token):
    with pytest.raises(BadTokenError):
        verify_call_token(token, SECRET)


def test_expired_token_is_4001():
    token = mint_call_token("jarvis-1", "u", "speaker", 60, None, SECRET, now=1000)
    with pytest.raises(BadTokenError) as exc:
        verify_call_token(token, SECRET, now=1000 + 61)
    assert exc.value.code == 4001


def test_future_issue_time_rejected():
    token = mint_call_token("jarvis-1", "u", "speaker", 60, None, SECRET, now=5000)
    with pytest.raises(BadTokenError):
        verify_call_token(token, SECRET, now=1000)


def test_forged_claim_shapes_rejected():
    now = time.time()
    base = {
        "room": "jarvis-1",
        "identity": "u",
        "mode": "speaker",
        "issued": int(now),
        "expires": int(now) + 60,
        "nonce": "n" * 20,
        "attrs": {},
    }
    for key, value in [
        ("mode", "owner"),
        ("room", "../x"),
        ("issued", True),
        ("attrs", {"a": 1}),
        ("expires", int(now) + 10_000),
    ]:
        claims = dict(base, **{key: value})
        with pytest.raises(BadTokenError):
            verify_call_token(_resign(claims), SECRET, now=now)


@pytest.mark.parametrize("room", ["", "../x", "a" * 65, "has space", None])
def test_mint_rejects_bad_room(room):
    with pytest.raises(ValueError):
        mint_call_token(room, "u", "speaker", 60, None, SECRET)


def test_valid_room_name_allowlist():
    assert valid_room_name("jarvis-1700000000")
    assert not valid_room_name("jarvis/1")


def test_mint_rejects_bad_mode_and_ttl():
    with pytest.raises(ValueError):
        mint_call_token("jarvis-1", "u", "owner", 60, None, SECRET)
    for ttl in (0, 901, True):
        with pytest.raises(ValueError):
            mint_call_token("jarvis-1", "u", "speaker", ttl, None, SECRET)


def test_memory_ledger_rejects_replay():
    now = time.time()
    token = mint_call_token("jarvis-1", "u", "speaker", 60, None, SECRET, now=now)
    ledger = MemoryNonceLedger()
    verify_call_token(token, SECRET, now=now, ledger=ledger)
    with pytest.raises(ReplayedTokenError) as exc:
        verify_call_token(token, SECRET, now=now, ledger=ledger)
    assert exc.value.code == 4002


def test_file_ledger_is_one_shot_across_instances(tmp_path):
    now = time.time()
    token = mint_call_token("jarvis-1", "u", "speaker", 60, None, SECRET, now=now)
    verify_call_token(token, SECRET, now=now, ledger=FileNonceLedger(tmp_path))
    with pytest.raises(ReplayedTokenError):
        verify_call_token(token, SECRET, now=now, ledger=FileNonceLedger(tmp_path))


def test_file_ledger_rejects_unsafe_nonce(tmp_path):
    ledger = FileNonceLedger(tmp_path)
    assert ledger.claim("../../escape", time.time() + 60, time.time()) is False
    assert not (tmp_path.parent / "escape.claimed").exists()
    assert list(tmp_path.glob("*.claimed")) == []


def test_load_secret_creates_once_with_0600(tmp_path):
    first = load_secret(tmp_path)
    assert len(first) == 32
    mode = stat.S_IMODE(os.stat(tmp_path / "voice_secret").st_mode)
    assert mode == 0o600
    assert load_secret(tmp_path) == first


def test_load_secret_tightens_loose_permissions(tmp_path):
    secret = load_secret(tmp_path)
    os.chmod(tmp_path / "voice_secret", 0o644)
    assert load_secret(tmp_path) == secret
    assert stat.S_IMODE(os.stat(tmp_path / "voice_secret").st_mode) == 0o600


def test_load_secret_defaults_to_jarvis_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    load_secret()
    assert (tmp_path / "home" / "voice_secret").is_file()
