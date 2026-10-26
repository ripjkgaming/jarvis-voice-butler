import jobs
import pytest


def test_mapping_and_owner(tmp_path):
    reg = jobs.Registry(tmp_path / "jobs")
    row = reg.register(
        "build-monitor",
        "request-1",
        "monitor_observation",
        "build",
        {"pid": 12},
        single_active=True,
    )
    assert reg.by_native("build-monitor", "request-1")["id"] == row["id"]
    assert "payload" not in jobs.public(row)
    with pytest.raises(jobs.JobError):
        reg.update(row["id"], "agent-tasks", {}, row["revision"])
    with pytest.raises(jobs.JobError):
        reg.register(
            "build-monitor",
            "request-2",
            "monitor_observation",
            "next",
            {},
            single_active=True,
        )


def test_cancel_receipt_is_not_settlement(tmp_path):
    reg = jobs.Registry(tmp_path / "jobs")
    row = reg.register("agent-tasks", "task-1", "adapter_work", "task", {})
    receipt = reg.request_cancel(row["id"])
    assert receipt["request_recorded"] and not receipt["work_stopped"]
    assert receipt["lifecycle"] == "cancellation_requested"
    assert reg.request_cancel(row["id"]) == receipt
    with pytest.raises(jobs.JobError):
        reg.update(row["id"], "agent-tasks", {}, row["revision"])
    current = reg.get(row["id"])
    settled = reg.settle(row["id"], "agent-tasks", "cancelled", {}, current["revision"])
    assert settled["lifecycle"] == "settled"
    assert reg.request_cancel(row["id"])["already_settled"]


def test_monitor_cannot_claim_build_result(tmp_path):
    reg = jobs.Registry(tmp_path / "jobs")
    row = reg.register("build-monitor", "r", "monitor_observation", "test", {})
    for outcome in ("succeeded", "failed", "cancelled"):
        with pytest.raises(jobs.JobError):
            reg.settle(row["id"], "build-monitor", outcome, {}, row["revision"])
    reg.settle(row["id"], "build-monitor", "ended_unknown", {}, row["revision"])
    with pytest.raises(jobs.JobError):
        reg.update(row["id"], "build-monitor", {}, row["revision"])


def test_unsafe_store_and_payload(tmp_path):
    target = tmp_path / "real"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(jobs.JobError):
        jobs.Registry(link)
    reg = jobs.Registry(target)
    with pytest.raises(jobs.JobError):
        reg.register("owner", "native", "adapter_work", "label", {"large": "x" * 17000})
    (target / "jobs.sqlite3").unlink()
    (target / "jobs.sqlite3").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(jobs.JobError):
        reg.get("job-" + "a" * 32)


def test_restart_and_bounded_history(tmp_path):
    reg = jobs.Registry(tmp_path / "jobs")
    for i in range(25):
        row = reg.register("build-monitor", str(i), "monitor_observation", "build", {})
        reg.settle(row["id"], "build-monitor", "ended_unknown", {}, row["revision"])
    reloaded = jobs.Registry(tmp_path / "jobs")
    assert len(reloaded.list("build-monitor")) == 20
    assert reloaded.by_native("build-monitor", "24")["outcome"] == "ended_unknown"


def test_concurrent_registration_one_active(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    reg = jobs.Registry(tmp_path / "jobs")

    def register(i):
        try:
            return reg.register(
                "build-monitor",
                str(i),
                "monitor_observation",
                "build",
                {},
                single_active=True,
            )
        except jobs.JobError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(register, range(8)))
    assert sum(isinstance(row, dict) for row in results) == 1
    assert all(isinstance(row, dict) or row == 409 for row in results)


def test_replaced_store_and_journal_are_refused(tmp_path):
    reg = jobs.Registry(tmp_path / "jobs")
    root = tmp_path / "jobs"
    (root / "jobs.sqlite3-journal").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(jobs.JobError):
        reg.list("owner")
    (root / "jobs.sqlite3-journal").unlink()
    root.rename(tmp_path / "old")
    root.mkdir(mode=0o700)
    with pytest.raises(jobs.JobError):
        reg.list("owner")


def test_foreign_and_corrupt_store_not_recreated(tmp_path):
    import sqlite3

    root = tmp_path / "jobs"
    root.mkdir(mode=0o700)
    path = root / "jobs.sqlite3"
    path.touch(mode=0o600)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated (value TEXT)")
    with pytest.raises(jobs.JobError):
        jobs.Registry(root)
    path.write_bytes(b"corrupt fixture")
    with pytest.raises(jobs.JobError):
        jobs.Registry(root)
    assert path.read_bytes() == b"corrupt fixture"


def test_corrupt_row_and_missing_db_fail_closed(tmp_path):
    reg = jobs.Registry(tmp_path / "jobs")
    row = reg.register("owner", "native", "adapter_work", "label", {})
    with reg.db() as db:
        db.execute("UPDATE jobs SET payload=? WHERE id=?", ("{}", row["id"]))
    with pytest.raises(jobs.JobError):
        reg.get(row["id"])
    (tmp_path / "jobs" / "jobs.sqlite3").unlink()
    with pytest.raises(jobs.JobError):
        reg.get(row["id"])
    assert not (tmp_path / "jobs" / "jobs.sqlite3").exists()


def test_legacy_json_api_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    native = jobs.start("Legacy tests", kind="tests", now=10)
    assert jobs.get(native)["status"] == "running"
    jobs.finish(native, False, summary="Synthetic result", failures=2, now=20)
    assert jobs.recent(1)[0]["failures"] == 2
    assert jobs.get(native)["status"] == "failed"
    assert (tmp_path / "jobs" / (native + ".json")).exists()
    registry = jobs.Registry(tmp_path / "job_registry")
    row = registry.register(
        "build-monitor", "request", "monitor_observation", "Build", {}
    )
    assert registry.get(row["id"])["scope"] == "monitor_observation"
    assert jobs.get(native)["status"] == "failed"
