"""Concurrent HUD polling must share sensor reads and bound log I/O."""

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

sys.path.insert(0, "src")

import bridge


@pytest.mark.parametrize(
    ("probe_name", "cache_name", "output"),
    [
        ("_volume_status", "_VOL_CACHE", "Volume: 0.50"),
        ("_net_status", "_NET_CACHE", "ethernet:connected:Wired"),
        ("_phone_tailnet", "_TAILNET_CACHE", '{"Peer": {}}'),
    ],
)
def test_concurrent_pollers_share_one_sensor_refresh(
    monkeypatch, probe_name, cache_name, output
):
    monkeypatch.setattr(bridge, cache_name, {})
    ready = Barrier(12)
    calls = []

    def run(*args, **kwargs):
        calls.append(args[0])
        # Model a slow subprocess: all readers arrive during this probe.
        time.sleep(0.03)
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(bridge.subprocess, "run", run)

    def poll(_):
        ready.wait(timeout=3)
        return getattr(bridge, probe_name)()

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(poll, range(12)))

    assert all(result == results[0] for result in results)
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("probe_name", "cache_name", "output"),
    [
        ("_volume_status", "_VOL_CACHE", "Volume: 0.50"),
        ("_net_status", "_NET_CACHE", "ethernet:connected:Wired"),
    ],
)
def test_slow_sensor_ttl_starts_after_refresh(
    monkeypatch, probe_name, cache_name, output
):
    monkeypatch.setattr(bridge, cache_name, {})
    now, calls = [100.0], []

    def run(*args, **kwargs):
        calls.append(args[0])
        now[0] += 6.0
        return SimpleNamespace(returncode=0, stdout=output)

    probe = getattr(bridge, probe_name)
    first = probe(run=run, clock=lambda: now[0])
    assert probe(run=run, clock=lambda: now[0]) == first
    assert len(calls) == 1
    now[0] += 5.1
    assert probe(run=run, clock=lambda: now[0]) == first
    assert len(calls) == 2


def test_unexpected_tailnet_failure_is_cached(monkeypatch):
    monkeypatch.setattr(bridge, "_TAILNET_CACHE", {})
    calls = []

    def broken(*args, **kwargs):
        calls.append(args[0])
        raise RuntimeError("bad sensor response")

    monkeypatch.setattr(bridge.subprocess, "run", broken)
    assert bridge._phone_tailnet() is None
    assert bridge._phone_tailnet() is None
    assert len(calls) == 1


def test_volume_write_invalidates_an_inflight_read_without_waiting(monkeypatch):
    monkeypatch.setattr(bridge, "_VOL_CACHE", {})
    started, finish = Event(), Event()

    def old_volume(*args, **kwargs):
        started.set()
        assert finish.wait(timeout=3)
        return SimpleNamespace(returncode=0, stdout="Volume: 0.10")

    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(bridge._volume_status, run=old_volume)
        assert started.wait(timeout=3)
        invalidating = pool.submit(bridge._invalidate_sensor_cache, "volume")
        try:
            invalidating.result(timeout=1)
        finally:
            finish.set()
        assert reading.result()["pct"] == 10
    assert (
        bridge._volume_status(
            run=lambda *args, **kwargs: SimpleNamespace(
                returncode=0, stdout="Volume: 0.80"
            )
        )["pct"]
        == 80
    )


def test_phone_read_remains_consistent_during_telemetry_push(monkeypatch):
    timestamp_read, finish = Event(), Event()

    class PausedSample(dict):
        def __getitem__(self, key):
            value = super().__getitem__(key)
            if key == "ts":
                timestamp_read.set()
                assert finish.wait(timeout=3)
            return value

    monkeypatch.setattr(
        bridge,
        "_PHONE_TELEMETRY",
        PausedSample(ts=100.0, battery=20, charging=False),
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        reading = pool.submit(bridge._phone_stats, now=110.0)
        assert timestamp_read.wait(timeout=3)
        try:
            bridge.record_phone_telemetry({"battery": 80, "charging": True})
        finally:
            finish.set()
        assert reading.result() == {"age_s": 10, "battery": 20, "charging": False}
    assert bridge._phone_stats()["battery"] == 80


def test_caption_poll_reads_only_the_tail(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    path = tmp_path / "captions.log"
    path.write_text("1\tjarvis\t" + "x" * 500 + "\n")
    with path.open("a") as stream:
        stream.write(("2\tjarvis\t" + "x" * 500 + "\n") * 200)
        stream.write("3\tsir\tLatest caption ✓\n")
    original_open = Path.open
    reads = []

    class CountedFile:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, *args):
            result = self.stream.read(*args)
            reads.append(len(result))
            return result

    def counted_open(self, *args, **kwargs):
        stream = original_open(self, *args, **kwargs)
        return CountedFile(stream) if self == path else stream

    monkeypatch.setattr(Path, "open", counted_open)
    assert bridge.read_captions(1) == [
        {"ts": 3, "role": "sir", "text": "Latest caption ✓"}
    ]
    assert sum(reads) <= 4096


def test_corrupt_caption_bytes_do_not_break_polling(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "captions.log").write_bytes(b"1\tjarvis\tHi \xff\n2\tsir\tHello\n")
    assert bridge.read_captions(1) == [{"ts": 2, "role": "sir", "text": "Hello"}]


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_log_tail_preserves_complete_unicode_lines(tmp_path, newline):
    path = tmp_path / "actions.log"
    lines = ["語" * 5000, "Latest ✓", "Done"]
    path.write_bytes(newline.join(lines).encode())
    assert bridge._tail_log(path, 2) == lines[-2:]
