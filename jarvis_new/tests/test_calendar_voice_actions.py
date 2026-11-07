"""Manual voice Calendar actions require an exact provider readback receipt."""

import pytest

import calendar_actions as ca
import claim_guard as cg
import google_api
from system.workspace_tools import WorkspaceTools

EVENT = {
    "title": "Computer Science Event",
    "date": "2027-01-16",
    "start": "10:00",
    "end": "12:00",
    "location": "",
}


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_TZ", "Asia/Singapore")
    monkeypatch.setenv("JARVIS_LOCAL", "1")


def test_create_only_verified_after_exact_readback(home, monkeypatch):
    monkeypatch.setattr(google_api, "calendar_list", lambda *a, **k: [])
    made = []
    monkeypatch.setattr(
        google_api,
        "calendar_create",
        lambda ev, *a, **k: made.append(ev) or {"id": "provider-1"},
    )
    monkeypatch.setattr(
        google_api,
        "calendar_get",
        lambda *a, **k: {
            "id": "provider-1",
            **google_api.event_body(EVENT),
            "htmlLink": "https://calendar.google.com/event?eid=test",
        },
    )
    out = ca.add(EVENT)
    assert out["ok"] and out["outcome"]["state"] == "verified"
    assert "2027-01-16" in out["say"] and "10:00" in out["say"]
    assert made[0]["jarvis_id"]


def test_provider_failure_and_readback_failure_never_claim_added(home, monkeypatch):
    monkeypatch.setattr(google_api, "calendar_list", lambda *a, **k: [])
    monkeypatch.setattr(google_api, "calendar_create", lambda *a, **k: {"id": "g"})
    monkeypatch.setattr(
        google_api,
        "calendar_get",
        lambda *a, **k: (_ for _ in ()).throw(google_api.GoogleError("offline")),
    )
    out = ca.add(EVENT)
    assert out["ok"] is False and out["outcome"]["state"] == "dispatched_unverified"
    assert "not verified" in out["say"]
    monkeypatch.setattr(
        google_api,
        "calendar_create",
        lambda *a, **k: (_ for _ in ()).throw(google_api.GoogleError("denied")),
    )
    out = ca.add(EVENT)
    assert out["ok"] is False and out["outcome"]["state"] == "failed"


def test_mismatched_readback_never_claims_success(home, monkeypatch):
    monkeypatch.setattr(google_api, "calendar_list", lambda *a, **k: [])
    monkeypatch.setattr(google_api, "calendar_create", lambda *a, **k: {"id": "g"})
    monkeypatch.setattr(
        google_api,
        "calendar_get",
        lambda *a, **k: {
            "id": "g",
            **google_api.event_body({**EVENT, "start": "02:00"}),
        },
    )
    assert ca.add(EVENT)["ok"] is False


def test_local_exam_and_calendar_read_cannot_back_calendar_creation():
    guard = cg.ClaimGuard()
    guard.note_user("Add Computer Science Event to my calendar on January 16")
    guard.note_tool("exam_schedule", True)
    guard.note_tool("calendar_upcoming", True)
    assert guard.check("I have added Computer Science Event to your calendar.")
    assert guard.check(
        "The Computer Science Event is now scheduled for January sixteenth."
    )


@pytest.mark.asyncio
async def test_voice_tool_is_registered_and_requires_exact_times(home):
    tools = WorkspaceTools()
    assert any(getattr(t, "__name__", "") == "calendar_add" for t in tools.tools)
    with pytest.raises(Exception, match=r"exact.*time|HH:MM"):
        await tools.calendar_add(
            None, title=EVENT["title"], date=EVENT["date"], start="morning", end=""
        )


def test_pending_async_result_and_failed_tool_do_not_back_success():
    from action_outcome import ActionOutcome

    guard = cg.ClaimGuard()
    guard.note_user("Add the event to my calendar")
    pending = ActionOutcome(
        "calendar.create", "pending", surface="google_calendar"
    ).result()
    guard.note_result("calendar_add", pending)
    assert guard.check("I have added the event to your calendar.")
    guard.note_result("calendar_add", {"error": "offline"}, is_error=True)
    assert guard.check("I have added the event to your calendar.")


@pytest.mark.asyncio
async def test_unverified_calendar_claim_never_reaches_captions_or_tts(monkeypatch):
    from types import SimpleNamespace

    from jarvis_rt.pipeline_direct import DirectBackend, _Reply

    guard = cg.ClaimGuard()
    guard.note_user("Add the event to my calendar")
    monkeypatch.setattr(cg, "GUARD", guard)
    backend = DirectBackend.__new__(DirectBackend)
    emitted = []
    spoken = []
    backend._emit = lambda kind, **data: emitted.append((kind, data))
    backend.tts = SimpleNamespace(
        sample_rate=24000,
        synthesize=lambda sentence: spoken.append(sentence) or b"\0\0",
    )
    backend._speaking = False
    reply = _Reply(contents=[], system=None, tools_enabled=True, calendar_guarded=True)
    backend._reply = reply
    backend._text(reply, "I have added it to your cal")
    backend._text(reply, "endar. ")
    for sentence in reply.splitter.flush():
        backend._queue_sentence(reply, sentence)
    reply.producer_done = True
    await backend._speak(reply)
    assert spoken == ["I haven't verified that Google Calendar change yet, Sir."]
    captions = " ".join(
        data.get("text", "") for kind, data in emitted if kind == "agent_text"
    )
    assert "I have added" not in captions


def test_verified_calendar_receipt_backs_success_only_for_matching_effect(
    home, monkeypatch
):
    from action_outcome import ActionOutcome

    guard = cg.ClaimGuard()
    guard.note_user("Add the event to my calendar")
    good = ActionOutcome(
        "calendar.create",
        "verified",
        "started",
        "matched",
        "google_calendar",
        evidence={"provider_id_matched": True, "fields_matched": True},
    )
    guard.note_result("calendar_add", good.result())
    assert guard.check("I have added the event to your calendar.") is None
    assert guard.check("I have deleted the event from your calendar.") is not None


def test_date_only_followup_keeps_calendar_receipt_requirement():
    guard = cg.ClaimGuard()
    guard.note_user("Add the exam to my calendar")
    guard.note_user("January 16 2027 at 10")
    guard.note_tool("exam_schedule", True)
    assert guard.check("The event is now scheduled for January sixteenth.") is not None


def test_fresh_calendar_read_can_confirm_state_but_never_write():
    from action_outcome import ActionOutcome

    guard = cg.ClaimGuard()
    guard.note_user("Check my calendar on January16 2027")
    receipt = ActionOutcome(
        "calendar.read",
        "verified",
        "not_started",
        "matched",
        "google_calendar",
        evidence={"provider_response": True, "event_count": 1},
    )
    result = receipt.result()
    result["events"] = [{"title": "Computer Science Event"}]
    guard.note_result("calendar_events_on", result)
    assert (
        guard.check("Computer Science Event is now scheduled for January sixteenth.")
        is None
    )
    assert (
        guard.check("A different appointment is now scheduled for January sixteenth.")
        is not None
    )
    assert guard.check("I have added the event to your calendar.") is not None


def test_speculative_first_calendar_turn_is_checked_before_release(monkeypatch):
    from jarvis_rt.pipeline_direct import DirectBackend, _Reply

    guard = cg.ClaimGuard()
    monkeypatch.setattr(cg, "GUARD", guard)
    backend = DirectBackend.__new__(DirectBackend)
    emitted = []
    backend._emit = lambda kind, **data: emitted.append((kind, data))
    backend._start_speaker = lambda reply: None
    reply = _Reply(contents=[], system=None, tools_enabled=True, hold=True)
    backend._text(reply, "I have added the exam to your calendar. ")
    reply.producer_done = True
    guard.note_user("Add the exam to my calendar")
    backend._release(reply)
    assert reply.calendar_guarded
    assert all("I have added" not in sentence for sentence in reply.sentences)
    assert all("I have added" not in data.get("text", "") for _, data in emitted)


@pytest.mark.asyncio
async def test_native_audio_before_final_transcript_cannot_leak_calendar_claim(
    monkeypatch,
):
    import asyncio

    from test_rt_session import FakeBackend

    from jarvis_rt import AgentSession

    guard = cg.ClaimGuard()
    monkeypatch.setattr(cg, "GUARD", guard)
    backend = FakeBackend()
    backend.requires_response_validation = True
    session = AgentSession(backend=backend)
    audio = []
    captions = []
    session.output.audio = audio.append
    session.output.transcription = lambda text, final: captions.append(text)
    session._on_agent_audio(b"\0\0" * 10)
    session._on_agent_text("I have added the exam to your calendar.")
    assert not audio and not captions
    guard.note_user("Add the exam to my calendar")
    session._finish_turn(interrupted=False)
    await asyncio.sleep(0)
    assert not audio
    assert all("I have added" not in text for text in captions)
    assert backend.log == [
        ("say", "I haven't verified that Google Calendar change yet, Sir.")
    ]
    await session.aclose()


@pytest.mark.asyncio
async def test_native_verified_audio_releases_once_and_interruption_discards(
    monkeypatch,
):
    from test_rt_session import FakeBackend

    from action_outcome import ActionOutcome
    from jarvis_rt import AgentSession

    guard = cg.ClaimGuard()
    guard.note_user("Add the exam to my calendar")
    guard.note_result(
        "calendar_add",
        ActionOutcome(
            "calendar.create",
            "verified",
            "started",
            "matched",
            "google_calendar",
            evidence={"provider_id_matched": True, "fields_matched": True},
        ).result(),
    )
    monkeypatch.setattr(cg, "GUARD", guard)
    backend = FakeBackend()
    backend.requires_response_validation = True
    session = AgentSession(backend=backend)
    audio = []
    session.output.audio = audio.append
    session._on_agent_audio(b"\0\0")
    session._on_agent_text("I have added it to your calendar.")
    session._finish_turn(interrupted=False)
    session._finish_turn(interrupted=False)
    assert audio == [b"\0\0"]
    session._on_agent_audio(b"\1\1")
    session._on_agent_text("What time should it start?")
    session._finish_turn(interrupted=True)
    assert audio == [b"\0\0"]
    await session.aclose()


def test_expired_calendar_request_still_needs_provider_proof():
    clock = {"now": 100.0}
    guard = cg.ClaimGuard(clock=lambda: clock["now"])
    guard.note_user("Add the event to my calendar")
    clock["now"] = 300.0
    guard.note_result("calendar_add", {"error": "timed out"}, is_error=True)
    assert guard.check("I have added it to your calendar.") is not None


def test_explicit_calendar_claim_needs_proof_without_user_transcript(monkeypatch):
    guard = cg.ClaimGuard()
    monkeypatch.setattr(cg, "GUARD", guard)
    assert cg.calendar_safe_text("I have added it to your calendar.").startswith(
        "I haven't verified"
    )
    assert cg.calendar_safe_text(
        "Added Computer Science Event to Google Calendar."
    ).startswith("I haven't verified")
    assert cg.calendar_safe_text(
        "I have added it to your calendar. I am not sure about the venue."
    ).startswith("I haven't verified")
    assert (
        cg.calendar_safe_text("What time should the event start?")
        == "What time should the event start?"
    )


@pytest.mark.asyncio
async def test_guest_cannot_dispatch_manual_calendar_mutation(home, monkeypatch):
    monkeypatch.setattr("phone_session.guest_required", lambda: True)
    monkeypatch.setattr(
        ca, "add", lambda *a: pytest.fail("guest must never reach provider helper")
    )
    with pytest.raises(Exception, match="authenticated owner"):
        await WorkspaceTools().calendar_add(
            None, title=EVENT["title"], date=EVENT["date"], start="10:00", end="12:00"
        )
