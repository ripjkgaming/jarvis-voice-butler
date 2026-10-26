"""Regression cases for lost memory and colliding background job records."""

import datetime as dt

import pytest

import jobs
import memory


@pytest.fixture(autouse=True)
def isolated_memory(monkeypatch):
    monkeypatch.delenv("JARVIS_VAULT", raising=False)
    monkeypatch.setenv("JARVIS_MEMORY", "1")


def test_same_millisecond_jobs_keep_separate_results():
    first = jobs.start("First build", "build", now=10)
    second = jobs.start("Second build", "build", now=10)
    assert first != second
    jobs.finish(first, True, summary="First succeeded", now=11)
    assert jobs.get(first)["title"] == "First build"
    assert jobs.get(second)["title"] == "Second build"
    assert jobs.get(second)["status"] == "running"


def test_failed_daily_write_keeps_capture_retryable(monkeypatch):
    captions = [
        {"ts": 100, "role": "sir", "text": "Remember our project"},
        {"ts": 101, "role": "sir", "text": "Continue it tomorrow"},
    ]
    summary = {"summary": "Continue the project tomorrow", "threads": []}
    real_append = memory._append
    monkeypatch.setattr(memory, "_append", lambda *args, **kwargs: False)
    result = memory.capture_once(2000, captions, lambda *_: (summary, None))
    assert result.startswith("failed:")
    assert memory.load_state().get("last_ts", 0) < 100
    monkeypatch.setattr(memory, "_append", real_append)
    assert memory.capture_once(2000, captions, lambda *_: (summary, None)) == "captured"
    assert (
        "Continue the project"
        in memory.daily_path(dt.date.fromtimestamp(101)).read_text()
    )


def test_forget_preserves_preference_if_archive_write_fails(monkeypatch):
    assert memory.remember("I prefer metric units", now=100)
    before = memory.prefs_path().read_text()
    monkeypatch.setattr(memory, "_append", lambda *args, **kwargs: False)
    assert memory.forget("metric", now=200) == []
    assert memory.prefs_path().read_text() == before


def test_daily_failure_does_not_append_threads_before_retry(monkeypatch):
    summary = {
        "summary": "Continue the project",
        "threads": [{"name": "Project", "next_step": "Write the report"}],
    }
    captions = [
        {"ts": 100, "role": "sir", "text": "Continue the project"},
        {"ts": 101, "role": "sir", "text": "Write the report next"},
    ]
    original = memory._append

    def fail_daily(path, *args, **kwargs):
        if path.parent.name == "Daily":
            return False
        return original(path, *args, **kwargs)

    monkeypatch.setattr(memory, "_append", fail_daily)
    assert memory.capture_once(2000, captions, lambda *_: (summary, None)).startswith(
        "failed:"
    )
    thread = memory.threads_dir() / "Project.md"
    assert not thread.exists()
    monkeypatch.setattr(memory, "_append", original)
    assert memory.capture_once(2000, captions, lambda *_: (summary, None)) == "captured"
    assert thread.read_text().count("- Next step: Write the report") == 1


def test_forget_does_not_claim_removal_when_preferences_write_fails(monkeypatch):
    assert memory.remember("I prefer metric units", now=100)
    before = memory.prefs_path().read_text()
    original = type(memory.prefs_path()).write_text

    def fail_preferences(path, *args, **kwargs):
        if path == memory.prefs_path():
            raise OSError("Synthetic read-only preferences")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(memory.prefs_path()), "write_text", fail_preferences)
    assert memory.forget("metric", now=200) == []
    assert memory.prefs_path().read_text() == before
