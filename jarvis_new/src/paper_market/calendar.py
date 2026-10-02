"""Bounded NYSE/Nasdaq regular-equity schedule, never inferred past 2028.

The holiday and early-close dates below were checked on 2026-10-02 against
https://www.nyse.com/markets/hours-calendars. Times use America/New_York,
including daylight saving changes. This is the published calendar; emergency
exchange closures and individual trading halts are not announced by it.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

CALENDAR_SOURCE = "https://www.nyse.com/markets/hours-calendars"
CALENDAR_VERIFIED_ON = "2026-10-02"
SUPPORTED_YEARS = frozenset({2026, 2027, 2028})
EASTERN = ZoneInfo("America/New_York")

_HOLIDAYS = frozenset(
    date.fromisoformat(day)
    for day in (
        "2026-01-01",
        "2026-01-19",
        "2026-02-16",
        "2026-04-03",
        "2026-05-25",
        "2026-06-19",
        "2026-07-03",
        "2026-09-07",
        "2026-11-26",
        "2026-12-25",
        "2027-01-01",
        "2027-01-18",
        "2027-02-15",
        "2027-03-26",
        "2027-05-31",
        "2027-06-18",
        "2027-07-05",
        "2027-09-06",
        "2027-11-25",
        "2027-12-24",
        # NYSE explicitly does not observe Saturday 2028-01-01 on Friday.
        "2028-01-17",
        "2028-02-21",
        "2028-04-14",
        "2028-05-29",
        "2028-06-19",
        "2028-07-04",
        "2028-09-04",
        "2028-11-23",
        "2028-12-25",
    )
)
_EARLY_CLOSES = frozenset(
    date.fromisoformat(day)
    for day in (
        "2026-11-27",
        "2026-12-24",
        "2027-11-26",
        "2028-07-03",
        "2028-11-24",
    )
)


@dataclass(frozen=True)
class MarketSession:
    is_open: bool
    status: str
    session_open_at: datetime | None
    session_close_at: datetime | None
    next_open_at: datetime | None
    reason: str
    calendar_source: str = CALENDAR_SOURCE

    def to_dict(self) -> dict:
        return {
            "is_open": self.is_open,
            "status": self.status,
            "session_open_at": _iso(self.session_open_at),
            "session_close_at": _iso(self.session_close_at),
            "next_open_at": _iso(self.next_open_at),
            "reason": self.reason,
            "calendar_source": self.calendar_source,
            "calendar_verified_on": CALENDAR_VERIFIED_ON,
            "supported_years": sorted(SUPPORTED_YEARS),
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _bounds(day: date) -> tuple[datetime, datetime] | None:
    if day.year not in SUPPORTED_YEARS or day.weekday() >= 5 or day in _HOLIDAYS:
        return None
    opening = datetime.combine(day, time(9, 30), EASTERN)
    closing = datetime.combine(day, time(13 if day in _EARLY_CLOSES else 16), EASTERN)
    return opening.astimezone(timezone.utc), closing.astimezone(timezone.utc)


def _next_open(now: datetime, day: date) -> datetime | None:
    # All published holiday gaps fit within this bounded lookahead. A date
    # beyond the verified calendar is intentionally not guessed.
    for offset in range(10):
        candidate = day + timedelta(days=offset)
        if candidate.year not in SUPPORTED_YEARS:
            return None
        bounds = _bounds(candidate)
        if bounds is not None and bounds[0] > now:
            return bounds[0]
    return None


def session_at(now: datetime | None = None) -> MarketSession:
    """Return the regular session at an aware instant, with UTC boundaries.

    Opening is inclusive and closing is exclusive. ``next_open_at`` is the
    next opening strictly after ``now``; when already open it is a later day.
    Unsupported calendar years fail closed with no predicted next opening.
    """
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("market clock must be a timezone-aware datetime")
    now = now.astimezone(timezone.utc)
    day = now.astimezone(EASTERN).date()
    if day.year not in SUPPORTED_YEARS:
        return MarketSession(
            False,
            "unsupported",
            None,
            None,
            None,
            "Trading disabled: the verified exchange calendar supports 2026-2028 only.",
        )
    bounds = _bounds(day)
    next_open = _next_open(now, day)
    if bounds is None:
        weekend = day.weekday() >= 5
        return MarketSession(
            False,
            "weekend" if weekend else "holiday",
            None,
            None,
            next_open,
            "Regular market closed for the weekend."
            if weekend
            else "Regular market closed for an exchange holiday.",
        )
    opening, closing = bounds
    if opening <= now < closing:
        return MarketSession(
            True,
            "open",
            opening,
            closing,
            next_open,
            "Regular market open; early close at 13:00 Eastern."
            if day in _EARLY_CLOSES
            else "Regular market open, 09:30-16:00 Eastern.",
        )
    before_open = now < opening
    return MarketSession(
        False,
        "pre_market" if before_open else "after_hours",
        opening,
        closing,
        next_open,
        "Regular market has not opened."
        if before_open
        else "Regular market closed; extended-hours fills are disabled.",
    )
