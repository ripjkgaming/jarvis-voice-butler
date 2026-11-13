import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from calls import rules_parser as rp

TZ = ZoneInfo("Asia/Singapore")
NOW = dt.datetime(2026, 11, 13, 10, 0, tzinfo=TZ).timestamp()


def at(day, h, m=0):
    return dt.datetime(2026, 11, day, h, m, tzinfo=TZ).timestamp()


def test_duration_answer_all_with_message():
    p = rp.parse("for the next 3 hours answer all calls and say I'm in a meeting", NOW)
    assert p.intent == "auto_answer"
    r = p.rule
    assert r.kind == "answer_all"
    assert r.start == NOW and r.end == NOW + 3 * 3600
    assert r.message == "I'm in a meeting"


def test_window_unknown_numbers():
    p = rp.parse("between 9am and 5pm handle calls from unknown numbers", NOW)
    assert p.rule.kind == "answer_unknown"
    assert (p.rule.start, p.rule.end) == (at(13, 9), at(13, 17))
    assert p.rule.daily_from is None


def test_window_already_over_rolls_to_tomorrow():
    now = at(13, 18)
    p = rp.parse("between 9am and 5pm answer all calls", now)
    assert (p.rule.start, p.rule.end) == (at(14, 9), at(14, 17))


def test_daily_window():
    p = rp.parse("every day from 9am to 5pm answer calls", NOW)
    assert (p.rule.daily_from, p.rule.daily_to) == ("09:00", "17:00")
    assert p.rule.start is None


def test_overnight_window():
    p = rp.parse("from 10pm to 6am reject calls", NOW)
    assert p.rule.kind == "reject"
    assert (p.rule.start, p.rule.end) == (at(13, 22), at(14, 6))


def test_until():
    p = rp.parse("take messages until 5pm", NOW)
    assert p.rule.kind == "message_only" and p.rule.end == at(13, 17)


def test_words_and_half_hour():
    p = rp.parse("answer all calls for the next half an hour", NOW)
    assert p.rule.end == NOW + 1800
    p = rp.parse("auto answer for two hours", NOW)
    assert p.rule.end == NOW + 7200


def test_no_window_is_open_ended():
    p = rp.parse("auto answer calls", NOW)
    assert p.rule.start is None and p.rule.end is None


@pytest.mark.parametrize(
    "t",
    [
        "stop auto answer",
        "cancel auto answer",
        "stop answering calls",
        "turn off auto-answer",
        "cancel the call rules",
    ],
)
def test_cancel(t):
    assert rp.parse(t, NOW).intent == "cancel_auto_answer"


def test_schedule_call():
    p = rp.parse("call Mum at 6pm and ask about dinner", NOW)
    assert p.intent == "schedule_call"
    assert p.contact == "Mum" and p.due == at(13, 18)
    assert p.goal == "ask about dinner"


def test_schedule_call_tomorrow_and_relative():
    p = rp.parse("call Dr Tan tomorrow at 9am to confirm the appointment", NOW)
    assert p.due == at(14, 9) and p.goal == "confirm the appointment"
    p = rp.parse("call Dad in 20 minutes", NOW)
    assert p.due == NOW + 1200 and p.goal == ""
    p = rp.parse("call Dad in half an hour", NOW)
    assert p.due == NOW + 1800


def test_past_clock_rolls_next_day():
    p = rp.parse("call Mum at 9am", NOW)
    assert p.due == at(14, 9)


def test_call_now():
    p = rp.parse("call Mum and tell her I'm late", NOW)
    assert p.intent == "call_now" and p.contact == "Mum"
    assert p.goal == "tell her I'm late"


def test_call_me_is_not_ours():
    assert rp.parse("call me at 8pm", NOW) is None


def test_non_commands():
    assert rp.parse("what's the weather", NOW) is None
    assert rp.parse("", NOW) is None


def test_errors():
    with pytest.raises(rp.ParseError):
        rp.parse("call at 6pm", NOW)
    with pytest.raises(rp.ParseError):
        rp.parse("from 9am to 9am answer calls", NOW)
