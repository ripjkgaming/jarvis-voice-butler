"""Hermetic tests for src/mail_watch_job.py _handle_one (no network)."""

from __future__ import annotations

import base64
import json
import os
import time

import pytest

import draft_engine
import mail_watch_job
from system import inbox as inbox_mod


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _full(
    msg_id: str,
    sender: str,
    subject: str,
    body: str,
    thread: str = "thread-1",
    extra_headers: tuple = (),
    snippet: str = "",
) -> dict:
    headers = [
        {"name": "From", "value": sender},
        {"name": "Subject", "value": subject},
        {"name": "Message-ID", "value": f"<{msg_id}@mail>"},
        {"name": "Date", "value": "Mon, 29 Sep 2026 09:00:00 +0000"},
        *({"name": k, "value": v} for k, v in extra_headers),
    ]
    return {
        "id": msg_id,
        "threadId": thread,
        "snippet": snippet or body[:80],
        "payload": {
            "headers": headers,
            "mimeType": "multipart/alternative",
            "parts": [{"mimeType": "text/plain", "body": {"data": _b64(body)}}],
        },
    }


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    """Isolated inbox + whatsapp/desktop + model fakes for _handle_one."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis-home"))
    # Keyword triage only by default; urgency-AI tests turn it on themselves.
    monkeypatch.setenv("JARVIS_URGENCY_AI", "0")
    monkeypatch.setenv("JARVIS_DRAFTS", "1")
    monkeypatch.setenv("JARVIS_AUTOREPLY", "1")
    monkeypatch.delenv("JARVIS_OWNER_EMAIL", raising=False)
    mail_watch_job._run["drafts"] = 0

    state = {"payloads": {}, "sends": [], "model_calls": [], "pings": []}
    model_text = json.dumps(
        {"needs_reply": True, "body": "Thanks, will do.", "summary": "A request."}
    )
    state["model_text"] = model_text

    monkeypatch.setattr(inbox_mod, "_gmail_access_token", lambda: "tok")

    def fake_api(path, token, params=None):
        assert token == "tok"
        return state["payloads"][path.rsplit("/", 1)[-1]]

    monkeypatch.setattr(inbox_mod, "_gmail_api", fake_api)
    monkeypatch.setattr(
        inbox_mod,
        "_gmail_send_api",
        lambda tok, raw, tid=None: (
            state["sends"].append((tok, raw, tid)) or {"id": "sent-1"}
        ),
    )

    async def fake_ping(text):
        state["pings"].append(text)
        return True

    monkeypatch.setattr(mail_watch_job, "_ping_whatsapp", fake_ping)

    def fake_notify(text, **kw):
        state.setdefault("notifies", []).append((text, kw))
        return {"route": "notification", "ok": True}

    import notify as _notify

    monkeypatch.setattr(_notify, "send", fake_notify)

    def fake_reply(prompt, **kw):
        state["model_calls"].append(prompt)
        return (state["model_text"], None)

    monkeypatch.setattr(draft_engine.claude_cli, "claude_reply", fake_reply)
    return state


async def test_normal_human_creates_pending_draft(fakes) -> None:
    fakes["payloads"]["m1"] = _full(
        "m1", "friend@mailbox.org", "Hello there", "Can we meet tomorrow?"
    )
    outcome = await mail_watch_job._handle_one("m1")
    assert outcome == "normal-logged"
    record = draft_engine.read_draft("m1")
    assert record is not None and record["status"] == "pending"
    assert record["body"] == "Thanks, will do."
    assert fakes["sends"] == []


async def test_bulk_message_creates_no_draft(fakes) -> None:
    fakes["payloads"]["m2"] = _full(
        "m2",
        "news@mailbox.org",
        "Weekly deals",
        "Sale now on.",
        extra_headers=(("List-Unsubscribe", "<mailto:u@x>"),),
    )
    outcome = await mail_watch_job._handle_one("m2")
    assert outcome == "normal-logged"
    assert draft_engine.read_draft("m2") is None
    assert fakes["model_calls"] == []
    assert fakes["sends"] == []


async def test_high_priority_autoconfirm_and_high_draft(fakes) -> None:
    fakes["payloads"]["m3"] = _full(
        "m3", "teacher@school.com", "URGENT: exam", "Exam moved to Friday."
    )
    outcome = await mail_watch_job._handle_one("m3")
    assert "replied=autoconfirm" in outcome
    assert len(fakes["sends"]) == 1
    record = draft_engine.read_draft("m3")
    assert record is not None and record["priority"] == "high"


async def test_skip_level_owner_gets_nothing(fakes, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_OWNER_EMAIL", "owner@mailbox.org")
    fakes["payloads"]["m4"] = _full(
        "m4", "owner@mailbox.org", "note to self", "Remember milk."
    )
    outcome = await mail_watch_job._handle_one("m4")
    assert outcome == "skip"
    assert draft_engine.read_draft("m4") is None
    assert fakes["model_calls"] == []
    assert fakes["sends"] == []


async def test_existing_draft_not_redrafted(fakes) -> None:
    fakes["payloads"]["m5"] = _full(
        "m5", "friend@mailbox.org", "Hello again", "Ping me back?"
    )
    assert await mail_watch_job._handle_one("m5") == "normal-logged"
    assert await mail_watch_job._handle_one("m5") == "normal-logged"
    assert len(fakes["model_calls"]) == 1
    assert len(draft_engine.list_drafts()) == 1


async def test_cap_three_per_run(fakes) -> None:
    mail_watch_job._run["drafts"] = 0
    for i in range(5):
        mid = f"cap{i}"
        fakes["payloads"][mid] = _full(
            mid, f"pal{i}@mailbox.org", f"Hello {i}", f"Message number {i}?"
        )
    for i in range(5):
        await mail_watch_job._handle_one(f"cap{i}")
    assert len(fakes["model_calls"]) == 3
    assert len(draft_engine.list_drafts()) == 3


async def test_drafts_disabled_keeps_normal_outcome(fakes, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_DRAFTS", "0")
    fakes["payloads"]["m6"] = _full(
        "m6", "friend@mailbox.org", "Hello there", "Can we meet tomorrow?"
    )
    outcome = await mail_watch_job._handle_one("m6")
    assert outcome == "normal-logged"
    assert draft_engine.read_draft("m6") is None
    assert fakes["model_calls"] == []


# --- _claude_urgency step ---


def _fake_urgency_and_draft(
    monkeypatch, state, urgency_text=None, urgency_warning=None
):
    """Claude fake that answers urgency + draft prompts separately."""
    import claude_cli
    import urgency_judge

    monkeypatch.setenv("JARVIS_CLAUDE", "1")
    monkeypatch.setenv("JARVIS_URGENCY_AI", "1")
    state.setdefault("urgency_calls", [])
    draft_text = json.dumps(
        {"needs_reply": True, "body": "Thanks, will do.", "summary": "A request."}
    )

    def fake(prompt, **kw):
        if str(kw.get("system", "")) == urgency_judge.URGENCY_SYSTEM:
            state["urgency_calls"].append(prompt)
            if urgency_warning is not None:
                return ("", urgency_warning)
            return (urgency_text, None)
        state["model_calls"].append(prompt)
        return (draft_text, None)

    monkeypatch.setattr(claude_cli, "claude_reply", fake)


async def test_claude_downgrades_keyword_high_to_normal(fakes, monkeypatch) -> None:
    _fake_urgency_and_draft(
        monkeypatch,
        fakes,
        urgency_text=json.dumps({"urgent": False, "reason": "routine notice"}),
    )
    fakes["payloads"]["u1"] = _full(
        "u1", "teacher@school.com", "URGENT: exam", "Exam moved to Friday."
    )
    outcome = await mail_watch_job._handle_one("u1")
    assert outcome == "normal-logged"
    assert "replied=autoconfirm" not in outcome
    assert fakes["sends"] == []
    record = draft_engine.read_draft("u1")
    assert record is not None and record["priority"] == "normal"
    assert record["body"] == "Thanks, will do."
    assert len(fakes["urgency_calls"]) == 1


async def test_claude_upgrades_keyword_normal_to_high(fakes, monkeypatch) -> None:
    _fake_urgency_and_draft(
        monkeypatch,
        fakes,
        urgency_text=json.dumps({"urgent": True, "reason": "deadline today"}),
    )
    fakes["payloads"]["u2"] = _full(
        "u2", "friend@mailbox.org", "Hello there", "Can we meet tomorrow?"
    )
    outcome = await mail_watch_job._handle_one("u2")
    assert "high" in outcome
    assert outcome.count("replied=autoconfirm") == 1
    assert len(fakes["sends"]) == 1
    record = draft_engine.read_draft("u2")
    assert record is not None and record["priority"] == "high"


async def test_claude_warning_keeps_keyword_high(fakes, monkeypatch) -> None:
    _fake_urgency_and_draft(
        monkeypatch, fakes, urgency_warning="Claude timed out after 60s"
    )
    fakes["payloads"]["u3"] = _full(
        "u3", "teacher@school.com", "URGENT: exam", "Exam moved to Friday."
    )
    outcome = await mail_watch_job._handle_one("u3")
    assert "replied=autoconfirm" in outcome
    assert len(fakes["sends"]) == 1
    record = draft_engine.read_draft("u3")
    assert record is not None and record["priority"] == "high"


async def test_bulk_never_calls_claude(fakes, monkeypatch) -> None:
    _fake_urgency_and_draft(
        monkeypatch,
        fakes,
        urgency_text=json.dumps({"urgent": True, "reason": "should not run"}),
    )
    fakes["payloads"]["u4"] = _full(
        "u4",
        "news@mailbox.org",
        "Weekly deals",
        "Sale now on.",
        extra_headers=(("List-Unsubscribe", "<mailto:u@x>"),),
    )
    outcome = await mail_watch_job._handle_one("u4")
    assert outcome == "normal-logged"
    assert draft_engine.read_draft("u4") is None
    assert fakes["urgency_calls"] == []
    assert fakes["model_calls"] == []
    assert fakes["sends"] == []


async def test_own_mail_skip_never_calls_claude(fakes, monkeypatch) -> None:
    _fake_urgency_and_draft(
        monkeypatch,
        fakes,
        urgency_text=json.dumps({"urgent": True, "reason": "should not run"}),
    )
    monkeypatch.setenv("JARVIS_OWNER_EMAIL", "owner@mailbox.org")
    fakes["payloads"]["u5"] = _full(
        "u5", "owner@mailbox.org", "note to self", "Remember milk."
    )
    outcome = await mail_watch_job._handle_one("u5")
    assert outcome == "skip"
    assert draft_engine.read_draft("u5") is None
    assert fakes["urgency_calls"] == []
    assert fakes["model_calls"] == []
    assert fakes["sends"] == []


# --- start_thread sidecar ---


def _notifies_of(state, kind: str) -> list:
    return [(t, kw) for t, kw in state.get("notifies", []) if kw.get("kind") == kind]


async def test_urgent_mail_notifies_via_notify_send(fakes) -> None:
    fakes["payloads"]["n1"] = _full(
        "n1", "teacher@school.com", "URGENT: exam", "Exam moved to Friday."
    )
    outcome = await mail_watch_job._handle_one("n1")
    assert "replied=autoconfirm" in outcome
    urgent = _notifies_of(fakes, "email-urgent")
    assert len(urgent) == 1
    text, kw = urgent[0]
    assert kw["source"] == "mail"
    assert kw["urgency"] == "urgent"
    assert kw["title"] == "Jarvis - priority mail"
    assert text == "teacher@school.com: URGENT: exam (acknowledged)"
    assert "teacher" in kw["speak_text"]
    assert "automatic acknowledgement" in kw["speak_text"]


async def test_urgent_mail_without_autoreply_does_not_claim_an_ack(
    fakes, monkeypatch
) -> None:
    monkeypatch.setenv("JARVIS_AUTOREPLY", "0")
    fakes["payloads"]["n3"] = _full(
        "n3", "teacher@school.com", "URGENT: exam", "Exam moved to Friday."
    )
    await mail_watch_job._handle_one("n3")
    text, kw = _notifies_of(fakes, "email-urgent")[0]
    assert "(acknowledged)" not in text
    assert "acknowledgement" not in kw["speak_text"]


async def test_created_draft_notifies_once(fakes) -> None:
    fakes["payloads"]["n2"] = _full(
        "n2", "friend@mailbox.org", "Hello there", "Can we meet tomorrow?"
    )
    outcome = await mail_watch_job._handle_one("n2")
    assert outcome == "normal-logged"
    drafts = _notifies_of(fakes, "email-draft")
    assert len(drafts) == 1
    _, kw = drafts[0]
    assert kw["source"] == "mail"
    assert kw["title"] == "Jarvis - reply draft ready"
    assert kw["fingerprint"] == "draft:n2"
    assert kw["speak_text"]


async def test_no_draft_notify_when_outcome_not_created(fakes, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_DRAFTS", "0")
    fakes["payloads"]["n3"] = _full(
        "n3", "friend@mailbox.org", "Hello there", "Can we meet tomorrow?"
    )
    assert await mail_watch_job._handle_one("n3") == "normal-logged"
    assert _notifies_of(fakes, "email-draft") == []


async def test_notify_error_does_not_break_handle_one(fakes, monkeypatch) -> None:
    import notify as _notify

    def bad_send(*a, **k):
        raise RuntimeError("notify down")

    monkeypatch.setattr(_notify, "send", bad_send)
    fakes["payloads"]["n4"] = _full(
        "n4", "teacher@school.com", "URGENT: exam", "Exam moved to Friday."
    )
    outcome = await mail_watch_job._handle_one("n4")
    assert "replied=autoconfirm" in outcome


def test_start_thread_sets_defaults_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_OWNER_EMAIL", raising=False)
    monkeypatch.delenv("JARVIS_AUTOREPLY", raising=False)

    async def fake_main():
        pass

    monkeypatch.setattr(mail_watch_job, "main", fake_main)
    thread, stop = mail_watch_job.start_thread(interval=0.05, first_delay=10.0)
    try:
        assert os.environ.get("JARVIS_OWNER_EMAIL") == "ripjkgaming@gmail.com"
        assert os.environ.get("JARVIS_AUTOREPLY") == "1"
    finally:
        stop.set()
        thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_start_thread_respects_existing_env(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_OWNER_EMAIL", "owner@mailbox.org")
    monkeypatch.setenv("JARVIS_AUTOREPLY", "0")

    async def fake_main():
        pass

    monkeypatch.setattr(mail_watch_job, "main", fake_main)
    thread, stop = mail_watch_job.start_thread(interval=0.05, first_delay=10.0)
    try:
        assert os.environ.get("JARVIS_OWNER_EMAIL") == "owner@mailbox.org"
        assert os.environ.get("JARVIS_AUTOREPLY") == "0"
    finally:
        stop.set()
        thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_start_thread_runs_main_repeatedly(monkeypatch) -> None:
    calls: list[int] = []

    async def fake_main():
        calls.append(1)

    monkeypatch.setattr(mail_watch_job, "main", fake_main)
    thread, stop = mail_watch_job.start_thread(interval=0.05, first_delay=0.0)
    try:
        assert thread.daemon is True
        deadline = time.time() + 5.0
        while len(calls) < 2 and time.time() < deadline:
            time.sleep(0.02)
        assert len(calls) >= 2
    finally:
        stop.set()
        thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_start_thread_stop_during_first_delay_never_runs(monkeypatch) -> None:
    calls: list[int] = []

    async def fake_main():
        calls.append(1)

    monkeypatch.setattr(mail_watch_job, "main", fake_main)
    thread, stop = mail_watch_job.start_thread(interval=0.05, first_delay=10.0)
    try:
        stop.set()
        thread.join(timeout=5.0)
        assert calls == []
    finally:
        stop.set()
        if thread.is_alive():
            thread.join(timeout=5.0)
    assert not thread.is_alive()


# --- ack dedupe, mail log, draft context ---


def test_ack_allowed_thread_and_sender_cooldown() -> None:
    acks: dict = {}
    now = 1_000_000.0
    assert mail_watch_job.ack_allowed(acks, "t1", "a@x.com", now)
    mail_watch_job.note_ack(acks, "t1", "a@x.com", now)
    assert not mail_watch_job.ack_allowed(acks, "t1", "b@x.com", now + 10)
    assert not mail_watch_job.ack_allowed(acks, "t2", "a@x.com", now + 3600)
    later = now + mail_watch_job.ACK_SENDER_COOLDOWN_S + 1
    assert mail_watch_job.ack_allowed(acks, "t2", "a@x.com", later)


@pytest.mark.asyncio
async def test_second_urgent_mail_in_thread_is_not_reacked(fakes) -> None:
    import mail_log

    fakes["payloads"]["u1"] = _full(
        "u1", "Teacher <t@school.edu>", "Urgent: form", "pls"
    )
    fakes["payloads"]["u2"] = _full(
        "u2", "Teacher <t@school.edu>", "Urgent: form", "again"
    )
    acks: dict = {}
    first = await mail_watch_job._handle_one("u1", acks)
    second = await mail_watch_job._handle_one("u2", acks)
    assert "replied=autoconfirm" in first
    assert "already-acked" in second
    assert len(fakes["sends"]) == 1
    raw = base64.urlsafe_b64decode(fakes["sends"][0][1].encode()).decode()
    assert "Auto-Submitted: auto-replied" in raw
    kinds = [e["kind"] for e in mail_log.recent()]
    assert kinds.count("acked") == 1 and kinds.count("flagged") == 2


@pytest.mark.asyncio
async def test_draft_gets_thread_and_style_context(fakes) -> None:
    fakes["payloads"]["n1"] = _full(
        "n1", "Aarav <a@x.com>", "Project", "Can you send the slides?", thread="th9"
    )
    fakes["payloads"]["th9"] = {
        "messages": [
            _full(
                "old", "Aarav <a@x.com>", "Project", "Started the deck", thread="th9"
            ),
            fakes["payloads"]["n1"],
        ]
    }
    fakes["payloads"]["messages"] = {"messages": [{"id": "s1"}]}
    fakes["payloads"]["s1"] = _full("s1", "me@x.com", "Re: stuff", "yo, sure thing - R")
    out = await mail_watch_job._handle_one("n1")
    assert out == "normal-logged"
    prompt = fakes["model_calls"][-1]
    assert "Started the deck" in prompt
    assert "yo, sure thing - R" in prompt
    assert prompt.index("EARLIER in this thread") < prompt.index("Can you send")


@pytest.mark.asyncio
async def test_draft_context_failures_are_soft(fakes) -> None:
    fakes["payloads"]["n2"] = _full(
        "n2", "Bo <b@x.com>", "Hi", "question?", thread="gone"
    )
    assert await mail_watch_job._handle_one("n2") == "normal-logged"
    assert fakes["model_calls"]


def test_state_path_follows_jarvis_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert mail_watch_job.state_path() == tmp_path / "mail_watch.state.json"


@pytest.mark.asyncio
async def test_event_suggestion_for_human_mail_only(fakes, monkeypatch) -> None:
    import event_extractor

    monkeypatch.setenv("JARVIS_EMAIL_EVENTS", "1")
    seen = []
    monkeypatch.setattr(
        event_extractor,
        "suggest",
        lambda parsed: seen.append(parsed["id"]) or "suggested",
    )
    fakes["payloads"]["h1"] = _full(
        "h1", "Bob <b@x.com>", "Dinner Friday 7pm?", "see you"
    )
    fakes["payloads"]["b1"] = _full(
        "b1",
        "News <news@x.com>",
        "Webinar Tuesday",
        "join",
        extra_headers=(("List-Unsubscribe", "<x>"),),
    )
    await mail_watch_job._handle_one("h1")
    await mail_watch_job._handle_one("b1")
    assert seen == ["h1"]


def test_error_retry_is_bounded() -> None:
    assert mail_watch_job.error_count("normal-logged") == 0
    assert mail_watch_job.error_count("error") == 1
    assert mail_watch_job.error_count("error:2") == 2
    assert mail_watch_job.retry_due("error") and mail_watch_job.retry_due("error:2")
    assert not mail_watch_job.retry_due("error:3")
    assert not mail_watch_job.retry_due("skip")


@pytest.mark.asyncio
async def test_main_retries_errored_mail(fakes, monkeypatch) -> None:
    fakes["payloads"]["messages"] = {
        "messages": [{"id": "e1"}, {"id": "ok1"}, {"id": "dead"}]
    }
    fakes["payloads"]["e1"] = _full("e1", "Bob <b@x.com>", "Hi", "hello")
    mail_watch_job._save_state(
        {"seen_map": {"e1": "error", "ok1": "skip", "dead": "error:3"}}
    )
    handled = []
    real = mail_watch_job._handle_one

    async def spy(msg_id, acks=None):
        handled.append(msg_id)
        return await real(msg_id, acks)

    monkeypatch.setattr(mail_watch_job, "_handle_one", spy)
    await mail_watch_job.main()
    assert handled == ["e1"]
    assert mail_watch_job._load_state()["seen_map"]["e1"] == "normal-logged"
