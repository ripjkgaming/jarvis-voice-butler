"""Real queue + policy simulation, with synthetic clock and output devices only."""

import datetime as dt
import fcntl

import pytest

import notify
from proactive import engine
from proactive.sources import calendar_source


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    import phone_calls

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(notify, "_in_quiet", lambda _: False)
    monkeypatch.setattr(notify, "_input_state", lambda: "idle")
    monkeypatch.setattr(notify, "_toast", lambda *_: True)
    monkeypatch.setattr(notify, "_log", lambda *_: None)
    monkeypatch.setattr(phone_calls, "offer_signal", lambda *_: None)
    notify._seen.clear()


def calendar_event(start=10_000):
    iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()  # noqa: E731
    return {
        "id": "wa-meeting",
        "summary": "WhatsApp planning",
        "start": {"dateTime": iso(start)},
        "end": {"dateTime": iso(start + 3600)},
    }


def worker(events, idle):
    return engine.ProactiveEngine(
        [lambda now: calendar_source.signals_for(events, now)],
        context_fn=lambda *_: engine.Context(idle_s=idle),
        say=lambda _: pytest.fail("Must not speak into an empty room"),
        log=lambda *_: None,
    )


def test_absence_restart_return_and_second_return_announce_once():
    events = [calendar_event()]
    # Due while away: high urgency must still persist until the owner returns.
    assert worker(events, 1800).run_once(9800)[0][1].reason == "away"
    assert len(notify.load_queue()) == 1
    notify._seen.clear()  # process-memory disappears on restart
    assert worker(events, 1800).run_once(9805) == []
    assert len(notify.load_queue()) == 1
    spoken = []

    def deliver(**kw):
        return notify.drain(speaker=lambda text, **_: spoken.append(text) or True, **kw)

    announcer = notify.Announcer()
    assert (
        announcer.tick(9810, state="idle", recent=False, do_drain=deliver)
        == "waiting-idle"
    )
    assert (
        announcer.tick(9815, state="active", recent=True, do_drain=deliver)
        == "waiting-sustained-input"
    )
    assert (
        announcer.tick(9820, state="unknown", recent=True, do_drain=deliver)
        == "waiting-unknown"
    )
    for now in (9825, 9830, 9835):
        announcer.tick(now, state="active", recent=True, do_drain=deliver)
    assert len(spoken) == 1 and "WhatsApp planning" in spoken[0]
    assert "starts in" not in spoken[0]  # queued relative wording would become false
    assert notify.load_queue() == []
    announcer.tick(9900, state="idle", recent=False, do_drain=deliver)
    for now in (9910, 9915, 9920):
        announcer.tick(now, state="active", recent=True, do_drain=deliver)
    assert len(spoken) == 1


def test_offline_speech_retries_and_only_acknowledges_success():
    worker([calendar_event()], None).run_once(9800)
    assert notify.drain(now=9805, speaker=lambda *_a, **_k: False)["spoken"] == 0
    assert len(notify.load_queue()) == 1
    assert notify.drain(now=9810, speaker=lambda *_a, **_k: True)["spoken"] == 1
    assert notify.load_queue() == []


def test_later_stage_replaces_earlier_stage_and_cancel_invalidates():
    events = [calendar_event()]
    worker(events, 1800).run_once(9400)
    worker(events, 1800).run_once(9800)
    assert len(notify.load_queue()) == 1
    assert notify.cancel_calendar_reminders("wa-meeting") == 1
    assert notify.drain(now=9810)["spoken"] == 0


def test_reschedule_with_same_google_id_creates_a_new_reminder():
    worker([calendar_event()], 1800).run_once(9800)
    notify.cancel_calendar_reminders("wa-meeting")
    assert worker([calendar_event(12000)], 1800).run_once(11800)
    assert len(notify.load_queue()) == 1


def test_durable_send_receipt_prevents_restart_duplicate_but_failure_retries():
    kwargs = {"fingerprint": "wa-1", "now": 1000, "toaster": lambda *_: False}
    result = notify.send("Added planning", **kwargs)
    assert result["queued"]
    notify._seen.clear()
    assert notify.send("Added planning", **kwargs)["route"] == "deduped"
    assert len(notify.load_queue()) == 1
    kwargs.update(fingerprint="wa-2", queue=False)
    assert notify.send("No output available", **kwargs)["ok"] is False
    assert notify.send("No output available", **kwargs)["route"] != "deduped"


def test_partial_speech_failure_does_not_repeat_already_delivered_item():
    for text in ("first", "second"):
        notify.enqueue({"ts": 1000, "text": text})

    def speaker(text, **_):
        if text == "second":
            raise OSError("speaker disconnected")
        return True

    assert notify.drain(now=1001, speaker=speaker)["spoken"] == 1
    assert [item["text"] for item in notify.load_queue()] == ["second"]


def test_expired_corrupt_entries_removed_and_overlapping_consumer_skips():
    notify.enqueue({"ts": "bad", "text": "corrupt"})
    notify.enqueue({"ts": 1000, "expires_at": 1005, "text": "expired"})
    assert notify.drain(now=1010)["spoken"] == 0
    assert notify.load_queue() == []
    notify.enqueue({"ts": 1010, "text": "pending"})
    with notify.queue_path().with_name("notify_drain.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert notify.drain(now=1011)["mode"] == "busy"
    assert len(notify.load_queue()) == 1


def test_calendar_survives_days_away_and_general_notification_pressure():
    worker([calendar_event()], 1800).run_once(9800)
    for i in range(60):
        notify.enqueue({"ts": 9800, "text": f"General notification {i}"})
    assert len([d for d in notify.load_queue() if d.get("event_id")]) == 1
    spoken = []
    result = notify.drain(
        now=9800 + 3 * 86400, speaker=lambda text, **_: spoken.append(text) or True
    )
    assert result["spoken"] == 1
    assert "WhatsApp planning" in spoken[0]
    assert notify.load_queue() == []


def test_many_calendar_reminders_are_never_summarized_or_truncated():
    for i in range(5):
        notify.enqueue(
            {
                "ts": 1000,
                "event_id": str(i),
                "text": "Reminder " + str(i),
                "speak_text": "Calendar reminder " + str(i) + ": " + "details " * 20,
            }
        )
    spoken = []
    for i in range(5):
        result = notify.drain(
            now=1100,
            speaker=lambda text, **_: spoken.append(text) or True,
            summarizer=lambda _: pytest.fail("calendar must preserve detail"),
        )
        assert result["spoken"] == 1
        assert len(notify.load_queue()) == 4 - i
    assert len(spoken) == 5 and all("details " * 20 in text for text in spoken)


def test_whatsapp_ingest_calendar_write_and_return_are_one_persisted_flow(monkeypatch):
    import json
    import subprocess

    import whatsapp_calendar as wc

    monkeypatch.setenv("JARVIS_TZ", "Asia/Singapore")
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_CHATGPT", "1")
    monkeypatch.setenv("JARVIS_WHATSAPP_EVENTS", "1")
    monkeypatch.setenv("JARVIS_AUTO_CALENDAR", "1")
    monkeypatch.setattr("chatgpt_cli.shutil.which", lambda _: "/fake/codex")
    now = dt.datetime(2026, 11, 6, 12, tzinfo=dt.timezone.utc).timestamp()
    event = {
        "has_event": True,
        "title": "Dentist",
        "date": "2026-11-07",
        "start": "15:00",
        "all_day": False,
        "confidence": 0.95,
    }

    def runner(argv, **_):
        payload = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps(event)},
        }
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps(payload)
            + "\n"
            + json.dumps({"type": "turn.completed", "usage": {}}),
            "",
        )

    assert (
        wc.ingest_messages(
            "Clinic",
            [
                {
                    "text": "Appointment confirmed tomorrow 3pm",
                    "timestamp": now,
                    "sender": "Clinic",
                    "me": False,
                }
            ],
            now=now,
        )
        == 1
    )
    made = []

    def insert(event):
        made.append(event)
        return {"id": "wa-meeting"}

    result = wc.process_pending(
        now=now,
        runner=runner,
        lister=lambda *a, **k: [],
        inserter=insert,
        notify_fn=lambda *a, **k: None,
    )
    assert result[0]["outcome"] == "auto-added" and len(made) == 1
    assert made[0]["date"] == "2026-11-07" and made[0]["start"] == "15:00"
    start = dt.datetime(2026, 11, 7, 7, tzinfo=dt.timezone.utc).timestamp()
    events = [{**calendar_event(start), "summary": made[0]["title"]}]
    worker(events, 1800).run_once(start - 200)
    spoken = []

    def deliver(**kwargs):
        return notify.drain(
            speaker=lambda text, **_: spoken.append(text) or True, **kwargs
        )

    announcer = notify.Announcer()
    for offset in (0, 5, 10):
        announcer.tick(
            start - 180 + offset, state="active", recent=True, do_drain=deliver
        )
    assert len(spoken) == 1 and "Dentist" in spoken[0]
    assert not notify.load_queue()
    assert wc.process_pending(now=now, runner=runner) == []


def test_queued_calendar_clock_uses_configured_timezone(monkeypatch):
    monkeypatch.setenv("JARVIS_TZ", "Asia/Singapore")
    start = dt.datetime(2026, 11, 7, 7, tzinfo=dt.timezone.utc).timestamp()
    signal = calendar_source.signals_for([calendar_event(start)], start - 200)[0]
    assert "07 Nov 15:00" in signal.detail


@pytest.mark.parametrize("suppression", ["in_call", "muted", "school"])
def test_actual_speech_policy_keeps_calendar_until_audio_eligible(
    monkeypatch, suppression
):
    import school
    import speak

    status = {"in_call": suppression == "in_call", "muted": suppression == "muted"}
    school_mode = suppression == "school"
    monkeypatch.setattr(notify, "_under_test", lambda: False)
    monkeypatch.setattr(speak, "wake_status", lambda: status)
    monkeypatch.setattr(school, "is_school", lambda: school_mode)
    monkeypatch.setattr(speak, "announce_call_enabled", lambda: False)
    dispatches = []

    class FakeThread:
        def __init__(self, **kwargs):
            dispatches.append(kwargs)

        def start(self):
            pass

    monkeypatch.setattr(speak.threading, "Thread", FakeThread)
    notify.enqueue({"ts": 1000, "event_id": "voice-test", "text": "Calendar reminder"})
    announcer = notify.Announcer()
    for now in (1000, 1005, 1010, 1015, 1020, 1025):
        announcer.tick(now, state="active", recent=True)
    assert len(notify.load_queue()) == 1
    assert dispatches == []  # no repeated toast-only pseudo-deliveries
    status.clear()
    school_mode = False
    for now in (1030, 1035, 1040):
        announcer.tick(now, state="active", recent=True)
    assert notify.load_queue() == []
    assert len(dispatches) == 1 and dispatches[0]["args"][2] is True


def test_identical_spoken_words_do_not_acknowledge_a_different_calendar_event():
    for event_id in ("a", "b"):
        notify.enqueue({"event_id": event_id, "ts": 1000, "text": "Meeting"})
    assert notify.drain(now=1001, speaker=lambda *a, **k: True)["spoken"] == 1
    assert len(notify.load_queue()) == 1
    assert notify.load_queue()[0]["event_id"] == "b"
