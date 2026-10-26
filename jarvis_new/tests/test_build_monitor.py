import os

import build_monitor as bm
import jobs
import pytest


class FakeProc:
    def __init__(self):
        self.value = bm.Identity("boot", os.getuid(), 42, 123, "S")
        self.calls = []

    def __call__(self, pid):
        self.calls.append(pid)
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


@pytest.fixture
def setup(tmp_path):
    proc = FakeProc()
    mon = bm.Monitor(
        jobs.Registry(tmp_path / "jobs"), proc_reader=proc, clock=lambda: 100
    )
    return mon, proc


def test_register_restart_and_no_discovery(setup):
    mon, proc = setup
    row = mon.register("request-1", 42, "build")
    assert mon.register("request-1", 42, "build")["id"] == row["id"]
    with pytest.raises(bm.MonitorError):
        mon.register("request-1", 42, "changed")
    with pytest.raises(bm.MonitorError):
        mon.register("request-2", 42, "other")
    another = bm.Monitor(mon.registry, proc_reader=proc, clock=lambda: 105)
    another.poll_once()
    assert another.get(row["id"])["status"] == "running"
    assert set(proc.calls) == {42}


@pytest.mark.parametrize(
    "identity,reason",
    [
        (None, "process_absent"),
        (bm.Identity("other", os.getuid(), 42, 123, "S"), "identity_changed"),
        (bm.Identity("boot", os.getuid(), 42, 999, "S"), "identity_changed"),
        (bm.Identity("boot", os.getuid(), 42, 123, "Z"), "process_exited"),
    ],
)
def test_truthful_disappearance(setup, identity, reason):
    mon, proc = setup
    row = mon.register("r", 42, "build")
    proc.value = identity
    mon.poll_once()
    got = mon.get(row["id"])
    assert got["status"] == "ended_unknown" and got["reason"] == reason
    assert got["outcome"] == "ended_unknown"


def test_permission_denied_is_unknown_then_recovers(setup):
    mon, proc = setup
    row = mon.register("r", 42, "build")
    saved = proc.value
    proc.value = PermissionError("private")
    mon.poll_once()
    assert mon.get(row["id"])["status"] == "observation_unknown"
    proc.value = saved
    mon.poll_once()
    assert mon.get(row["id"])["status"] == "running"


def test_request_and_detach_race(setup):
    mon, _proc = setup
    row = mon.register("r", 42, "build")
    mon.registry.request_cancel(row["id"])
    mon.poll_once()
    got = mon.get(row["id"])
    assert got["status"] == "monitoring_stopped"
    assert got["outcome"] == "monitoring_stopped"
    assert mon.stop_monitoring(row["id"])["outcome"] == "monitoring_stopped"


def test_registration_rejects_invalid_identity(setup):
    mon, proc = setup
    for pid in (0, 1, True, "42", -1):
        with pytest.raises(bm.MonitorError):
            mon.register("r", pid, "build")
    proc.value = bm.Identity("boot", os.getuid() + 1, 42, 123, "S")
    with pytest.raises(bm.MonitorError):
        mon.register("r", 42, "build")


def test_bounded_tail_and_controls(setup, tmp_path):
    mon, _ = setup
    root = tmp_path / "workspace"
    root.mkdir()
    log = root / "build.log"
    log.write_bytes(
        b"x" * 30000 + b"\n\x1b]0;evil\x07\x1b[31mhello\x1b[0m\ninvalid\xff\n"
    )
    row = mon.register("r", 42, "build", str(root), "build.log")
    got = mon.tail(row["id"], 2)
    assert got["available"] and len(got["text"]) <= 8000
    assert "\x1b" not in got["text"] and "evil" not in got["text"]
    assert got["bytes_read"] <= 16384 and got["truncated"]
    assert "text" not in mon.get(row["id"]) and "log" not in mon.get(row["id"])


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", ".env", "hidden/.auth"])
def test_bad_log_paths(setup, tmp_path, path):
    mon, _ = setup
    with pytest.raises(bm.MonitorError):
        mon.register("r", 42, "build", str(tmp_path), path)


def test_log_replacement_link_and_fifo(setup, tmp_path):
    mon, _ = setup
    root = tmp_path / "workspace"
    root.mkdir()
    log = root / "build.log"
    log.write_text("first")
    row = mon.register("r", 42, "build", str(root), "build.log")
    log.rename(root / "old.log")
    log.write_text("new")
    assert not mon.tail(row["id"])["available"]
    mon.stop_monitoring(row["id"])
    log.unlink()
    log.symlink_to(root / "old.log")
    with pytest.raises(bm.MonitorError):
        mon.register("r2", 42, "build", str(root), "build.log")
    log.unlink()
    os.mkfifo(log)
    with pytest.raises(bm.MonitorError):
        mon.register("r3", 42, "build", str(root), "build.log")


def test_stat_parser():
    fields = ["S"] + ["0"] * 18 + ["123"]
    got = bm.parse_stat("42 (name ) with spaces) " + " ".join(fields))
    assert got == (42, "S", 123)
    with pytest.raises(ValueError):
        bm.parse_stat("not stat")


def test_cancel_arrives_during_observation(setup):
    mon, proc = setup
    row = mon.register("r", 42, "build")

    def racing(pid):
        mon.registry.request_cancel(row["id"])
        return proc.value

    mon.proc_reader = racing
    mon.poll_once()
    assert mon.registry.get(row["id"])["lifecycle"] == "cancellation_requested"
    mon.poll_once()
    assert mon.get(row["id"])["outcome"] == "monitoring_stopped"


def test_identity_changes_during_registration(setup):
    mon, proc = setup
    values = iter([proc.value, bm.Identity("boot", os.getuid(), 42, 999, "S")])
    mon.proc_reader = lambda pid: next(values)
    with pytest.raises(bm.MonitorError):
        mon.register("r", 42, "build")
    assert mon.latest() is None


def test_symlink_root_parent_and_hardlink_log(setup, tmp_path):
    mon, _ = setup
    real = tmp_path / "real"
    real.mkdir()
    (real / "build.log").write_text("synthetic")
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    with pytest.raises(bm.MonitorError):
        mon.register("r", 42, "build", str(tmp_path / "link"), "build.log")
    os.link(real / "build.log", real / "other.log")
    with pytest.raises(bm.MonitorError):
        mon.register("r2", 42, "build", str(real), "build.log")


def test_log_truncation_during_read_unavailable(setup, tmp_path, monkeypatch):
    mon, _ = setup
    log = tmp_path / "build.log"
    log.write_bytes(b"x" * 20000)
    row = mon.register("r", 42, "build", str(tmp_path), "build.log")
    real = os.pread

    def truncate(fd, size, offset):
        raw = real(fd, size, offset)
        log.write_bytes(b"")
        return raw

    monkeypatch.setattr(os, "pread", truncate)
    assert not mon.tail(row["id"])["available"]


def test_thread_election_no_live_proc(setup, monkeypatch):
    mon, proc = setup
    monkeypatch.setattr(bm, "service", lambda: mon)
    monkeypatch.setattr(bm, "enabled", lambda: True)
    first = bm.start_thread(interval=0.01)
    assert bm.start_thread(interval=0.01) is None
    thread, stop = first
    stop.set()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert proc.calls == []


def test_corrupt_monitor_payload_fails_closed(setup):
    mon, _ = setup
    row = mon.register("r", 42, "build")
    current = mon.registry.get(row["id"])
    mon.registry.update(row["id"], bm.OWNER, {}, current["revision"])
    with pytest.raises(bm.MonitorError):
        mon.get(row["id"])
    with pytest.raises(bm.MonitorError):
        mon.poll_once()
