import datetime
import json

import pytest

import system.budget as budget_mod
from system.budget import (
    budget_limit_minutes,
    load_ledger,
    month_key,
    record_session,
    status,
)


def test_month_key_format() -> None:
    assert month_key(datetime.date(2026, 9, 14)) == "2026-09"


def test_budget_limit_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_MINUTE_BUDGET", raising=False)
    assert budget_limit_minutes() == 800
    monkeypatch.setenv("JARVIS_MINUTE_BUDGET", "100")
    assert budget_limit_minutes() == 100
    monkeypatch.setenv("JARVIS_MINUTE_BUDGET", "junk")
    assert budget_limit_minutes() == 800


def test_record_and_status_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(budget_mod, "LEDGER_PATH", tmp_path / "minutes.json")
    monkeypatch.setenv("JARVIS_MINUTE_BUDGET", "100")
    assert status()["used_minutes"] == 0.0
    record_session(90)
    record_session(30)
    result = status()
    assert result["used_minutes"] == 2.0
    assert result["limit_minutes"] == 100.0
    assert result["pct"] == pytest.approx(0.02)
    stored = json.loads((tmp_path / "minutes.json").read_text())
    assert stored[month_key()] == 120.0


def test_status_ignores_other_months(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(budget_mod, "LEDGER_PATH", tmp_path / "minutes.json")
    (tmp_path / "minutes.json").write_text(json.dumps({"2000-01": 99999}))
    assert status()["used_minutes"] == 0.0


def test_corrupt_ledger_never_raises(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    ledger = tmp_path / "minutes.json"
    ledger.write_text("not json {{{")
    monkeypatch.setattr(budget_mod, "LEDGER_PATH", ledger)
    assert load_ledger() == {}
    assert status()["used_minutes"] == 0.0
    record_session(60)
    assert status()["used_minutes"] == 1.0
