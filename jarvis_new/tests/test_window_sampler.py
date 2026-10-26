import os
import threading

import pytest

import build_monitor as bm
import jobs
import window_monitor as wm
import window_sampler as ws

REAL_START = ws.ensure_started
WID = "{11111111-1111-1111-1111-111111111111}"


@pytest.fixture
def setup(tmp_path):
    identity = bm.Identity("boot", os.getuid(), 42, 123, "S")
    mon = bm.Monitor(
        jobs.Registry(tmp_path / "registry"), proc_reader=lambda pid: identity
    )

    class Windows:
        current_window = wm.Window(WID, "synthetic", "code", 42, ":1.7")
        failed = False

        def find(self, name):
            return [self.current_window]

        def current(self, wid):
            if self.failed:
                raise wm.WindowError("unavailable")
            return self.current_window

    windows = Windows()
    resolver = wm.Resolver(mon, windows=windows)
    row = resolver.select("synthetic", "request", scope="window_activity")["job"]
    return mon, windows, resolver, row["id"]


def test_automatic_sampler_observes_window_close_without_status_tool(setup):
    mon, windows, resolver, job_id = setup
    sampler = ws.Sampler(mon, resolver=resolver, allowed=lambda: True)
    assert sampler.tick()
    windows.current_window = None
    sampler.tick()
    row = mon.registry.get(job_id)
    assert row["payload"]["status"] == "window_closed"
    assert row["outcome"] == "ended_unknown"
    assert sampler.tick() is False


def test_transport_unknown_is_not_exit_and_guest_prevents_reads(setup):
    mon, windows, resolver, job_id = setup
    windows.failed = True
    sampler = ws.Sampler(mon, resolver=resolver, allowed=lambda: True)
    assert sampler.tick()
    assert mon.registry.get(job_id)["payload"]["status"] == "observation_unknown"
    before = mon.registry.get(job_id)["revision"]
    sampler.allowed = lambda: False
    with pytest.raises(wm.WindowError):
        sampler.tick()
    assert mon.registry.get(job_id)["revision"] == before


def test_detach_settles_only_observation(setup):
    mon, _, resolver, job_id = setup
    mon.registry.request_cancel(job_id)
    ws.Sampler(mon, resolver=resolver, allowed=lambda: True).tick()
    assert mon.registry.get(job_id)["outcome"] == "monitoring_stopped"


@pytest.mark.parametrize("interval", [True, 0, 1, 61, float("nan"), float("inf")])
def test_sampler_interval_bounded(setup, interval):
    with pytest.raises(ValueError):
        ws.Sampler(setup[0], interval=interval)


def test_real_thread_election_sampling_and_owned_drain(setup, monkeypatch):
    mon, windows, resolver, job_id = setup
    entered = threading.Event()
    release = threading.Event()
    original = ws.Sampler

    class Probe(original):
        def __init__(self, mon):
            super().__init__(mon, resolver=resolver, allowed=lambda: True)

        def run(self):
            self.tick()
            entered.set()
            release.wait(1)
            self.tick()

    monkeypatch.setattr(ws, "Sampler", Probe)
    try:
        assert REAL_START(mon)
        assert entered.wait(1)
        pair = ws._workers[str(mon.registry.root)]
        assert REAL_START(mon) and ws._workers[str(mon.registry.root)] is pair
        windows.current_window = None
        release.set()
        pair[0].join(2)
        assert not pair[0].is_alive()
        assert mon.registry.get(job_id)["outcome"] == "ended_unknown"
        assert ws.stop_owned(mon)
    finally:
        release.set()
        ws.stop_owned(mon)


def test_detach_works_with_unavailable_metadata(setup):
    mon, windows, resolver, job_id = setup
    mon.registry.request_cancel(job_id)
    windows.failed = True
    ws.Sampler(mon, resolver=resolver, allowed=lambda: True).tick()
    assert mon.registry.get(job_id)["outcome"] == "monitoring_stopped"


def test_retiring_observer_is_drained_before_replacement(setup, monkeypatch):
    mon, windows, resolver, old_id = setup
    windows.current_window = None
    entered = threading.Event()
    original = ws.Sampler
    created = []

    class Probe(original):
        def __init__(self, mon):
            super().__init__(mon, resolver=resolver, allowed=lambda: True)
            self.first = not created
            created.append(self)

        def tick(self):
            if self.first:
                self.resolver.status(old_id)
                alive = super().tick()
                assert not alive
                entered.set()
                self.stop.wait(0.15)
                return alive
            return super().tick()

    monkeypatch.setattr(ws, "Sampler", Probe)
    try:
        assert REAL_START(mon)
        assert entered.wait(1)
        windows.current_window = wm.Window(WID, "synthetic", "code", 42, ":1.7")
        new = resolver.select("synthetic", "fresh", scope="window_activity")["job"]
        assert REAL_START(mon)
        assert len(created) == 2
        assert ws._workers[str(mon.registry.root)][0].is_alive()
        assert mon.registry.get(new["id"])["lifecycle"] == "running"
    finally:
        ws.stop_owned(mon)
