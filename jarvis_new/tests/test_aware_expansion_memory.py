from __future__ import annotations

from aware import expansion_memory as em


def setup_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("JARVIS_VAULT", str(tmp_path / "vault"))
    from aware import state as st

    st.update(enabled=True)


def test_record_is_idempotent_and_writes_readable_vault_entry(tmp_path, monkeypatch):
    setup_paths(tmp_path, monkeypatch)
    data = {"summary": "Dentist appointment Tuesday", "message": "raw source"}
    assert em.record(
        "commitment", "whatsapp:chat-1", 1_800_000_000, 0.9, data, key="msg-1"
    )
    assert not em.record(
        "commitment", "whatsapp:chat-1", 1_800_000_000, 0.9, data, key="msg-1"
    )

    note = next((tmp_path / "vault").rglob("*.md")).read_text()
    assert "Dentist appointment Tuesday" in note
    assert "raw source" not in note
    assert em.context(limit=5)[0]["summary"] == "Dentist appointment Tuesday"
    assert em.status()["total"] == 1
    assert em.status()["sources"]["whatsapp:chat-1"] == 1
    assert em.status()["retries"] == 0


def test_derived_summaries_use_vault_redaction(tmp_path, monkeypatch):
    setup_paths(tmp_path, monkeypatch)
    em.record(
        "commitment",
        "whatsapp:chat-1",
        1_800_000_000,
        0.8,
        {"summary": "Meeting tomorrow; password is letmein123"},
        key="m-secret",
    )
    row = em.context(limit=1)[0]
    note = next((tmp_path / "vault").rglob("*.md")).read_text()
    assert "letmein123" not in row["summary"]
    assert "letmein123" not in note


def test_corrections_cannot_supersede_another_source(tmp_path, monkeypatch):
    setup_paths(tmp_path, monkeypatch)
    em.record(
        "commitment", "whatsapp:chat-1", 100, 0.8, {"summary": "Plan A"}, key="plan-1"
    )
    em.record(
        "commitment",
        "whatsapp:chat-2",
        200,
        0.9,
        {"summary": "Unrelated message", "corrects": "plan-1"},
        key="other-1",
    )
    assert all(row["superseded_by"] is None for row in em.context(limit=10))


def test_correction_supersedes_old_memory_without_losing_history(tmp_path, monkeypatch):
    setup_paths(tmp_path, monkeypatch)
    em.record(
        "commitment",
        "whatsapp:chat-1",
        100,
        0.8,
        {"summary": "Meeting at 3"},
        key="plan-1",
    )
    em.record(
        "commitment",
        "whatsapp:chat-1",
        200,
        0.95,
        {"summary": "Meeting moved to 4", "corrects": "plan-1"},
        key="correction-1",
    )

    rows = em.context(limit=10)
    assert len(rows) == 2
    assert rows[0]["summary"] == "Meeting moved to 4"
    assert rows[0]["supersedes"] == rows[1]["id"]
    assert rows[1]["superseded_by"] == rows[0]["id"]
    assert len(em.context(limit=10, current_only=True)) == 1


def test_exercise_events_preserve_departure_and_completion_as_distinct_facts(
    tmp_path, monkeypatch
):
    setup_paths(tmp_path, monkeypatch)
    em.record(
        "exercise_departure",
        "camera",
        100,
        0.99,
        {"summary": "Left desk after prompt"},
        key="event-1",
    )
    em.record(
        "exercise_completed",
        "user_report",
        200,
        0.8,
        {"summary": "Reported completing pull-ups"},
        key="event-2",
    )

    rows = em.context(limit=5)
    assert {row["kind"] for row in rows} == {"exercise_departure", "exercise_completed"}
    departure = next(row for row in rows if row["kind"] == "exercise_departure")
    completed = next(row for row in rows if row["kind"] == "exercise_completed")
    assert "pull-ups" not in departure["summary"]
    assert "pull-ups" in completed["summary"]


def test_status_summarizes_outcomes_and_retry_without_exposing_source_data(
    tmp_path, monkeypatch
):
    setup_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(em, "_vault_append", lambda *args: False)
    em.record(
        "health_incident",
        "diagnostics",
        100,
        0.7,
        {"summary": "Disk nearly full", "raw": "private"},
        key="disk-1",
    )

    result = em.status()
    assert result["retries"] == 1
    assert result["outcomes"]["vault_write_pending"] == 1
    assert "Disk nearly full" not in str(result)
    assert "private" not in str(result)
    monkeypatch.setattr(em, "_vault_append", lambda *args: True)
    assert em.retry_pending() == 1
    assert em.status()["retries"] == 0


def test_process_pending_records_only_plans_and_acks_composite_ids(
    tmp_path, monkeypatch
):
    from aware.message_store import Store

    setup_paths(tmp_path, monkeypatch)
    store = Store(":memory:")
    store.ingest(
        "chat-1",
        "A",
        [{"id": "same-id", "timestamp": 1_800_000_000, "text": "Meeting tomorrow?"}],
        boundary=1_700_000_000,
        complete=True,
        now=1_800_000_000,
    )
    store.ingest(
        "chat-2",
        "B",
        [{"id": "same-id", "timestamp": 1_800_000_001, "text": "Thanks, got it."}],
        boundary=1_700_000_000,
        complete=True,
        now=1_800_000_001,
    )

    result = em.process_pending(store, now=1_800_000_002)
    assert result["processed"] == 2
    assert result["recorded"] == 1
    assert result["skipped"] == 1
    assert result["acknowledged"] == 2
    assert result["retrying"] == 0
    assert result["stopped"] == ""
    assert store.pending("memory") == []
    rows = em.context(limit=10, kinds=["commitment"])
    assert len(rows) == 1
    assert rows[0]["source"] == "whatsapp:chat-1"


def test_process_pending_leaves_message_unacked_when_record_fails(
    tmp_path, monkeypatch
):
    from aware.message_store import Store

    setup_paths(tmp_path, monkeypatch)
    store = Store(":memory:")
    store.ingest(
        "chat",
        "Chat",
        [{"id": "m1", "timestamp": 10, "text": "Meeting tomorrow"}],
        boundary=0,
        complete=True,
        now=10,
    )
    monkeypatch.setattr(
        em, "record", lambda *args, **kwargs: (_ for _ in ()).throw(OSError())
    )

    result = em.process_pending(store, now=11)
    assert result["retrying"] == 1
    assert [(m["chat_id"], m["id"]) for m in store.pending("memory")] == [
        ("chat", "m1")
    ]


def test_explicit_cancellation_supersedes_only_matching_plan(tmp_path, monkeypatch):
    from aware.message_store import Store

    setup_paths(tmp_path, monkeypatch)
    store = Store(":memory:")
    store.ingest(
        "chat",
        "Chat",
        [
            {
                "id": "m1",
                "timestamp": 10,
                "order": 1,
                "text": "Dentist appointment Tuesday at 3",
            },
            {
                "id": "m2",
                "timestamp": 11,
                "order": 2,
                "text": "Cancel dentist appointment",
            },
            {"id": "m3", "timestamp": 12, "order": 3, "text": "Cancel the flight"},
        ],
        boundary=0,
        complete=True,
        now=12,
    )

    result = em.process_pending(store, now=13)
    rows = em.context(limit=10, kinds=["commitment"], source="whatsapp:chat")
    original = next(
        row for row in rows if row["summary"].startswith("Possible upcoming")
    )
    cancellation = next(
        row for row in rows if "cancellation of prior" in row["summary"]
    )
    unmatched = next(row for row in rows if "unmatched cancellation" in row["summary"])
    assert result["recorded"] == 3
    assert cancellation["supersedes"] == original["id"]
    assert original["superseded_by"] == cancellation["id"]
    assert cancellation["state"] == "cancelled"
    assert unmatched["supersedes"] is None
    assert unmatched["state"] == "unmatched_change"


def test_reschedule_supersedes_matching_plan_and_keeps_new_plan_active(
    tmp_path, monkeypatch
):
    from aware.message_store import Store

    setup_paths(tmp_path, monkeypatch)
    store = Store(":memory:")
    store.ingest(
        "chat",
        "Chat",
        [
            {
                "id": "m1",
                "timestamp": 10,
                "order": 1,
                "text": "Dentist appointment Tuesday",
            },
            {
                "id": "m2",
                "timestamp": 11,
                "order": 2,
                "text": "Move dentist appointment to Thursday",
            },
        ],
        boundary=0,
        complete=True,
        now=11,
    )

    em.process_pending(store, now=12)
    rows = em.context(limit=10, kinds=["commitment"], source="whatsapp:chat")
    previous = next(
        row for row in rows if row["summary"].startswith("Possible upcoming")
    )
    changed = next(row for row in rows if "rescheduled commitment" in row["summary"])
    assert changed["supersedes"] == previous["id"]
    assert previous["superseded_by"] == changed["id"]
    assert changed["state"] == "active"


def test_live_control_stop_between_messages_leaves_later_message_pending(
    tmp_path, monkeypatch
):
    from aware.message_store import Store

    setup_paths(tmp_path, monkeypatch)
    store = Store(":memory:")
    store.ingest(
        "chat",
        "Chat",
        [
            {"id": "m1", "timestamp": 10, "order": 1, "text": "Meeting tomorrow"},
            {"id": "m2", "timestamp": 11, "order": 2, "text": "Appointment tomorrow"},
        ],
        boundary=0,
        complete=True,
        now=11,
    )
    controls = iter((True, True, False))
    monkeypatch.setattr(em, "_controls_on", lambda: next(controls))

    result = em.process_pending(store, now=12)
    assert result["processed"] == 1
    assert result["acknowledged"] == 1
    assert result["stopped"] == "controls_changed"
    assert [(m["chat_id"], m["id"]) for m in store.pending("memory")] == [
        ("chat", "m2")
    ]


def test_process_pending_holds_partial_bootstrap_and_closes_owned_store(
    tmp_path, monkeypatch
):
    from aware import message_store

    setup_paths(tmp_path, monkeypatch)
    store = message_store.Store(":memory:")
    store.ingest(
        "chat",
        "Chat",
        [{"id": "m1", "timestamp": 10, "text": "Meeting tomorrow"}],
        boundary=0,
        complete=False,
        now=10,
    )
    closed = []

    class OwnedStore:
        def pending(self, *args, **kwargs):
            return store.pending(*args, **kwargs)

        def acknowledge(self, *args, **kwargs):
            return store.acknowledge(*args, **kwargs)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(message_store, "Store", OwnedStore)
    result = em.process_pending(now=11)
    assert result["processed"] == 0
    assert result["recorded"] == 0
    assert [(m["chat_id"], m["id"]) for m in store.pending("memory")] == [
        ("chat", "m1")
    ]
    assert store.pending("memory", complete_only=True) == []
    assert closed == [True]


def test_control_status_uses_fresh_wall_time(tmp_path, monkeypatch):
    from aware import state as st

    setup_paths(tmp_path, monkeypatch)
    st.update(enabled=True, paused_until=20)
    monkeypatch.setattr(em, "_wall_time", lambda: 10)
    assert not em._controls_on()
    monkeypatch.setattr(em, "_wall_time", lambda: 21)
    assert em._controls_on()
