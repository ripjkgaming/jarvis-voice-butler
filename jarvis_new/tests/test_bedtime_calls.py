"""Offline bedtime tests; only mocked Calendar reads and temporary call ledgers."""

import datetime as dt
import urllib.parse
from zoneinfo import ZoneInfo

import pytest

import bedtime_calls as bedtime
from phone_calls import CallQueue, quiet

SG = ZoneInfo("Asia/Singapore")


def ts(day="2026-10-07", clock="23:00:00"):
    return dt.datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=SG).timestamp()


def event(start="2026-10-08T08:25:00+08:00", **extra):
    return {"start": {"dateTime": start}, **extra}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(bedtime, "_checked_day", None)


@pytest.mark.parametrize(
    "clock,qualifies",
    [("08:19:59", False), ("08:20:00", True), ("08:30:00", True), ("08:30:01", False)],
)
def test_inclusive_window(clock, qualifies):
    tomorrow = dt.date(2026, 10, 8)
    assert (
        bedtime.has_early_event([event(f"2026-10-08T{clock}+08:00")], tomorrow)
        is qualifies
    )


@pytest.mark.parametrize(
    "value",
    [
        event(status="cancelled"),
        event(attendees=[{"self": True, "responseStatus": "declined"}]),
        {"start": {"date": "2026-10-08"}},
        event("2026-10-08T08:25:00"),
        event("2026-10-07T08:25:00+08:00"),
        event("bad"),
    ],
)
def test_irrelevant_and_ambiguous_events_do_not_trigger(value):
    assert not bedtime.has_early_event([value], dt.date(2026, 10, 8))


@pytest.mark.parametrize(
    "start,tomorrow",
    [
        ("2026-03-07T16:20:00-08:00", "2026-03-08"),
        ("2026-03-08T17:20:00-07:00", "2026-03-09"),
        ("2026-10-08T00:30:00Z", "2026-10-08"),
    ],
)
def test_offset_and_dst_conversion(start, tomorrow):
    assert bedtime.has_early_event([event(start)], dt.date.fromisoformat(tomorrow))


def test_naive_named_zone_and_dst_ambiguity():
    assert bedtime.has_early_event(
        [{"start": {"dateTime": "2026-10-08T08:20:00", "timeZone": "Asia/Singapore"}}],
        dt.date(2026, 10, 8),
    )
    assert (
        bedtime.event_start(
            {
                "start": {
                    "dateTime": "2026-11-01T01:25:00",
                    "timeZone": "America/New_York",
                }
            }
        )
        is None
    )
    assert (
        bedtime.event_start(
            {
                "start": {
                    "dateTime": "2026-03-08T02:25:00",
                    "timeZone": "America/New_York",
                }
            }
        )
        is None
    )


def test_complete_day_reader_pages_and_sg_boundaries(monkeypatch):
    import google_api

    monkeypatch.setattr(google_api, "_calendar_token", lambda **k: "fake")
    calls = []
    pages = [{"items": [event()], "nextPageToken": "page2"}, {"items": []}]

    def read(method, url, token, **kwargs):
        calls.append(urllib.parse.parse_qs(urllib.parse.urlparse(url).query))
        return pages.pop(0)

    monkeypatch.setattr(google_api, "_request_json", read)
    assert len(bedtime.read_calendar_day(dt.date(2026, 10, 8))) == 1
    assert calls[0]["timeMin"] == ["2026-10-07T16:00:00Z"]
    assert calls[0]["timeMax"] == ["2026-10-08T16:00:00Z"]
    assert calls[1]["pageToken"] == ["page2"]


def test_incomplete_day_reader_fails_closed(monkeypatch):
    import google_api

    monkeypatch.setattr(google_api, "_calendar_token", lambda **k: "fake")
    counter = []

    def read(*args, **kwargs):
        counter.append(1)
        return {"items": [event()], "nextPageToken": str(len(counter))}

    monkeypatch.setattr(google_api, "_request_json", read)
    with pytest.raises(bedtime.CalendarIncompleteError):
        bedtime.read_calendar_day(dt.date(2026, 10, 8))
    assert len(counter) == bedtime.MAX_PAGES


@pytest.mark.parametrize("clock", ["22:59:59", "23:00:45", "23:30:00", "06:00:00"])
def test_no_fetch_or_catchup_outside_window(tmp_path, clock):
    q = CallQueue(tmp_path / "calls.db")

    def forbidden(day):
        raise AssertionError("Calendar must not be read")

    assert not bedtime.poll_schedule(
        q, ts(clock=clock), reader=forbidden, now_fn=lambda: ts(clock=clock)
    )
    with q.db() as c:
        assert c.execute("SELECT count(*) FROM calls").fetchone()[0] == 0


def test_one_invitation_shared_gates_and_fixed_expiry(tmp_path, monkeypatch):
    n = ts(clock="23:00:20")
    q = CallQueue(tmp_path / "calls.db")
    q.configure(True, True, True, n)
    reads = []

    def read(day):
        reads.append(day)
        return [event()]

    assert bedtime.poll_schedule(q, n, reader=read, now_fn=lambda: n)
    invitation = q.pending(n)["invitation"]
    assert "06:00" in invitation["title"]
    assert invitation["expires"] == ts() + 45
    assert not bedtime.poll_schedule(q, n + 1, reader=read, now_fn=lambda: n + 1)
    assert len(reads) == 1
    # The persisted queue dedupe also prevents replay after a module restart.
    monkeypatch.setattr(bedtime, "_checked_day", None)
    q.claim(invitation["id"], "decline", n + 1)
    assert not bedtime.poll_schedule(q, n + 2, reader=read, now_fn=lambda: n + 2)
    assert q.pending(n + 45)["invitation"] is None


@pytest.mark.parametrize("condition", ["off", "updates_off", "stale", "busy", "cap"])
def test_existing_call_gates_are_retained(tmp_path, condition):
    n = ts()
    q = CallQueue(tmp_path / "calls.db")
    q.configure(
        condition != "off",
        True,
        condition != "updates_off",
        n - 61 if condition == "stale" else n,
    )
    if condition == "busy":
        assert q.offer("update", "already-ringing", "Existing", n)
    if condition == "cap":
        for i in range(3):
            when = n - 100 + i
            q.configure(True, True, True, when)
            assert q.offer("update", str(i), "Prior invitation", when)
            q.claim(q.pending(when)["invitation"]["id"], "decline", when)
        q.configure(True, True, True, n)
    assert not bedtime.poll_schedule(
        q, n, reader=lambda day: [event()], now_fn=lambda: n
    )


def test_fetch_failure_and_slow_fetch_do_not_offer(tmp_path, monkeypatch):
    n = ts()
    q = CallQueue(tmp_path / "calls.db")
    q.configure(True, True, True, n)
    logs = []

    def failed(day):
        raise RuntimeError("PRIVATE EVENT CONTENT")

    assert not bedtime.poll_schedule(
        q, n, reader=failed, now_fn=lambda: n, log_fn=logs.append
    )
    assert all("PRIVATE" not in x for x in logs)
    monkeypatch.setattr(bedtime, "_checked_day", None)
    assert not bedtime.poll_schedule(
        q, n, reader=lambda day: [event()], now_fn=lambda: n + 46
    )
    with q.db() as c:
        assert c.execute("SELECT count(*) FROM calls").fetchone()[0] == 0


def test_no_early_event_means_no_invented_other_night_call(tmp_path):
    n = ts()
    q = CallQueue(tmp_path / "calls.db")
    q.configure(True, True, True, n)
    assert not bedtime.poll_schedule(
        q, n, reader=lambda day: [event("2026-10-08T10:00:00+08:00")], now_fn=lambda: n
    )
    with q.db() as c:
        assert c.execute("SELECT count(*) FROM calls").fetchone()[0] == 0


def test_weekly_checkin_and_school_quiet_policy_unchanged(tmp_path):
    q = CallQueue(tmp_path / "calls.db")
    n = ts(clock="19:05:00")
    q.configure(True, True, True, n)
    assert not bedtime.poll_schedule(q, n, reader=lambda day: [], now_fn=lambda: n)
    assert q.poll_schedule(n)
    assert q.pending(n)["invitation"]["kind"] == "checkin"
    assert quiet(ts(clock="08:00:00"))
    assert not quiet(ts(clock="15:30:00"))
