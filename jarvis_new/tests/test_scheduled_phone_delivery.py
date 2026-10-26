"""Explicit scheduled invitations share delivery gates, not proactive budgets."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from phone_calls import CallQueue


def now_at(value="2026-10-14T19:00:00"):
    return (
        datetime.fromisoformat(value)
        .replace(tzinfo=ZoneInfo("Asia/Singapore"))
        .timestamp()
    )


def test_scheduled_invite_ignores_proactive_toggles_and_budget(tmp_path):
    q = CallQueue(tmp_path / "calls.db")
    n = now_at()
    q.configure(True, True, True, n)
    for index in range(3):
        assert q.offer("update", str(index), "Task finished", n)
        q.claim(q.pending(n)["invitation"]["id"], "decline", n)
    q.configure(True, False, False, n)
    q.set_schedule(False)
    receipt = q.offer_scheduled("scheduled:task", "Your requested check-in", n, n + 45)
    assert receipt["status"] == "invited"
    invite = q.pending(n)["invitation"]
    assert invite["kind"] == "scheduled"
    assert q.claim(invite["id"], "accept", n)
    assert q.accepted(invite["id"], n) == "Your requested check-in"
    assert (
        q.offer_scheduled("scheduled:task", "Different title", n + 1, n + 46) == receipt
    )


def test_scheduled_offline_does_not_replay_after_reconnect(tmp_path):
    q = CallQueue(tmp_path / "calls.db")
    n = now_at()
    first = q.offer_scheduled("scheduled:task", "Check-in", n, n + 45)
    assert first == {"status": "blocked", "reason": "disabled", "call_id": None}
    q.configure(True, True, True, n + 5)
    assert q.offer_scheduled("scheduled:task", "Check-in", n + 5, n + 45) == first
    assert q.pending(n + 5)["invitation"] is None


def test_scheduled_fresh_listener_quiet_busy_and_deadline_gates(tmp_path):
    n = now_at()
    for reason in ("offline", "quiet_hours", "busy", "expired"):
        q = CallQueue(tmp_path / (reason + ".db"))
        current = now_at("2026-10-14T10:00:00") if reason == "quiet_hours" else n
        q.configure(True, True, True, current - 61 if reason == "offline" else current)
        if reason == "busy":
            assert q.offer("update", "existing", "Existing call", current)
        result = q.offer_scheduled(
            "scheduled:task",
            "Check-in",
            current,
            current if reason == "expired" else current + 45,
        )
        assert result["status"] == "blocked"
        assert result["reason"] == reason


def test_scheduled_claim_is_atomic_and_does_not_consume_proactive_cap(tmp_path):
    q = CallQueue(tmp_path / "calls.db")
    n = now_at()
    q.configure(True, True, True, n)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: q.offer_scheduled("same", "Check-in", n, n + 45), range(4)
            )
        )
    assert all(result == results[0] for result in results)
    with q.db() as c:
        assert c.execute("SELECT count(*) FROM calls").fetchone()[0] == 1
    q.claim(q.pending(n)["invitation"]["id"], "decline", n)
    for index in range(3):
        assert q.offer("update", str(index), "Proactive update", n)
        q.claim(q.pending(n)["invitation"]["id"], "decline", n)


def test_scheduled_call_refuses_guest_mode_without_replay(tmp_path, monkeypatch):
    import phone_session

    q = CallQueue(tmp_path / "calls.db")
    n = now_at()
    q.configure(True, True, True, n)
    monkeypatch.setattr(phone_session, "guest_required", lambda: True)
    receipt = q.offer_scheduled("scheduled:private", "Check-in", n, n + 45)
    assert receipt["status"] == "blocked"
    assert receipt["reason"] == "guest_mode"
    monkeypatch.setattr(phone_session, "guest_required", lambda: False)
    assert q.offer_scheduled("scheduled:private", "Check-in", n, n + 45) == receipt
    assert q.pending(n)["invitation"] is None
