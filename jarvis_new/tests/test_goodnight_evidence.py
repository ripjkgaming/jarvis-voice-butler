import json
import sqlite3

import pytest
from goodnight_evidence import TaskInventory, json_rows, sql_rows


def stores(tmp_path):
    for name in ("tasks", "jobs", "assigner", "job_registry"):
        (tmp_path / name).mkdir()
    with sqlite3.connect(tmp_path / "assigner/jobs.sqlite3") as db:
        db.execute(
            "CREATE TABLE jobs(request_id TEXT, fingerprint TEXT, payload TEXT, created REAL, reported INTEGER)"
        )
    with sqlite3.connect(tmp_path / "job_registry/jobs.sqlite3") as db:
        db.execute(
            "CREATE TABLE jobs(id TEXT, owner TEXT, native_id TEXT, payload TEXT)"
        )
        db.execute("PRAGMA user_version=1")
    return TaskInventory(
        tmp_path,
        owned_clear=lambda: True,
        external_clear=lambda: True,
        build_exited=lambda row: None,
    )


def test_empty_known_stores_need_external_and_owned_coverage(tmp_path):
    inventory = stores(tmp_path)
    assert inventory.clear() is True
    inventory.external_clear = lambda: None
    assert inventory.clear() is None
    inventory.external_clear = lambda: True
    inventory.owned_clear = lambda: False
    assert inventory.clear() is None


def test_missing_corrupt_unknown_task_state(tmp_path):
    inventory = stores(tmp_path)
    task = tmp_path / "tasks/one.json"
    for data in (
        "{",
        "{}",
        '{"status":"running"}',
        '{"status":"waiting"}',
        '{"status":"done","executor_started":true}',
    ):
        task.write_text(data)
        assert inventory.clear() is not True
    task.write_text('{"status":"done","executor_started":false}')
    assert inventory.clear() is True
    (tmp_path / "assigner/jobs.sqlite3").unlink()
    assert inventory.clear() is None


@pytest.mark.parametrize(
    "status,uncertain",
    [
        ("pending", False),
        ("working", False),
        ("done", True),
        ("delivery_uncertain", True),
    ],
)
def test_assigner_active_queued_remote_uncertain(tmp_path, status, uncertain):
    inventory = stores(tmp_path)
    with sqlite3.connect(tmp_path / "assigner/jobs.sqlite3") as db:
        db.execute(
            "INSERT INTO jobs(payload) VALUES(?)",
            (json.dumps({"status": status, "remote_uncertain": uncertain}),),
        )
    assert inventory.clear() is False


def test_no_twenty_row_cap_or_schema_guess(tmp_path):
    inventory = stores(tmp_path)
    with sqlite3.connect(tmp_path / "assigner/jobs.sqlite3") as db:
        for n in range(25):
            db.execute(
                "INSERT INTO jobs(payload) VALUES(?)",
                (json.dumps({"status": "working" if n == 0 else "done"}),),
            )
    assert len(sql_rows(tmp_path / "assigner/jobs.sqlite3")) == 25
    assert inventory.clear() is False
    with sqlite3.connect(tmp_path / "assigner/jobs.sqlite3") as db:
        db.execute("PRAGMA user_version=99")
    assert inventory.clear() is None


def test_symlinks_and_oversized_inventory_hold(tmp_path, monkeypatch):
    inventory = stores(tmp_path)
    (tmp_path / "data").write_text('{"status":"done"}')
    (tmp_path / "tasks/task.json").symlink_to(tmp_path / "data")
    assert inventory.clear() is None
    (tmp_path / "tasks/task.json").unlink()
    monkeypatch.setattr("goodnight_evidence.MAX_ROWS", 0)
    (tmp_path / "tasks/task.json").write_text("{}")
    with pytest.raises(ValueError):
        json_rows(tmp_path / "tasks")


def test_unknown_monitor_settlement_is_not_build_exit(tmp_path):
    import jobs

    inventory = stores(tmp_path)
    (tmp_path / "job_registry").chmod(0o700)
    (tmp_path / "job_registry/jobs.sqlite3").chmod(0o600)
    registry = jobs.Registry(tmp_path / "job_registry")  # synthetic temp store only
    row = registry.register(
        "build-monitor", "build", "monitor_observation", "build", {}
    )
    registry.settle(
        row["id"], "build-monitor", "monitoring_stopped", {}, row["revision"]
    )
    assert inventory.clear() is False
    inventory.build_exited = lambda row: True  # synthetic authoritative exit
    assert inventory.clear() is True


@pytest.fixture(autouse=True)
def supported_test_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_GOODNIGHT_V2", "1")
