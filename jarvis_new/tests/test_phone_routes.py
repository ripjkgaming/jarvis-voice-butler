"""Route table of PLAN.md §6.1 over real HTTP (bridge handler harness)."""
# ruff: noqa: F811  (pytest fixtures imported from _phone_http)

from _phone_authn import Authenticator
from _phone_http import (  # noqa: F401
    PW,
    TOKEN,
    Client,
    fake_laptop,
    opened,
    paired,
    phone_env,
    server,
)

import bridge


def test_unpaired_state_is_401_pair(server):
    status, body, _ = Client(server).request("GET", "/api/phone/v1/state")
    assert status == 401 and body == {
        "ok": False,
        "error": "unauthorized",
        "auth": "pair",
    }


def test_full_flow_pair_passkey_gate_state(server):
    client, body = paired(server)
    assert "jl_dev" in client.cookies
    auth = Authenticator()
    cred = auth.register(body["registration"]["challenge"])
    status, out, _ = client.request(
        "POST", "/api/phone/v1/passkey", {"credential": cred}
    )
    assert status == 200, out
    status, out, _ = client.request("GET", "/api/phone/v1/state")
    assert status == 401 and out["gate"] == "cold"
    status, out, headers = client.request(
        "POST", "/api/phone/v1/gate", {"passphrase": PW}
    )
    assert status == 200 and out["gate"] == "open"
    assert headers["Cache-Control"] == "no-store"
    status, state, _ = client.request("GET", "/api/phone/v1/state")
    assert status == 200 and state["gate"]["state"] == "open"
    assert state["laptop"]["guest"] is False


def test_wrong_passphrase_reports_tries(server):
    client, _ = paired(server)
    status, body, _ = client.request("POST", "/api/phone/v1/gate", {"passphrase": "x"})
    assert status == 401 and body["tries_left"] == 4


def test_device_cookie_needs_phone_header(server):
    client = opened(server)
    status, _body, _ = client.request("GET", "/api/phone/v1/state", phone=False)
    assert status == 401


def test_control_with_device_and_stepup(server, fake_laptop):
    client, body = paired(server)
    auth = Authenticator()
    cred = auth.register(body["registration"]["challenge"])
    client.request("POST", "/api/phone/v1/passkey", {"credential": cred})
    client.request("POST", "/api/phone/v1/gate", {"passphrase": PW})
    fake_laptop.guest = True
    status, out, _ = client.request("POST", "/api/phone/v1/control", {"action": "lock"})
    assert status == 200 and out["status"] == "verified"
    status, out, _ = client.request(
        "POST", "/api/phone/v1/control", {"action": "unlock"}
    )
    assert status == 403 and out == {"ok": False, "error": "stepup", "stepup": "unlock"}
    status, ch, _ = client.request(
        "POST", "/api/phone/v1/challenge", {"purpose": "unlock"}
    )
    assert status == 200
    status, out, _ = client.request(
        "POST",
        "/api/phone/v1/control",
        {"action": "unlock", "assertion": auth.assert_(ch["challenge"])},
    )
    assert status == 200 and out["status"] == "verified"
    # Replaying the same challenge fails.
    status, out, _ = client.request(
        "POST",
        "/api/phone/v1/control",
        {"action": "unlock", "assertion": auth.assert_(ch["challenge"])},
    )
    assert status == 403
    _, ch, _ = client.request(
        "POST", "/api/phone/v1/challenge", {"purpose": "guest_off"}
    )
    status, out, _ = client.request(
        "POST",
        "/api/phone/v1/control",
        {"action": "guest_off", "assertion": auth.assert_(ch["challenge"])},
    )
    assert out["status"] == "verified" and fake_laptop.guest is False


def test_existing_routes_accept_device_with_open_gate(server, monkeypatch):
    client, _ = paired(server)
    status, body, _ = client.request(
        "POST", "/api/phone/telemetry", {"battery": 55, "charging": False}
    )
    assert status == 401 and body["gate"] == "cold"
    status, caps, _ = client.request("GET", "/api/phone/v1/capabilities")
    assert status == 200
    assert caps["features"]["control"] is True
    assert caps["features"]["state"] is True
    assert caps["features"]["webauthn"] is True
    client.request("POST", "/api/phone/v1/gate", {"passphrase": PW})
    status, body, _ = client.request(
        "POST", "/api/phone/telemetry", {"battery": 55, "charging": False}
    )
    assert status == 200
    monkeypatch.setattr(
        bridge, "handle_route", lambda payload: (200, {"ok": True, "reply": "routed"})
    )
    status, body, _ = client.request(
        "POST",
        "/api/phone/v1/command",
        {"api_version": 1, "text": "hi", "instant": True},
    )
    assert status == 200 and body["via_route"] is True


def test_device_principal_does_not_leak_to_other_routes(server):
    client = opened(server)
    status, _, _ = client.request("GET", "/api/actions")
    assert status == 401
    status, _, _ = client.request("GET", "/api/status")
    assert status == 401


def test_body_caps(server):
    client, _ = paired(server)
    big = b'{"passphrase": "' + b"x" * 5000 + b'"}'
    status, body, _ = client.request("POST", "/api/phone/v1/gate", raw=big)
    assert status == 400
    ok_size = b'{"credential": {"pad": "' + b"x" * 12000 + b'"}}'
    status, body, _ = client.request("POST", "/api/phone/v1/passkey", raw=ok_size)
    assert status == 400 and "challenge" in body["error"]


def test_gate_close_then_cold(server):
    client = opened(server)
    status, _, _ = client.request("POST", "/api/phone/v1/gate/close", {})
    assert status == 200 and "jl_gate" not in client.cookies
    status, body, _ = client.request("GET", "/api/phone/v1/state")
    assert status == 401 and body["gate"] == "cold"


def test_bearer_owner_cli_still_works(server):
    c = Client(server)
    status, body, _ = c.request(
        "GET", "/phone/v1/state", auth="Bearer " + TOKEN, phone=False
    )
    assert status == 200 and body["gate"]["state"] == "open"


def test_state_get_only_control_post_only(server):
    client = opened(server)
    status, _, _ = client.request("POST", "/api/phone/v1/state", {})
    assert status == 405
    status, _, _ = client.request("GET", "/api/phone/v1/control")
    assert status == 405
