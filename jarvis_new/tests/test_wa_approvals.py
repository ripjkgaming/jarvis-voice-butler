"""Offline owner-input approval tests. All chat providers are fake."""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import wa_approvals as approvals


def test_queue_ids_revision_history_and_request_dedupe(tmp_path):
    path = tmp_path / "drafts.json"
    path.write_text(json.dumps([{"chat": "Old", "text": "legacy", "ts": 1}]))
    before = path.read_bytes()
    old = approvals.pending(path)[0]
    assert old["id"] and old["revision"]
    assert path.read_bytes() == before
    a = approvals.queue(path, "Group", "hello", request_id="response1")
    b = approvals.queue(path, "Group", "hello", request_id="response1")
    assert a == b
    assert len(approvals.pending(path)) == 2
    assert (
        approvals.queue(path, "Group", "hello", request_id="response2")["id"] != a["id"]
    )


def test_corrupt_queue_is_not_overwritten(tmp_path):
    path = tmp_path / "drafts.json"
    path.write_text("broken")
    with pytest.raises(ValueError):
        approvals.queue(path, "Group", "hello")
    assert path.read_text() == "broken"


def test_claim_rejects_edited_or_consumed_draft(tmp_path):
    path = tmp_path / "drafts.json"
    row = approvals.queue(path, "Group", "hello")
    data = json.loads(path.read_text())
    data[0]["text"] = "edited"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        approvals.claim(path, row["id"], row["revision"])
    row = approvals.pending(path)[0]
    approvals.claim(path, row["id"], row["revision"])
    with pytest.raises(ValueError):
        approvals.claim(path, row["id"], row["revision"])


class Speech:
    interrupted = False

    def exception(self):
        return None

    async def wait_for_playout(self):
        await asyncio.sleep(0)


class Session:
    def __init__(self):
        self.callbacks = {}
        self.lines = []
        self.speech = Speech()

    def on(self, name, callback):
        self.callbacks[name] = callback

    def say(self, text, **kwargs):
        self.lines.append(text)
        return self.speech


@pytest.fixture
def flow(tmp_path):
    path = tmp_path / "drafts.json"
    row = approvals.queue(path, "Group", "hello")
    owner = {"identity": "owner-room"}
    sent = []
    target = {"name": "Group", "jid": "123@g.us"}

    async def resolve(name):
        assert name == "Group"
        return {"ok": True, **target}

    async def send(recipient, text, authorized):
        assert authorized()
        sent.append((dict(recipient), text))
        return {"ok": True}

    session = Session()
    gate = approvals.SpokenApproval(
        session,
        "owner-room",
        lambda: owner["identity"],
        resolve=resolve,
        send=send,
    )
    return path, row, owner, sent, session, gate


def event(text, *, final=True, created=None, item="input1"):
    return SimpleNamespace(
        transcript=text,
        is_final=final,
        created_at=time.time() if created is None else created,
        item_id=item,
    )


async def approve(gate, text=None, **kwargs):
    gate.user_state(SimpleNamespace(new_state="speaking"))
    gate.user_input(event(text or gate.confirmation, **kwargs))
    await gate.drain()


@pytest.mark.asyncio
async def test_fresh_explicit_input_sends_once_after_full_review(flow):
    path, row, owner, sent, session, gate = flow
    await gate.review(path, row["id"])
    assert "hello" in session.lines[0] and "Group" in session.lines[0]
    assert approvals._norm(gate.confirmation) not in approvals._norm(session.lines[0])
    phrase = gate.confirmation
    await approve(gate)
    assert sent == [({"name": "Group", "jid": "123@g.us"}, "hello")]
    await approve(gate, phrase)
    assert len(sent) == 1
    assert approvals.pending(path) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "okay",
        "approve all",
        "send it",
        "don't send it",
        "change hello",
        "what time is it",
    ],
)
async def test_ambiguous_negative_unrelated_input_never_sends(flow, text):
    path, row, owner, sent, session, gate = flow
    await gate.review(path, row["id"])
    phrase = gate.confirmation
    await approve(gate, text)
    await approve(gate, phrase, item="input2")
    assert not sent


@pytest.mark.asyncio
async def test_partial_output_and_model_text_are_not_approval(flow):
    path, row, owner, sent, session, gate = flow
    await gate.review(path, row["id"])
    gate.user_input(event(gate.confirmation, final=False))
    gate.agent_item(
        SimpleNamespace(
            item=SimpleNamespace(role="assistant", text_content=gate.confirmation)
        )
    )
    await approve(gate)
    assert not sent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "revoked",
        "edited",
        "expired",
        "stale_event",
        "no_audio",
        "interrupted",
        "failed_playout",
        "cancelled",
        "other_session",
    ],
)
async def test_fail_closed_review_and_confirmation(flow, failure):
    path, row, owner, sent, session, gate = flow
    if failure == "interrupted":
        session.speech.interrupted = True
    if failure == "failed_playout":
        session.speech.exception = lambda: RuntimeError("tts")
    await gate.review(path, row["id"])
    phrase = gate.confirmation
    if failure == "revoked":
        owner["identity"] = ""
    elif failure == "other_session":
        owner["identity"] = "other-room"
    elif failure == "edited":
        data = json.loads(path.read_text())
        data[0]["text"] = "edited"
        path.write_text(json.dumps(data))
    elif failure == "expired":
        gate._ready_mono -= approvals.CONFIRM_TTL + 1
    elif failure == "cancelled":
        gate.user_input(event("cancel", final=False))
    if failure == "no_audio":
        gate.user_input(event(phrase))
        await gate.drain()
    else:
        await approve(gate, phrase, created=0 if failure == "stale_event" else None)
    assert not sent


@pytest.mark.asyncio
async def test_uncertain_send_cannot_be_replayed(flow):
    path, row, owner, sent, session, gate = flow

    async def failed(*args):
        sent.append("attempt")
        raise RuntimeError("transport lost")

    gate._send = failed
    await gate.review(path, row["id"])
    await approve(gate)
    await gate.review(path, row["id"])
    await approve(gate)
    assert sent == ["attempt"]
    assert json.loads(path.read_text())[0]["status"] == "uncertain"


@pytest.mark.asyncio
async def test_any_new_draft_invalidates_prior_review(flow):
    path, row, owner, sent, session, gate = flow
    await gate.review(path, row["id"])
    approvals.queue(path, "Other", "new message")
    await approve(gate)
    assert not sent


@pytest.mark.asyncio
async def test_cancel_immediately_after_confirmation_stops_queued_dispatch(flow):
    path, row, owner, sent, session, gate = flow
    await gate.review(path, row["id"])
    gate.user_state(SimpleNamespace(new_state="speaking"))
    gate.user_input(event(gate.confirmation))
    gate.user_input(event("stop", final=False, item="cancel"))
    await gate.drain()
    assert not sent


@pytest.mark.asyncio
async def test_failed_background_review_announces_specific_refusal(flow):
    path, row, owner, sent, session, gate = flow

    async def unresolved(name):
        return {"ok": False, "err": "exact conversation ID unavailable"}

    gate._resolve = unresolved
    gate.start_review(path, row["id"])
    await gate.drain()
    assert "exact conversation ID unavailable" in session.lines[-1]
    assert not gate.confirmation and not sent


@pytest.mark.asyncio
async def test_other_input_during_slow_resolution_invalidates_review(flow):
    path, row, owner, sent, session, gate = flow
    ready = asyncio.Event()
    release = asyncio.Event()

    async def resolve(name):
        ready.set()
        await release.wait()
        return {"ok": True, "name": "Group", "jid": "123@g.us"}

    gate._resolve = resolve
    gate.start_review(path, row["id"])
    await ready.wait()
    gate.user_input(event("cancel"))
    release.set()
    await gate.drain()
    assert not gate.confirmation and not sent


def test_confirmation_digits_accept_words_or_stt_number():
    assert approvals._norm(
        "Confirm WhatsApp dispatch one two zero four"
    ) == approvals._norm("confirm whatsapp dispatch 1204")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["empty", "error", "good"])
async def test_injected_audio_requires_frames_and_completed_playout(flow, kind):
    path, row, owner, sent, session, gate = flow

    class TTS:
        @asynccontextmanager
        async def synthesize(self, line):
            async def stream():
                if kind == "error":
                    raise RuntimeError("synth failure")
                if kind == "good":
                    yield SimpleNamespace(frame=SimpleNamespace(samples_per_channel=10))

            yield stream()

    class Playout(Speech):
        def __init__(self, audio):
            self.audio = audio

        async def wait_for_playout(self):
            async for frame in self.audio:
                assert frame.samples_per_channel > 0

    def say(text, **kwargs):
        session.lines.append(text)
        assert "audio" in kwargs
        return Playout(kwargs["audio"])

    session.say = say
    gate._tts = TTS()
    await gate.review(path, row["id"])
    assert bool(gate.confirmation) == (kind == "good")
    assert not sent


def test_install_only_in_active_private_owner_scope(monkeypatch):
    import phone_session

    session = Session()
    monkeypatch.setattr(phone_session, "identity", lambda: "")
    assert approvals.install(session) is None
    assert not session.callbacks
    monkeypatch.setattr(phone_session, "identity", lambda: "owner-room")
    gate = approvals.install(session)
    assert gate.authorized()
    assert set(session.callbacks) == {
        "user_input_transcribed",
        "user_state_changed",
        "conversation_item_added",
        "agent_state_changed",
        "close",
    }
    session.callbacks["close"](None)
    assert not gate.authorized()


@pytest.mark.asyncio
async def test_duplicate_final_event_before_dispatch_does_not_cancel_or_resend(flow):
    path, row, owner, sent, session, gate = flow
    await gate.review(path, row["id"])
    gate.user_state(SimpleNamespace(new_state="speaking"))
    approved = event(gate.confirmation)
    gate.user_input(approved)
    gate.user_input(approved)
    await gate.drain()
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_edit_between_claim_and_send_invalidates_authorization(flow):
    path, row, owner, sent, session, gate = flow

    async def send(target, text, authorized):
        data = json.loads(path.read_text())
        data[0]["text"] = "changed during dispatch"
        path.write_text(json.dumps(data))
        assert not authorized()
        return {"ok": False}

    gate._send = send
    await gate.review(path, row["id"])
    await approve(gate)
    assert not sent
    assert json.loads(path.read_text())[0]["status"] == "uncertain"


@pytest.mark.asyncio
async def test_oversize_legacy_draft_is_not_silently_truncated(flow):
    path, row, owner, sent, session, gate = flow
    data = json.loads(path.read_text())
    data[0]["text"] = "a" * 501
    path.write_text(json.dumps(data))
    await gate.review(path, row["id"])
    assert not gate.confirmation and not sent


@pytest.mark.asyncio
async def test_playback_start_disarms_before_assistant_item_commit(flow):
    path, row, owner, sent, session, gate = flow
    await gate.review(path, row["id"])
    phrase = gate.confirmation
    gate.playback_started(SimpleNamespace(created_at=time.time()))
    await approve(gate, phrase)
    assert not sent and not gate.confirmation


@pytest.mark.asyncio
async def test_new_speaking_start_revokes_inflight_dispatch_before_transcript(flow):
    path, row, owner, sent, session, gate = flow
    started, resume = asyncio.Event(), asyncio.Event()

    async def send(target, text, authorized):
        started.set()
        await resume.wait()
        if authorized():
            sent.append(text)
        return {"ok": bool(sent)}

    gate._send = send
    await gate.review(path, row["id"])
    gate.user_state(SimpleNamespace(new_state="speaking"))
    gate.user_input(event(gate.confirmation))
    await started.wait()
    gate.user_state(SimpleNamespace(new_state="speaking"))
    resume.set()
    await gate.drain()
    assert not sent
    assert json.loads(path.read_text())[0]["status"] == "uncertain"


@pytest.mark.asyncio
async def test_older_review_finally_cannot_clear_new_presentation(flow):
    path, row, owner, sent, session, gate = flow
    starts = [asyncio.Event(), asyncio.Event()]
    releases = [asyncio.Event(), asyncio.Event()]
    count = 0

    async def resolve(name):
        nonlocal count
        index = count
        count += 1
        starts[index].set()
        await releases[index].wait()
        return {"ok": True, "name": "Group", "jid": "123@g.us"}

    gate._resolve = resolve
    gate.start_review(path, row["id"])
    await starts[0].wait()
    gate.start_review(path, row["id"])
    await starts[1].wait()
    releases[0].set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert gate._presenting == gate._generation
    gate.user_input(event("cancel"))
    releases[1].set()
    await gate.drain()
    assert not gate.confirmation and not sent


def test_install_hooks_every_actual_audio_segment(monkeypatch):
    import phone_session

    session = Session()
    audio = Session()
    session.output = SimpleNamespace(audio=audio)
    monkeypatch.setattr(phone_session, "identity", lambda: "owner-room")
    gate = approvals.install(session)
    assert audio.callbacks["playback_started"] == gate.playback_started
    assert session.callbacks["agent_state_changed"] == gate.agent_state


