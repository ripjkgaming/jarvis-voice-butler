from datetime import datetime, timezone

import pytest

from paper_market.calendar import session_at


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def test_current_closed_session_has_correct_singapore_open():
    session = session_at(utc("2026-10-02T00:00:00"))
    assert not session.is_open
    assert session.status == "after_hours"
    assert session.next_open_at == utc("2026-10-02T13:30:00")
    assert session.session_close_at == utc("2026-10-01T20:00:00")


@pytest.mark.parametrize(
    ("now", "opened"),
    [
        ("2026-10-02T13:29:59", False),
        ("2026-10-02T13:30:00", True),
        ("2026-10-02T19:59:59", True),
        ("2026-10-02T20:00:00", False),
    ],
)
def test_regular_session_boundaries(now, opened):
    assert session_at(utc(now)).is_open is opened


def test_winter_session_uses_eastern_standard_time():
    session = session_at(utc("2026-12-01T14:00:00"))
    assert not session.is_open
    assert session.session_open_at == utc("2026-12-01T14:30:00")
    assert session.session_close_at == utc("2026-12-01T21:00:00")


@pytest.mark.parametrize(
    "day",
    [
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
        "2028-01-17",
        "2028-02-21",
        "2028-04-14",
        "2028-05-29",
        "2028-06-19",
        "2028-07-04",
        "2028-09-04",
        "2028-11-23",
        "2028-12-25",
    ],
)
def test_official_holidays_are_closed(day):
    session = session_at(utc(day + "T16:00:00"))
    assert not session.is_open
    assert session.status == "holiday"
    assert session.session_open_at is None


@pytest.mark.parametrize(
    ("day", "close"),
    [
        ("2026-11-27", "18:00:00"),
        ("2026-12-24", "18:00:00"),
        ("2027-11-26", "18:00:00"),
        ("2028-07-03", "17:00:00"),
        ("2028-11-24", "18:00:00"),
    ],
)
def test_official_early_close_sessions(day, close):
    session = session_at(utc(day + "T16:00:00"))
    assert session.is_open
    assert session.session_close_at == utc(day + "T" + close)
    assert not session_at(session.session_close_at).is_open


def test_new_year_2028_has_no_observed_friday_holiday():
    session = session_at(utc("2027-12-31T20:00:00"))
    assert session.is_open
    assert session.session_close_at == utc("2027-12-31T21:00:00")


def test_weekend_and_holiday_skip_to_next_trading_session():
    session = session_at(utc("2026-07-02T21:00:00"))
    assert session.next_open_at == utc("2026-07-06T13:30:00")
    assert session_at(utc("2026-07-04T16:00:00")).status == "weekend"


@pytest.mark.parametrize("now", ["2025-10-01T16:00:00", "2029-10-01T16:00:00"])
def test_unsupported_year_fails_closed(now):
    session = session_at(utc(now))
    assert not session.is_open
    assert session.status == "unsupported"
    assert session.next_open_at is None


def test_schedule_does_not_guess_beyond_supported_years():
    session = session_at(utc("2028-12-29T22:00:00"))
    assert session.next_open_at is None


def test_naive_clock_is_rejected():
    with pytest.raises(ValueError, match="timezone"):
        session_at(datetime(2026, 10, 2, 14))
