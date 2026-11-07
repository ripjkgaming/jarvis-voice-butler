"""The tailscale serve /api mount: the prefix is stripped before any routing."""
# ruff: noqa: F811  (pytest fixtures imported from _phone_http)

from _phone_http import TOKEN, Client, fake_laptop, phone_env, server  # noqa: F401

BEARER = "Bearer " + TOKEN


def test_api_prefix_same_as_bare(server):
    c = Client(server)
    a = c.request("GET", "/api/health", auth=BEARER, phone=False)
    b = c.request("GET", "/health", auth=BEARER, phone=False)
    assert a[0] == b[0] == 200
    assert a[1]["version"] == b[1]["version"]


def test_api_phone_state_same_as_bare(server):
    c = Client(server)
    a = c.request("GET", "/api/phone/v1/state", auth=BEARER)
    b = c.request("GET", "/phone/v1/state", auth=BEARER)
    assert a[0] == b[0] == 200
    assert set(a[1]) == set(b[1])
    assert a[1]["laptop"]["host"] == b[1]["laptop"]["host"]


def test_prefix_stripped_before_guest_gate(server, fake_laptop):
    fake_laptop.guest = True
    c = Client(server)
    status, body, _ = c.request(
        "POST", "/api/suit", {"open": True}, auth=BEARER, phone=False
    )
    assert status == 403 and "guest" in body["error"]
    status, body, _ = c.request(
        "POST", "/api/phone/v1/control", {"action": "lock"}, auth=BEARER
    )
    assert status == 200 and body["status"] == "verified"


def test_only_exact_api_prefix_is_stripped(server):
    c = Client(server)
    status, _, _ = c.request("GET", "/apiary", auth=BEARER, phone=False)
    assert status == 404
