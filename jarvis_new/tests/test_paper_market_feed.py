from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from http.client import IncompleteRead
from io import BytesIO
from urllib.error import URLError
from urllib.request import Request

import pytest

from paper_market import feed

NOW = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)


def payload(**changes):
    meta = {
        "symbol": "AAPL",
        "currency": "USD",
        "exchangeName": "NMS",
        "instrumentType": "EQUITY",
        "regularMarketPrice": 253.25,
        "regularMarketTime": int((NOW - timedelta(seconds=20)).timestamp()),
        "chartPreviousClose": 250,
    }
    meta.update(changes)
    return {"chart": {"error": None, "result": [{"meta": meta}]}}


def quote(**changes):
    values = {
        "symbol": "AAPL",
        "price": Decimal("253.25"),
        "quoted_at": NOW - timedelta(seconds=20),
        "fetched_at": NOW,
    }
    values.update(changes)
    return feed.Quote(**values)


def test_chart_price_uses_real_market_timestamp_and_decimal():
    result = feed.parse_chart_quote(payload(), "AAPL", now=NOW)
    assert result.price == Decimal("253.25")
    assert result.quoted_at == NOW - timedelta(seconds=20)
    assert result.fetched_at == NOW
    assert result.previous_close == Decimal("250")
    assert result.change_percent == Decimal("1.300")
    assert "unofficial" in result.source
    feed.validate_quote(result, NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"symbol": "MSFT"},
        {"currency": "EUR"},
        {"regularMarketPrice": 0},
        {"regularMarketPrice": -1},
        {"regularMarketPrice": "NaN"},
        {"regularMarketPrice": "Infinity"},
        {"regularMarketPrice": True},
        {"regularMarketPrice": None},
        {"regularMarketTime": None},
        {"regularMarketTime": "NaN"},
        {"regularMarketTime": True},
        {"regularMarketTime": float("inf")},
        {"regularMarketTime": int(NOW.timestamp()) + 1},
        {"exchangeName": "PNK"},
        {"instrumentType": "CRYPTOCURRENCY"},
        {"exchangeName": {}},
        {"instrumentType": []},
        {"regularMarketTime": 10**1000},
    ],
)
def test_invalid_provider_metadata_is_rejected(changes):
    with pytest.raises(feed.QuoteError):
        feed.parse_chart_quote(payload(**changes), "AAPL", now=NOW)


@pytest.mark.parametrize(
    "raw",
    [{}, {"chart": {"result": None}}, {"chart": {"error": {"code": "Not Found"}}}],
)
def test_missing_or_error_payload_is_rejected(raw):
    with pytest.raises(feed.QuoteError):
        feed.parse_chart_quote(raw, "AAPL", now=NOW)


def test_old_closed_quote_can_be_displayed_but_cannot_fill():
    closed_now = datetime(2026, 10, 2, 0, tzinfo=timezone.utc)
    previous_close = int(datetime(2026, 10, 1, 20, tzinfo=timezone.utc).timestamp())
    result = feed.parse_chart_quote(
        payload(regularMarketTime=previous_close), "AAPL", now=closed_now
    )
    assert result.quoted_at.timestamp() == previous_close
    with pytest.raises(feed.QuoteError):
        feed.validate_quote(result, closed_now)


def test_freshness_boundary_is_inclusive():
    feed.validate_quote(quote(quoted_at=NOW - timedelta(seconds=120)), NOW)
    with pytest.raises(feed.QuoteError, match="stale"):
        feed.validate_quote(quote(quoted_at=NOW - timedelta(seconds=121)), NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"price": Decimal("NaN")},
        {"price": Decimal("Infinity")},
        {"price": Decimal("-1")},
        {"price": Decimal("0")},
        {"price": 12.5},
        {"symbol": "aapl"},
        {"symbol": "AAPL/evil"},
        {"currency": "CAD"},
        {"quoted_at": NOW + timedelta(seconds=1)},
        {"quoted_at": NOW.replace(tzinfo=None)},
    ],
)
def test_fill_validation_rejects_untrusted_quotes(changes):
    with pytest.raises(feed.QuoteError):
        feed.validate_quote(quote(**changes), NOW)


def test_fresh_quote_must_belong_to_open_regular_session():
    open_now = datetime(2026, 10, 2, 13, 30, 30, tzinfo=timezone.utc)
    prior_to_open = replace(
        quote(), quoted_at=open_now - timedelta(seconds=60), fetched_at=open_now
    )
    with pytest.raises(feed.QuoteError, match="regular session"):
        feed.validate_quote(prior_to_open, open_now)


def test_cannot_fill_a_quote_after_early_close():
    closed_now = datetime(2026, 11, 27, 18, 0, 5, tzinfo=timezone.utc)
    last_quote = quote(
        quoted_at=closed_now - timedelta(seconds=20), fetched_at=closed_now
    )
    with pytest.raises(feed.QuoteError, match="closed"):
        feed.validate_quote(last_quote, closed_now)


def test_fetch_failures_are_reported_per_symbol_and_never_synthesized(monkeypatch):
    requested = []

    def fake_fetch(symbol, *, timeout):
        requested.append(symbol)
        if symbol == "MSFT":
            raise URLError("provider unavailable")
        return payload()

    monkeypatch.setattr(feed, "_fetch_chart", fake_fetch)
    quotes, errors = feed.fetch_quotes(["aapl", "MSFT", "AAPL"], now=NOW)
    assert set(quotes) == {"AAPL"}
    assert set(errors) == {"MSFT"}
    assert set(requested) == {"AAPL", "MSFT"}
    assert len(requested) == 2


def test_bad_requested_symbol_never_reaches_network(monkeypatch):
    monkeypatch.setattr(
        feed, "_fetch_chart", lambda *args, **kwargs: pytest.fail("network")
    )
    quotes, errors = feed.fetch_quotes(["../../evil"], now=NOW)
    assert quotes == {}
    assert "../../evil" in errors


def test_truncated_network_response_is_reported_without_a_price(monkeypatch):
    def incomplete(*args, **kwargs):
        raise IncompleteRead(b"{")

    monkeypatch.setattr(feed, "_fetch_chart", incomplete)
    quotes, errors = feed.fetch_quotes(["AAPL"], now=NOW)
    assert quotes == {}
    assert set(errors) == {"AAPL"}


def test_network_request_uses_only_fixed_public_get_endpoint(monkeypatch):
    observed = {}

    class Opener:
        def open(self, request, timeout):
            observed.update(
                url=request.full_url, method=request.method, timeout=timeout
            )
            return BytesIO(b'{"chart": {"result": null}}')

    def build(handler):
        assert isinstance(handler, feed._NoRedirect)
        return Opener()

    monkeypatch.setattr(feed, "build_opener", build)
    feed._fetch_chart("BRK-B", timeout=5)
    assert observed == {
        "url": "https://query1.finance.yahoo.com/v8/finance/chart/BRK-B?interval=1m&range=1d&includePrePost=false",
        "method": "GET",
        "timeout": 5,
    }


@pytest.mark.parametrize(
    "response", [b"not json", b"x" * (feed.MAX_RESPONSE_BYTES + 1)]
)
def test_bad_or_oversize_network_response_fails_closed(monkeypatch, response):
    class Opener:
        def open(self, request, timeout):
            return BytesIO(response)

    monkeypatch.setattr(feed, "build_opener", lambda handler: Opener())
    with pytest.raises(feed.QuoteError):
        feed._fetch_chart("AAPL", timeout=5)


def test_http_redirects_are_never_followed():
    request = Request("https://query1.finance.yahoo.com/")
    with pytest.raises(feed.QuoteError, match="redirects"):
        feed._NoRedirect().redirect_request(
            request, None, 302, "", {}, "https://example.org"
        )


@pytest.mark.parametrize("maximum_age", [0, -1, 121, float("inf"), float("nan"), True])
def test_freshness_gate_cannot_be_relaxed(maximum_age):
    with pytest.raises(feed.QuoteError):
        feed.validate_quote(quote(), NOW, max_age_seconds=maximum_age)


def test_more_than_fifty_symbols_fails_before_network(monkeypatch):
    monkeypatch.setattr(
        feed, "_fetch_chart", lambda *args, **kwargs: pytest.fail("network")
    )
    with pytest.raises(ValueError, match="50"):
        feed.fetch_quotes(["AAPL"] * 51, now=NOW)


@pytest.mark.parametrize(
    "price",
    [
        "1e100000000",
        "1e-100000000",
        "10000000.00000001",
        "0.000000001",
        "10.000000001",
        "1." + "0" * 100 + "1",
        10**1000,
    ],
)
def test_extreme_or_overprecise_prices_are_rejected_before_arithmetic(price):
    with pytest.raises(feed.QuoteError):
        feed.parse_chart_quote(payload(regularMarketPrice=price), "AAPL", now=NOW)
    with pytest.raises(feed.QuoteError):
        feed.validate_quote(quote(price=Decimal(str(price))), NOW)


@pytest.mark.parametrize(
    "price", ["0.00000001", "10000000", "9999999.99999999", "330.3200000000"]
)
def test_supported_price_boundaries_and_insignificant_zeros(price):
    result = feed.parse_chart_quote(payload(regularMarketPrice=price), "AAPL", now=NOW)
    assert result.price == Decimal(price)
    feed.validate_quote(result, NOW)


def test_bad_previous_close_is_omitted_without_synthesizing_a_signal():
    result = feed.parse_chart_quote(
        payload(chartPreviousClose="1e-100000000"), "AAPL", now=NOW
    )
    assert result.previous_close is None
    assert result.change_percent is None


def test_display_mark_validator_permits_staleness_but_checks_numeric_safety():
    old = quote(quoted_at=NOW - timedelta(days=1))
    feed.validate_mark_quote(old, NOW)
    with pytest.raises(feed.QuoteError):
        feed.validate_mark_quote(replace(old, price=Decimal("1e100000000")), NOW)


def test_invalid_injected_previous_close_cannot_crash_signal_display():
    assert quote(previous_close=Decimal("1e-100000000")).change_percent is None
