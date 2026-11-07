"""phone_device: pairing codes, device store, cookies and request auth."""

import hashlib
import json
import stat
import sys

import pytest
from _phone_authn import ORIGIN, Authenticator

sys.path.insert(0, "src")

import phone_device as pd


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def svc(tmp_path, clock, monkeypatch):
    gate = tmp_path / "gate"
    salt = bytes(range(16))
    digest = hashlib.scrypt(b"open sesame", salt=salt, n=16384, r=8, p=1, dklen=64)
    gate.write_text(f"{salt.hex()}:{digest.hex()}\n")
    monkeypatch.setenv("JARVIS_PHONE_GATE", str(gate))
    return pd.PhoneAuth(
        root=tmp_path / "phone", origin=ORIGIN, bearer="tok", clock=clock
    )


def hdr(
    cookie: str | None = None, *, origin: str | None = ORIGIN, phone=True, auth=None
):
    h = {}
    if cookie:
        h["Cookie"] = cookie
    if origin:
        h["Origin"] = origin
    if phone:
        h["X-Jarvis-Phone"] = "1"
    if auth:
        h["Authorization"] = auth
    return h


def pair(svc, name="Pixel 7"):
    code = svc.store.issue_code()
    status, body, cookies = svc.handle(
        "POST", "/phone/v1/pair", hdr(), {"code": code, "name": name}
    )
    assert status == 200, body
    dev = next(c for c in cookies if c.startswith("jl_dev="))
    return body, dev.split(";")[0]


# --- pairing codes --------------------------------------------------------------


def test_code_format_is_crockford_10(svc):
    code = svc.store.issue_code()
    assert len(code) == 10
    assert set(code) <= set(pd.CROCKFORD)


def test_code_file_is_hashed_and_private(svc):
    code = svc.store.issue_code()
    path = svc.store.root / "pair.json"
    data = json.loads(path.read_text())
    assert code not in path.read_text()
    assert hashlib.sha256(code.encode()).hexdigest() in data
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_code_single_use(svc):
    code = svc.store.issue_code()
    assert svc.store.consume_code(code)
    assert not svc.store.consume_code(code)


def test_code_normalised_for_manual_entry(svc):
    code = svc.store.issue_code()
    messy = "-".join([code[:5].lower(), code[5:]])
    assert svc.store.consume_code(messy)


def test_code_expires_after_600s(svc, clock):
    code = svc.store.issue_code()
    clock.t += 601
    assert not svc.store.consume_code(code)


def test_max_three_pending_codes(svc):
    for _ in range(3):
        svc.store.issue_code()
    with pytest.raises(pd.PairError):
        svc.store.issue_code()


def test_expired_codes_free_slots(svc, clock):
    for _ in range(3):
        svc.store.issue_code()
    clock.t += 700
    svc.store.issue_code()


# --- pairing route --------------------------------------------------------------


def test_pair_sets_device_cookie_and_returns_registration(svc):
    body, _dev = pair(svc)
    assert body["ok"] and body["device_id"].startswith("jl-")
    reg = body["registration"]
    assert reg["rp"]["id"] == "ripjk.tailea4e7f.ts.net"
    assert reg["challenge"]
    status, _, _cookies = svc.handle(
        "POST", "/phone/v1/pair", hdr(), {"code": "0000000000", "name": "x"}
    )
    assert status == 401


def test_device_cookie_attributes(svc):
    code = svc.store.issue_code()
    _, _, cookies = svc.handle(
        "POST", "/phone/v1/pair", hdr(), {"code": code, "name": "p"}
    )
    dev = next(c for c in cookies if c.startswith("jl_dev="))
    for attr in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/", "Max-Age=15552000"):
        assert attr in dev


def test_devices_file_stores_only_secret_hash(svc):
    _, dev = pair(svc)
    secret = dev.split("=", 1)[1].split(".", 1)[1]
    raw = (svc.store.root / "devices.json").read_text()
    assert secret not in raw
    assert hashlib.sha256(secret.encode()).hexdigest() in raw
    assert stat.S_IMODE((svc.store.root / "devices.json").stat().st_mode) == 0o600


def test_pair_rate_limited_to_5_per_minute(svc, clock):
    for _ in range(5):
        status, _, _ = svc.handle(
            "POST", "/phone/v1/pair", hdr(), {"code": "AAAAAAAAAA", "name": "x"}
        )
        assert status == 401
    code = svc.store.issue_code()
    status, _body, _ = svc.handle(
        "POST", "/phone/v1/pair", hdr(), {"code": code, "name": "x"}
    )
    assert status == 429
    clock.t += 61
    status, _, _ = svc.handle(
        "POST", "/phone/v1/pair", hdr(), {"code": code, "name": "x"}
    )
    assert status == 200


def test_max_three_devices_then_409(svc):
    for _ in range(3):
        pair(svc)
    code = svc.store.issue_code()
    status, _body, _ = svc.handle(
        "POST", "/phone/v1/pair", hdr(), {"code": code, "name": "x"}
    )
    assert status == 409


def test_pair_requires_phone_header_and_origin(svc):
    code = svc.store.issue_code()
    for headers in (
        hdr(phone=False),
        hdr(origin="https://evil.example"),
        hdr(origin=None),
    ):
        status, _, _ = svc.handle(
            "POST", "/phone/v1/pair", headers, {"code": code, "name": "x"}
        )
        assert status in (401, 403)
    status, _, _ = svc.handle(
        "POST", "/phone/v1/pair", hdr(), {"code": code, "name": "x"}
    )
    assert status == 200  # the rejected attempts did not burn the code


def test_device_auth_disabled_without_origin(tmp_path, clock):
    svc = pd.PhoneAuth(root=tmp_path / "p", origin="", bearer="tok", clock=clock)
    code = svc.store.issue_code()
    status, _body, _ = svc.handle(
        "POST", "/phone/v1/pair", hdr(), {"code": code, "name": "x"}
    )
    assert status == 503


def test_revoke_and_list(svc):
    body, _ = pair(svc)
    devices = svc.store.list()
    assert [d["id"] for d in devices] == [body["device_id"]]
    assert svc.store.revoke(body["device_id"])
    assert svc.store.list() == []
    assert not svc.store.revoke(body["device_id"])


# --- cookie auth -----------------------------------------------------------------


def test_cookie_parsing():
    assert pd.parse_cookies("a=1; jl_dev=jl-1.abc; b=") == {
        "a": "1",
        "jl_dev": "jl-1.abc",
        "b": "",
    }
    secret = "c2VjcmV0c2VjcmV0c2VjcmV0"
    assert pd.parse_device_cookie(f"jl-12ab34cd.{secret}") == ("jl-12ab34cd", secret)
    for bad in (
        "",
        "nodot",
        ".x",
        "jl-1.",
        "jl-12ab34cd.short",
        "jl-12ab34cd.a b",
        "x" * 600,
    ):
        assert pd.parse_device_cookie(bad) is None


def test_device_principal_from_cookie(svc):
    _, dev = pair(svc)
    p = svc.authorize("GET", hdr(dev))
    assert p and p.kind == "device" and p.gate == "cold"


def test_bad_secret_rejected(svc):
    _, dev = pair(svc)
    forged = dev[:-3] + ("AAA" if not dev.endswith("AAA") else "BBB")
    assert svc.authorize("GET", hdr(forged)) is None


def test_unknown_device_rejected(svc):
    assert svc.authorize("GET", hdr("jl_dev=jl-deadbeef.c2VjcmV0")) is None


def test_bearer_still_works(svc):
    p = svc.authorize("GET", {"Authorization": "Bearer tok"})
    assert p and p.kind == "bearer" and p.gate == "open"
    assert svc.authorize("GET", {"Authorization": "Bearer nope"}) is None


def test_device_auth_requires_header_and_origin(svc):
    _, dev = pair(svc)
    assert svc.authorize("GET", hdr(dev, phone=False)) is None
    assert svc.authorize("GET", hdr(dev, origin="https://evil.example")) is None
    assert svc.authorize("POST", hdr(dev, origin=None)) is None
    # Same-origin GETs may omit Origin.
    assert svc.authorize("GET", hdr(dev, origin=None)) is not None


def test_unauthorized_state_says_pair(svc):
    status, body, _ = svc.handle("GET", "/phone/v1/state", hdr(), None)
    assert status == 401
    assert body == {"ok": False, "error": "unauthorized", "auth": "pair"}


# --- passkey registration ------------------------------------------------------------


def test_passkey_registration_within_pairing_window(svc):
    body, dev = pair(svc)
    auth = Authenticator()
    cred = auth.register(body["registration"]["challenge"])
    status, out, _ = svc.handle(
        "POST", "/phone/v1/passkey", hdr(dev), {"credential": cred}
    )
    assert status == 200 and out["ok"]
    device = svc.store.get(body["device_id"])
    assert len(device["credentials"]) == 1


def test_passkey_registration_refused_after_window_without_gate(svc, clock):
    _body, dev = pair(svc)
    clock.t += 601
    status, out, _ = svc.handle(
        "POST", "/phone/v1/challenge", hdr(dev), {"purpose": "register"}
    )
    assert status == 401 and out["error"] == "gate"


def test_register_challenge_can_be_refetched(svc):
    _body, dev = pair(svc)
    status, out, _ = svc.handle(
        "POST", "/phone/v1/challenge", hdr(dev), {"purpose": "register"}
    )
    assert status == 200
    auth = Authenticator()
    cred = auth.register(out["registration"]["challenge"])
    status, _, _ = svc.handle(
        "POST", "/phone/v1/passkey", hdr(dev), {"credential": cred}
    )
    assert status == 200


def test_passkey_registration_rejects_reused_challenge(svc):
    body, dev = pair(svc)
    challenge = body["registration"]["challenge"]
    first = Authenticator().register(challenge)
    assert (
        svc.handle("POST", "/phone/v1/passkey", hdr(dev), {"credential": first})[0]
        == 200
    )
    second = Authenticator().register(challenge)
    status, _, _ = svc.handle(
        "POST", "/phone/v1/passkey", hdr(dev), {"credential": second}
    )
    assert status == 400


def test_audit_lines_written(svc, tmp_path):
    body, _ = pair(svc)
    svc.store.revoke(body["device_id"])
    log = (tmp_path / "actions.log").read_text()
    assert "phone_pair" in log and body["device_id"] in log
    assert "phone_revoke" in log
