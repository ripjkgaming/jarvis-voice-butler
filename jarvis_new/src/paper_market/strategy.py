"""Observed intraday features and risk policy. No model calls or price forecasts."""

from datetime import datetime
from decimal import Decimal

from .calendar import session_at

STOP_LOSS = Decimal("0.01")
TAKE_PROFIT = Decimal("0.02")
ENTRY_CUTOFF_SECONDS = 1800
EXIT_WINDOW_SECONDS = 600


def intraday_context(quote, now):
    session = session_at(now)
    bars = [
        bar
        for bar in quote.bars
        if session.session_open_at is not None
        and datetime.fromisoformat(bar["at"]) >= session.session_open_at
        and (now - datetime.fromisoformat(bar["at"])).total_seconds() >= 60
    ]
    result = {
        "status": "unavailable",
        "completed_bars": len(bars),
        "recent_bars": bars[-20:],
    }
    if len(bars) < 16:
        return result
    latest = datetime.fromisoformat(bars[-1]["at"])
    recent = bars[-16:]
    if (now - latest).total_seconds() > 180 or any(
        (
            datetime.fromisoformat(b["at"]) - datetime.fromisoformat(a["at"])
        ).total_seconds()
        != 60
        for a, b in zip(recent, recent[1:])
    ):
        return result
    volume = sum(bar["volume"] for bar in bars)
    if volume <= 0:
        return result
    close = Decimal(bars[-1]["close"])
    vwap = (
        sum(
            (Decimal(b["high"]) + Decimal(b["low"]) + Decimal(b["close"]))
            / 3
            * b["volume"]
            for b in bars
        )
        / volume
    )

    def pct(old):
        return str(((close / Decimal(old) - 1) * 100).quantize(Decimal("0.0001")))

    result.update(
        status="ready",
        as_of=bars[-1]["at"],
        vwap_usd=str(vwap.quantize(Decimal("0.0001"))),
        momentum_5m_pct=pct(bars[-6]["close"]),
        momentum_15m_pct=pct(bars[-16]["close"]),
        session_return_pct=pct(bars[0]["open"]),
        above_vwap=close > vwap,
        session_high_usd=str(max(Decimal(b["high"]) for b in bars)),
        session_low_usd=str(min(Decimal(b["low"]) for b in bars)),
    )
    return result
