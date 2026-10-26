"""Replacement expectations for the intentionally retired five-minute watcher."""

import pytest

import goodnight


def test_legacy_watcher_cannot_arm_or_lock():
    assert not goodnight.enabled()
    assert not goodnight.is_goodnight("goodnight")
    assert goodnight.decide(10000, None, None) == "hold"
    assert goodnight.watch("old-token") == "retired"
    with pytest.raises(RuntimeError):
        goodnight.arm()


def test_legacy_cancellation_is_explicit_and_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    state = tmp_path / "goodnight.json"
    state.write_text("{}")
    assert goodnight.pending()
    assert goodnight.cancel()
    assert not state.exists()
