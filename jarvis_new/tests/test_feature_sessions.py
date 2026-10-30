"""Installed SDK events, fake capture/voice only; no live content or model."""

import asyncio
import time
from types import SimpleNamespace

import pytest

from feature_sessions import FeatureSession, HostConsent
from jarvis_rt import ChatMessage
from jarvis_rt.events import (
    ConversationItemAddedEvent,
    UserInputTranscribedEvent,
)
from jarvis_rt.session import _Input, _Output


class Session:
    def __init__(self):
        self.handlers = {}
        self.lines = []
        self.interrupts = 0
        self.clears = 0
        self.jarvis_caption_tail = SimpleNamespace(clear_sensitive=lambda: None)
        self.input = _Input()
        self.output = _Output()

    def on(self, name, fn):
        self.handlers.setdefault(name, []).append(fn)

    def off(self, name, fn):
        self.handlers[name].remove(fn)

    def interrupt(self, **kw):
        self.interrupts += 1

    def clear_user_turn(self):
        self.clears += 1

    def emit(self, name, event):
        for fn in list(self.handlers.get(name, [])):
            fn(event)

    def say(self, text, **kw):
        self.lines.append((text, kw))

        class Speech:
            interrupted = False

            async def wait_for_playout(self):
                pass

            def exception(self):
                return None

        return Speech()


class Backend:
    def __init__(self):
        self.calls = []
        self.closed = False

    async def select(self):
        self.calls.append("select")
        return "selected"

    async def capture(self, selected, check):
        check()
        self.calls.append("capture")
        return b"fake"

    async def ocr(self, image, check):
        check()
        self.calls.append("ocr")
        return "Synthetic error: connection refused"

    async def close(self):
        self.closed = True


@pytest.fixture
def owner(monkeypatch):
    import phone_session

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_SCREEN_READING", "1")
    monkeypatch.setattr(phone_session, "private", lambda: False)
    monkeypatch.setattr(phone_session, "guest_required", lambda: False)


@pytest.mark.asyncio
async def test_empty_consent_blocks_before_capture(owner):
    s = Session()
    b = Backend()
    f = FeatureSession(s, backend=b, authorized=lambda: True)
    assert await f.handle_message(
        ChatMessage(role="user", content=["look at my screen"])
    )
    assert not b.calls
    assert not s.jarvis_screen_turn_id
    await f.close()


@pytest.mark.asyncio
async def test_sdk_mirrored_final_is_consumed_once_and_private_output(owner):
    s = Session()
    b = Backend()
    spoken = []

    async def deliver(text):
        assert not s.output.transcription_enabled
        assert not f.captions_allowed
        spoken.append(text)

    f = FeatureSession(s, backend=b, authorized=lambda: True, deliver=deliver)
    f.bind_caption_tail(s.jarvis_caption_tail)
    f.configure(
        HostConsent(screen_local_ocr=True, expires_at=time.time() + 60), confirmed=True
    )
    f.install(realtime=True)
    event = UserInputTranscribedEvent(
        transcript="look at my screen", is_final=True, item_id="turn-1"
    )
    s.emit("user_input_transcribed", event)
    s.emit(
        "conversation_item_added",
        ConversationItemAddedEvent(
            item=ChatMessage(id="turn-1", role="user", content=["look at my screen"])
        ),
    )
    await f.drain()
    assert b.calls == ["select", "select", "capture", "ocr"]
    assert len(spoken) == 1
    assert not f.captions_allowed  # late SDK callbacks stay quarantined this session
    assert not s.output.transcription_enabled
    assert not s.jarvis_screen_turn_id
    assert await f.handle_message(
        ChatMessage(id="turn-1", role="user", content=["look at my screen"])
    )
    assert len(spoken) == 1
    await f.close()
    assert b.closed


@pytest.mark.asyncio
async def test_missing_provider_id_and_interim_never_dispatch(owner):
    s = Session()
    b = Backend()
    f = FeatureSession(s, backend=b, authorized=lambda: True)
    f.bind_caption_tail(s.jarvis_caption_tail)
    f.configure(
        HostConsent(screen_local_ocr=True, expires_at=time.time() + 60), confirmed=True
    )
    f.install(realtime=True)
    s.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(
            transcript="look at my screen", is_final=False, item_id="x"
        ),
    )
    s.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(transcript="look at my screen", is_final=True),
    )
    await f.drain()
    assert not b.calls
    await f.close()


@pytest.mark.asyncio
async def test_stop_only_interrupts_speech_and_task_control_once(owner):
    s = Session()
    calls = []

    class Tasks:
        async def cancel_task(self, context, task_id=""):
            calls.append(task_id)
            return {"say": "Cancellation requested; preparation may finish."}

    f = FeatureSession(s, task_tools=Tasks(), authorized=lambda: True)
    assert await f.handle_message(
        ChatMessage(id="stop-1", role="user", content=["stop"])
    )
    assert not calls and s.interrupts == 1
    msg = ChatMessage(id="cancel-1", role="user", content=["cancel task task-example"])
    assert await f.handle_message(msg)
    assert await f.handle_message(msg)
    assert calls == ["task-example"]
    await f.close()


@pytest.mark.asyncio
async def test_new_turn_and_guest_revoke_inflight(owner):
    s = Session()
    b = Backend()
    ready = asyncio.Event()
    gate = asyncio.Event()
    state = {"ok": True}

    async def explain(question, evidence):
        ready.set()
        await gate.wait()
        return "secret"

    f = FeatureSession(s, backend=b, authorized=lambda: state["ok"], explain=explain)
    f.bind_caption_tail(s.jarvis_caption_tail)
    f.configure(
        HostConsent(screen_local_ocr=True, expires_at=time.time() + 60), confirmed=True
    )
    task = asyncio.create_task(
        f.handle_message(
            ChatMessage(id="x", role="user", content=["look at my screen"])
        )
    )
    await asyncio.wait_for(ready.wait(), 2)
    state["ok"] = False
    f.revoke_authority()
    await task
    assert all("secret" not in line for line, _ in s.lines)
    assert not s.jarvis_screen_turn_id
    await f.close()


@pytest.mark.asyncio
async def test_tool_registration_uses_installed_sdk_and_empty_grants(owner):
    from librarian import Librarian
    from system.librarian_tools import LibrarianTools

    service = Librarian()
    tools = LibrarianTools(service)
    assert {t.id for t in tools.tools} == {
        "librarian_find",
        "librarian_answer",
        "librarian_duplicates",
    }
    assert (await tools.librarian_find("synthetic"))["status"] == "needs_approval"
    service.close()


@pytest.mark.asyncio
async def test_real_sdk_event_bus_without_starting_transport(owner, monkeypatch):
    from jarvis_rt import AgentSession

    s = AgentSession(llm=None)
    monkeypatch.setattr(s, "clear_user_turn", lambda: None)
    b = Backend()
    lines = []

    async def deliver(text):
        lines.append(text)

    f = FeatureSession(s, backend=b, authorized=lambda: True, deliver=deliver)
    f.bind_caption_tail(SimpleNamespace(clear_sensitive=lambda: None))
    f.configure(
        HostConsent(screen_local_ocr=True, expires_at=time.time() + 60), confirmed=True
    )
    f.install(realtime=True)
    s.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(
            transcript="look at my screen", is_final=True, item_id="native-sdk"
        ),
    )
    s.emit(
        "conversation_item_added",
        ConversationItemAddedEvent(
            item=ChatMessage(
                id="native-sdk", role="user", content=["look at my screen"]
            )
        ),
    )
    await f.drain()
    assert len(lines) == 1
    assert not s.output.transcription_enabled
    await f.close()


@pytest.mark.asyncio
async def test_sdk_agent_hook_consumes_controls_with_fast_path_disabled(
    owner, monkeypatch
):
    import agent
    from intent import fast_path
    from jarvis_rt import StopResponse

    monkeypatch.setattr(agent, "_default_agent_llm", lambda: None)
    monkeypatch.setenv("JARVIS_FAST_PATH", "0")
    monkeypatch.setenv("JARVIS_PIPELINE", "local")
    monkeypatch.setattr(
        fast_path, "match_async", lambda *a: pytest.fail("control fell through")
    )
    a = agent.Assistant()
    s = Session()
    f = FeatureSession(
        s,
        task_tools=a.task_tools,
        librarian=a.librarian_service,
        authorized=lambda: True,
    )
    a._session = s  # jarvis_rt Agent binds its session here
    assert {"librarian_find", "librarian_answer", "librarian_duplicates"} <= {
        t.id for t in a.tools
    }
    for name in [
        "cancel_task",
        "task_status",
        "cancel_tool",
        "redirect_task",
        "computer_use_cancel",
    ]:
        assert agent._never_demote(name)
    with pytest.raises(StopResponse):
        await a.on_user_turn_completed(None, ChatMessage(role="user", content=["stop"]))
    assert s.interrupts == 1
    await f.close()


@pytest.mark.asyncio
async def test_real_hud_tail_drops_sensitive_and_late_chunks(owner, monkeypatch):
    import agent

    writes = []
    monkeypatch.setattr(agent, "_write_live_caption", lambda *args: writes.append(args))
    s = Session()
    f = FeatureSession(s, authorized=lambda: True)
    tail = agent._HudLiveCaption(f)
    f.bind_caption_tail(tail)
    await tail.capture_text("ordinary")
    tail.flush()
    assert writes
    writes.clear()
    async with f.sensitive():
        await tail.capture_text("synthetic secret")
        tail.flush()
    await tail.capture_text("late synthetic secret")
    tail.flush()
    assert not writes and not tail._text
    await f.close()


@pytest.mark.asyncio
async def test_consent_file_and_local_grant_are_explicit_and_revoked(owner, tmp_path):
    import importlib.util
    from pathlib import Path

    from feature_sessions import load_host_consent
    from librarian import Librarian
    from librarian_sources import LocalSource

    spec = importlib.util.spec_from_file_location(
        "consent_writer", Path(__file__).parents[1] / "scripts/feature_consent.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    folder = tmp_path / "synthetic-scope"
    folder.mkdir()
    consent_path = tmp_path / "consent.json"
    module.write_consent(consent_path, folder=folder, content=False, disclosure=False)
    consent = load_host_consent(str(consent_path))
    service = Librarian(adapters={"local": LocalSource()})
    f = FeatureSession(Session(), librarian=service, authorized=lambda: True)
    with pytest.raises(ValueError):
        f.configure(consent)
    assert not service._grants
    f.configure(consent, confirmed=True)
    assert len(service._grants) == 1
    assert all(
        not g.content and not g.model_disclosure for g in service._grants.values()
    )
    f.revoke_authority()
    assert not service._grants
    await f.close()
    with pytest.raises(FileExistsError):
        module.write_consent(consent_path)
    link = tmp_path / "link"
    link.symlink_to(consent_path)
    with pytest.raises(OSError):
        load_host_consent(str(link))


@pytest.mark.asyncio
async def test_local_audio_delivery_cannot_use_generate_reply_or_chat_context(owner):
    import contextlib

    emitted = []

    class SpeechSession(Session):
        def generate_reply(self, *a, **kw):
            pytest.fail("sensitive text entered model")

        def say(self, text, **kw):
            assert kw["add_to_chat_ctx"] is False and "audio" in kw

            class Speech:
                interrupted = False

                async def wait_for_playout(self):
                    async for frame in kw["audio"]:
                        emitted.append(frame)

                def exception(self):
                    return None

            return Speech()

    class Voice:
        @contextlib.asynccontextmanager
        async def synthesize(self, text):
            async def chunks():
                yield SimpleNamespace(frame=SimpleNamespace(samples_per_channel=3))

            yield chunks()

        async def aclose(self):
            pass

    s = SpeechSession()
    f = FeatureSession(s, authorized=lambda: True)
    f.bind_caption_tail(SimpleNamespace(clear_sensitive=lambda: None))
    f._tts = Voice()
    async with f.sensitive():
        await f._deliver("synthetic private reply")
    assert emitted and not s.output.transcription_enabled
    await f.close()


@pytest.mark.asyncio
async def test_control_error_is_consumed_and_sanitized(owner):
    class Tasks:
        async def cancel_task(self, *a, **kw):
            raise RuntimeError("secret sentinel")

    s = Session()
    f = FeatureSession(s, task_tools=Tasks(), authorized=lambda: True)
    assert await f.handle_message(
        ChatMessage(role="user", content=["cancel task task-example"])
    )
    assert all("sentinel" not in line for line, _ in s.lines)
    await f.close()


@pytest.mark.asyncio
async def test_sensitive_delivery_cancellation_drains_owned_speech(owner):
    started = asyncio.Event()
    settled = asyncio.Event()
    state = {"interrupted": False}

    class Speech:
        interrupted = False

        def interrupt(self):
            state["interrupted"] = True

        async def wait_for_playout(self):
            started.set()
            await settled.wait()

        def exception(self):
            return None

    class VoiceSession(Session):
        def say(self, text, **kw):
            return Speech()

    f = FeatureSession(VoiceSession(), authorized=lambda: True)
    f._tts = SimpleNamespace(
        synthesize=lambda text: None, aclose=lambda: asyncio.sleep(0)
    )
    f.bind_caption_tail(SimpleNamespace(clear_sensitive=lambda: None))

    async def speak():
        async with f.sensitive():
            await f._deliver("synthetic")

    task = asyncio.create_task(speak())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and f.sensitive_active
    task.cancel()
    await asyncio.sleep(0)
    settled.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert state["interrupted"] and not f.sensitive_active and not f.captions_allowed
    await f.close()


@pytest.mark.asyncio
async def test_guest_revocation_clears_even_host_injected_librarian_grants(
    owner, tmp_path
):
    from librarian import Grant, Librarian
    from librarian_sources import LocalSource

    service = Librarian(adapters={"local": LocalSource()})
    service.approve(Grant("pre-existing", "local", str(tmp_path)), confirmed=True)
    f = FeatureSession(Session(), librarian=service, authorized=lambda: True)
    f.revoke_authority()
    assert not service._grants and not service.index.sources()
    await f.close()


@pytest.mark.asyncio
async def test_partial_host_grants_rollback_and_echo_never_dispatch(owner, tmp_path):
    from librarian import Librarian
    from librarian_sources import LocalSource

    service = Librarian(adapters={"local": LocalSource()})
    b = Backend()
    s = Session()
    f = FeatureSession(s, backend=b, librarian=service, authorized=lambda: True)
    good = {"grant_id": "good", "source": "local", "scope": str(tmp_path)}
    with pytest.raises(ValueError):
        f.configure(
            HostConsent(
                expires_at=time.time() + 60, librarian_grants=(good, {"source": "bad"})
            ),
            confirmed=True,
        )
    assert not service._grants
    f.is_echo = lambda text: True
    f.install(realtime=True)
    s.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(
            transcript="look at my screen", is_final=True, item_id="echo"
        ),
    )
    await f.drain()
    assert not b.calls and not s.lines
    assert await f.handle_message(
        ChatMessage(role="user", content=["cancel task task-example"])
    )
    assert not s.interrupts
    await f.close()


@pytest.mark.asyncio
async def test_expired_screen_consent_cannot_authorize_capture(owner):
    s = Session()
    b = Backend()
    f = FeatureSession(s, backend=b, authorized=lambda: True)
    f._expires = time.time() - 1
    f.screen.disclosure_accepted = True
    assert not f.screen_authorized()
    assert await f.handle_message(
        ChatMessage(role="user", content=["look at my screen"])
    )
    assert not b.calls
    await f.close()


@pytest.mark.asyncio
async def test_sdk_close_event_and_shutdown_callback_drain_once(owner):
    s = Session()
    b = Backend()
    f = FeatureSession(s, backend=b, authorized=lambda: True)
    f.install(realtime=True)
    s.emit("close", SimpleNamespace())
    await asyncio.gather(f.close(), f.close())
    assert b.closed and not s.jarvis_screen_turn_id
    assert all(not handlers for handlers in s.handlers.values())


@pytest.mark.asyncio
async def test_explicit_existing_drive_grant_does_not_connect_at_configuration(
    owner, tmp_path
):
    from librarian import Librarian
    from librarian_sources import DriveSource

    calls = []
    service = Librarian(
        adapters={"drive": DriveSource(lambda *a, **kw: calls.append(a))}
    )
    f = FeatureSession(Session(), librarian=service, authorized=lambda: True)
    grant = {
        "grant_id": "folder",
        "source": "drive",
        "scope": "synthetic-folder",
        "account": "school",
        "content": False,
        "model_disclosure": False,
    }
    f.configure(
        HostConsent(expires_at=time.time() + 60, librarian_grants=(grant,)),
        confirmed=True,
    )
    assert not calls
    assert service._grants["folder"].account == "school"
    await f.close()


@pytest.mark.asyncio
async def test_sensitive_echo_cannot_mint_controls_or_enter_user_context(owner):
    s = Session()
    calls = []

    class Tasks:
        async def cancel_task(self, context, task_id=""):
            calls.append(task_id)
            return {"say": "cancelled"}

    f = FeatureSession(s, task_tools=Tasks(), authorized=lambda: True)
    f.bind_caption_tail(s.jarvis_caption_tail)
    f.install(realtime=True)
    async with f.sensitive():
        msg = ChatMessage(
            id="echoed-screen-text", role="user", content=["cancel task task-sentinel"]
        )
        assert await f.handle_message(msg)
        assert "sentinel" not in (msg.text_content or "")
        event = UserInputTranscribedEvent(
            transcript="look at my screen", is_final=True, item_id="echo-screen"
        )
        s.emit("user_input_transcribed", event)
        assert not event.transcript
        assert not s.input.audio_enabled
    late = ChatMessage(
        id="late-ocr", role="user", content=["synthetic private OCR passage"]
    )
    assert await f.handle_message(late)
    assert "private OCR" not in (late.text_content or "")
    stop = ChatMessage(id="safe-stop", role="user", content=["stop"])
    assert await f.handle_message(stop)
    assert not calls and s.interrupts
    await f.drain()
    await f.close()


@pytest.mark.asyncio
async def test_shutdown_drains_text_enrollment_probe(owner):
    selected = asyncio.Event()
    cleaned = asyncio.Event()

    class Probe(Backend):
        async def select(self):
            selected.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

    s = Session()
    b = Probe()
    f = FeatureSession(s, backend=b, authorized=lambda: True)
    f.bind_caption_tail(s.jarvis_caption_tail)
    f.configure(
        HostConsent(screen_local_ocr=True, expires_at=time.time() + 60), confirmed=True
    )
    request = asyncio.create_task(
        f.handle_message(ChatMessage(role="user", content=["look at my screen"]))
    )
    await asyncio.wait_for(selected.wait(), 1)
    await asyncio.wait_for(f.close(), 2)
    assert request.done() and cleaned.is_set() and b.closed
    await request


@pytest.mark.asyncio
async def test_five_feature_release_screen_flag_is_independent_of_consent(
    owner, monkeypatch
):
    monkeypatch.delenv("JARVIS_SCREEN_READING", raising=False)
    s = Session()
    b = Backend()
    f = FeatureSession(s, backend=b, authorized=lambda: True)
    f.bind_caption_tail(s.jarvis_caption_tail)
    f.configure(
        HostConsent(screen_local_ocr=True, expires_at=time.time() + 60), confirmed=True
    )
    assert not f.screen.disclosure_accepted
    assert await f.handle_message(
        ChatMessage(role="user", content=["look at my screen"])
    )
    assert not b.calls
    assert await f.handle_message(ChatMessage(role="user", content=["stop"]))
    assert s.interrupts
    await f.close()
