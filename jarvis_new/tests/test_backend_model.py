"""Hermetic tests for src/backend_model.py (no real Claude calls)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import claude_cli
from backend_model import (
    backend_think,
    extract_events,
    find_schedule_file,
    parse_events,
)


# --- parse_events ---


def test_parse_events_valid_array() -> None:
    reply = json.dumps(
        [
            {
                "title": "Mock: Maths Paper 2",
                "date": "2026-06-01",
                "start": "09:00",
                "end": "11:00",
                "location": "Hall",
                "notes": "Paper 2",
            }
        ]
    )
    events = parse_events(reply)
    assert len(events) == 1
    assert events[0]["title"] == "Mock: Maths Paper 2"
    assert events[0]["date"] == "2026-06-01"
    assert events[0]["start"] == "09:00"
    assert events[0]["location"] == "Hall"


def test_parse_events_wrapped_in_prose() -> None:
    payload = json.dumps([{"title": "Physics Mock", "date": "2026-06-02"}])
    reply = f"Here are the entries you asked for:\n```json\n{payload}\n```\nDone."
    events = parse_events(reply)
    assert len(events) == 1
    assert events[0]["title"] == "Physics Mock"


def test_parse_events_bad_dates_dropped() -> None:
    reply = json.dumps(
        [
            {"title": "Good", "date": "2026-06-01"},
            {"title": "Bad month", "date": "2026-13-01"},
            {"title": "Bad day", "date": "2026-02-30"},
            {"title": "Wrong shape", "date": "06/01/2026"},
            {"title": "Empty date", "date": ""},
        ]
    )
    events = parse_events(reply)
    assert [e["title"] for e in events] == ["Good"]


def test_parse_events_missing_title_dropped() -> None:
    reply = json.dumps(
        [
            {"title": "", "date": "2026-06-01"},
            {"date": "2026-06-01"},
            {"title": "  ", "date": "2026-06-01"},
            {"title": "Kept", "date": "2026-06-01"},
            "not a dict",
            42,
        ]
    )
    events = parse_events(reply)
    assert [e["title"] for e in events] == ["Kept"]


def test_parse_events_non_list() -> None:
    assert parse_events(json.dumps({"title": "Solo", "date": "2026-06-01"})) == []
    assert parse_events("there is no json here at all") == []
    assert parse_events("") == []
    assert parse_events("[not valid json") == []


# --- find_schedule_file ---


def _touch(path: Path, mtime: float) -> Path:
    path.write_bytes(b"x")
    import os

    os.utime(path, (mtime, mtime))
    return path


def test_find_schedule_file_direct_path_wins(tmp_path: Path) -> None:
    direct = tmp_path / "other" / "random.txt"
    direct.parent.mkdir(parents=True)
    direct.write_text("hi")
    elsewhere = tmp_path / "Downloads"
    elsewhere.mkdir()
    _touch(elsewhere / "exam timetable.pdf", 2000.0)
    found = find_schedule_file(str(direct), search_dirs=[elsewhere])
    assert found == direct


def test_find_schedule_file_hint_words_must_all_match(tmp_path: Path) -> None:
    base = tmp_path / "Downloads"
    base.mkdir()
    _touch(base / "mock maths paper.pdf", 1000.0)
    _touch(base / "mock physics paper.pdf", 2000.0)
    found = find_schedule_file("mock maths", search_dirs=[base])
    assert found is not None and found.name == "mock maths paper.pdf"
    assert find_schedule_file("mock chemistry", search_dirs=[base]) is None


def test_find_schedule_file_no_hint_picks_newest_keyword_file(
    tmp_path: Path,
) -> None:
    base = tmp_path / "Downloads"
    base.mkdir()
    _touch(base / "exam timetable.pdf", 1000.0)
    _touch(base / "mock schedule.pdf", 3000.0)
    _touch(base / "holiday photos.pdf", 4000.0)  # no keyword: ignored
    found = find_schedule_file("", search_dirs=[base])
    assert found is not None and found.name == "mock schedule.pdf"


def test_find_schedule_file_unreadable_suffix_ignored(tmp_path: Path) -> None:
    base = tmp_path / "Downloads"
    base.mkdir()
    _touch(base / "exam notes.zip", 5000.0)  # keyword but unreadable suffix
    _touch(base / "exam notes.pdf", 1000.0)
    found = find_schedule_file("", search_dirs=[base])
    assert found is not None and found.name == "exam notes.pdf"
    only_bad = tmp_path / "Other"
    only_bad.mkdir()
    _touch(only_bad / "exam notes.exe", 5000.0)
    assert find_schedule_file("", search_dirs=[only_bad]) is None


# --- extract_events ---


def test_extract_events_missing_file(tmp_path: Path) -> None:
    events, warning = extract_events(tmp_path / "nope.pdf")
    assert events == []
    assert warning and "No file" in warning


def test_extract_events_unsupported_suffix(tmp_path: Path) -> None:
    bad = tmp_path / "notes.exe"
    bad.write_bytes(b"x")
    events, warning = extract_events(bad)
    assert events == []
    assert warning and "not" in warning.lower()


def test_extract_events_success_stages_and_cleans_up(
    tmp_path: Path, monkeypatch
) -> None:
    import shutil as _shutil

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_CLAUDE", "1")
    # Route `claude` resolution through claude_cli's shutil reference.
    fake_which = lambda name: "/usr/bin/claude" if name == "claude" else None  # noqa: E731
    monkeypatch.setattr(claude_cli.shutil, "which", fake_which)
    # claude_reply binds `which=shutil.which` as a default arg, so patch the
    # default too; otherwise the real lookup runs and the test flakes.
    monkeypatch.setattr(
        claude_cli.claude_reply, "__defaults__", (60.0, None, fake_which)
    )

    src = tmp_path / "mock timetable.pdf"
    src.write_bytes(b"%PDF-fake")
    payload = [{"title": "Mock: Maths", "date": "2026-06-01", "start": "09:00"}]
    seen: dict = {}

    def fake_runner(argv, **kw):
        seen["argv"] = list(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    events, warning = extract_events(src, runner=fake_runner)
    assert warning is None
    assert len(events) == 1 and events[0]["title"] == "Mock: Maths"
    argv = seen["argv"]
    assert argv[argv.index("--model") + 1] == claude_cli.BACKEND_MODEL
    assert argv[argv.index("--tools") + 1] == "Read"
    inbox = tmp_path / "claude-cwd" / "inbox"
    leftovers = list(inbox.glob("*")) if inbox.is_dir() else []
    assert leftovers == []
    # Original file untouched.
    assert src.is_file()


# --- backend_think ---


def test_backend_think_empty_task_warning() -> None:
    assert backend_think("") == ("", "No task given")
    assert backend_think("   ") == ("", "No task given")


def test_backend_think_passes_model(monkeypatch) -> None:
    seen: dict = {}
    monkeypatch.setenv("JARVIS_CLAUDE", "1")

    def fake_reply(prompt, **kw):
        seen["prompt"] = prompt
        seen.update(kw)
        return ("careful answer", None)

    monkeypatch.setattr(claude_cli, "claude_reply", fake_reply)
    reply, warning = backend_think("plan my revision")
    assert (reply, warning) == ("careful answer", None)
    assert seen["model"] == claude_cli.BACKEND_MODEL
    assert "plan my revision" in seen["prompt"]
