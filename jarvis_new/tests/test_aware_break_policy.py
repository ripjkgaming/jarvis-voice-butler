from datetime import datetime
from zoneinfo import ZoneInfo

from aware.break_policy import Policy

SG = ZoneInfo("Asia/Singapore")


def epoch(day: int, hour: int = 12, minute: int = 0):
    return datetime(2026, 11, day, hour, minute, tzinfo=SG).timestamp()


def leisure(at, **overrides):
    data = {
        "activity": "leisure",
        "confidence": 0.9,
        "present": True,
        "presence_at": at,
        "observed_at": at,
        "content": "Watching a comedy episode for entertainment",
        "task": "Relaxing",
        "in_call": False,
        "presentation": False,
        "scheduled_break": False,
        "exercise_active": False,
    }
    data.update(overrides)
    return data


def test_requires_multiple_fresh_observations_for_at_least_two_minutes():
    p = Policy(path=":memory:")
    start = epoch(12)
    assert p.observe(leisure(start), now=start)["reason"] == "leisure_not_persistent"
    decision = p.observe(leisure(start + 120), now=start + 120)
    assert decision["eligible"] is True
    assert decision["reason"] == "eligible"


def test_app_name_alone_is_not_leisure_evidence():
    p = Policy(path=":memory:")
    start = epoch(12)
    first = leisure(start, content="", task="", app="YouTube")
    second = leisure(start + 180, content="", task="", app="YouTube")
    assert p.observe(first, now=start)["reason"] == "uncertain_activity"
    assert p.observe(second, now=start + 180)["eligible"] is False


def test_recent_observations_can_support_content_activity_classification():
    p = Policy(path=":memory:")
    start = epoch(12)
    obs = {
        "activity": "uncertain",
        "confidence": 0.8,
        "present": True,
        "presence_at": start,
        "observed_at": start,
        "app": "browser",
        "content": "",
        "task": "",
        "in_call": False,
        "presentation": False,
        "scheduled_break": False,
        "exercise_active": False,
        "recent_observations": [
            {
                "activity": "leisure",
                "content": "Watching a movie",
                "observed_at": start - 60,
            },
            {
                "activity": "leisure",
                "content": "Watching a movie",
                "observed_at": start,
            },
        ],
    }
    assert p.observe(obs, now=start)["reason"] == "leisure_not_persistent"


def test_work_uncertain_and_stale_observations_reset_leisure_streak():
    p = Policy(path=":memory:")
    start = epoch(12)
    p.observe(leisure(start), now=start)
    assert (
        p.observe(
            leisure(start + 90, activity="work", content="Writing a report"),
            now=start + 90,
        )["reason"]
        == "work"
    )
    assert (
        p.observe(leisure(start + 130), now=start + 130)["reason"]
        == "leisure_not_persistent"
    )
    stale = leisure(start + 250, observed_at=start - 1000, presence_at=start - 1000)
    assert p.observe(stale, now=start + 250)["reason"] == "stale_observation"
    assert (
        p.observe(leisure(start + 300), now=start + 300)["reason"]
        == "leisure_not_persistent"
    )


def test_presence_and_all_intervention_inhibitors_are_required():
    p = Policy(path=":memory:")
    start = epoch(12)
    assert (
        p.observe(leisure(start, present=False), now=start)["reason"] == "not_present"
    )
    p.observe(leisure(start), now=start)
    for inhibitor in ("in_call", "presentation", "scheduled_break", "exercise_active"):
        result = p.observe(leisure(start + 130, **{inhibitor: True}), now=start + 130)
        assert result["eligible"] is False
        assert result["reason"] == inhibitor
        assert (
            p.observe(leisure(start + 140), now=start + 140)["reason"]
            == "leisure_not_persistent"
        )


def test_daily_counts_gap_and_singapore_date_survive_restart(tmp_path):
    path = tmp_path / "breaks.sqlite3"
    start = epoch(12)
    p = Policy(path=path)
    p.record_intervention(now=start, reason="prompt")
    p2 = Policy(path=path)
    p2.observe(leisure(start + 120), now=start + 120)
    assert p2.observe(leisure(start + 240), now=start + 240)["reason"] == "cooldown"
    p2.observe(leisure(start + 3480), now=start + 3480)
    assert p2.observe(leisure(start + 3600), now=start + 3600)["eligible"] is True
    for offset in (3600, 7200, 10800, 14400):
        p2.record_intervention(now=start + offset, reason="prompt")
        p2.observe(leisure(start + offset + 120), now=start + offset + 120)
        p2.observe(leisure(start + offset + 240), now=start + offset + 240)
    p2.observe(leisure(start + 17880), now=start + 17880)
    assert p2.observe(leisure(start + 18000), now=start + 18000)["eligible"] is True
    p2.record_intervention(now=start + 18000, reason="prompt")
    p2.observe(leisure(start + 21480), now=start + 21480)
    assert (
        p2.observe(leisure(start + 21600), now=start + 21600)["reason"]
        == "daily_maximum"
    )
    next_day = epoch(13, 0, 5)
    assert (
        p2.observe(leisure(next_day), now=next_day)["reason"]
        == "leisure_not_persistent"
    )


def test_emergency_release_is_separate_and_enters_cooldown(tmp_path):
    p = Policy(path=tmp_path / "breaks.sqlite3")
    start = epoch(12)
    p.record_intervention(now=start, reason="prompt")
    p.record_intervention(now=start + 20, reason="emergency_release")
    p.observe(leisure(start + 3480), now=start + 3480)
    decision = p.observe(leisure(start + 3600), now=start + 3600)
    assert decision["reason"] == "cooldown"
    assert decision["daily_count"] == 1
    assert decision["emergency_releases"] == 1


def test_observe_is_observation_only_and_does_not_increment_count(tmp_path):
    p = Policy(path=tmp_path / "breaks.sqlite3")
    start = epoch(12)
    p.observe(leisure(start), now=start)
    decision = p.observe(leisure(start + 120), now=start + 120)
    assert decision["eligible"] is True
    assert decision["daily_count"] == 0
    assert p.observe(leisure(start + 121), now=start + 121)["eligible"] is True
    assert decision["mode"] == "observation_only"


def test_presence_timestamp_is_mandatory_and_unknown_inhibitor_fails_closed():
    p = Policy(path=":memory:")
    start = epoch(12)
    no_presence_time = leisure(start)
    no_presence_time.pop("presence_at")
    assert p.observe(no_presence_time, now=start)["reason"] == "stale_presence"
    unknown = leisure(start, in_call=None)
    assert p.observe(unknown, now=start)["reason"] == "inhibitor_unknown"


def test_duplicate_observation_timestamp_does_not_count_twice():
    p = Policy(path=":memory:")
    start = epoch(12)
    p.observe(leisure(start), now=start)
    decision = p.observe(leisure(start), now=start)
    assert decision["leisure_observations"] == 1
    assert decision["eligible"] is False


def test_cooldown_survives_singapore_midnight(tmp_path):
    p = Policy(path=tmp_path / "breaks.sqlite3")
    previous_day = epoch(12, 23, 30)
    next_day = epoch(13, 0, 20)
    p.record_intervention(now=previous_day, reason="prompt")
    p.observe(leisure(next_day - 120), now=next_day - 120)
    decision = p.observe(leisure(next_day), now=next_day)
    assert decision["daily_count"] == 0
    assert decision["reason"] == "cooldown"


def test_default_database_uses_private_permissions_and_thread_safe_connection(
    tmp_path, monkeypatch
):
    import stat
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "private-home"))
    p = Policy()
    start = epoch(12)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda i: p.record_intervention(now=start + i, reason="prompt"),
                range(12),
            )
        )
    assert len(results) == 12
    final = p.observe(leisure(start + 120), now=start + 120)
    assert final["daily_count"] == 12
    assert (
        stat.S_IMODE(
            (tmp_path / "private-home/aware/break_policy.sqlite3").stat().st_mode
        )
        == 0o600
    )
    assert stat.S_IMODE((tmp_path / "private-home/aware").stat().st_mode) == 0o700


def test_neutral_video_is_not_leisure_but_educational_context_is_work():
    p = Policy(path=":memory:")
    start = epoch(12)
    neutral = leisure(start, content="Watching a YouTube video", task="")
    decision = p.observe(neutral, now=start)
    assert decision["reason"] == "uncertain_activity"
    lecture = leisure(
        start + 10,
        content="Physics lecture about quantum mechanics",
        task="",
        activity="uncertain",
    )
    assert p.observe(lecture, now=start + 10)["reason"] == "work"


def test_explicit_comedy_entertainment_is_leisure_evidence():
    p = Policy(path=":memory:")
    start = epoch(12)
    first = leisure(start, content="Comedy entertainment episode", task="")
    second = leisure(start + 120, content="Comedy entertainment episode", task="")
    assert p.observe(first, now=start)["reason"] == "leisure_not_persistent"
    assert p.observe(second, now=start + 120)["eligible"] is True


def test_non_finite_observation_and_presence_timestamps_fail_closed():
    p = Policy(path=":memory:")
    start = epoch(12)
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert p.observe(leisure(start, observed_at=bad), now=start)["reason"] == (
            "stale_observation"
        )
        assert p.observe(leisure(start, presence_at=bad), now=start)["reason"] == (
            "stale_presence"
        )
        assert p.observe(leisure(start), now=bad)["reason"] == "invalid_timestamp"


def test_non_finite_confidence_cannot_support_leisure():
    p = Policy(path=":memory:")
    start = epoch(12)
    for confidence in (float("nan"), float("inf"), float("-inf")):
        decision = p.observe(
            leisure(start, confidence=confidence, activity="uncertain"), now=start
        )
        assert decision["reason"] == "uncertain_activity"
        assert decision["eligible"] is False


def test_record_intervention_rejects_non_finite_time():
    p = Policy(path=":memory:")
    try:
        p.record_intervention(now=float("nan"))
    except ValueError as exc:
        assert "finite" in str(exc)
    else:
        raise AssertionError("non-finite intervention time was accepted")
