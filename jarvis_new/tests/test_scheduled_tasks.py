"""Offline durable one-shot scheduling; transports never contact a real device."""

import datetime as dt
import threading
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pytest

import scheduled_tasks as tasks

ZONE = ZoneInfo("Asia/Singapore")
NOW = dt.datetime(2026, 10, 17, 20, 0, tzinfo=ZONE).timestamp()


@pytest.mark.parametrize(
    "when,expected",
    [
        ("8:30pm", "2026-10-17T20:30:00+08:00"),
        ("08:30", "2026-10-18T08:30:00+08:00"),
        ("tomorrow at 9am", "2026-10-18T09:00:00+08:00"),
        ("in 90 seconds", "2026-10-17T20:01:30+08:00"),
        ("2026-10-18T01:00:00Z", "2026-10-18T09:00:00+08:00"),
    ],
)
def test_parse_time(when, expected):
    due = tasks.parse_when(when, now=NOW)
    assert dt.datetime.fromtimestamp(due, ZONE).isoformat() == expected


@pytest.mark.parametrize(
    "when", ["today 9am", "2026-02-30 09:00", "in -1 minutes", "25:30", "next someday"]
)
def test_invalid_time_rejected(when):
    with pytest.raises(tasks.ScheduleError):
        tasks.parse_when(when, now=NOW)


def test_restart_and_idempotent_create_then_single_reminder_delivery(tmp_path):
    path = tmp_path / "schedule.sqlite"
    store = tasks.Store(path)
    first = store.create(
        "reminder", "Drink water", NOW + 10, now=NOW, request_id="turn"
    )
    assert (
        store.create("reminder", "Drink water", NOW + 10, now=NOW, request_id="turn")[
            "id"
        ]
        == first["id"]
    )
    sent = []
    store = tasks.Store(path)
    assert store.tick(now=NOW + 9, reminder=lambda row: sent.append(row) or True) == 0
    assert store.tick(now=NOW + 10, reminder=lambda row: sent.append(row) or True) == 1
    assert store.tick(now=NOW + 20, reminder=lambda row: sent.append(row) or True) == 0
    assert len(sent) == 1 and store.get(first["id"])["status"] == "queued"


def test_parallel_ticks_dispatch_only_once(tmp_path):
    path = tmp_path / "schedule.sqlite"
    tasks.Store(path).create("reminder", "Water", NOW + 1, now=NOW)
    sent, lock = [], threading.Lock()

    def deliver(row):
        with lock:
            sent.append(row["id"])
        return True

    with ThreadPoolExecutor(2) as pool:
        counts = list(
            pool.map(
                lambda _: tasks.Store(path).tick(now=NOW + 2, reminder=deliver),
                range(2),
            )
        )
    assert sum(counts) == 1 and len(sent) == 1


def test_cancel_and_overdue_never_dispatch(tmp_path):
    store = tasks.Store(tmp_path / "schedule.sqlite")
    row = store.create("call", "Check in", NOW + 1, now=NOW)
    assert store.cancel(row["id"])
    reminder = store.create("reminder", "Old", NOW + 2, now=NOW)
    assert (
        store.tick(
            now=NOW + 3600,
            reminder=lambda _: pytest.fail("expired"),
            call=lambda _: pytest.fail("cancelled"),
        )
        == 0
    )
    assert store.get(row["id"])["status"] == "cancelled"
    assert store.get(reminder["id"])["status"] == "missed"


def test_call_receipt_is_invitation_not_completed_delivery(tmp_path):
    store = tasks.Store(tmp_path / "schedule.sqlite")
    row = store.create("call", "Check in", NOW + 1, now=NOW)
    store.tick(now=NOW + 2, call=lambda _: {"status": "invited", "reason": ""})
    assert store.get(row["id"])["status"] == "invited"


def test_failed_and_uncertain_dispatch_are_never_replayed(tmp_path):
    store = tasks.Store(tmp_path / "schedule.sqlite")
    row = store.create("reminder", "Water", NOW + 1, now=NOW)

    def interrupted(_):
        raise OSError("transport unavailable")

    store.tick(now=NOW + 2, reminder=interrupted)
    assert store.get(row["id"])["status"] == "failed"
    assert store.tick(now=NOW + 3, reminder=lambda _: pytest.fail("replayed")) == 0


def test_school_call_rejected_at_creation(tmp_path):
    due = dt.datetime(2026, 10, 19, 10, 0, tzinfo=ZONE).timestamp()
    with pytest.raises(tasks.ScheduleError, match=r"08:00.*15:30"):
        tasks.Store(tmp_path / "schedule.sqlite").create(
            "call", "Check in", due, now=NOW
        )


def test_crashed_claim_becomes_uncertain_without_replay(tmp_path):
    path = tmp_path / "schedule.sqlite"
    store = tasks.Store(path)
    row = store.create("reminder", "Water", NOW + 1, now=NOW)
    with store.db() as db:
        db.execute(
            "UPDATE scheduled_tasks SET status='dispatching',claimed=?", (NOW + 2,)
        )
    restarted = tasks.Store(path)
    assert (
        restarted.tick(
            now=NOW + 100, reminder=lambda _: pytest.fail("uncertain replay")
        )
        == 0
    )
    assert restarted.get(row["id"])["status"] == "uncertain"


def test_notify_delivery_uses_existing_immediate_route_and_stable_fingerprint(
    tmp_path, monkeypatch
):
    import notify

    sent = []
    monkeypatch.setattr(
        notify,
        "send",
        lambda text, **kwargs: (
            sent.append((text, kwargs))
            or {"ok": True, "queued": False, "route": "notification"}
        ),
    )
    store = tasks.Store(tmp_path / "schedule.sqlite")
    row = store.create("reminder", "Drink water", NOW + 1, now=NOW)
    assert store.tick(now=NOW + 2) == 1
    assert store.get(row["id"])["status"] == "notified"
    assert sent[0][1]["fingerprint"] == row["id"] and sent[0][1]["queue"] is True
    assert store.tick(now=NOW + 3) == 0 and len(sent) == 1


def test_scheduled_call_integrates_with_real_queue_without_ringing_device(
    tmp_path, monkeypatch
):
    import phone_calls

    queue = phone_calls.CallQueue(tmp_path / "calls.sqlite")
    queue.configure(True, False, False, NOW, listener=True)
    monkeypatch.setattr(phone_calls, "queue", lambda: queue)
    store = tasks.Store(tmp_path / "schedule.sqlite")
    row = store.create("call", "Time to study", NOW + 1, now=NOW)
    assert store.tick(now=NOW + 2) == 1
    assert store.get(row["id"])["status"] == "invited"
    assert store.tick(now=NOW + 3) == 0
    with queue.db() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM calls WHERE key=?", (row["id"],)
            ).fetchone()[0]
            == 1
        )


def test_scheduled_offline_call_records_blocked_not_delivered(tmp_path, monkeypatch):
    import phone_calls

    queue = phone_calls.CallQueue(tmp_path / "calls.sqlite")
    monkeypatch.setattr(phone_calls, "queue", lambda: queue)
    store = tasks.Store(tmp_path / "schedule.sqlite")
    row = store.create("call", "Check in", NOW + 1, now=NOW)
    store.tick(now=NOW + 2)
    assert store.get(row["id"])["status"] == "blocked"
    assert store.get(row["id"])["reason"] == "disabled"


def test_explicit_scheduler_does_not_require_proactive_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_PROACTIVE_ENGINE", "0")
    store = tasks.Store(tmp_path / "schedule.sqlite")
    store.create("reminder", "Water", NOW + 1, now=NOW)
    assert store.tick(now=NOW + 2, reminder=lambda _: True) == 1


@pytest.mark.parametrize(
    "day,when",
    [
        ((2026, 10, 31, 20), "tomorrow at 1:30am"),
        ((2026, 3, 7, 20), "tomorrow at 2:30am"),
    ],
)
def test_short_clock_rejects_dst_ambiguity_and_gap(monkeypatch, day, when):
    monkeypatch.setenv("JARVIS_TZ", "America/New_York")
    now = dt.datetime(*day, tzinfo=ZoneInfo("America/New_York")).timestamp()
    with pytest.raises(tasks.ScheduleError):
        tasks.parse_when(when, now=now)


def test_each_dispatch_rechecks_grace_after_slow_previous_handoff(tmp_path):
    store = tasks.Store(tmp_path / "schedule.sqlite")
    first = store.create("reminder", "First", NOW + 1, now=NOW)
    second = store.create("call", "Second", NOW + 2, now=NOW)
    clock = [NOW + 3]

    def deliver(_):
        clock[0] += 120
        return True

    assert (
        store.tick(
            now=NOW + 3,
            clock=lambda: clock[0],
            reminder=deliver,
            call=lambda _: pytest.fail("late call"),
        )
        == 1
    )
    assert store.get(first["id"])["status"] == "queued"
    assert store.get(second["id"])["status"] == "missed"


def test_due_reminder_never_discloses_to_guest(tmp_path, monkeypatch):
    import notify
    import phone_session

    store = tasks.Store(tmp_path / "schedule.sqlite")
    row = store.create("reminder", "Private appointment", NOW + 1, now=NOW)
    monkeypatch.setattr(phone_session, "guest_required", lambda: True)
    monkeypatch.setattr(notify, "send", lambda *a, **k: pytest.fail("guest disclosure"))
    store.tick(now=NOW + 2)
    assert store.get(row["id"])["status"] == "blocked"
    assert store.get(row["id"])["reason"] == "guest_mode"


@pytest.mark.parametrize("when", ["2026-10-18", "2026-10-18T"])
def test_iso_date_requires_explicit_time(when):
    with pytest.raises(tasks.ScheduleError):
        tasks.parse_when(when, now=NOW)
