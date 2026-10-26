import multiprocessing
import threading

import pytest
from goodnight_fence import Fence
from goodnight_supported import SupportedInventory


def _child_start(root, entered, finished):
    entered.set()
    with Fence(root)():
        finished.set()


def test_cross_process_start_waits_for_final_lock(tmp_path):
    ctx = multiprocessing.get_context("fork")
    entered, finished = ctx.Event(), ctx.Event()
    fence = Fence(tmp_path)
    with fence():
        child = ctx.Process(target=_child_start, args=(tmp_path, entered, finished))
        child.start()
        assert entered.wait(1)
        assert not finished.wait(0.03)
    child.join(2)
    assert child.exitcode == 0
    assert finished.is_set()


def test_start_serializes_with_final_lock(tmp_path):
    fence = Fence(tmp_path)
    entered, finished = threading.Event(), threading.Event()

    def start():
        entered.set()
        with fence():
            finished.set()

    with fence() as lease:
        thread = threading.Thread(target=start)
        thread.start()
        assert entered.wait(1)
        assert not finished.wait(0.02)
        assert lease.valid()
    thread.join(1)
    assert finished.is_set()


def test_executor_lease_holds_after_terminal_record(tmp_path):
    fence = Fence(tmp_path)
    assert fence.work_clear()
    with fence.work():
        assert not fence.work_clear()
    assert fence.work_clear()


def test_missing_unused_supported_stores_are_clear(tmp_path):
    inv = SupportedInventory(tmp_path, Fence(tmp_path))
    assert inv.clear() is True
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks/a.json").write_text('{"status":"running"}')
    assert inv.clear() is False


def test_unknown_or_corrupt_supported_store_holds(tmp_path):
    (tmp_path / "assigner").mkdir()
    (tmp_path / "assigner/jobs.sqlite3").write_text("invalid")
    assert SupportedInventory(tmp_path, Fence(tmp_path)).clear() is None


def test_flag_off_keeps_existing_producer_behavior(tmp_path, monkeypatch):
    import jobs

    monkeypatch.setenv("JARVIS_GOODNIGHT_V2", "0")
    job_id = jobs.start("synthetic")
    assert jobs.get(job_id)["status"] == "running"
    assert not (tmp_path / "goodnight-runtime").exists()


def test_actual_legacy_registration_cannot_cross_final_fence(tmp_path):
    import jobs

    fence = Fence(tmp_path)
    entered, done = threading.Event(), threading.Event()

    def register():
        entered.set()
        jobs.start("synthetic")
        done.set()

    with fence():
        thread = threading.Thread(target=register)
        thread.start()
        assert entered.wait(1)
        assert not done.wait(0.03)
        assert not (tmp_path / "jobs").exists()
    thread.join(1)
    assert done.is_set()
    assert SupportedInventory(tmp_path, fence).clear() is False


@pytest.fixture(autouse=True)
def supported_test_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_GOODNIGHT_V2", "1")
