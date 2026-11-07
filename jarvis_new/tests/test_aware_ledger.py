"""Always Aware budget ledger (PLAN.md §1.4): 240 calls, 1.0 M tokens/day."""

import json
import os

import pytest

from aware import ledger

DAY = 86_400.0
T0 = 1_780_000_000.0


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_AWARE_DAILY_CALLS", raising=False)
    monkeypatch.delenv("JARVIS_AWARE_DAILY_TOKENS", raising=False)
    return tmp_path


def test_caps_default_env_and_invalid():
    assert ledger.caps({}) == (240, 1_000_000)
    env = {"JARVIS_AWARE_DAILY_CALLS": "10", "JARVIS_AWARE_DAILY_TOKENS": "5000"}
    assert ledger.caps(env) == (10, 5000)
    bad = {"JARVIS_AWARE_DAILY_CALLS": "x", "JARVIS_AWARE_DAILY_TOKENS": "-3"}
    assert ledger.caps(bad) == (240, 1_000_000)


def test_record_accumulates_persists_and_is_private(_home):
    ledger.record(1200, now=T0)
    row = ledger.record(800, now=T0 + 60)
    assert row == {"calls": 2, "tokens": 2000}
    assert ledger.today(T0) == {"calls": 2, "tokens": 2000}
    path = _home / "aware" / "budget.json"
    assert (os.stat(path).st_mode & 0o777) == 0o600
    assert ledger.record(-5, now=T0)["tokens"] == 2000


def test_days_are_separate():
    ledger.record(100, now=T0)
    ledger.record(50, now=T0 + DAY)
    assert ledger.today(T0)["tokens"] == 100
    assert ledger.today(T0 + DAY)["tokens"] == 50


@pytest.mark.parametrize(
    "calls,tokens,want",
    [
        (167, 0, "ok"),
        (168, 0, "active_only"),
        (0, 699_999, "ok"),
        (0, 700_000, "active_only"),
        (240, 0, "stop"),
        (0, 1_000_000, "stop"),
    ],
)
def test_level_thresholds(calls, tokens, want):
    assert ledger.level_for({"calls": calls, "tokens": tokens}, 240, 1_000_000) == want


def test_level_reads_file_and_summary():
    env = {"JARVIS_AWARE_DAILY_CALLS": "2"}
    ledger.record(10, now=T0)
    assert ledger.level(T0, env) == "ok"
    ledger.record(10, now=T0)
    assert ledger.level(T0, env) == "stop"
    s = ledger.summary(T0, env)
    assert s["calls"] == 2 and s["call_cap"] == 2 and s["level"] == "stop"


def test_prune_keeps_fourteen_days(_home):
    for i in range(20):
        ledger.record(1, now=T0 + i * DAY)
    data = json.loads((_home / "aware" / "budget.json").read_text())
    assert len(data) == 14
    assert ledger.today(T0 + 19 * DAY)["calls"] == 1


def test_corrupt_file_is_empty(_home):
    (_home / "aware").mkdir(exist_ok=True)
    (_home / "aware" / "budget.json").write_text("{nope")
    assert ledger.today(T0) == {"calls": 0, "tokens": 0}
    assert ledger.record(5, now=T0)["calls"] == 1
