"""jobs.start/finish mirror into the HUD activity feed (reactor progress ring)."""

import activity
import jobs


def _mirrored():
    return [i for i in activity.list_items() if i["id"].startswith("job-")]


def test_start_appears_as_indeterminate_running_item():
    job_id = jobs.start("The jarvis tests", "tests")
    items = _mirrored()
    assert len(items) == 1
    item = items[0]
    assert item["status"] == "running"
    assert item["progress"] is None  # unknown length: ring sweeps
    assert item["kind"] == "build"
    assert item["title"] == "The jarvis tests"
    assert activity.valid_id(item["id"])
    assert job_id.endswith(item["id"][len("job-") :])


def test_finish_closes_the_item_ok_and_failed():
    ok = jobs.start("lint", "terminal")
    bad = jobs.start("build", "build")
    jobs.finish(ok, True, "clean")
    jobs.finish(bad, False, "2 failures", failures=2)
    by_title = {i["title"]: i for i in _mirrored()}
    assert by_title["lint"]["status"] == "done"
    assert by_title["lint"]["kind"] == "task"
    assert by_title["build"]["status"] == "failed"


def test_finish_of_unknown_job_never_raises():
    jobs.finish("never-started", True)
    assert _mirrored() == []


def test_activity_failure_does_not_break_jobs(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("feed down")

    monkeypatch.setattr(activity, "start", boom)
    monkeypatch.setattr(activity, "finish", boom)
    job_id = jobs.start("x", "tests")
    jobs.finish(job_id, True)
    assert jobs.get(job_id)["status"] == "done"
