"""GET /phone/v1/state aggregator: exact shape, never blocks, TTLs, presence env."""

import sys
import threading
import time

import pytest
from test_phone_control import Fake

sys.path.insert(0, "src")

import phone_control as pc

GATE = {"state": "open", "expires_in_s": 40000.0, "soft_in_s": 600.0}


def wait_until(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def fake():
    return Fake()


def test_shape_matches_plan(fake):
    ctl = pc.PhoneControl(fake.deps(), presence_enabled=True)
    ctl.state(GATE)  # first call starts the probes
    assert wait_until(lambda: ctl.state(GATE)["laptop"]["presence"] == "present")
    out = ctl.state(GATE)
    assert set(out) == {
        "ok",
        "api_version",
        "ts",
        "laptop",
        "voice",
        "gate",
        "caps",
        "ages",
    }
    assert out["ok"] is True and out["api_version"] == 1
    assert isinstance(out["ts"], float)
    assert out["laptop"] == {
        "host": "ripjk",
        "guest": False,
        "locked": False,
        "screens": "on",
        "mic_muted": False,
        "presence": "present",
        "power": {"percent": 87, "charging": True},
    }
    assert out["voice"] == {
        "call_live": False,
        "voice_owner": None,
        "agent_state": None,
        "waking": False,
    }
    assert out["gate"] == GATE
    assert out["caps"] == {"remote": False, "presence": True, "webauthn": True}
    assert set(out["ages"]) == {"locked", "screens", "mic_muted", "presence"}
    assert all(isinstance(v, float) for v in out["ages"].values())


def test_slow_probe_never_blocks(fake):
    gate = threading.Event()

    def slow_run_tool(tool, args):
        gate.wait(5)
        return fake.run_phone_tool(tool, args)

    def slow_run(argv, **kw):
        if argv[0] == "gdbus":
            gate.wait(5)
        return fake.run(argv, **kw)

    ctl = pc.PhoneControl(
        fake.deps(
            run=slow_run, run_phone_tool=slow_run_tool, wake_rpc=lambda p: gate.wait(5)
        ),
        presence_enabled=True,
    )
    try:
        t0 = time.perf_counter()
        out = ctl.state(GATE)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.05
        assert out["laptop"]["locked"] is None
        assert out["laptop"]["screens"] is None
        assert out["laptop"]["mic_muted"] is None
        assert out["ages"]["locked"] is None
    finally:
        gate.set()


def test_state_server_time_p95_under_20ms(fake):
    ctl = pc.PhoneControl(fake.deps(), presence_enabled=False)
    ctl.state(GATE)
    wait_until(lambda: ctl.state(GATE)["laptop"]["screens"] == "on")
    times = []
    for _ in range(200):
        t0 = time.perf_counter()
        ctl.state(GATE)
        times.append(time.perf_counter() - t0)
    times.sort()
    assert times[int(len(times) * 0.95)] < 0.020


def test_ttls_refresh_in_background(fake):
    now = [100.0]
    ctl = pc.PhoneControl(fake.deps(clock=lambda: now[0]), presence_enabled=False)
    ctl.state(GATE)
    assert wait_until(lambda: ctl.state(GATE)["laptop"]["locked"] is False)
    fake.locked = True
    now[0] += 1.0  # inside 2 s TTL: cached value
    assert ctl.state(GATE)["laptop"]["locked"] is False
    now[0] += 1.5  # stale: kicks a refresh, still returns the cached value
    ctl.state(GATE)
    assert wait_until(lambda: ctl.state(GATE)["laptop"]["locked"] is True)
    screens_probes = sum(1 for t in fake.tools if t[0] == "screens_state")
    now[0] += 5  # screens TTL is 10 s
    ctl.state(GATE)
    time.sleep(0.05)
    assert sum(1 for t in fake.tools if t[0] == "screens_state") == screens_probes


def test_presence_disabled(fake, monkeypatch):
    monkeypatch.setenv("JARVIS_PHONE_PRESENCE", "0")
    calls = []
    ctl = pc.PhoneControl(
        fake.deps(presence=lambda: calls.append(1) or {"status": "present"})
    )
    out = ctl.state(GATE)
    time.sleep(0.05)
    assert out["laptop"]["presence"] == "unknown"
    assert out["caps"]["presence"] is False
    assert calls == []


def test_power_unknown_is_null(fake):
    ctl = pc.PhoneControl(fake.deps(laptop_power=lambda: None), presence_enabled=False)
    assert ctl.state(GATE)["laptop"]["power"] is None


def test_power_discharging(fake):
    ctl = pc.PhoneControl(
        fake.deps(
            laptop_power=lambda: {"battery": 41, "status": "Discharging", "ac": False}
        ),
        presence_enabled=False,
    )
    assert ctl.state(GATE)["laptop"]["power"] == {"percent": 41, "charging": False}


def test_probe_exception_is_null(fake):
    def boom(*a, **k):
        raise RuntimeError("x")

    ctl = pc.PhoneControl(fake.deps(wake_rpc=boom), presence_enabled=False)
    ctl.state(GATE)
    time.sleep(0.05)
    assert ctl.state(GATE)["laptop"]["mic_muted"] is None


def test_screens_off_when_every_output_disabled(fake):
    fake.screens_on = False
    ctl = pc.PhoneControl(fake.deps(), presence_enabled=False)
    ctl.state(GATE)
    assert wait_until(lambda: ctl.state(GATE)["laptop"]["screens"] == "off")
