"""Registered screen host and native accepted transport; actual SDK, fake devices."""

import ast
import asyncio
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_screen_control_socket import request, verifier
from test_screen_session_holder import FakeRoomIO

import jarvis_rt.room_io as room_io_module
import screen_host
from feature_sessions import HostConsent
from jarvis_rt import AgentSession, ChatMessage, StopResponse
from jarvis_rt.events import UserInputTranscribedEvent
from screen_control_socket import NativeControlServer
from screen_host import ScreenHostConfig, start_screen_host
from screen_local_stt import DrainingScreenSTT


class Backend:
    def __init__(self):
        self.calls = []
        self.closed = False

    async def select(self):
        self.calls.append("select")
        return "synthetic selected window"

    async def capture(self, selected, check):
        check()
        self.calls.append("capture")
        return b"synthetic mocked PNG"

    async def ocr(self, image, check):
        check()
        self.calls.append("ocr")
        return "Synthetic permission denied"

    async def close(self):
        self.closed = True


class Server(NativeControlServer):
    async def start(self):
        self.bound = True  # no real socket bind in device-free host tests


class Room:
    def __init__(self, context):
        self.context = context

    async def close(self):
        self.context.ended += 1


class Context:
    def __init__(self):
        self.ended = 0
        self.callbacks = []
        self.room = Room(self)
        self.connected = 0

    async def connect(self):
        self.connected += 1

    def add_shutdown_callback(self, callback):
        self.callbacks.append(callback)


@pytest.fixture
async def rig(monkeypatch, tmp_path):
    FakeRoomIO.instances = []
    FakeRoomIO.entered = None
    FakeRoomIO.release = None
    FakeRoomIO.fail = False
    monkeypatch.setattr(room_io_module, "RoomIO", FakeRoomIO)
    monkeypatch.setenv("JARVIS_SCREEN_READING", "1")
    sessions, backends, captions, reads = [], [], [], []
    owner = {"allowed": True}

    def session_factory():
        session = AgentSession(llm=None)
        sessions.append(session)
        return session

    def backend_factory():
        backend = Backend()
        backends.append(backend)
        return backend

    def consent(path):
        reads.append(path)
        return HostConsent(screen_local_ocr=True, expires_at=time.time() + 60)

    config = ScreenHostConfig(
        str(tmp_path / "native.sock"),
        "/explicit/native-shell",
        str(tmp_path / "explicit-consent.json"),
    )
    context = Context()
    kwargs = {
        "config": config,
        "session_factory": session_factory,
        "backend_factory": backend_factory,
        "owner": lambda: owner["allowed"],
        "consent_loader": consent,
        "caption": lambda role, text: captions.append((role, text)),
        "server_factory": Server,
        "verifier_factory": lambda path: verifier(),
    }
    yield SimpleNamespace(
        ctx=context,
        kwargs=kwargs,
        sessions=sessions,
        backends=backends,
        captions=captions,
        reads=reads,
        owner=owner,
    )
    if hasattr(context, "jarvis_screen_host"):
        await context.jarvis_screen_host.close()


async def user_turn(host, text, identity="trusted-user"):
    session = host.holder.session
    message = ChatMessage(id=identity, role="user", content=[text])
    with pytest.raises(StopResponse):
        await session.current_agent.on_user_turn_completed(None, message)


@pytest.mark.asyncio
async def test_registered_host_native_resume_user_snapshot_stop_rollover_hangup(rig):
    host = await start_screen_host(rig.ctx, **rig.kwargs)
    assert rig.ctx.jarvis_screen_host is host
    assert rig.ctx.connected == 1 and len(rig.ctx.callbacks) == 1
    assert host.controls.bound and not rig.sessions and not rig.reads
    assert await request(host.controls, b"resume_quiet\n") == b"ok listening 1\n"
    old = rig.sessions[0]
    assert old._started and old.backend is None
    assert old.options.get("llm") is None
    assert not old.jarvis_features.screen.disclosure_accepted
    assert not old.jarvis_features.screen_authorized()
    assert not rig.reads and not rig.backends[0].calls
    spoken = []

    async def deliver(text):
        spoken.append(text)

    old.jarvis_features._deliver_override = deliver
    await user_turn(host, "look at my screen")
    assert rig.reads == [rig.kwargs["config"].consent_path]
    assert rig.backends[0].calls.count("capture") == 1
    assert spoken and "permission denied" in spoken[0].lower()
    assert not old.input.audio_enabled
    assert not rig.captions
    assert old.jarvis_features.input_quarantined
    old_callback = next(iter(old._handlers["user_input_transcribed"]))
    assert await request(host.controls, b"stop\n") == b"ok stopped 0\n"
    assert rig.backends[0].closed and old._closed
    assert rig.ctx.ended == 0
    assert await request(host.controls, b"resume_quiet\n") == b"ok listening 3\n"
    fresh = rig.sessions[1]
    assert fresh is not old and fresh.input.audio_enabled
    assert not fresh.jarvis_features.screen.disclosure_accepted
    assert not fresh.jarvis_features.screen_authorized()
    assert len(rig.reads) == 1  # physical Resume never regrants consent
    old_callback(
        UserInputTranscribedEvent(transcript="old private echo", is_final=True)
    )
    assert not rig.captions
    assert all(io.room is rig.ctx.room for io in FakeRoomIO.instances)
    assert all(not io.options.delete_room_on_close for io in FakeRoomIO.instances)
    assert await request(host.controls, b"hangup\n") == b"ok closed 0\n"
    await rig.ctx.callbacks[0]()
    assert rig.ctx.ended == 1
    assert all(backend.closed for backend in rig.backends)


@pytest.mark.asyncio
async def test_echo_and_duplicate_id_never_reload_consent_or_recapture(rig):
    host = await start_screen_host(rig.ctx, **rig.kwargs)
    await request(host.controls, b"resume_quiet\n")
    session = host.holder.session

    async def deliver(text):
        pass

    session.jarvis_features._deliver_override = deliver
    await user_turn(host, "look at my screen")
    await user_turn(host, "look at my screen")
    assert len(rig.reads) == 1 and rig.backends[0].calls.count("capture") == 1
    await user_turn(host, "look at my screen", "late-echo")
    assert len(rig.reads) == 1
    await host.close()


@pytest.mark.asyncio
async def test_generation_bound_observers_and_caption_sink_are_fresh(rig):
    host = await start_screen_host(rig.ctx, **rig.kwargs)
    await request(host.controls, b"resume_quiet\n")
    old = host.holder.session
    old.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(transcript="ordinary", is_final=True),
    )
    assert rig.captions == [("user", "ordinary")]
    old_tail = old.jarvis_caption_tail
    async with old.jarvis_features.sensitive():
        await old_tail.capture_text("private")
        old_tail.flush()
    old.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(transcript="private echo", is_final=True),
    )
    assert rig.captions == [("user", "ordinary")]
    await request(host.controls, b"resume_quiet\n")
    fresh = host.holder.session
    assert fresh.jarvis_caption_tail is not old_tail
    fresh.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(transcript="fresh", is_final=True),
    )
    assert rig.captions == [("user", "ordinary"), ("user", "fresh")]
    await old_tail.capture_text("queued private text")
    old_tail.flush()
    assert len(rig.captions) == 2
    await host.close()


@pytest.mark.asyncio
async def test_guest_watch_retires_sdk_without_ending_room_or_auto_resuming(rig):
    host = await start_screen_host(rig.ctx, **rig.kwargs)
    await request(host.controls, b"resume_quiet\n")
    rig.owner["allowed"] = False
    async with asyncio.timeout(2):
        while not rig.sessions[0]._closed:
            await asyncio.sleep(0.01)
    assert rig.ctx.ended == 0
    assert await request(host.controls, b"resume_quiet\n") == b"error refused\n"
    rig.owner["allowed"] = True
    await asyncio.sleep(0.25)
    assert len(rig.sessions) == 1 and host.holder.state == "stopped"
    await host.close()


@pytest.mark.asyncio
async def test_host_entrypoint_branch_uses_registered_host_before_legacy_work(
    rig, monkeypatch
):
    original = screen_host.start_screen_host

    async def injected(ctx):
        return await original(ctx, **rig.kwargs)

    monkeypatch.setattr(screen_host, "start_screen_host", injected)
    monkeypatch.setenv("JARVIS_SCREEN_HOST", "1")
    tree = ast.parse((Path(__file__).parents[1] / "src/agent.py").read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "my_agent"
    )
    # Execute the exact added early branch without importing legacy constructors,
    # dotenv/memory or any live model modules from the broader application.
    function.decorator_list = []
    function.args.args[0].annotation = None
    function.body = [function.body[0]]
    namespace = {"os": os}
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
            "staged-entrypoint",
            "exec",
        ),
        namespace,
    )
    await namespace["my_agent"](rig.ctx)
    host = rig.ctx.jarvis_screen_host
    assert await request(host.controls, b"resume_quiet\n") == b"ok listening 1\n"
    assert rig.sessions[0]._started
    await rig.ctx.callbacks[0]()


@pytest.mark.asyncio
async def test_native_auth_failure_never_constructs_sdk_or_reads_consent(rig):
    from test_screen_control_socket import Peer

    host = await start_screen_host(rig.ctx, **rig.kwargs)
    assert (
        await request(host.controls, b"resume_quiet\n", Peer(uid=os.getuid() + 1))
        == b"error refused\n"
    )
    assert not rig.sessions and not rig.reads
    await host.close()


@pytest.mark.asyncio
async def test_screen_stt_cancel_waits_for_mock_decoder_thread(monkeypatch):
    import screen_local_stt

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    cancelled_seen = []
    provider = DrainingScreenSTT(model_size="tiny.en")
    monkeypatch.setattr(
        screen_local_stt, "frames_to_mono16k", lambda _: SimpleNamespace(size=16000)
    )

    def decode(audio, language, cancelled):
        entered.set()
        release.wait(2)
        cancelled_seen.append(cancelled.is_set())
        finished.set()
        return []

    monkeypatch.setattr(provider, "_transcribe", decode)
    task = asyncio.create_task(provider._recognize_impl(object()))
    async with asyncio.timeout(2):
        while not entered.is_set():
            await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    assert not task.done() and not finished.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set() and cancelled_seen == [True]
    await provider.aclose()


def test_screen_host_requires_explicit_control_config(monkeypatch):
    monkeypatch.delenv("JARVIS_SCREEN_CONTROL_SOCKET", raising=False)
    monkeypatch.delenv("JARVIS_SCREEN_CONTROL_EXE", raising=False)
    with pytest.raises(ValueError):
        ScreenHostConfig.from_env()


@pytest.mark.asyncio
async def test_screen_whisper_only_uses_already_local_model_assets(monkeypatch):
    import sys

    calls = []

    def model(name, **kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setitem(
        sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=model)
    )
    provider = DrainingScreenSTT(model_size="tiny.en")
    assert provider._load() is provider._load()
    assert len(calls) == 1 and calls[0]["local_files_only"] is True
    await provider.aclose()
