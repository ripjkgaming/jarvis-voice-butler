"""Wake-event broadcast: subscribers hear wake / call_ended, never block the loop."""

import json
import socket
import time
from pathlib import Path

import pytest

from wake_events import WakeEventBroadcaster, encode_event, wake_events_path


def _connect(path: Path) -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2)
    s.connect(str(path))
    return s


def _wait_subs(bus: WakeEventBroadcaster, n: int) -> None:
    for _ in range(100):
        if bus.subscriber_count() >= n:
            return
        time.sleep(0.02)
    raise AssertionError("subscriber never registered")


def test_path_follows_jarvis_home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert wake_events_path() == tmp_path / "wake-events.sock"


def test_encode_event_is_newline_json():
    raw = encode_event("wake", 12.5)
    assert raw.endswith(b"\n")
    assert json.loads(raw) == {"event": "wake", "ts": 12.5}


def test_multiple_subscribers_receive_events(tmp_path):
    sock = tmp_path / "ev.sock"
    bus = WakeEventBroadcaster(sock)
    assert bus.start()
    try:
        a, b = _connect(sock), _connect(sock)
        _wait_subs(bus, 2)
        bus.publish("wake")
        bus.publish("call_ended")
        for s in (a, b):
            data = b""
            while data.count(b"\n") < 2:
                data += s.recv(4096)
            events = [json.loads(line)["event"] for line in data.splitlines()]
            assert events == ["wake", "call_ended"]
    finally:
        bus.stop()


def test_publish_without_subscribers_never_blocks_or_raises(tmp_path):
    bus = WakeEventBroadcaster(tmp_path / "ev.sock")
    assert bus.start()
    try:
        t0 = time.monotonic()
        for _ in range(500):
            bus.publish("wake")
        assert time.monotonic() - t0 < 0.5
    finally:
        bus.stop()


def test_publish_before_start_is_silent(tmp_path):
    WakeEventBroadcaster(tmp_path / "x.sock").publish("wake")


def test_dead_subscriber_is_dropped(tmp_path):
    sock = tmp_path / "ev.sock"
    bus = WakeEventBroadcaster(sock)
    assert bus.start()
    try:
        a = _connect(sock)
        _wait_subs(bus, 1)
        a.close()
        for _ in range(5):
            bus.publish("wake")
            time.sleep(0.1)
        assert bus.subscriber_count() == 0
    finally:
        bus.stop()


def test_second_broadcaster_yields_to_live_one(tmp_path):
    sock = tmp_path / "ev.sock"
    first = WakeEventBroadcaster(sock)
    assert first.start()
    try:
        assert WakeEventBroadcaster(sock).start() is False
    finally:
        first.stop()


def test_wake_client_publishes_call_ended_on_hangup(monkeypatch):
    import wake_client

    seen = []

    class Bus:
        def publish(self, e):
            seen.append(e)

    c = wake_client.WakeClient.__new__(wake_client.WakeClient)
    import threading

    c._mic_lock = threading.Lock()
    c._in_call = False
    c._events = Bus()
    c._set_in_call(True)
    assert seen == []
    c._set_in_call(False)
    assert seen == ["call_ended"]
    c._set_in_call(False)  # no second event
    assert seen == ["call_ended"]


@pytest.mark.parametrize("missing_bus", [True])
def test_wake_client_without_bus_is_unchanged(missing_bus):
    import threading

    import wake_client

    c = wake_client.WakeClient.__new__(wake_client.WakeClient)
    c._mic_lock = threading.Lock()
    c._in_call = True
    c._set_in_call(False)  # must not raise with no _events attribute
    assert c._in_call is False
