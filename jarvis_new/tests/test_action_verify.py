import os
from pathlib import Path

import pytest

from action_outcome import parse_outcome
from system.action_verify import write_verified


def test_write_matches_utf8_bytes(tmp_path):
    result = write_verified(tmp_path / "a" / "notes.txt", "line\n☃", roots=(tmp_path,))
    outcome = parse_outcome(result)
    assert outcome.verified and outcome.evidence["bytes"] == len("line\n☃".encode())
    assert (tmp_path / "a" / "notes.txt").read_text() == "line\n☃"
    assert "line" not in str(result)


@pytest.mark.parametrize("text", ["", "abc"])
def test_empty_and_existing_write(tmp_path, text):
    target = tmp_path / "note"
    target.write_text("old")
    assert parse_outcome(write_verified(target, text, roots=(tmp_path,))).verified
    assert target.read_text() == text


def test_limits_and_outside_are_blocked(tmp_path):
    assert parse_outcome(
        write_verified(tmp_path / "large", "a" * 262145, roots=(tmp_path,))
    ).retry_safe
    assert not (tmp_path / "large").exists()
    assert (
        parse_outcome(write_verified(Path("/outside"), "", roots=(tmp_path,))).state
        == "blocked"
    )


def test_symlink_directory_and_target_refused(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    assert not parse_outcome(
        write_verified(tmp_path / "link" / "x", "x", roots=(tmp_path,))
    ).verified
    target = tmp_path / "real" / "x"
    target.write_text("old")
    (tmp_path / "linked").symlink_to(target)
    assert not parse_outcome(
        write_verified(tmp_path / "linked", "x", roots=(tmp_path,))
    ).verified
    assert target.read_text() == "old"


def test_hardlink_refused(tmp_path):
    target = tmp_path / "a"
    target.write_text("old")
    os.link(target, tmp_path / "b")
    assert not parse_outcome(write_verified(target, "new", roots=(tmp_path,))).verified
    assert target.read_text() == "old"


def test_revoked_before_effect_never_creates(tmp_path):
    def denied():
        raise PermissionError("private authority")

    result = write_verified(
        tmp_path / "new" / "a", "x", roots=(tmp_path,), authorize=denied
    )
    assert parse_outcome(result).state == "blocked"
    assert not (tmp_path / "new").exists()
    assert "private authority" not in str(result)


def test_read_failure_after_write_is_unverified(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "pread", lambda *a: (_ for _ in ()).throw(OSError()))
    result = write_verified(tmp_path / "a", "x", roots=(tmp_path,))
    assert parse_outcome(result).state == "dispatched_unverified"
    assert not parse_outcome(result).retry_safe


def test_replaced_destination_never_verifies(tmp_path, monkeypatch):
    target = tmp_path / "a"
    original = os.write

    def write(fd, data):
        n = original(fd, data)
        target.unlink()
        target.write_text("other")
        return n

    monkeypatch.setattr(os, "write", write)
    out = parse_outcome(write_verified(target, "x", roots=(tmp_path,)))
    assert out.state == "failed" and out.check == "mismatched"
    assert not out.retry_safe


def test_parent_replacement_never_verifies_original_path(tmp_path, monkeypatch):
    parent = tmp_path / "dir"
    parent.mkdir()
    target = parent / "a"
    original = os.write

    def write(fd, data):
        n = original(fd, data)
        parent.rename(tmp_path / "moved")
        parent.mkdir()
        target.write_text("x")
        return n

    monkeypatch.setattr(os, "write", write)
    out = parse_outcome(write_verified(target, "x", roots=(tmp_path,)))
    assert not out.verified and not out.retry_safe
