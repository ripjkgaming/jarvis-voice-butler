"""HTTP harness for the phone routes: a real bridge on an ephemeral port."""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.request

import pytest
from _phone_authn import ORIGIN

sys.path.insert(0, "src")

import bridge
import phone_control
import phone_device

PW = "open sesame"
TOKEN = "tok-123"


class Client:
    def __init__(self, server):
        self.base = f"http://127.0.0.1:{server.server_address[1]}"
        self.cookies: dict[str, str] = {}

    def request(
        self, method, path, body=None, *, phone=True, origin=ORIGIN, auth=None, raw=None
    ):
        data = (
            raw
            if raw is not None
            else (json.dumps(body).encode() if body is not None else None)
        )
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if phone:
            req.add_header("X-Jarvis-Phone", "1")
        if origin and method == "POST":
            req.add_header("Origin", origin)
        if auth:
            req.add_header("Authorization", auth)
        if self.cookies:
            req.add_header(
                "Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status, headers, text = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            status, headers, text = exc.code, exc.headers, exc.read()
        for row in headers.get_all("Set-Cookie") or []:
            name, value = row.split(";", 1)[0].split("=", 1)
            if "Max-Age=0" in row:
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value
        try:
            payload = json.loads(text.decode() or "null")
        except ValueError:
            payload = None
        return status, payload, headers


@pytest.fixture
def phone_env(tmp_path, monkeypatch):
    salt = bytes(range(16))
    digest = hashlib.scrypt(PW.encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
    (tmp_path / "gate").write_text(f"{salt.hex()}:{digest.hex()}\n")
    monkeypatch.setenv("JARVIS_PHONE_GATE", str(tmp_path / "gate"))
    monkeypatch.setenv("JARVIS_PHONE_ORIGIN", ORIGIN)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(phone_device, "_SERVICE", None)
    return tmp_path


@pytest.fixture
def fake_laptop(monkeypatch):
    from test_phone_control import Fake

    fake = Fake()
    import watch_mode

    monkeypatch.setattr(watch_mode, "guest_required", lambda: fake.guest)
    monkeypatch.setattr(
        bridge,
        "_PHONE_CONTROL",
        phone_control.PhoneControl(fake.deps(), presence_enabled=False),
    )
    return fake


@pytest.fixture
def server(phone_env, fake_laptop):
    srv, _ = bridge._run_in_thread(token=TOKEN)
    yield srv
    srv.shutdown()
    srv.server_close()


def paired(server) -> tuple[Client, dict]:
    client = Client(server)
    code = phone_device.service(TOKEN).store.issue_code()
    status, body, _ = client.request(
        "POST", "/api/phone/v1/pair", {"code": code, "name": "Pixel"}
    )
    assert status == 200, body
    return client, body


def opened(server) -> Client:
    client, _ = paired(server)
    status, body, _ = client.request("POST", "/api/phone/v1/gate", {"passphrase": PW})
    assert status == 200, body
    return client
