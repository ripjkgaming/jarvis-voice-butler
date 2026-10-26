"""Regression coverage for damaged digest input and bounded reads."""

import json

import pytest

import mail_log


def test_record_bad_timestamp_never_breaks_mail(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    mail_log.record("sent", ts="invalid", subject="Sent successfully")


def test_recent_skips_damaged_timestamp_and_keeps_older_events(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    entries = [
        {"ts": 100, "kind": "sent", "subject": "Valid"},
        {"ts": "damaged", "kind": "sent"},
        {"ts": [], "kind": "sent"},
    ]
    mail_log.path().write_text("\n".join(json.dumps(e) for e in entries))
    assert [e["subject"] for e in mail_log.recent()] == ["Valid"]


@pytest.mark.parametrize("limit", [0, -1])
def test_recent_nonpositive_limit_returns_no_events(monkeypatch, tmp_path, limit):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    mail_log.record("sent", ts=100)
    assert mail_log.recent(limit=limit) == []


def test_record_preserves_explicit_zero_timestamp(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    mail_log.record("sent", ts=0)
    assert mail_log.recent()[0]["ts"] == 0
