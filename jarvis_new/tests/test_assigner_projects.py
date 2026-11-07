import pytest

import projects
from assigner_headless import Jobs


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("JARVIS_ASSIGNER_ROUTING", "1")
    monkeypatch.setattr(projects, "_announce", lambda _: None)


def test_disabled_route_has_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ASSIGNER_ROUTING", "0")
    meta = projects.start_code("write hello", directory=str(tmp_path), request_id="r")
    assert meta["status"] == "blocked"
    assert "not activated" in meta["error"]


def test_request_id_required_before_dispatch(tmp_path):
    meta = projects.start_code("write hello", directory=str(tmp_path))
    assert meta["status"] == "blocked"
    assert "request ID" in meta["error"]


def test_project_dedup_status_cancel_and_single_report(tmp_path, monkeypatch):
    class FakeService:
        def __init__(self):
            self.jobs = Jobs(tmp_path / "ledger")

        def submit(self, *args, **kwargs):
            return self.jobs.submit(*args, **kwargs)

        def kick(self):
            pass

    service = FakeService()
    monkeypatch.setattr(projects, "_assigner_service", lambda: service)
    monkeypatch.setattr(projects, "resolve_directory", lambda *a: (tmp_path, None))
    one = projects.start_code("write hello", directory=str(tmp_path), request_id="r")
    two = projects.start_code("write hello", directory=str(tmp_path), request_id="r")
    assert one["id"] == two["id"]
    assert len(projects.list_projects()) == 1
    service.jobs.update(
        "r",
        status="done",
        result={
            "request_id": "r",
            "status": "done",
            "summary": "done",
            "details": "hello",
        },
    )
    meta = projects.get_project(one["id"])
    assert meta["status"] == "done" and meta["summary"] == "done"
    assert projects.claim_completion(one["id"])
    assert not projects.claim_completion(one["id"])
    assert not projects.cancel_project(one["id"])


def test_cancelled_queued_project_never_announces_done(tmp_path, monkeypatch):
    class FakeService:
        def __init__(self):
            self.jobs = Jobs(tmp_path / "ledger")

        def submit(self, *args, **kwargs):
            return self.jobs.submit(*args, **kwargs)

        def kick(self):
            pass

    service = FakeService()
    monkeypatch.setattr(projects, "_assigner_service", lambda: service)
    monkeypatch.setattr(projects, "resolve_directory", lambda *a: (tmp_path, None))
    meta = projects.start_code("write hello", request_id="r")
    assert projects.cancel_project(meta["id"])
    service.jobs.update("r", status="done", result={"summary": "late"})
    assert projects.get_project(meta["id"])["status"] == "cancelled"


def test_request_reuse_with_other_scope_fails(tmp_path, monkeypatch):
    class FakeService:
        def __init__(self):
            self.jobs = Jobs(tmp_path / "ledger")

        def submit(self, *args, **kwargs):
            return self.jobs.submit(*args, **kwargs)

        def kick(self):
            pass

    service = FakeService()
    monkeypatch.setattr(projects, "_assigner_service", lambda: service)
    monkeypatch.setattr(projects, "resolve_directory", lambda *a: (tmp_path, None))
    projects.start_code("write hello", request_id="r")
    other = projects.start_code("delete everything", request_id="r")
    assert other["status"] == "blocked"
    assert service.jobs.get("r")["goal"] == "write hello"


def test_independent_archive_writers_use_unique_atomic_files(tmp_path, monkeypatch):
    import importlib.util
    import threading
    from pathlib import Path

    modules = []
    for name in ("archive_process_a", "archive_process_b"):
        spec = importlib.util.spec_from_file_location(name, projects.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    meta = modules[0]._new_meta(
        "code",
        "report",
        "scope",
        "gpt-6-luna",
        status="done",
        engine="assigner",
        request_id="r",
    )
    job = {
        "project_id": meta["id"],
        "request_id": "r",
        "status": "done",
        "result": {"summary": "done", "details": "code"},
    }
    barrier, errors = threading.Barrier(2), []
    original = Path.replace

    def replace(path, target):
        if path.name == "meta.tmp":
            barrier.wait(timeout=2)
        return original(path, target)

    monkeypatch.setattr(Path, "replace", replace)

    def sync(module):
        try:
            module._sync_code_job(job)
        except Exception as exc:
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=sync, args=(m,)) for m in modules]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert not any(t.is_alive() for t in threads)
    assert errors == []
    assert projects._read_meta(meta["id"])["summary"] == "done"


def test_failed_reporting_archive_preserves_raw_worker_proposal(tmp_path, monkeypatch):
    service = type("Service", (), {"jobs": Jobs(tmp_path / "ledger")})()
    monkeypatch.setattr(projects, "_assigner_service", lambda: service)
    meta = projects._new_meta(
        "code", "proposal", "scope", "gpt-6-luna", engine="assigner", request_id="r"
    )
    service.jobs.submit(
        "r", "return code", str(tmp_path), "high", project_id=meta["id"]
    )
    service.jobs.update(
        "r",
        status="blocked",
        error="coordinator unavailable",
        worker_results=[{"raw": "original patch", "thread_id": "worker"}],
    )
    projects._sync_code_job(service.jobs.get("r"))
    assert (projects._dir(meta["id"]) / "worker-1.md").read_text() == "original patch"
    assert projects._read_meta(meta["id"])["status"] == "blocked"


def test_context_hashes_and_exact_proposal_remain_in_archive(tmp_path, monkeypatch):
    service = type("Service", (), {"jobs": Jobs(tmp_path / "ledger")})()
    monkeypatch.setattr(projects, "_assigner_service", lambda: service)
    meta = projects._new_meta(
        "code", "proposal", "scope", "gpt-6-luna", engine="assigner", request_id="r"
    )
    service.jobs.submit(
        "r", "return code", str(tmp_path), "high", project_id=meta["id"]
    )
    source = {"files": [{"path": "calc.py", "sha256": "original-source-hash"}]}
    validation = {"tests_run": False, "status": "matches-snapshot"}
    service.jobs.update(
        "r",
        status="blocked",
        error="reporting failed",
        context_manifest=source,
        patch_validation=validation,
        proposal_patch="exact patch",
    )
    projects._sync_code_job(service.jobs.get("r"))
    assert (projects._dir(meta["id"]) / "proposal.patch").read_text() == "exact patch"
    assert (
        "original-source-hash"
        in (projects._dir(meta["id"]) / "context-manifest.md").read_text()
    )
    assert projects._read_meta(meta["id"])["patch_validation"]["tests_run"] is False
    assert any(
        document["name"] == "proposal.patch" and document["text"] == "exact patch"
        for document in projects.documents(meta["id"])
    )


class _KickService:
    def __init__(self, root):
        self.jobs = Jobs(root)
        self.kicks = 0

    def submit(self, *args, **kwargs):
        return self.jobs.submit(*args, **kwargs)

    def kick(self):
        self.kicks += 1


def test_routing_defaults_on_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_ASSIGNER_ROUTING")
    service = _KickService(tmp_path / "ledger")
    monkeypatch.setattr(projects, "_assigner_service", lambda: service)
    monkeypatch.setattr(projects, "resolve_directory", lambda *a: (tmp_path, None))
    meta = projects.start_code("write hello", directory=str(tmp_path), request_id="r")
    assert meta["status"] != "blocked"
    assert service.jobs.get("r") is not None


def test_refresh_kicks_queued_job_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_ASSIGNER_ROUTING")
    service = _KickService(tmp_path / "ledger")
    monkeypatch.setattr(projects, "_assigner_service", lambda: service)
    monkeypatch.setattr(projects, "resolve_directory", lambda *a: (tmp_path, None))
    meta = projects.start_code("write hello", directory=str(tmp_path), request_id="r")
    before = service.kicks
    projects.get_project(meta["id"])
    assert service.kicks == before + 1


def test_refresh_does_not_kick_when_routing_disabled(tmp_path, monkeypatch):
    service = _KickService(tmp_path / "ledger")
    monkeypatch.setattr(projects, "_assigner_service", lambda: service)
    monkeypatch.setattr(projects, "resolve_directory", lambda *a: (tmp_path, None))
    meta = projects.start_code("write hello", directory=str(tmp_path), request_id="r")
    monkeypatch.setenv("JARVIS_ASSIGNER_ROUTING", "0")
    before = service.kicks
    projects.get_project(meta["id"])
    assert service.kicks == before
