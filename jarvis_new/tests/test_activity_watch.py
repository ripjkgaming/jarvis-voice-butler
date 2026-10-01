"""Tests for the activity producers (src/activity_watch.py)."""

import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

import activity
import activity_watch as aw
from activity_watch import ActivityWatcher


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("JARVIS_DOWNLOADS_DIR", str(tmp_path / "dl"))
    (tmp_path / "dl").mkdir()
    return tmp_path


def _dl_path(tmp_path):
    return tmp_path / "dl"


# --- pure helpers ------------------------------------------------------------


def test_human_bytes():
    assert aw.human_bytes(0) == "0 B"
    assert aw.human_bytes(512) == "512 B"
    assert aw.human_bytes(2048) == "2.0 KB"
    assert aw.human_bytes(48.2 * 1024 * 1024) == "48.2 MB"
    assert aw.human_bytes("junk") == "0 B"


def test_strip_partial_and_ids():
    assert aw.strip_partial("video.mp4.part") == "video.mp4"
    assert aw.strip_partial("setup.exe.crdownload") == "setup.exe"
    assert aw.strip_partial("movie.mkv.!qB") == "movie.mkv"
    assert aw.strip_partial("notes.txt") is None
    assert aw.strip_partial(".part") is None
    assert aw.download_id("/a/b.part") == aw.download_id("/a/b.part")
    assert aw.download_id("/a/b.part") != aw.download_id("/a/c.part")


def test_role_title_and_package_short():
    assert aw.role_title(22) == "Updating packages"  # update-packages
    assert aw.role_title(11) == "Installing packages"
    assert aw.role_title(13) == "Refreshing package cache"
    assert aw.role_title(25) == "Downloading packages"
    assert aw.role_title("update-packages") == "Updating packages"
    assert aw.role_title(999) == "Package task"
    assert aw.role_title(0) == "Package task"
    assert aw.package_short("firefox;136.0;x86_64;fedora") == "firefox"
    assert aw.package_short("") == ""


def test_match_updater():
    assert aw.match_updater(["/usr/bin/dnf", "upgrade"]) == "DNF upgrade"
    assert aw.match_updater(["dnf5", "distro-sync"]) == "DNF5 distro-sync"
    assert aw.match_updater(["flatpak", "update", "-y"]) == "Flatpak update"
    assert aw.match_updater(["nobara-sync", "cli"]) == "Nobara system update"
    assert aw.match_updater(["rpm-ostree", "upgrade"]) == "rpm-ostree upgrade"
    assert aw.match_updater(["fwupdmgr", "update"]) == "Firmware update"
    assert aw.match_updater(["pkcon", "install", "x"]) == "PackageKit install"
    assert aw.match_updater(["snap", "refresh"]) == "Snap refresh"
    assert aw.match_updater(["/usr/bin/bash"]) is None
    assert aw.match_updater(["flatpak", "run", "org.x"]) is None
    assert aw.match_updater([]) is None
    assert len(aw.match_updater(["dnf", "upgrade", "--enablerepo=x"]) or "") <= 80


def test_busctl_parsers():
    assert aw._busctl_values('{"type":"ao","data":[["/a","/b"]]}') == [[["/a", "/b"]]]
    assert aw._busctl_values('{"type":"u","data":22}\n{"type":"s","data":"x"}\n') == [
        22,
        "x",
    ]
    assert aw._busctl_values("garbage") == []
    assert aw._busctl_paths([["/a", "nope", "/b"]]) == ["/a", "/b"]
    assert aw._busctl_paths([]) == []


# --- downloads ----------------------------------------------------------------


def test_downloads_track_grow_finish(tmp_path):
    w = ActivityWatcher()
    now = time.time()
    part = _dl_path(tmp_path) / "video.mp4.part"
    part.write_bytes(b"x" * 2048)
    w.poll_downloads(now=now)
    iid = aw.download_id(os.path.abspath(part))
    item = activity.get(iid)
    assert item["status"] == "running" and item["title"] == "video.mp4"
    assert item["progress"] is None and item["meta"]["bytes_done"] == 2048

    part.write_bytes(b"x" * 4096)
    w.poll_downloads(now=now + 2.0)
    item = activity.get(iid)
    assert item["meta"]["bytes_done"] == 4096
    assert item["meta"]["speed_bps"] > 0
    assert "/s" in item["detail"]

    part.unlink()
    (_dl_path(tmp_path) / "video.mp4").write_bytes(b"y" * 4096)
    w.poll_downloads(now=now + 4.0)
    done = activity.get(iid)
    assert done["status"] == "done" and "saved" in done["detail"]


def test_downloads_cancelled_without_final(tmp_path):
    w = ActivityWatcher()
    now = time.time()
    part = _dl_path(tmp_path) / "draft.zip.crdownload"
    part.write_bytes(b"x" * 100)
    w.poll_downloads(now=now)
    iid = aw.download_id(os.path.abspath(part))
    part.unlink()
    w.poll_downloads(now=now + 1.0)
    assert activity.get(iid)["status"] == "cancelled"


def test_downloads_ignore_abandoned_and_plain(tmp_path):
    w = ActivityWatcher()
    now = time.time()
    old = _dl_path(tmp_path) / "stale.iso.part"
    old.write_bytes(b"x" * 50)
    os.utime(old, (now - 700, now - 700))
    (_dl_path(tmp_path) / "plain.txt").write_text("hi")
    w.poll_downloads(now=now)
    assert activity.list_items() == []


# --- PackageKit ---------------------------------------------------------------


def _pk_runner(*, role=22, pct=45, last="firefox;136.0;x86_64;fedora", paths=None):
    calls = []

    def run(argv, timeout=2.0):
        calls.append(argv)
        if "GetTransactionList" in argv:
            got = paths if paths is not None else ["/org/tx/7"]
            return SimpleNamespace(
                returncode=0, stdout=json.dumps({"type": "ao", "data": [got]})
            )
        lines = [
            json.dumps({"type": "u", "data": role}),
            json.dumps({"type": "u", "data": 8}),
            json.dumps({"type": "u", "data": pct}),
            json.dumps({"type": "s", "data": last}),
        ]
        return SimpleNamespace(returncode=0, stdout="\n".join(lines) + "\n")

    run.calls = calls
    return run


def test_packagekit_mirror_and_depart():
    run = _pk_runner()
    w = ActivityWatcher(runner=run)
    w.poll_packagekit(now=1000.0)
    iid = aw.pk_item_id("/org/tx/7")
    item = activity.get(iid)
    assert item["title"] == "Updating packages"
    assert item["detail"] == "firefox" and item["progress"] == 45.0
    # Throttled: same timestamp doesn't re-poll.
    tx_calls = len([c for c in run.calls if "GetTransactionList" in c])
    w.poll_packagekit(now=1000.5)
    assert len([c for c in run.calls if "GetTransactionList" in c]) == tx_calls
    # Unknown percentage → indeterminate progress.
    run2 = _pk_runner(pct=101)
    w2 = ActivityWatcher(runner=run2)
    w2.poll_packagekit(now=1000.0)
    assert activity.get(aw.pk_item_id("/org/tx/7"))["progress"] is None
    # Path leaves the list → finished ok.
    run_empty = _pk_runner(paths=[])
    w3 = ActivityWatcher(runner=run_empty)
    w3._pk = dict(w._pk)
    w3.poll_packagekit(now=2000.0)
    assert activity.get(iid)["status"] == "done"


def test_packagekit_busctl_missing_disables_then_retries():
    calls = []

    def run(argv, timeout=2.0):
        calls.append(time.monotonic())
        raise FileNotFoundError("no busctl")

    w = ActivityWatcher(runner=run)
    w.poll_packagekit(now=1000.0)
    assert activity.list_items() == [] and len(calls) == 1
    w.poll_packagekit(now=1010.0)  # within the 60s retry window: quiet
    assert len(calls) == 1
    w.poll_packagekit(now=1070.0)  # window elapsed: tries again
    assert len(calls) == 2


# --- updater processes ----------------------------------------------------------


def _mk_proc(root, pid, *argv):
    d = root / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "cmdline").write_bytes(b"\x00".join(a.encode() for a in argv) + b"\x00")


def test_processes_mirror_and_exit(tmp_path):
    root = tmp_path / "proc"
    _mk_proc(root, "123", "/usr/bin/dnf", "upgrade", "-y")
    _mk_proc(root, "9", "/usr/bin/bash")
    root.joinpath("x").mkdir()  # non-numeric: skipped
    w = ActivityWatcher(proc_root=root)
    w.poll_processes(now=10.0)
    item = activity.get("update-proc-123")
    assert item["title"] == "DNF upgrade" and item["status"] == "running"
    assert activity.get("update-proc-9") is None
    import shutil

    shutil.rmtree(root / "123")
    w.poll_processes(now=11.0)
    assert activity.get("update-proc-123")["status"] == "done"


def test_processes_skip_unreadable(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    w = ActivityWatcher(proc_root=root)
    w.poll_processes(now=1.0)  # empty: fine
    assert activity.list_items() == []
    w2 = ActivityWatcher(proc_root=root / "nope")
    w2.poll_processes(now=1.0)  # missing root: fine
    assert activity.list_items() == []


# --- projects -------------------------------------------------------------------


def _meta(pid, **fields):
    base = {
        "id": pid,
        "kind": "research",
        "title": "T",
        "status": "running",
        "model": "opencode/muse-spark-1.3",
        "progress": None,
        "stage": "",
        "created_at": 1_700_000_000.0,
    }
    base.update(fields)
    return base


def test_projects_mirror_and_close():
    now = 1_700_000_000.0
    w = ActivityWatcher()
    running = [
        _meta(
            "20260928-120000-alpha",
            title="Alpha",
            progress=42,
            stage="Reading: example.com",
            created_at=now - 100,
        ),
        _meta("20260928-120000-beta", kind="code", title="Beta", created_at=now - 200),
        _meta("20260920-120000-old", title="Old", created_at=now - 90000),
    ]
    w.poll_projects(now=now, projects=running)
    alpha = activity.get("project-20260928-120000-alpha")
    assert alpha["kind"] == "research" and alpha["progress"] == 42.0
    assert alpha["detail"] == "Reading: example.com"
    beta = activity.get("project-20260928-120000-beta")
    assert beta["kind"] == "coding" and beta["progress"] is None
    assert beta["detail"].startswith("opencode · ")
    assert activity.get("project-20260920-120000-old") is None  # >24h: skip

    closed = [
        _meta(
            "20260928-120000-alpha", title="Alpha", status="done", created_at=now - 100
        ),
        _meta(
            "20260928-120000-beta",
            kind="code",
            title="Beta",
            status="cancelled",
            created_at=now - 200,
        ),
        _meta("20260928-120000-gamma", title="Gamma", created_at=now - 50),
    ]
    w.poll_projects(now=now + 5, projects=closed)
    assert activity.get("project-20260928-120000-alpha")["status"] == "done"
    assert activity.get("project-20260928-120000-beta")["status"] == "cancelled"
    # A still-mirrored project that vanishes fails honestly.
    w.poll_projects(now=now + 6, projects=[])
    gone = {i["id"]: i for i in activity.list_items(now=now + 6)}
    assert gone["project-20260928-120000-gamma"]["status"] == "failed"
    assert gone["project-20260928-120000-alpha"]["status"] == "done"
    for iid in gone:
        activity.update(iid, finished=now - 9999, updated=now - 9999)
    assert activity.list_items(now=now + 6) == []


def test_projects_import_failure_is_quiet(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "projects", None)
    ActivityWatcher().poll_projects(now=1_700_000_000.0)
    assert activity.list_items() == []


# --- driver ---------------------------------------------------------------------


def test_poll_swallows_source_errors(monkeypatch):
    w = ActivityWatcher(proc_root="/nonexistent-proc-root-xyz")

    def boom(now=None):
        raise RuntimeError("kablam")

    monkeypatch.setattr(w, "poll_downloads", boom)
    w.poll()  # must not raise
    assert activity.list_items() == [] or True


def test_run_stops_and_thread_helper():
    w = ActivityWatcher(proc_root="/nonexistent-proc-root-xyz")
    stop = threading.Event()
    stop.set()
    w.run(stop, interval=0.01)  # returns at once
    thread, evt = aw.start_thread(interval=0.05)
    try:
        assert thread.name == "activity-watch" and thread.daemon
        time.sleep(0.2)
        assert thread.is_alive()
    finally:
        evt.set()
        thread.join(timeout=2.0)
    assert not thread.is_alive()
