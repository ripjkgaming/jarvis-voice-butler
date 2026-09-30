"""Hermetic tests for src/drafts_ui.py (no shell, no network, no Gmail)."""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "src")

import drafts_ui


@pytest.mark.parametrize(
    "text",
    [
        "open my drafts",
        "open drafts",
        "show drafts",
        "show my drafts",
        "show my email drafts",
        "show reply drafts",
        "pull up my drafts",
        "pull up drafts",
        "bring up my drafts",
        "bring up email drafts",
        "Jarvis, open my drafts please",
        "show my drafts please",
    ],
)
def test_parse_voice_open(text: str) -> None:
    assert drafts_ui.parse_voice(text) == {"op": "open"}


@pytest.mark.parametrize(
    "text",
    [
        "close drafts",
        "close my drafts",
        "hide drafts",
        "hide my email drafts",
        "dismiss drafts",
        "dismiss my reply drafts",
        "put away drafts",
        "put away my drafts",
    ],
)
def test_parse_voice_close(text: str) -> None:
    assert drafts_ui.parse_voice(text) == {"op": "close"}


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "what's the weather",
        "draft a whatsapp",
        "draft an email",
        "draft a whatsapp message for mum",
        "open project two",
        "open research projects",
        "show the project archive",
        "close the project archive",
        "search for batteries",
        "open youtube",
        "send the draft",
    ],
)
def test_parse_voice_negatives(text: str) -> None:
    assert drafts_ui.parse_voice(text) is None


def _draft(did: str, status: str, created: float) -> dict:
    return {
        "id": did,
        "to": "t@school.edu",
        "subject": "Re: x",
        "body": "b",
        "summary": "s",
        "sender": "T",
        "created": created,
        "status": status,
        "priority": "normal",
    }


def test_pending_drafts_filters_and_sorts_oldest_first(monkeypatch) -> None:
    import draft_engine

    def fake_list(status=None):
        all_rows = [
            _draft("new-live", "announced", 300.0),
            _draft("old-live", "pending", 100.0),
            _draft("mid-live", "pending", 200.0),
            _draft("sent", "sent", 50.0),
            _draft("gone", "discarded", 10.0),
        ]
        if status is None:
            return list(all_rows)
        return [d for d in all_rows if d["status"] == status]

    monkeypatch.setattr(draft_engine, "list_drafts", fake_list)
    rows = drafts_ui.pending_drafts()
    assert [d["id"] for d in rows] == ["old-live", "mid-live", "new-live"]


def _fake_shell(monkeypatch) -> list:
    import projects

    calls: list = []

    def fake(verb: str, run=None) -> bool:
        calls.append(verb)
        return True

    monkeypatch.setattr(projects, "shell_verb", fake)
    return calls


def test_open_drafts_empty_never_calls_shell(monkeypatch) -> None:
    monkeypatch.setattr(drafts_ui, "pending_drafts", lambda: [])
    calls = _fake_shell(monkeypatch)
    say, opened = drafts_ui.open_drafts(
        run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no shell"))
    )
    assert say == "No drafts, Sir."
    assert opened is False
    assert calls == []


def test_open_drafts_nonempty_calls_draftsshow(monkeypatch) -> None:
    rows = [_draft("m1", "pending", 1.0), _draft("m2", "announced", 2.0)]
    monkeypatch.setattr(drafts_ui, "pending_drafts", lambda: rows)
    calls = _fake_shell(monkeypatch)
    say, opened = drafts_ui.open_drafts()
    assert opened is True
    assert calls == ["draftsshow"]
    assert "2" in say


def test_open_drafts_single_count_line(monkeypatch) -> None:
    monkeypatch.setattr(
        drafts_ui, "pending_drafts", lambda: [_draft("m1", "pending", 1.0)]
    )
    _fake_shell(monkeypatch)
    say, opened = drafts_ui.open_drafts()
    assert opened is True
    assert "1" in say or "One" in say


def test_close_drafts_calls_draftshide(monkeypatch) -> None:
    calls = _fake_shell(monkeypatch)
    assert drafts_ui.close_drafts() is True
    assert calls == ["draftshide"]


def test_sync_close_skips_shell_when_drafts_remain(monkeypatch) -> None:
    monkeypatch.setattr(
        drafts_ui, "pending_drafts", lambda: [_draft("m1", "pending", 1.0)]
    )
    calls = _fake_shell(monkeypatch)
    assert drafts_ui.sync_close() is False
    assert calls == []


def test_sync_close_hides_when_empty(monkeypatch) -> None:
    monkeypatch.setattr(drafts_ui, "pending_drafts", lambda: [])
    calls = _fake_shell(monkeypatch)
    assert drafts_ui.sync_close() is True
    assert calls == ["draftshide"]
