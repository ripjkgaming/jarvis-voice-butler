"""Tests for the activity feed store + CLI (src/activity.py)."""

import json
import os
import re

import pytest

import activity


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    return tmp_path


def test_start_get_roundtrip():
    iid = activity.start("download", "big.iso", detail="d", source="web")
    assert re.fullmatch(r"download-[0-9a-f]{8}", iid)
    item = activity.get(iid)
    assert item["kind"] == "download"
    assert item["title"] == "big.iso"
    assert item["detail"] == "d"
    assert item["status"] == "running"
    assert item["progress"] is None
    assert item["finished"] is None
    assert item["source"] == "web"
    assert item["started"] <= item["updated"]
    # One JSON file, no stray .tmp left behind.
    files = list((activity.activity_dir()).glob("*"))
    assert [f.name for f in files] == [f"{iid}.json"]


def test_start_coerces_bad_kind_and_truncates():
    iid = activity.start("nope", "t" * 200, detail="d" * 500)
    item = activity.get(iid)
    assert item["kind"] == "task"
    assert len(item["title"]) == 120
    assert len(item["detail"]) == 200
    assert iid.startswith("task-")


def test_start_custom_id_and_rerun_updates():
    iid = activity.start("build", "v1", item_id="build-x")
    assert iid == "build-x"
    again = activity.start("build", "v2", detail="new", item_id="build-x")
    assert again == "build-x"
    item = activity.get("build-x")
    assert item["title"] == "v2" and item["detail"] == "new"
    # Bad ids never raise: a fresh id is generated instead.
    other = activity.start("task", "t", item_id="../evil")
    assert other != "../evil" and activity.get(other)["title"] == "t"


def test_update_known_fields_clamps_progress():
    iid = activity.start("task", "t", progress=10)
    assert activity.update(iid, progress=1000, detail="d", title="t2") is True
    item = activity.get(iid)
    assert item["progress"] == 100.0
    assert activity.update(iid, progress=-5) is True
    assert activity.get(iid)["progress"] == 0.0
    assert activity.update(iid, progress=None) is True
    assert activity.get(iid)["progress"] is None
    # Unknown fields ignored, bad values skipped, missing item is False.
    assert activity.update(iid, bogus=1, kind="nope", status="nope") is True
    assert activity.get(iid)["kind"] == "task"
    assert activity.update("missing-id") is False


def test_update_never_raises_on_garbage():
    assert activity.update("../x", progress="nan-obj") is False
    assert activity.update("", **{"a" * 999: object()}) is False
    assert activity.get("../x") is None
    assert activity.finish("") is False


def test_finish_ok_failed_cancelled():
    iid = activity.start("build", "b", progress=12)
    assert activity.finish(iid, ok=True) is True
    item = activity.get(iid)
    assert item["status"] == "done" and item["progress"] == 100.0
    assert item["finished"] is not None

    iid2 = activity.start("build", "b2", progress=12)
    activity.finish(iid2, ok=False)
    item2 = activity.get(iid2)
    assert item2["status"] == "failed" and item2["progress"] == 12.0

    iid3 = activity.start("download", "d")
    activity.finish(iid3, ok=False, status="cancelled", detail="gone")
    item3 = activity.get(iid3)
    assert item3["status"] == "cancelled" and item3["detail"] == "gone"
    # Indeterminate stays indeterminate even when ok.
    assert item3["progress"] is None
    assert activity.finish("missing-id") is False


def _backdate(iid, **fields):
    assert activity.update(iid, **fields) is True


def test_list_filters_and_sorts():
    now = 1_700_000_000.0
    old_run = activity.start("download", "stale")
    fresh_run = activity.start("download", "fresh")
    done_old = activity.start("task", "done-old")
    done_new = activity.start("task", "done-new")
    activity.finish(done_old, ok=True)
    activity.finish(done_new, ok=True)
    _backdate(old_run, started=now - 5000, updated=now - 5000)
    _backdate(fresh_run, started=now - 10, updated=now - 10)
    _backdate(done_old, finished=now - 5000, updated=now - 5000)
    _backdate(done_new, finished=now - 5, updated=now - 5)
    items = activity.list_items(now=now)
    ids = [i["id"] for i in items]
    assert old_run not in ids and done_old not in ids  # stale / expired
    assert ids[0] == fresh_run  # running first...
    assert ids[1] == done_new  # ...then finished, newest first


def test_list_boundary_windows():
    now = 1_700_000_000.0
    iid = activity.start("task", "edge")
    _backdate(iid, started=now - 1800, updated=now - 1800)
    assert [i["id"] for i in activity.list_items(now=now)] == [iid]
    _backdate(iid, updated=now - 1800.5)
    assert activity.list_items(now=now) == []


def test_prune_removes_expired():
    now = 1_700_000_000.0
    gone = activity.start("task", "old-done")
    activity.finish(gone, ok=True)
    ancient = activity.start("task", "ancient-run")
    keep = activity.start("task", "keep")
    _backdate(gone, finished=now - 4000, updated=now - 4000)
    _backdate(ancient, started=now - 99999, updated=now - 99999)
    removed = activity.prune(now=now)
    assert removed == 2
    assert activity.get(gone) is None and activity.get(ancient) is None
    assert activity.get(keep) is not None
    assert activity.prune(now=now) == 0


def test_list_skips_corrupt_files():
    iid = activity.start("task", "fine")
    (activity.activity_dir() / "junk.json").write_text("{not json")
    items = activity.list_items()
    assert [i["id"] for i in items] == [iid]


def test_cli_start_update_finish_list(capsys):
    assert (
        activity.main(["start", "--kind", "download", "--title", "X", "--detail", "D"])
        == 0
    )
    iid = capsys.readouterr().out.strip()
    assert activity.get(iid)["title"] == "X"
    assert activity.main(["update", iid, "--progress", "42", "--title", "X2"]) == 0
    assert capsys.readouterr().out.strip() == "ok"
    assert activity.get(iid)["progress"] == 42.0
    assert activity.main(["finish", iid, "--failed", "--detail", "boom"]) == 0
    assert capsys.readouterr().out.strip() == "ok"
    assert activity.get(iid)["status"] == "failed"
    assert activity.main(["list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert any(i["id"] == iid for i in listed)
    # Missing item: nonzero exit, still no traceback.
    assert activity.main(["finish", "missing-id"]) == 1
    assert activity.main(["update", "missing-id"]) == 1


def test_progress_nan_and_bool_ignored():
    iid = activity.start("task", "t", progress=50)
    assert activity.update(iid, progress=float("nan")) is True
    assert activity.get(iid)["progress"] == 50.0
    assert activity.update(iid, progress=True) is True
    assert activity.get(iid)["progress"] == 50.0
    assert activity.start("task", "t2", progress=float("nan"))
    assert activity.get(activity.start("task", "t3"))["progress"] is None


def test_meta_roundtrip_and_env_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "j home"))
    iid = activity.start("task", "t", meta={"a": 1})
    assert activity.get(iid)["meta"] == {"a": 1}
    assert (tmp_path / "j home" / "activity" / f"{iid}.json").exists()
    assert os.environ["JARVIS_HOME"] == str(tmp_path / "j home")


def test_start_reuses_finished_id_for_next_run(tmp_path, monkeypatch) -> None:
    """A repeated Jarvis tool reuses its id: finish() must close the new run."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    import activity

    first = activity.start("task", "opening app", item_id="jarvis-open-app")
    assert activity.finish(first)
    second = activity.start("task", "opening app", item_id="jarvis-open-app")
    assert second == "jarvis-open-app"
    assert activity.get(second)["status"] == "running"
    assert activity.finish(second)
    assert activity.get(second)["status"] == "done"
