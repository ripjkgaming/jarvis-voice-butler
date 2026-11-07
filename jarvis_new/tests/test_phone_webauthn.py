"""phone_webauthn: CBOR, none-attestation registration, ES256 assertions."""

import sys

import pytest
from _phone_authn import ORIGIN, RP_ID, Authenticator, b64u, cbor

sys.path.insert(0, "src")

import phone_webauthn as wa


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _registered(auth: Authenticator, challenge: str = "c" * 43) -> dict:
    cred = wa.parse_registration(
        auth.register(challenge), challenge=challenge, origin=ORIGIN, rp_id=RP_ID
    )
    return {**cred, "created": 0}


# --- CBOR ------------------------------------------------------------------


def test_cbor_roundtrip_types():
    obj = {"a": 1, -2: b"\x01\x02", "l": [0, 23, 24, 255, 256, 65536, -1, -25]}
    obj["t"] = True
    assert wa.cbor_loads(cbor(obj)) == obj


def test_cbor_rejects_indefinite_length():
    with pytest.raises(wa.CBORError):
        wa.cbor_loads(b"\x5f\x41\x00\xff")  # indefinite byte string
    with pytest.raises(wa.CBORError):
        wa.cbor_loads(b"\xbf\x01\x02\xff")  # indefinite map


def test_cbor_rejects_trailing_and_truncated():
    with pytest.raises(wa.CBORError):
        wa.cbor_loads(cbor(1) + b"\x00")
    with pytest.raises(wa.CBORError):
        wa.cbor_loads(b"\x59\x00")  # truncated length


def test_cbor_rejects_tags_and_floats():
    with pytest.raises(wa.CBORError):
        wa.cbor_loads(b"\xc1\x01")  # tag (major 6)
    with pytest.raises(wa.CBORError):
        wa.cbor_loads(b"\xfb" + b"\x00" * 8)  # float64


def test_cbor_depth_is_bounded():
    with pytest.raises(wa.CBORError):
        wa.cbor_loads(b"\x81" * 64 + b"\x00")


# --- registration ----------------------------------------------------------


def test_registration_none_attestation_parsed():
    auth = Authenticator()
    cred = _registered(auth)
    assert cred["id"] == b64u(auth.cred_id)
    nums = auth.key.public_key().public_numbers()
    assert cred["x"] == b64u(nums.x.to_bytes(32, "big"))
    assert cred["y"] == b64u(nums.y.to_bytes(32, "big"))
    assert cred["sign_count"] == 0


@pytest.mark.parametrize(
    "kwargs, reg_kwargs",
    [
        ({"challenge": "other"}, {}),
        ({}, {"origin": "https://evil.example"}),
        ({}, {"fmt": "packed"}),
        ({}, {"flags": 0x41}),  # UV missing
        ({}, {"flags": 0x05}),  # AT missing
    ],
)
def test_registration_rejections(kwargs, reg_kwargs):
    auth = Authenticator()
    challenge = "c" * 43
    resp = auth.register(challenge, **reg_kwargs)
    with pytest.raises(wa.WebAuthnError):
        wa.parse_registration(
            resp,
            challenge=kwargs.get("challenge", challenge),
            origin=ORIGIN,
            rp_id=RP_ID,
        )


def test_registration_rejects_wrong_rp():
    auth = Authenticator(rp_id="evil.example")
    challenge = "c" * 43
    with pytest.raises(wa.WebAuthnError):
        wa.parse_registration(
            auth.register(challenge), challenge=challenge, origin=ORIGIN, rp_id=RP_ID
        )


def test_registration_rejects_non_es256():
    auth = Authenticator(alg=-257)
    challenge = "c" * 43
    with pytest.raises(wa.WebAuthnError):
        wa.parse_registration(
            auth.register(challenge), challenge=challenge, origin=ORIGIN, rp_id=RP_ID
        )


def test_registration_options_shape():
    opts = wa.registration_options(
        rp_id=RP_ID, device_id="jl-1", name="Pixel", challenge="abc", exclude=["x"]
    )
    assert opts["rp"] == {"id": RP_ID, "name": "LOCKE//LINK"}
    assert opts["pubKeyCredParams"] == [{"type": "public-key", "alg": -7}]
    assert opts["attestation"] == "none"
    sel = opts["authenticatorSelection"]
    assert sel["authenticatorAttachment"] == "platform"
    assert sel["residentKey"] == "preferred"
    assert sel["userVerification"] == "required"
    assert opts["excludeCredentials"] == [{"type": "public-key", "id": "x"}]
    assert opts["challenge"] == "abc"


# --- assertions --------------------------------------------------------------


def _verify(auth, cred, challenge="g" * 43, **kw):
    return wa.verify_assertion(
        auth.assert_(challenge, **kw),
        credentials=[cred],
        challenge=challenge,
        origin=ORIGIN,
        rp_id=RP_ID,
    )


def test_assertion_verifies_and_returns_count():
    auth = Authenticator()
    cred = _registered(auth)
    cred_id, count = _verify(auth, cred)
    assert cred_id == cred["id"]
    assert count == 1


def test_client_challenge_extracted():
    auth = Authenticator()
    assert wa.client_challenge(auth.assert_("zz")) == "zz"
    assert wa.client_challenge({"response": {}}) is None


@pytest.mark.parametrize(
    "kw",
    [
        {"origin": "https://evil.example"},
        {"rp_id": "evil.example"},
        {"flags": 0x01},  # UV missing
        {"flags": 0x04},  # UP missing
        {"kind": "webauthn.create"},
        {"tamper": True},
    ],
)
def test_assertion_rejections(kw):
    auth = Authenticator()
    cred = _registered(auth)
    with pytest.raises(wa.WebAuthnError):
        _verify(auth, cred, **kw)


def test_assertion_wrong_challenge_rejected():
    auth = Authenticator()
    cred = _registered(auth)
    with pytest.raises(wa.WebAuthnError):
        wa.verify_assertion(
            auth.assert_("aaa"),
            credentials=[cred],
            challenge="bbb",
            origin=ORIGIN,
            rp_id=RP_ID,
        )


def test_assertion_sign_count_regression_rejected():
    auth = Authenticator()
    cred = _registered(auth)
    cred["sign_count"] = 5
    with pytest.raises(wa.WebAuthnError):
        _verify(auth, cred, count=5)
    _, count = _verify(auth, cred, count=6)
    assert count == 6


def test_assertion_zero_counts_allowed():
    auth = Authenticator()
    cred = _registered(auth)
    _, count = _verify(auth, cred, count=0)
    assert count == 0


def test_assertion_unknown_credential_rejected():
    auth = Authenticator()
    other = Authenticator()
    cred = _registered(other)
    with pytest.raises(wa.WebAuthnError):
        _verify(auth, cred)


def test_assertion_garbage_rejected():
    with pytest.raises(wa.WebAuthnError):
        wa.verify_assertion(
            {"id": "x", "response": {"clientDataJSON": "!!"}},
            credentials=[],
            challenge="a",
            origin=ORIGIN,
            rp_id=RP_ID,
        )


# --- challenges ---------------------------------------------------------------


def test_challenge_single_use_and_purpose_bound():
    clock = Clock()
    store = wa.ChallengeStore(clock=clock)
    c = store.issue("jl-1", "unlock")
    assert len(wa.b64u_decode(c)) == 32
    assert not store.consume("jl-1", "restart", c)
    assert not store.consume("jl-2", "unlock", c)
    assert store.consume("jl-1", "unlock", c)
    assert not store.consume("jl-1", "unlock", c)  # reused


def test_challenge_expires_after_60s():
    clock = Clock()
    store = wa.ChallengeStore(clock=clock)
    c = store.issue("jl-1", "gate")
    clock.t += 60.5
    assert not store.consume("jl-1", "gate", c)


def test_challenge_capacity_bounded():
    store = wa.ChallengeStore(clock=Clock())
    for _ in range(200):
        store.issue("jl-1", "gate")
    assert len(store) <= store.MAX
