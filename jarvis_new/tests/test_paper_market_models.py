"""Fixed model routing and TradingAgents news cache coverage."""

import json
from pathlib import Path

from paper_market import EFFORT, MODEL
from paper_market.ta_runner import (
    DEEP_MODEL,
    QUICK_MODEL,
    _news_signature,
    _PaperNewsContext,
    cached_signals,
)
from paper_market.worker import RUNTIME_EFFORT, RUNTIME_MODEL


def test_paper_market_runtime_is_fixed_to_sol_medium():
    assert (MODEL, EFFORT) == ("gpt-6.1-sol", "medium")
    assert (RUNTIME_MODEL, RUNTIME_EFFORT) == ("gpt-6.1-sol", "medium")
    assert DEEP_MODEL == QUICK_MODEL == "gpt-6.1-sol:medium"


def test_tradingagents_cli_route_pins_model_and_effort():
    source = (Path(__file__).parents[1] / "src/paper_market/codex_llm.py").read_text()
    assert 'self.model = "gpt-6.1-sol:medium"' in source
    assert '"-m",\n                self.model' in source
    assert 'f"model_reasoning_effort={self.effort}"' in source


def test_news_revision_changes_cache_key_and_includes_context(tmp_path):
    first = [{"id": "article-7", "symbols": ["AAPL"], "content": "old"}]
    revision = [{"id": "article-7", "symbols": ["AAPL"], "content": "updated"}]
    assert _news_signature(first) != _news_signature(revision)
    context = _PaperNewsContext(
        cash="950.00",
        holdings=[{"symbol": "AAPL", "quantity": "1", "average_cost_usd": "50"}],
        news=[
            {
                "id": "article-7",
                "symbols": ["AAPL"],
                "source": "Ironsight",
                "published_at": "2026-11-12T09:00:00Z",
                "summary": "Company update",
                "reason": "May affect revenue",
                "content": "Article text",
            }
        ],
    )
    rendered = context.render("AAPL")
    assert "Article text" in rendered
    assert "Ironsight" in rendered
    assert "AAPL" in rendered
    assert context.fingerprint()
    (tmp_path / "ta").mkdir()
    old_cache = tmp_path / "ta" / f"signals-2026-11-12-{_news_signature(first)}.json"
    old_cache.write_text(json.dumps({"AAPL": {"rating": "Hold"}}))
    assert cached_signals(tmp_path, "2026-11-12", first) == {"AAPL": {"rating": "Hold"}}
    assert cached_signals(tmp_path, "2026-11-12", revision) == {}
