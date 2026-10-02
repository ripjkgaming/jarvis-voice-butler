"""Read-only, unauthenticated Yahoo chart prices for the local paper ledger.

Yahoo Finance is an unofficial best-effort source and may delay, throttle, or
stop returning quotes. Failed requests never produce replacement prices.
Fetching permits historical prices for display. Every simulated fill must
call ``validate_quote`` again with the actual fill time.
"""

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import quote as url_quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .calendar import session_at

SOURCE = "Yahoo Finance (unofficial)"
MAX_QUOTE_AGE_SECONDS = 120
MAX_SYMBOLS = 50
MAX_RESPONSE_BYTES = 1_000_000
MIN_PRICE_USD = Decimal("0.00000001")
MAX_PRICE_USD = Decimal("10000000")
MAX_PRICE_DECIMAL_PLACES = 8
MAX_PRICE_TEXT_LENGTH = 64
_SYMBOL = re.compile(r"[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,3})?\Z")
_EXCHANGES = frozenset({"NYQ", "NMS", "NGM", "NCM", "PCX", "ASE", "BTS", "BATS"})


class QuoteError(ValueError):
    """A quote cannot safely be used for its requested purpose."""


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    quoted_at: datetime
    source: str = SOURCE
    fetched_at: datetime | None = None
    currency: str = "USD"
    previous_close: Decimal | None = None

    @property
    def change_percent(self) -> Decimal | None:
        try:
            current, previous = _price(self.price), _price(self.previous_close)
        except QuoteError:
            return None
        return (current / previous - 1) * 100

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "price": str(self.price),
            "quoted_at": self.quoted_at.isoformat(),
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "source": self.source,
            "currency": self.currency,
            "previous_close": str(self.previous_close)
            if self.previous_close is not None
            else None,
            "change_percent": str(self.change_percent)
            if self.change_percent is not None
            else None,
        }


def normalize_symbol(symbol: str) -> str:
    if not isinstance(symbol, str):
        raise QuoteError("symbol must be a string")
    normalized = symbol.strip().upper()
    if not _SYMBOL.fullmatch(normalized):
        raise QuoteError("invalid US equity symbol")
    return normalized


def _aware(value: datetime, label: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise QuoteError(f"{label} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _price(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise QuoteError("price must be a positive finite number")
    try:
        text = str(value)
        if len(text) > MAX_PRICE_TEXT_LENGTH:
            raise QuoteError("price representation exceeds the supported length")
        parsed = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise QuoteError("price must be a positive finite number") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise QuoteError("price must be a positive finite number")
    # Comparisons do not expand a Decimal's exponent; reject extremes before
    # normalization, formatting as fixed point, division, or ledger arithmetic.
    if not MIN_PRICE_USD <= parsed <= MAX_PRICE_USD:
        raise QuoteError("price must be between USD 0.00000001 and 10000000")
    sign, digits, exponent = parsed.as_tuple()
    # Strip only insignificant zeroes, exactly, without Decimal.normalize(),
    # which can round against the caller's arithmetic context.
    while digits[-1] == 0:
        digits = digits[:-1]
        exponent += 1
    if exponent < -MAX_PRICE_DECIMAL_PLACES:
        raise QuoteError("price supports at most eight fractional decimal places")
    return Decimal((sign, digits, exponent))


def _validate_structure(quote: Quote, now: datetime) -> None:
    if not isinstance(quote, Quote):
        raise QuoteError("expected a timestamped Quote")
    if normalize_symbol(quote.symbol) != quote.symbol:
        raise QuoteError("quote symbol must be canonical uppercase")
    if quote.currency != "USD":
        raise QuoteError("only USD quotes are supported")
    if not isinstance(quote.price, Decimal):
        raise QuoteError("quote price must use Decimal")
    _price(quote.price)
    if quote.previous_close is not None:
        if not isinstance(quote.previous_close, Decimal):
            raise QuoteError("previous close must use Decimal")
        _price(quote.previous_close)
    quoted_at = _aware(quote.quoted_at, "quote timestamp")
    if quoted_at > now:
        raise QuoteError("quote timestamp is in the future")
    if quote.fetched_at is not None:
        fetched_at = _aware(quote.fetched_at, "fetch timestamp")
        if fetched_at > now or quoted_at > fetched_at:
            raise QuoteError("inconsistent quote/fetch timestamp")
    if not isinstance(quote.source, str) or not quote.source.strip():
        raise QuoteError("quote source is required")


def validate_mark_quote(quote: Quote, now: datetime | None = None) -> None:
    """Validate a safe timestamped display mark, allowing stale/closed quotes.

    This checks the same numeric, identity and timestamp constraints as the
    fill validator. It does not authorize trading or invent a new timestamp.
    """
    now = _aware(datetime.now(timezone.utc) if now is None else now, "market clock")
    _validate_structure(quote, now)


def validate_quote(
    quote: Quote,
    now: datetime | None = None,
    *,
    max_age_seconds: float = MAX_QUOTE_AGE_SECONDS,
    require_open: bool = True,
) -> None:
    """Raise QuoteError unless a quote is fresh and eligible for a paper fill.

    ``require_open=False`` permits a freshness check outside regular hours,
    but must not be used to authorize fills. Callers can tighten, never relax,
    the hard 120-second quote-age limit.
    """
    now = _aware(datetime.now(timezone.utc) if now is None else now, "market clock")
    _validate_structure(quote, now)
    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or not math.isfinite(max_age_seconds)
        or not 0 < max_age_seconds <= MAX_QUOTE_AGE_SECONDS
    ):
        raise QuoteError("quote freshness limit must be within 0-120 seconds")
    if (now - quote.quoted_at).total_seconds() > max_age_seconds:
        raise QuoteError(
            "quote is stale; a real quote no older than 120 seconds is required"
        )
    if require_open:
        current = session_at(now)
        if not current.is_open:
            raise QuoteError(f"regular market is closed: {current.reason}")
        quoted_session = session_at(quote.quoted_at)
        if (
            not quoted_session.is_open
            or quoted_session.session_open_at != current.session_open_at
        ):
            raise QuoteError("quote is outside the current regular session")


def parse_chart_quote(
    payload: object, symbol: str, *, now: datetime | None = None
) -> Quote:
    """Parse Yahoo metadata without replacing its market timestamp with now.

    Stale and prior-session quotes remain useful as explicitly timestamped
    display marks; ``validate_quote`` is the separate fill-eligibility gate.
    """
    symbol = normalize_symbol(symbol)
    now = _aware(datetime.now(timezone.utc) if now is None else now, "market clock")
    if not isinstance(payload, dict) or not isinstance(payload.get("chart"), dict):
        raise QuoteError("invalid chart response")
    chart = payload["chart"]
    if chart.get("error") is not None:
        raise QuoteError("market data provider returned an error")
    results = chart.get("result")
    if (
        not isinstance(results, list)
        or len(results) != 1
        or not isinstance(results[0], dict)
    ):
        raise QuoteError("market data provider returned no unambiguous quote")
    meta = results[0].get("meta")
    if not isinstance(meta, dict):
        raise QuoteError("quote metadata is missing")
    if meta.get("symbol") != symbol:
        raise QuoteError("provider quote symbol does not match requested symbol")
    if meta.get("currency") != "USD":
        raise QuoteError("only USD quotes are supported")
    exchange = meta.get("exchangeName")
    if not isinstance(exchange, str) or exchange not in _EXCHANGES:
        raise QuoteError("quote is not from a supported US equity exchange")
    instrument = meta.get("instrumentType")
    if not isinstance(instrument, str) or instrument not in {"EQUITY", "ETF"}:
        raise QuoteError("only US equities and ETFs are supported")
    timestamp = meta.get("regularMarketTime")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        raise QuoteError("provider market timestamp is missing or invalid")
    if (
        isinstance(timestamp, float) and not math.isfinite(timestamp)
    ) or timestamp <= 0:
        raise QuoteError("provider market timestamp is missing or invalid")
    try:
        quoted_at = datetime.fromtimestamp(timestamp, timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise QuoteError("provider market timestamp is out of range") from exc
    previous_close = None
    if meta.get("chartPreviousClose") is not None:
        # An absent comparison point never makes a signal up.
        with suppress(QuoteError):
            previous_close = _price(meta["chartPreviousClose"])
    result = Quote(
        symbol=symbol,
        price=_price(meta.get("regularMarketPrice")),
        quoted_at=quoted_at,
        fetched_at=now,
        previous_close=previous_close,
    )
    _validate_structure(result, now)
    return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise QuoteError("market data redirects are disabled")


def _fetch_chart(symbol: str, *, timeout: float) -> object:
    # The hostname and route are fixed. No keys, cookies, credentials, or order
    # endpoints are used; this module only performs public-data GET requests.
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{url_quote(symbol, safe='')}?interval=1m&range=1d&includePrePost=false"
    )
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 JarvisPaperMarket/1.0",
            "Accept": "application/json",
        },
        method="GET",
    )
    with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise QuoteError("market data response exceeded the size limit")
    try:
        return json.loads(raw, parse_float=Decimal)
    except (ValueError, UnicodeError, RecursionError, InvalidOperation) as exc:
        raise QuoteError("market data response was not valid JSON") from exc


def fetch_quotes(
    symbols,
    *,
    now: datetime | None = None,
    timeout: float = 8.0,
) -> tuple[dict[str, Quote], dict[str, str]]:
    """Synchronously fetch up to 50 symbols with four bounded concurrent GETs.

    Return ``(quotes, errors)`` by symbol, including old quotes for display.
    There are no retries, fabricated prices, or credentials. An async caller
    should use ``asyncio.to_thread(fetch_quotes, symbols)``. ``now`` is only
    for a controlled clock; by default each fetch records its completion time.
    """
    if now is not None:
        _aware(now, "market clock")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or not 0 < timeout <= 15
    ):
        raise ValueError("market data timeout must be within 0-15 seconds")
    if isinstance(symbols, str):
        symbols = [symbols]
    requested = []
    errors = {}
    for index, value in enumerate(symbols):
        if index >= MAX_SYMBOLS:
            raise ValueError(f"at most {MAX_SYMBOLS} symbols may be requested")
        try:
            symbol = normalize_symbol(value)
        except QuoteError as exc:
            errors[str(value)] = str(exc)
            continue
        if symbol not in requested:
            requested.append(symbol)
    quotes = {}
    if not requested:
        return quotes, errors

    def fetch_one(symbol: str) -> Quote:
        return parse_chart_quote(_fetch_chart(symbol, timeout=timeout), symbol, now=now)

    with ThreadPoolExecutor(max_workers=min(4, len(requested))) as pool:
        futures = {pool.submit(fetch_one, symbol): symbol for symbol in requested}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                quotes[symbol] = future.result()
            except QuoteError as exc:
                errors[symbol] = str(exc)
            except HTTPError as exc:
                errors[symbol] = f"market data unavailable (HTTP {exc.code})"
            except (URLError, TimeoutError, OSError, HTTPException):
                errors[symbol] = "market data request failed or timed out"
    return quotes, errors
