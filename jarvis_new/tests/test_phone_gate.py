"""Phone passphrase gate: scrypt file, lockout, open/soft/cold, passkey reopen."""

import hashlib
import sys

import pytest
from _phone_authn import ORIGIN, Authenticator
from test_phone_device import Clock, hdr, pair

sys.path.insert(0, "src")

import phone_device as pd

PW = "open sesame"


def write_gate(path, pw=PW):
    salt = bytes(range(16))
    digest = hashlib.scrypt(pw.encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
    path.write_text(f"{salt.hex()}:{digest.hex()}\n")


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def svc(tmp_path, clock, monkeypatch):
    write_gate(tmp_path / "gate")
    monkeypatch.setenv("JARVIS_PHONE_GATE", str(tmp_path / "gate"))
    return pd.PhoneAuth(
        root=tmp_path / "phone", origin=ORIGIN, bearer="tok", clock=clock
    )


def cookie_of(cookies, name):
    row = next(c for c in cookies if c.startswith(name + "="))
    return row.split(";")[0]


def paired_with_passkey(svc):
    body, dev = pair(svc)
    auth = Authenticator()
    cred = auth.register(body["registration"]["challenge"])
    assert (
        svc.handle("POST", "/phone/v1/passkey", hdr(dev), {"credential": cred})[0]
        == 200
    )
    return dev, auth


def open_gate(svc, dev):
    status, body, cookies = svc.handle(
        "POST", "/phone/v1/gate", hdr(dev), {"passphrase": PW}
    )
    assert status == 200, body
    return body, cookie_of(cookies, "jl_gate")


# --- scrypt file ---------------------------------------------------------------


def test_verify_passphrase_against_fixture(tmp_path):
    write_gate(tmp_path / "g")
    assert pd.verify_passphrase(PW, tmp_path / "g")
    assert not pd.verify_passphrase("wrong", tmp_path / "g")


def test_malformed_gate_file_never_verifies(tmp_path):
    (tmp_path / "g").write_text("zz:zz\n")
    assert not pd.verify_passphrase(PW, tmp_path / "g")


def test_gate_path_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_PHONE_GATE", str(tmp_path / "x"))
    assert pd.gate_file() == tmp_path / "x"
    monkeypatch.delenv("JARVIS_PHONE_GATE")
    assert str(pd.gate_file()).endswith(".config/locke/gate")


def test_missing_gate_file_is_503(svc, tmp_path):
    _, dev = pair(svc)
    (tmp_path / "gate").unlink()
    status, body, _ = svc.handle("POST", "/phone/v1/gate", hdr(dev), {"passphrase": PW})
    assert status == 503
    assert body["error"] == "gate not configured; run deck passwd"


# --- open gate -------------------------------------------------------------------


def test_correct_passphrase_opens_gate(svc):
    _, dev = pair(svc)
    body, gate = open_gate(svc, dev)
    assert body["ok"] and body["gate"] == "open"
    assert body["soft_after_s"] == 600
    assert 43000 < body["expires_in_s"] <= 43200
    p = svc.authorize("GET", hdr(f"{dev}; {gate}"))
    assert p.gate == "open"


def test_gate_cookie_is_session_cookie(svc):
    _, dev = pair(svc)
    _, _, cookies = svc.handle("POST", "/phone/v1/gate", hdr(dev), {"passphrase": PW})
    row = next(c for c in cookies if c.startswith("jl_gate="))
    assert "Max-Age" not in row and "Expires" not in row
    for attr in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/"):
        assert attr in row


def test_gate_needs_device(svc):
    status, body, _ = svc.handle("POST", "/phone/v1/gate", hdr(), {"passphrase": PW})
    assert status == 401 and body["auth"] == "pair"


def test_gate_bound_to_device(svc):
    _, dev_a = pair(svc)
    _, dev_b = pair(svc)
    _, gate = open_gate(svc, dev_a)
    assert svc.authorize("GET", hdr(f"{dev_b}; {gate}")).gate == "cold"


def test_state_needs_open_gate(svc):
    _, dev = pair(svc)
    status, body, _ = svc.handle("GET", "/phone/v1/state", hdr(dev), None)
    assert status == 401 and body == {"ok": False, "error": "gate", "gate": "cold"}


def test_gate_close_clears(svc):
    _, dev = pair(svc)
    _, gate = open_gate(svc, dev)
    status, _, cookies = svc.handle(
        "POST", "/phone/v1/gate/close", hdr(f"{dev}; {gate}"), {}
    )
    assert status == 200
    assert any(c.startswith("jl_gate=;") and "Max-Age=0" in c for c in cookies)
    assert svc.authorize("GET", hdr(f"{dev}; {gate}")).gate == "cold"


# --- transitions -------------------------------------------------------------------


def test_open_to_soft_after_idle_and_activity_keeps_open(svc, clock):
    _, dev = pair(svc)
    _, gate = open_gate(svc, dev)
    both = hdr(f"{dev}; {gate}")
    for _ in range(5):
        clock.t += 590
        assert svc.authorize("GET", both).gate == "open"  # each request touches
    clock.t += 601
    assert svc.authorize("GET", both).gate == "soft"
    # A soft gate is not refreshed by requests.
    clock.t += 5
    assert svc.authorize("GET", both).gate == "soft"


def test_cold_after_12h_even_with_activity(svc, clock):
    _, dev = pair(svc)
    _, gate = open_gate(svc, dev)
    both = hdr(f"{dev}; {gate}")
    for _ in range(80):
        clock.t += 550
        svc.authorize("GET", both)
    assert svc.authorize("GET", both).gate == "cold"


def test_soft_reopens_with_assertion(svc, clock):
    dev, auth = paired_with_passkey(svc)
    _, gate = open_gate(svc, dev)
    both = hdr(f"{dev}; {gate}")
    clock.t += 700
    assert svc.authorize("GET", both).gate == "soft"
    status, ch, _ = svc.handle("POST", "/phone/v1/challenge", both, {"purpose": "gate"})
    assert status == 200
    assert ch["userVerification"] == "required" and ch["timeout"] == 60000
    assert ch["allowCredentials"][0]["id"]
    status, body, _cookies = svc.handle(
        "POST", "/phone/v1/gate", both, {"assertion": auth.assert_(ch["challenge"])}
    )
    assert status == 200 and body["gate"] == "open"
    assert svc.authorize("GET", both).gate == "open"


def test_cold_gate_refuses_assertion(svc, clock):
    dev, auth = paired_with_passkey(svc)
    _, gate = open_gate(svc, dev)
    both = hdr(f"{dev}; {gate}")
    clock.t += 43201
    status, ch, _ = svc.handle("POST", "/phone/v1/challenge", both, {"purpose": "gate"})
    status, body, _ = svc.handle(
        "POST", "/phone/v1/gate", both, {"assertion": auth.assert_(ch["challenge"])}
    )
    assert status == 401 and body["gate"] == "cold"


def test_restart_makes_every_gate_cold(svc, tmp_path, clock):
    _, dev = pair(svc)
    _, gate = open_gate(svc, dev)
    fresh = pd.PhoneAuth(
        root=tmp_path / "phone", origin=ORIGIN, bearer="tok", clock=clock
    )
    p = fresh.authorize("GET", hdr(f"{dev}; {gate}"))
    assert p.kind == "device" and p.gate == "cold"


def test_bad_assertion_fails_and_counts(svc, clock):
    dev, auth = paired_with_passkey(svc)
    _, gate = open_gate(svc, dev)
    both = hdr(f"{dev}; {gate}")
    clock.t += 700
    _, ch, _ = svc.handle("POST", "/phone/v1/challenge", both, {"purpose": "gate"})
    status, body, _ = svc.handle(
        "POST",
        "/phone/v1/gate",
        both,
        {"assertion": auth.assert_(ch["challenge"], tamper=True)},
    )
    assert status == 401 and body["tries_left"] == 4


# --- lockout -------------------------------------------------------------------------


def fail(svc, dev):
    return svc.handle("POST", "/phone/v1/gate", hdr(dev), {"passphrase": "nope"})


def test_lockout_after_5_failures_then_doubles(svc, clock, tmp_path):
    _, dev = pair(svc)
    for left in (4, 3, 2, 1):
        status, body, _ = fail(svc, dev)
        assert status == 401 and body["tries_left"] == left and body["retry_after"] == 0
    status, body, _ = fail(svc, dev)
    assert body["tries_left"] == 0 and body["retry_after"] == 60
    # While locked out even the right passphrase is refused.
    status, body, _ = svc.handle("POST", "/phone/v1/gate", hdr(dev), {"passphrase": PW})
    assert status == 429 and 0 < body["retry_after"] <= 60
    clock.t += 61
    _, body, _ = fail(svc, dev)
    assert body["retry_after"] == 120
    for want in (240, 480, 960, 1920, 3600, 3600):
        clock.t += 4000
        _, body, _ = fail(svc, dev)
        assert body["retry_after"] == want
    assert "phone_gate fail" in (tmp_path / "actions.log").read_text()


def test_success_resets_lockout(svc, clock):
    _, dev = pair(svc)
    for _ in range(5):
        fail(svc, dev)
    clock.t += 61
    open_gate(svc, dev)
    _, body, _ = fail(svc, dev)
    assert body["tries_left"] == 4 and body["retry_after"] == 0


def test_lockout_is_per_device(svc):
    _, dev_a = pair(svc)
    _, dev_b = pair(svc)
    for _ in range(5):
        fail(svc, dev_a)
    open_gate(svc, dev_b)
