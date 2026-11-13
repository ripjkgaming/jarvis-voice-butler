"""away_log (append-only away journal) and away_worker (asleep-only loop).

Every test runs against a throwaway JARVIS_HOME. The worker tests use a fake
Handler, so nothing real is ever sent.
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path

import pytest

import away_log
import away_worker


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_AWAY_LIVE", raising=False)
    return tmp_path


def _lines(home: Path) -> list[str]:
    path = home / "away_log.jsonl"
    return path.read_text().splitlines() if path.exists() else []


# --- log path -------------------------------------------------------------


def test_log_path_honours_jarvis_home(home: Path) -> None:
    assert away_log.log_path() == home / "away_log.jsonl"


def test_log_path_defaults_to_home_dot_jarvis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_HOME", raising=False)
    assert away_log.log_path() == Path.home() / ".jarvis" / "away_log.jsonl"


# --- writing --------------------------------------------------------------


def test_log_event_writes_one_line_and_returns_row(home: Path) -> None:
    row = away_log.log_event("email_replied", "Replied to Ana", {"id": "m1"}, now=100.0)
    assert row == {
        "ts": 100.0,
        "type": "event",
        "kind": "email_replied",
        "summary": "Replied to Ana",
        "detail": {"id": "m1"},
    }
    lines = _lines(home)
    assert len(lines) == 1
    assert json.loads(lines[0]) == row


def test_append_only_keeps_earlier_lines_unchanged(home: Path) -> None:
    away_log.start_away(now=1.0)
    first = _lines(home)
    away_log.log_event("task_done", "Sent the form", now=2.0)
    away_log.log_event("other", "Renamed a file", now=3.0)
    after = _lines(home)
    assert after[: len(first)] == first
    assert len(after) == len(first) + 2


def test_unknown_kind_raises_and_writes_nothing(home: Path) -> None:
    with pytest.raises(ValueError):
        away_log.log_event("emailed_maybe", "nope", now=1.0)
    assert _lines(home) == []


@pytest.mark.parametrize("kind", away_log.KINDS)
def test_every_declared_kind_is_accepted(kind: str) -> None:
    row = away_log.log_event(kind, f"a {kind} item", now=5.0)
    assert row["kind"] == kind


def test_summary_is_whitespace_collapsed_and_capped() -> None:
    row = away_log.log_event("other", "line one\n\n  line   two " + "x" * 1000, now=1.0)
    assert "\n" not in row["summary"]
    assert "  " not in row["summary"]
    assert len(row["summary"]) == away_log.SUMMARY_MAX


def test_detail_may_be_none_list_or_arbitrary_object(home: Path) -> None:
    away_log.log_event("other", "none", None, now=1.0)
    away_log.log_event("other", "list", [1, "two"], now=2.0)
    away_log.log_event("other", "object", object(), now=3.0)
    rows = [json.loads(line) for line in _lines(home)]
    assert rows[0]["detail"] is None
    assert rows[1]["detail"] == [1, "two"]
    assert isinstance(rows[2]["detail"], str)


def test_start_and_end_markers(home: Path) -> None:
    away_log.start_away(now=10.0)
    away_log.end_away(now=20.0)
    rows = [json.loads(line) for line in _lines(home)]
    assert rows == [
        {"ts": 10.0, "type": "away_start"},
        {"ts": 20.0, "type": "away_end"},
    ]


def test_log_event_uses_wall_clock_when_now_missing(home: Path) -> None:
    import time

    before = time.time()
    row = away_log.log_event("other", "now-less")
    assert before <= row["ts"] <= time.time()


# --- reporting ------------------------------------------------------------


def test_report_defaults_to_latest_away_start(home: Path) -> None:
    away_log.log_event("email_replied", "old session", now=5.0)
    away_log.start_away(now=10.0)
    away_log.log_event("email_replied", "first new", now=11.0)
    away_log.end_away(now=12.0)
    away_log.start_away(now=20.0)
    away_log.log_event("calendar_scheduled", "latest", now=21.0)
    report = away_log.away_report()
    assert report["since"] == 20.0
    assert [i["summary"] for i in report["items"]] == ["latest"]


def test_report_without_any_start_covers_all_time(home: Path) -> None:
    away_log.log_event("email_replied", "a", now=1.0)
    away_log.log_event("task_done", "b", now=2.0)
    report = away_log.away_report()
    assert report["since"] == 0.0
    assert [i["summary"] for i in report["items"]] == ["a", "b"]


def test_report_explicit_since_filters_by_timestamp(home: Path) -> None:
    away_log.log_event("other", "early", now=1.0)
    away_log.log_event("other", "boundary", now=5.0)
    away_log.log_event("other", "late", now=9.0)
    report = away_log.away_report(since=5.0)
    assert [i["summary"] for i in report["items"]] == ["boundary", "late"]
    assert report["since"] == 5.0


def test_report_counts_contain_every_kind_even_when_zero(home: Path) -> None:
    away_log.log_event("email_drafted", "d", now=1.0)
    report = away_log.away_report(since=0)
    assert set(report["counts"]) == set(away_log.KINDS)
    assert report["counts"]["email_drafted"] == 1
    assert report["counts"]["whatsapp_replied"] == 0


def test_report_items_are_oldest_first_even_if_written_out_of_order(home: Path) -> None:
    away_log.log_event("other", "third", now=30.0)
    away_log.log_event("other", "first", now=10.0)
    away_log.log_event("other", "second", now=20.0)
    report = away_log.away_report(since=0)
    assert [i["summary"] for i in report["items"]] == ["first", "second", "third"]


def test_deferred_decisions_contain_only_deferred(home: Path) -> None:
    away_log.log_event("email_replied", "replied", now=1.0)
    away_log.log_event(
        "deferred_decision", "bank asks for payment", {"id": "b1"}, now=2.0
    )
    away_log.log_event("other", "other", now=3.0)
    away_log.log_event("deferred_decision", "sun asks to move trip", now=4.0)
    report = away_log.away_report(since=0)
    assert [d["summary"] for d in report["deferred_decisions"]] == [
        "bank asks for payment",
        "sun asks to move trip",
    ]
    assert report["deferred_decisions"][0]["detail"] == {"id": "b1"}


def test_missing_file_gives_empty_report(home: Path) -> None:
    report = away_log.away_report()
    assert report["items"] == []
    assert report["deferred_decisions"] == []
    assert all(v == 0 for v in report["counts"].values())


def test_report_is_a_plain_dict_round_trippable_through_json(home: Path) -> None:
    away_log.log_event(
        "whatsapp_replied", "Replied to Aarav", {"chat": "Aarav"}, now=1.0
    )
    report = away_log.away_report(since=0)
    assert json.loads(json.dumps(report)) == report


# --- corrupt lines --------------------------------------------------------


def test_corrupt_and_foreign_lines_are_skipped(home: Path) -> None:
    good_before = {
        "ts": 1.0,
        "type": "event",
        "kind": "other",
        "summary": "ok-1",
        "detail": None,
    }
    good_after = {
        "ts": 4.0,
        "type": "event",
        "kind": "task_done",
        "summary": "ok-2",
        "detail": None,
    }
    junk = [
        "",
        "   ",
        '{"ts": 2.0, "type": "event", "kind": "other", "summ',  # truncated
        "[1, 2]",
        "42",
        "null",
        '"just a string"',
        '{"type": "event", "kind": "other", "summary": "no ts"}',
        '{"ts": "soon", "type": "event", "kind": "other", "summary": "str ts"}',
        '{"ts": true, "type": "event", "kind": "other", "summary": "bool ts"}',
        '{"ts": NaN, "type": "event", "kind": "other", "summary": "nan ts"}',
        '{"ts": Infinity, "type": "event", "kind": "other", "summary": "inf ts"}',
        '{"ts": 3.0, "type": "event", "kind": "invented", "summary": "bad kind"}',
        '{"ts": 3.0, "type": "mystery"}',
    ]
    body = "\n".join([json.dumps(good_before), *junk, json.dumps(good_after)]) + "\n"
    (home / "away_log.jsonl").write_text(body, encoding="utf-8")
    report = away_log.away_report(since=0)
    assert [i["summary"] for i in report["items"]] == ["ok-1", "ok-2"]
    assert report["counts"]["other"] == 1
    assert report["counts"]["task_done"] == 1


def test_non_utf8_bytes_do_not_break_reading(home: Path) -> None:
    good = json.dumps(
        {"ts": 1.0, "type": "event", "kind": "other", "summary": "fine", "detail": None}
    )
    (home / "away_log.jsonl").write_bytes(b"\xff\xfe garbage\n" + good.encode() + b"\n")
    report = away_log.away_report(since=0)
    assert [i["summary"] for i in report["items"]] == ["fine"]


def test_a_nan_timestamp_is_not_treated_as_valid(home: Path) -> None:
    assert math.isnan(float("nan"))
    (home / "away_log.jsonl").write_text('{"ts": NaN, "type": "away_start"}\n')
    report = away_log.away_report()
    assert report["since"] == 0.0


# --- concurrency ----------------------------------------------------------


def test_concurrent_writers_never_interleave_lines(home: Path) -> None:
    threads, per_thread = 8, 50
    errors: list[BaseException] = []

    def writer(n: int) -> None:
        try:
            for i in range(per_thread):
                away_log.log_event("other", f"t{n}-{i}", {"n": n, "i": i}, now=float(i))
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    workers = [threading.Thread(target=writer, args=(n,)) for n in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    assert errors == []
    lines = _lines(home)
    assert len(lines) == threads * per_thread
    parsed = [json.loads(line) for line in lines]
    summaries = [p["summary"] for p in parsed]
    assert len(set(summaries)) == threads * per_thread
    for p in parsed:
        assert p["summary"] == f"t{p['detail']['n']}-{p['detail']['i']}"


# --- spoken summary -------------------------------------------------------


def _report(**counts: int) -> dict:
    base = dict.fromkeys(away_log.KINDS, 0)
    base.update(counts)
    return {"since": 0.0, "counts": base, "items": [], "deferred_decisions": []}


def _deferred(summary: str) -> dict:
    return {"ts": 1.0, "kind": "deferred_decision", "summary": summary, "detail": None}


def test_spoken_summary_quiet_night_says_nothing_needed() -> None:
    text = away_log.spoken_summary(_report())
    assert text == "Nothing needed doing while you were away."


def test_spoken_summary_groups_counts_into_one_sentence() -> None:
    text = away_log.spoken_summary(_report(email_replied=2, calendar_scheduled=1))
    assert text.count("While you were away") == 1
    assert "replied to two emails" in text
    assert "scheduled one calendar event" in text


def test_spoken_summary_uses_singular_for_one_item() -> None:
    text = away_log.spoken_summary(_report(whatsapp_replied=1))
    assert "answered one WhatsApp message" in text
    assert "messages" not in text


def test_spoken_summary_lists_deferred_last_and_singular_wording() -> None:
    report = _report(email_replied=1)
    report["deferred_decisions"] = [_deferred("Bank wants a payment confirmed")]
    text = away_log.spoken_summary(report)
    assert text.index("replied") < text.index("Bank wants")
    assert "One thing needs your decision, Sir: Bank wants a payment confirmed." in text


def test_spoken_summary_numbers_several_deferred_items() -> None:
    report = _report()
    report["deferred_decisions"] = [_deferred(f"item {i}") for i in range(3)]
    text = away_log.spoken_summary(report)
    assert "Three things need your decision, Sir." in text
    assert "First, item 0." in text
    assert "Second, item 1." in text
    assert "Third, item 2." in text


def test_spoken_summary_caps_long_deferred_lists() -> None:
    report = _report()
    report["deferred_decisions"] = [_deferred(f"thing {i}") for i in range(7)]
    text = away_log.spoken_summary(report)
    assert "Seven things need your decision, Sir." in text
    assert "thing 4" in text
    assert "thing 5" not in text
    assert "And 2 more are in the log." in text


def test_spoken_summary_is_speakable_and_short() -> None:
    report = _report(
        email_replied=2, email_drafted=1, calendar_scheduled=1, whatsapp_replied=1
    )
    report["deferred_decisions"] = [
        _deferred("x" * 300),
        _deferred("line\nbreak **bold**"),
    ]
    text = away_log.spoken_summary(report)
    assert "\n" not in text
    assert "  " not in text
    assert "*" not in text and "#" not in text
    assert len(text) < 600


def test_spoken_summary_accepts_a_real_report(home: Path) -> None:
    away_log.start_away(now=1.0)
    away_log.log_event("email_replied", "Replied to Ana", now=2.0)
    away_log.log_event("deferred_decision", "Sun wants the trip moved", now=3.0)
    text = away_log.spoken_summary(away_log.away_report())
    assert "replied to one email" in text
    assert "Sun wants the trip moved" in text


# --- worker ---------------------------------------------------------------


class FakeHandler(away_worker.Handler):
    """Records every call. Never touches Gmail, Calendar or WhatsApp."""

    def __init__(self, mail=None, chats=None, fail_mail=False) -> None:
        self.mail = list(mail or [])
        self.chats = list(chats or [])
        self.fail_mail = fail_mail
        self.calls: list[tuple[str, object]] = []
        self.since_seen: list[float] = []

    def new_emails(self, since: float) -> list[dict]:
        self.since_seen.append(since)
        return list(self.mail)

    def process_email(self, msg: dict) -> None:
        self.calls.append(("email", msg["id"]))
        if self.fail_mail:
            raise RuntimeError("gmail exploded")

    def process_calendar(self, msg: dict) -> None:
        self.calls.append(("calendar", msg["id"]))

    def new_whatsapp(self) -> list[dict]:
        return list(self.chats)

    def process_whatsapp(self, chat: dict) -> None:
        self.calls.append(("whatsapp", chat["name"]))


def _sleep(home: Path, state: str) -> None:
    (home / "sleep_state.json").write_text(json.dumps({"state": state}))


def test_worker_does_nothing_while_awake(home: Path) -> None:
    handler = FakeHandler(mail=[{"id": "m1"}], chats=[{"name": "Aarav"}])
    assert away_worker.cycle(handler, now=10.0) == "awake"
    assert handler.calls == []
    assert _lines(home) == []


def test_worker_treats_missing_or_corrupt_sleep_state_as_awake(home: Path) -> None:
    handler = FakeHandler(mail=[{"id": "m1"}])
    (home / "sleep_state.json").write_text("{not json")
    assert away_worker.cycle(handler, now=10.0) == "awake"
    (home / "sleep_state.json").write_text(json.dumps({"state": "snoozing"}))
    assert away_worker.cycle(handler, now=11.0) == "awake"
    assert handler.calls == []


def test_worker_asleep_logs_start_and_handles_each_mail_once(home: Path) -> None:
    _sleep(home, "asleep")
    handler = FakeHandler(mail=[{"id": "m1"}, {"id": "m2"}])
    assert away_worker.cycle(handler, now=100.0) == "asleep"
    assert handler.since_seen == [100.0]
    assert handler.calls == [
        ("email", "m1"),
        ("calendar", "m1"),
        ("email", "m2"),
        ("calendar", "m2"),
    ]
    assert json.loads(_lines(home)[0]) == {"ts": 100.0, "type": "away_start"}

    handler.calls.clear()
    away_worker.cycle(handler, now=400.0)
    assert handler.calls == []  # both already seen; no double replies
    assert handler.since_seen == [100.0, 100.0]


def test_worker_wake_writes_end_marker_once(home: Path) -> None:
    _sleep(home, "asleep")
    away_worker.cycle(FakeHandler(), now=1.0)
    _sleep(home, "awake")
    away_worker.cycle(FakeHandler(), now=2.0)
    away_worker.cycle(FakeHandler(), now=3.0)
    rows = [json.loads(line) for line in _lines(home)]
    assert [r["type"] for r in rows] == ["away_start", "away_end"]


def test_worker_turns_a_handler_exception_into_a_deferred_decision(home: Path) -> None:
    _sleep(home, "asleep")
    handler = FakeHandler(mail=[{"id": "m9", "subject": "Hi"}], fail_mail=True)
    away_worker.cycle(handler, now=5.0)
    report = away_log.away_report(since=0)
    assert len(report["deferred_decisions"]) == 1
    assert "gmail exploded" in report["deferred_decisions"][0]["summary"]
    # The calendar step still ran for the same mail.
    assert ("calendar", "m9") in handler.calls


def test_worker_sends_nothing_when_gmail_read_fails(home: Path) -> None:
    class Broken(FakeHandler):
        def new_emails(self, since: float) -> list[dict]:
            raise OSError("network down")

    _sleep(home, "asleep")
    handler = Broken(chats=[{"name": "Aarav"}])
    assert away_worker.cycle(handler, now=5.0) == "asleep"
    summaries = [
        r["summary"] for r in (away_log.away_report(since=0)["deferred_decisions"])
    ]
    assert any("Could not read Gmail" in s for s in summaries)
    assert ("whatsapp", "Aarav") in handler.calls


def test_worker_processes_whatsapp_chats_passed_by_handler(home: Path) -> None:
    _sleep(home, "asleep")
    handler = FakeHandler(chats=[{"name": "Aarav"}])
    away_worker.cycle(handler, now=5.0)
    assert ("whatsapp", "Aarav") in handler.calls


def test_defer_pattern_flags_money_legal_and_health_mail() -> None:
    hits = [
        "Your invoice is attached",
        "Refund request for order 9",
        "Court date set for Monday",
        "Your verification code is 123456",
        "Hospital appointment update",
    ]
    for text in hits:
        assert away_worker.DEFER_RE.search(text), text
    assert away_worker.DEFER_RE.search("Lunch on Sunday?") is None


def test_live_mode_refuses_to_start_without_owner_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JARVIS_OWNER_EMAIL", raising=False)
    with pytest.raises(RuntimeError):
        away_worker.SystemHandler(dry_run=False)
    assert away_worker.SystemHandler(dry_run=True).dry_run is True


def test_live_flag_is_env_only_and_defaults_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JARVIS_AWAY_LIVE", raising=False)
    assert away_worker.live_enabled() is False
    monkeypatch.setenv("JARVIS_AWAY_LIVE", "0")
    assert away_worker.live_enabled() is False
    monkeypatch.setenv("JARVIS_AWAY_LIVE", "1")
    assert away_worker.live_enabled() is True


def test_report_subcommand_prints_spoken_summary(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    away_log.start_away(now=1.0)
    away_log.log_event("task_done", "Sent the form", now=2.0)
    assert away_worker.main(["report"]) == 0
    out = capsys.readouterr().out
    assert "finished one task" in out
