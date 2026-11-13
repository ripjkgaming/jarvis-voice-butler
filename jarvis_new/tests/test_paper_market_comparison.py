from datetime import timedelta
from decimal import Decimal

import pytest
from test_paper_market_engine import NOW, decision, proposal, quotes

from paper_market.comparison import Comparison, ReadinessError
from paper_market.engine import TradeError, apply_proposal


def test_prepare_does_not_start_the_clock_or_touch_previous_experiment(tmp_path):
    comparison = Comparison(tmp_path)
    prepared = comparison.prepare()
    assert prepared["started_at"] is None
    assert not list(tmp_path.glob("comparison/*/*/ledger.sqlite3"))
    assert comparison.snapshot(NOW)["status"] == "preparing"
    with pytest.raises(ReadinessError):
        comparison.start(NOW)


def ready():
    return {"ready": True, "checks": {"test_integrations": True}, "blockers": []}


def test_accounts_isolated_shared_deadline_and_restart(tmp_path):
    c = Comparison(tmp_path, readiness=ready)
    c.prepare()
    manifest = c.start(NOW)
    assert c.start(NOW + timedelta(days=1)) == manifest
    accounts = c.ledgers()
    assert set(accounts) == {"codex", "copy", "bot"}
    for ledger in accounts.values():
        exp = ledger.experiment()
        assert exp["started_at"] == manifest["started_at"]
        assert exp["expires_at"] == manifest["expires_at"]
        assert Decimal(exp["cash"]) == 1000
        assert timedelta(hours=168) == (
            __import__("datetime").datetime.fromisoformat(exp["expires_at"]) - NOW
        )
    ledger = accounts["codex"]
    apply_proposal(ledger, decision(ledger), proposal(), quotes(), NOW)
    assert Decimal(ledger.experiment()["cash"]) < 1000
    assert accounts["copy"].experiment()["cash"] == "1000.00"
    assert accounts["bot"].experiment()["cash"] == "1000.00"
    assert Comparison(tmp_path).ledgers()["codex"].experiment() == ledger.experiment()


def test_expiry_cancels_and_freezes_results_without_liquidation(tmp_path):
    c = Comparison(tmp_path, readiness=ready)
    c.prepare()
    c.start(NOW)
    ledger = c.ledgers()["codex"]
    apply_proposal(ledger, decision(ledger), proposal(), quotes(), NOW)
    ledger.begin_decision("late", NOW + timedelta(seconds=1), {})
    cutoff = NOW + timedelta(hours=168)
    c.finalize(cutoff, vault=tmp_path / "vault")
    frozen = c.snapshot(cutoff)
    assert frozen == c.snapshot(cutoff + timedelta(days=1))
    codex = frozen["portfolios"][0]
    assert codex["holdings"]
    assert codex["decisions"][0]["status"] == "cancelled"
    assert not codex["account"]["valuation_fresh"]
    with pytest.raises(TradeError):
        apply_proposal(ledger, "late", proposal(), quotes(cutoff), cutoff)
    assert list((tmp_path / "vault").glob("Paper Market/*.md"))


def test_new_preparation_preserves_previous_comparison(tmp_path):
    c = Comparison(tmp_path, readiness=ready)
    c.prepare()
    old = c.start(NOW)
    with pytest.raises(ReadinessError):
        c.prepare(new=True)
    c.finalize(NOW + timedelta(hours=168), vault=tmp_path / "vault")
    new = c.prepare(new=True)
    assert new["id"] != old["id"]
    assert (c.root / old["id"] / "final.json").exists()


def _evidence(c, name, ready_=True, blockers=()):
    import json

    (c.root / f"readiness-{name}.json").write_text(
        json.dumps({"ready": ready_, "blockers": list(blockers)})
    )


def _all_but_copy(c):
    for name in ("news", "models", "bot", "trial"):
        _evidence(c, name)
    _evidence(c, "copy", False, ["Collective2 API4 credentials are not configured"])


def test_copy_gated_launch_runs_codex_and_bot_only(tmp_path):
    c = Comparison(tmp_path)
    c.prepare()
    _all_but_copy(c)
    with pytest.raises(ReadinessError, match="credentials"):
        c.start(NOW)  # not gated: copy blocks the launch
    manifest = c.start(NOW, gate=("copy",))
    assert set(c.ledgers()) == {"codex", "bot"}
    assert manifest["gated"] == {"copy": ["blocked: needs credentials"]}
    assert not (c.root / manifest["id"] / "copy").exists()
    snap = c.snapshot(NOW + timedelta(hours=1))
    copy = next(p for p in snap["portfolios"] if p["id"] == "copy")
    assert copy["blocked"] is True
    assert copy["blocked_reason"] == "blocked: needs credentials"
    assert copy["fills"] == [] and copy["account"]["trade_count"] == 0
    assert snap["expires_at"] == manifest["expires_at"]
    assert set(snap["usage_costs"]["tokens_by_portfolio"]) == {"codex", "copy", "bot"}


def test_a_ready_portfolio_cannot_be_gated_out(tmp_path):
    c = Comparison(tmp_path)
    c.prepare()
    _all_but_copy(c)
    with pytest.raises(ReadinessError, match="cannot be gated"):
        c.start(NOW, gate=("copy", "bot"))


def test_stage_latency_summary(tmp_path):
    from paper_market.news import NewsStore

    store = NewsStore(tmp_path / "news.sqlite3")
    for ms in (100, 200, 300):
        store.record_latency("inference:codex", ms)
    store.record_latency("inference:codex", 5, outcome="failed")
    summary = store.latency_summary()["inference:codex"]
    assert summary["count"] == 3 and summary["failed"] == 1
    assert summary["p50_ms"] == 200 and summary["max_ms"] == 300


def test_service_json_ignores_client_disconnect():
    from paper_market import service

    handler = service.handler_for(None, None)
    h = handler.__new__(handler)

    class Broken:
        def write(self, raw):
            raise BrokenPipeError

    h.wfile = Broken()
    h.send_response = h.send_header = h.end_headers = lambda *a, **k: None
    h._json(200, {"ok": True})
    assert h.close_connection is True
