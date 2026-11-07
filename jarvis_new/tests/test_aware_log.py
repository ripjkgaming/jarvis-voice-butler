"""Always Aware log, spans and memory feed (PLAN.md §2)."""

import datetime as dt
import os
import time

import pytest

from aware import log as alog
from aware import memory_feed, spans

T0 = time.mktime((2026, 11, 7, 14, 0, 0, 0, 0, -1))
DAY = 86_400.0


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_VAULT", str(tmp_path / "vault"))
    return tmp_path


def obs(**kw):
    base = {
        "summary": "Debugging auth tests",
        "activity": "debugging",
        "app": "konsole",
        "subject": "jarvis_new",
        "importance": 1,
        "signals": [],
        "sensitive": False,
        "opportunities": [],
    }
    base.update(kw)
    return base


# ------------------------------------------------------------------ log


def test_tick_entry_hashes_title_unless_important():
    e = alog.tick_entry(tick_id="t1", interval_s=180, obs=obs(), title="secret.txt")
    assert "title" not in e and e["title_hash"] and e["title_hash"] != "secret.txt"
    e2 = alog.tick_entry(
        tick_id="t2",
        interval_s=180,
        obs=obs(importance=2),
        title="pw: password=hunter2",
    )
    assert "hunter2" not in e2["title"]


def test_sensitive_obs_drops_opportunities():
    e = alog.tick_entry(
        tick_id="t",
        interval_s=1,
        obs=obs(sensitive=True),
        opportunities=[{"kind": "code_fix", "title": "x"}],
    )
    assert e["sensitive"] and e["opportunities"] == []


def test_write_skip_private_and_read(_home):
    alog.skip("idle", now=T0)
    alog.write({"outcome": "seen", "summary": "a"}, now=T0 + 1)
    path = _home / "aware" / "log" / f"{dt.date.fromtimestamp(T0)}.jsonl"
    assert (os.stat(path).st_mode & 0o777) == 0o600
    rows = alog.read_day(dt.date.fromtimestamp(T0))
    assert rows[0] == {"ts": T0, "outcome": "skip", "reason": "idle"}
    assert alog.recent(3, now=T0 + 2)[0]["summary"] == "a"


def test_recent_spans_yesterday_and_limits():
    alog.write({"outcome": "seen", "summary": "y"}, now=T0 - DAY)
    for i in range(4):
        alog.write({"outcome": "seen", "summary": f"s{i}"}, now=T0 + i)
    got = alog.recent(3, now=T0 + 10)
    assert [r["summary"] for r in got] == ["s1", "s2", "s3"]
    assert len(alog.recent(10, now=T0 + 10)) == 5


def test_prune_keeps_fourteen_days(_home):
    for back in (0, 13, 14, 15, 30):
        alog.skip("x", now=T0 - back * DAY)
    removed = alog.prune(now=T0)
    assert removed == 2
    assert len(list((_home / "aware" / "log").glob("*.jsonl"))) == 3


def test_spoken_recent():
    assert "haven't seen" in alog.spoken_recent([])
    rows = [
        {"ts": T0, "summary": "Reading mail."},
        {"ts": T0 + 200, "summary": "Coding the HUD"},
    ]
    say = alog.spoken_recent(rows)
    assert say.startswith("At 14:03: Coding the HUD.") and "Reading mail" in say
    assert say.count(".") <= 3


# ------------------------------------------------------------------ spans


def seen(**kw):
    return {"outcome": "seen", **obs(**kw)}


def test_same_subject_collapses_and_new_subject_opens():
    s = spans.merge([], seen(), T0)
    s = spans.merge(s, seen(app="code", summary="Fixing import"), T0 + 180)
    s = spans.merge(s, {"outcome": "unchanged"}, T0 + 360)
    assert (
        len(s) == 1 and s[0]["end"] == T0 + 360 and s[0]["apps"] == ["konsole", "code"]
    )
    s = spans.merge(s, seen(subject="YouTube", activity="media"), T0 + 540)
    assert len(s) == 2 and s[1]["subject"] == "YouTube"


def test_gap_closes_span_and_skips_ignored():
    s = spans.merge([], seen(), T0)
    s = spans.merge(s, {"outcome": "skip", "reason": "idle"}, T0 + 100)
    assert len(s) == 1
    s = spans.merge(s, {"outcome": "unchanged"}, T0 + spans.GAP_S + 100)
    assert s[0]["end"] == T0
    s = spans.merge(s, seen(), T0 + spans.GAP_S + 200)
    assert len(s) == 2


def test_important_notes_kept_and_importance_max():
    s = spans.merge([], seen(), T0)
    s = spans.merge(s, seen(importance=3, summary="Tests failing"), T0 + 180)
    assert s[0]["importance"] == 3 and s[0]["notes"][-1] == "Tests failing"


def test_update_around_and_spoken_at():
    spans.update(seen(), T0)
    spans.update(seen(summary="x"), T0 + 600)
    spans.update(
        seen(subject="mail", app="thunderbird", summary="Reading mail"), T0 + 900
    )
    hits = spans.around(T0 + 300)
    assert hits[0]["subject"] == "jarvis_new"
    say = spans.spoken_at(T0 + 300)
    assert say.startswith("From 14:00 to 14:10 you were on jarvis_new")
    assert "mail" in say
    assert spans.spoken_at(T0 + 5 * 3600) == ""


def test_describe_format():
    span = {
        "start": T0 + 300,
        "end": T0 + 4800,
        "subject": "jarvis_new",
        "apps": ["Konsole", "VS Code"],
        "notes": ["debugging auth tests"],
    }
    assert (
        spans.describe(span)
        == "14:05-15:20 jarvis_new: debugging auth tests (Konsole, VS Code)"
    )


def test_spans_prune_ninety_days(_home):
    spans.save_day(dt.date.fromtimestamp(T0 - 91 * DAY), [])
    spans.save_day(dt.date.fromtimestamp(T0 - 10 * DAY), [])
    assert spans.prune(now=T0) == 1


# ------------------------------------------------------------------ memory feed


def test_feed_writes_closed_qualifying_spans_once(_home):
    import memory

    spans.update(seen(importance=2, summary="Tests failing"), T0)  # important, short
    spans.update(seen(subject="mail", summary="Reading mail"), T0 + 300)  # short, dull
    spans.update(seen(subject="hud", summary="Styling HUD"), T0 + 600)  # last, open
    n = memory_feed.feed(now=T0 + 700)
    assert n == 1
    text = memory.daily_path(dt.date.fromtimestamp(T0)).read_text()
    assert "## Seen" in text and "jarvis_new: Tests failing" in text
    assert "mail" not in text
    assert memory_feed.feed(now=T0 + 800) == 0


def test_feed_long_span_after_close(_home):
    import memory

    for i in range(7):
        spans.update(seen(subject="essay", summary="Writing essay"), T0 + i * 180)
    assert memory_feed.feed(now=T0 + 6 * 180 + 10) == 0  # still open
    assert memory_feed.feed(now=T0 + 6 * 180 + spans.GAP_S + 10) == 1
    assert "essay" in memory.daily_path(dt.date.fromtimestamp(T0)).read_text()


def test_feed_respects_memory_switch(monkeypatch):
    monkeypatch.setenv("JARVIS_MEMORY", "0")
    spans.update(seen(importance=3), T0)
    assert memory_feed.feed(now=T0 + 2 * spans.GAP_S) == 0


def test_day_summary_includes_seen(_home):
    import memory

    spans.update(seen(importance=2, summary="Tests failing"), T0)
    memory_feed.feed(now=T0 + 2 * spans.GAP_S)
    summary = memory.day_summary(dt.date.fromtimestamp(T0))
    assert "Seen:" in summary and "jarvis_new" in summary
