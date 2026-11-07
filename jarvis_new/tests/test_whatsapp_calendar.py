"""Simulated WhatsApp messages use fake models and Calendar only."""

import datetime as dt
import json
import subprocess

import pytest

import event_extractor as ex
import whatsapp_calendar as wc

NOW = dt.datetime(2026, 11, 6, 12, tzinfo=dt.timezone.utc).timestamp()
EVENT = {
    "has_event": True,
    "title": "Dentist",
    "date": "2026-11-07",
    "start": "15:00",
    "all_day": False,
    "confidence": 0.95,
}


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_TZ", "Asia/Singapore")
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_CHATGPT", "1")
    monkeypatch.setenv("JARVIS_WHATSAPP_EVENTS", "1")
    monkeypatch.setenv("JARVIS_AUTO_CALENDAR", "1")
    monkeypatch.setattr("chatgpt_cli.shutil.which", lambda _: "/usr/bin/codex")
    return tmp_path


def model(event=EVENT):
    def runner(argv, **kw):
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": json.dumps(event)},
                }
            )
            + "\n"
            + json.dumps({"type": "turn.completed", "usage": {}}),
            "",
        )

    return runner


def msg(text="Your dentist appointment is confirmed tomorrow at 3pm.", **kw):
    return {
        "text": text,
        "when": "12:00, 06/11/2026",
        "sender": "Clinic",
        "me": False,
        **kw,
    }


def test_simulated_message_adds_once_and_preserves_source(home):
    made, notices = [], []
    assert wc.ingest_messages("Clinic", [msg()], now=NOW) == 1
    assert wc.ingest_messages("Clinic", [msg()], now=NOW) == 0
    results = wc.process_pending(
        now=NOW,
        runner=model(),
        lister=lambda *a, **k: [],
        inserter=lambda event: made.append(event) or {"id": "fake-1"},
        notify_fn=lambda *a, **kw: notices.append(kw),
    )
    assert results[0]["outcome"] == "auto-added"
    assert len(made) == 1 and notices[0]["source"] == "whatsapp"
    assert ex._ledger_load()[0]["source"] == "whatsapp"
    assert wc.process_pending(now=NOW, runner=model()) == []


def test_own_blocked_and_historical_messages_do_not_ingest(home, monkeypatch):
    monkeypatch.setenv("JARVIS_WA_BLOCK", "Blocked")
    assert wc.ingest_messages("Clinic", [msg(me=True)], now=NOW) == 0
    assert wc.ingest_messages("Blocked", [msg()], now=NOW) == 0
    assert wc.ingest_messages("Clinic", [msg(when="12:00, 01/01/2026")], now=NOW) == 0
    assert wc.ingest_messages("Clinic", [msg(when="")], now=NOW) == 0


def test_ambiguous_changes_are_review_only(home):
    assert (
        wc.ingest_messages(
            "Clinic", [msg("Appointment cancelled tomorrow at 3pm")], now=NOW
        )
        == 1
    )
    out = wc.process_pending(
        now=NOW, runner=model(), inserter=lambda _: pytest.fail("must not write")
    )
    assert out[0]["outcome"] == "review-required"


def test_cross_channel_duplicate(home):
    ex._ledger_save(
        [
            {
                "event_key": ex.event_key("Dentist", "2026-11-07"),
                "status": "added",
                "msg_id": "email-1",
            }
        ]
    )
    wc.ingest_messages("Clinic", [msg()], now=NOW)
    out = wc.process_pending(
        now=NOW,
        runner=model(),
        lister=lambda *a, **k: [],
        inserter=lambda _: pytest.fail("duplicate write"),
    )
    assert out[0]["outcome"] == "auto-duplicate"


def test_invalid_timed_event_is_not_silently_all_day(home):
    assert ex.validate({**EVENT, "start": "25:99"}, NOW) is None
    assert ex.validate({**EVENT, "confidence": float("nan")}, NOW) is None


def test_relative_date_prompt_anchors_source_timezone(home):
    prompt = ex.build_prompt(
        {
            "source": "whatsapp",
            "date": "2026-11-06T20:00:00+08:00",
            "body": "tomorrow 3pm",
        },
        dt.date(2026, 11, 6),
    )
    assert (
        "Asia/Singapore" in prompt
        and "WhatsApp" in prompt
        and "2026-11-06T20:00" in prompt
    )


def test_calendar_provider_conflict_reads_exact_event(home, monkeypatch):
    import google_api

    monkeypatch.setattr(google_api, "_calendar_token", lambda **kw: "fake-token")
    calls = []

    def request(method, url, *args, **kw):
        calls.append((method, url))
        if method == "POST":
            raise google_api.GoogleError("already exists", http_status=409)
        return {"id": "jarvis12345", "summary": "Dentist"}

    monkeypatch.setattr(google_api, "_request_json", request)
    made = google_api.calendar_create(
        {**ex.validate(EVENT, NOW), "jarvis_id": "jarvis12345"}
    )
    assert made["id"] == "jarvis12345"
    assert calls[1][0] == "GET" and calls[1][1].endswith("/events/jarvis12345")


def test_default_inserter_uses_same_provider_id_after_lost_ledger(home, monkeypatch):
    import google_api

    ids = []
    monkeypatch.setattr(
        google_api,
        "calendar_create",
        lambda ev, *a, **kw: ids.append(ev["jarvis_id"]) or {"id": ev["jarvis_id"]},
    )
    record = {
        "id": "wa-source-1",
        "source": "whatsapp",
        "event": ex.validate(EVENT, NOW),
    }
    assert (
        ex.auto_add_event(record, now=NOW, lister=lambda *a, **k: [])["status"]
        == "added"
    )
    ex.ledger_path().unlink()
    assert (
        ex.auto_add_event(record, now=NOW, lister=lambda *a, **k: [])["status"]
        == "added"
    )
    assert ids[0] == ids[1]


def test_status_never_exposes_message_bodies(home):
    import auto_calendar_bridge

    wc.ingest_messages(
        "Clinic", [msg("Appointment cancelled tomorrow at 3pm")], now=NOW
    )
    wc.process_pending(now=NOW)
    state = auto_calendar_bridge.status()
    assert len(state["pending"]) == 1
    assert "body" not in json.dumps(state)
    assert (
        auto_calendar_bridge.dismiss(
            {"id": state["pending"][0]["id"], "action": "dismiss"}
        )[0]
        == 200
    )
    assert auto_calendar_bridge.status()["pending"] == []


def test_distinct_same_title_same_day_times_are_both_preserved(home):
    made = []

    def ins(ev):
        return made.append(ev) or {"id": str(len(made))}

    one = {"id": "one", "event": ex.validate(EVENT, NOW)}
    two = {"id": "two", "event": ex.validate({**EVENT, "start": "17:00"}, NOW)}
    assert (
        ex.auto_add_event(one, now=NOW, lister=lambda *a, **k: [], inserter=ins)[
            "status"
        ]
        == "added"
    )
    assert (
        ex.auto_add_event(two, now=NOW, lister=lambda *a, **k: [], inserter=ins)[
            "status"
        ]
        == "added"
    )
    assert len(made) == 2


def test_cancellation_in_same_scan_quarantines_previous_invitation(home):
    wc.ingest_messages(
        "Clinic",
        [msg(), msg("Appointment cancelled", when="13:00, 06/11/2026")],
        now=NOW,
    )
    out = wc.process_pending(
        now=NOW, inserter=lambda _: pytest.fail("stale invitation must not write")
    )
    assert all(row["outcome"] == "review-required" for row in out)


def test_explicit_joined_group_announcement_is_ingested(home):
    announcement = msg(
        "All Saturday shuttlers: training tomorrow at 1pm in the sports hall."
    )
    assert wc.ingest_messages("Training", [announcement], now=NOW, is_group=True) == 1


def test_third_party_group_plan_without_addressing_owner_is_ignored(home):
    assert (
        wc.ingest_messages(
            "Friends",
            [msg("My dentist appointment tomorrow at 3pm")],
            now=NOW,
            is_group=True,
        )
        == 0
    )


def test_calendar_dedupe_covers_singapore_early_morning(home):
    event = {**ex.validate(EVENT, NOW), "start": "01:00"}
    bounds = []

    def lister(a, b, **kw):
        bounds.append((a, b))
        return [{"summary": "Dentist", "start": {"dateTime": "2026-11-06T17:00:00Z"}}]

    assert ex.already_on_calendar(event, lister)
    assert bounds[0][0] == "2026-11-07T00:00:00+08:00"


def test_extractor_outage_retries_without_losing_source(home, monkeypatch):
    wc.ingest_messages("Clinic", [msg()], now=NOW)
    monkeypatch.setattr(ex, "extract", lambda *a, **kw: (None, "temporary timeout"))
    out = wc.process_pending(now=NOW)
    assert out[0]["outcome"] == "extract-retry"
    row = next(iter(wc._load()["messages"].values()))
    assert row["body"] and row["retry_at"] > NOW
    assert wc.process_pending(now=NOW + 1) == []
    monkeypatch.setattr(ex, "extract", lambda *a, **kw: (ex.validate(EVENT, NOW), None))
    made = []
    out = wc.process_pending(
        now=NOW + 61,
        lister=lambda *a, **k: [],
        inserter=lambda ev: made.append(ev) or {"id": "recovered"},
        notify_fn=lambda *a, **kw: None,
    )
    assert out[0]["outcome"] == "auto-added" and len(made) == 1
    assert "body" not in next(iter(wc._load()["messages"].values()))


@pytest.mark.parametrize(
    "patch", [{"has_event": "false"}, {"all_day": "false"}, {"confidence": True}]
)
def test_malformed_model_boolean_fields_are_rejected(home, patch):
    assert ex.validate({**EVENT, **patch}, NOW) is None


def test_shared_calendar_stores_are_private(home):
    import stat

    ex.save({"id": "private-source", "status": "pending", "event": {}})
    ex._ledger_save([])
    assert stat.S_IMODE(ex._path("private-source").stat().st_mode) == 0o600
    assert stat.S_IMODE(ex.ledger_path().stat().st_mode) == 0o600


def test_private_phone_reads_cannot_stage_background_calendar_work(home, monkeypatch):
    monkeypatch.setattr("phone_session.private", lambda: True)
    assert wc.ingest_messages("Clinic", [msg()], now=NOW) == 0
    assert wc._load()["messages"] == {}


def test_destination_change_does_not_suppress_new_calendar_event(home, monkeypatch):
    made = []

    def ins(ev):
        made.append(ev)
        return {"id": str(len(made))}

    one = {"id": "first-source", "event": ex.validate(EVENT, NOW)}
    two = {"id": "second-source", "event": ex.validate(EVENT, NOW)}
    assert (
        ex.auto_add_event(one, now=NOW, lister=lambda *a, **k: [], inserter=ins)[
            "status"
        ]
        == "added"
    )
    monkeypatch.setenv("JARVIS_CALENDAR_ID", "other-calendar")
    assert (
        ex.auto_add_event(two, now=NOW, lister=lambda *a, **k: [], inserter=ins)[
            "status"
        ]
        == "added"
    )
    assert ex._ledger_load()[0]["calendar_id"] == "primary"


def test_tutor_alternative_times_require_review(home):
    wc.ingest_messages(
        "Tutor",
        [
            msg(
                "Are you fine with face to face class arnd 2 on Monday or anytime on Tuesday"
            )
        ],
        now=NOW,
    )
    assert (
        wc.process_pending(
            now=NOW, inserter=lambda _: pytest.fail("ambiguous options")
        )[0]["outcome"]
        == "review-required"
    )


@pytest.mark.asyncio
async def test_failed_read_retries_after_chat_is_marked_read(home):
    class FakeWA:
        calls = 0

        async def list_chats(self, n):
            return [{"name": "Clinic", "unread": 1 if self.calls == 0 else 0}]

        async def read_chat(self, name, n):
            self.calls += 1
            if self.calls == 1:
                return {"ok": False, "err": "DOM read failed after opening"}
            return {
                "ok": True,
                "name": "Clinic",
                "group_state": "contact",
                "messages": [msg()],
            }

    wa = FakeWA()
    await wc.run(wa, now=NOW)
    assert wc._load()["read_jobs"]["Clinic"]["last_error"]
    assert await wc.run(wa, now=NOW + 1) == [] and wa.calls == 1
    made = []
    out = await wc.run(
        wa,
        now=NOW + 61,
        runner=model(),
        lister=lambda *a, **kw: [],
        inserter=lambda ev: made.append(ev) or {"id": "recovery"},
        notify_fn=lambda *a, **kw: None,
    )
    assert wa.calls == 2 and len(made) == 1 and out[0]["outcome"] == "auto-added"
    assert wc._load()["read_jobs"] == {}


def test_bridge_calendar_activity_refuses_guest(home, monkeypatch):
    from test_bridge import _get

    import bridge

    monkeypatch.setattr(bridge, "_effective_guest", lambda body: True)
    server, _ = bridge._run_in_thread()
    try:
        code, body = _get(server, "/auto-calendar/status")
        assert code == 403 and body["ok"] is False
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_reconciliation_stages_all_names_before_first_read_and_resumes(home):
    class FakeWA:
        def __init__(self):
            self.calls = []

        async def list_chats(self, n):
            return [{"name": f"Clinic {i}", "unread": 0} for i in range(30)]

        async def read_chat(self, name, n):
            # A crash/read marker cannot erase the remaining reconciliation set.
            if not self.calls:
                assert set(wc._load()["read_jobs"]) == {
                    f"Clinic {i}" for i in range(30)
                }
            self.calls.append(name)
            return {"ok": True, "name": name, "group_state": "contact", "messages": []}

    wa = FakeWA()
    await wc.run(wa, now=NOW, scan_all=True)
    assert len(wa.calls) == 20
    assert len(wc._load()["read_jobs"]) == 10
    await wc.run(wa, now=NOW + 90)
    assert len(wa.calls) == 30 and len(set(wa.calls)) == 30
    assert wc._load()["read_jobs"] == {}


@pytest.mark.asyncio
async def test_failed_chat_read_preserves_identity_and_error_history(home):
    class FakeWA:
        async def list_chats(self, n):
            return [{"name": "Clinic exact name", "unread": 1}]

        async def read_chat(self, name, n):
            return {"ok": False, "err": "conversation changed while reading"}

    await wc.run(FakeWA(), now=NOW)
    history = wc._load()["read_history"]
    assert history[-1]["name"] == "Clinic exact name"
    assert history[-1]["status"] == "retry"
    assert "conversation changed" in history[-1]["error"]
    assert "body" not in history[-1]


@pytest.mark.asyncio
async def test_requested_reconciliation_is_consumed_by_normal_watcher(home):
    wc.request_reconciliation(now=NOW)
    assert wc._load()["reconciliation_requested_at"] == NOW

    class FakeWA:
        async def list_chats(self, n):
            return [{"name": "Clinic", "unread": 0}]

        async def read_chat(self, name, n):
            return {"ok": True, "name": name, "group_state": "contact", "messages": []}

    await wc.run(FakeWA(), now=NOW + 20)
    state = wc._load()
    assert "reconciliation_requested_at" not in state
    assert state["last_reconciliation"]["names"] == ["Clinic"]
    assert state["read_history"][-1]["status"] == "read"


def test_provider_check_is_separate_from_historical_add_receipt(home):
    import auto_calendar_bridge as cb

    cb.record_provider_check(
        {"id": "provider-id", "status": "cancelled", "updated": "2026-11-07T00:00:00Z"},
        account="personal",
        calendar_id="primary",
        now=NOW,
    )
    row = cb._display(
        {"google_id": "provider-id", "status": "added", "title": "Original addition"}
    )
    assert row["status"] == "added"
    assert row["provider_status"] == "cancelled"
    assert "Current calendar status: cancelled" in row["reason"]
    assert "provider-id" in (home / "auto_calendar_provider_checks.json").read_text()
    assert (home / "auto_calendar_provider_checks.json").stat().st_mode & 0o777 == 0o600
