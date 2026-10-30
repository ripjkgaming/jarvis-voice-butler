import asyncio
import threading
from types import SimpleNamespace

import pytest

from goodnight_fence import Fence
from goodnight_host import Binding, Conversations
from goodnight_runtime import Host
from goodnight_v2 import Controller, Evidence, IntentGate


class Session:
    agent_state = "listening"
    user_state = "listening"

    def __init__(self):
        self.handlers = {}
        self.said = []

    def on(self, name, callback):
        self.handlers.setdefault(name, []).append(callback)

    def off(self, name, callback):
        self.handlers[name].remove(callback)

    def say(self, text):
        self.said.append(text)

        async def done():
            return

        return SimpleNamespace(wait_for_playout=done)

    def final(self, text, event_id="trusted"):
        for callback in self.handlers["user_input_transcribed"]:
            callback(SimpleNamespace(is_final=True, transcript=text, item_id=event_id))


@pytest.mark.asyncio
async def test_explicit_final_owner_path_status_cancel_close(tmp_path, monkeypatch):
    async def local(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", local)
    session = Session()
    idle = SimpleNamespace(read=lambda: 0, start=lambda: None, close=lambda: None)
    feature = SimpleNamespace(
        authorized=lambda: True, sensitive_active=False, is_echo=lambda text: False
    )
    binding = Binding(
        session,
        feature,
        fence=Fence(tmp_path),
        idle=idle,
        session_lock=SimpleNamespace(identity=lambda: "same", lock=lambda _: True),
    ).install()
    session.final("goodnight")
    for _ in range(5):
        await asyncio.sleep(0)
    assert binding.armed()
    assert len(session.said) == 1
    session.final("goodnight")  # same event cannot extend/rearm
    for _ in range(3):
        await asyncio.sleep(0)
    assert len(session.said) == 1
    assert binding.consume(SimpleNamespace(id="trusted", text_content="goodnight"))
    assert not binding.consume(SimpleNamespace(id="history", text_content="goodnight"))
    session.final("cancel goodnight", "cancel")
    for _ in range(5):
        await asyncio.sleep(0)
    assert not binding.armed()
    await binding.close()
    assert not binding.conversations.path.exists()


def test_all_current_conversations_must_be_fresh_and_quiet(tmp_path):
    now = [100.0]
    a = Conversations(Fence(tmp_path), clock=lambda: now[0])
    b = Conversations(Fence(tmp_path), clock=lambda: now[0])
    a.update(agent="listening", user="away", allowed=True)
    assert a.clear() is True
    b.update(agent="thinking", user="listening", allowed=True)
    assert a.clear() is False
    b.update(agent="idle", user="away", allowed=True)
    assert a.clear() is True
    now[0] += 16
    assert a.clear() is None


def test_other_call_activity_visible_while_final_fence_is_held(tmp_path):
    now = [0.0]
    fence = Fence(tmp_path)
    call = Conversations(fence, clock=lambda: now[0])
    call.activity()
    call.update(agent="listening", user="away", allowed=True)
    now[0] = 700
    assert call.idle_seconds() == 700
    with fence():
        thread = threading.Thread(target=call.activity)
        thread.start()
        thread.join(1)
        assert not thread.is_alive()  # signal never waits behind final fence
        assert call.idle_seconds() == 0


def test_cancel_and_expiry_while_acquiring_final_fence(tmp_path):
    now, requests = [0.0], []
    host = Host(
        idle=SimpleNamespace(read=lambda: 10000, close=lambda: None),
        inventory=SimpleNamespace(clear=lambda: True),
        session_lock=SimpleNamespace(
            identity=lambda: "same", lock=lambda _: requests.append(1) or True
        ),
        owner_allowed=lambda: True,
        conversation_clear=lambda: True,
        launch_fence=Fence(tmp_path),
        clock=lambda: now[0],
    )
    host.controller.arm(owner=True, direct_intent=True, session="same")
    for n in range(1, 61):
        now[0] = n * 10
        if n < 60:
            host.tick()

    def finalizer(expected, commit):
        host.controller.cancel()
        return commit(host.probe(), lambda: requests.append(1) or True)

    host.controller.finalizer = finalizer
    host.tick()
    assert not requests
    assert host.controller.status()["state"] == "cancelled"
    host.controller.arm(owner=True, direct_intent=True, session="same")
    for n in range(1, 60):
        now[0] = 600 + n * 10
        host.controller.finalizer = lambda *args: None
        host.tick()

    def expired(expected, commit):
        now[0] = 4200
        return commit(host.probe(), lambda: requests.append(1) or True)

    now[0] = 1200
    host.controller.finalizer = expired
    host.tick()
    assert not requests
    assert host.controller.status()["state"] == "expired"


def test_committed_request_refuses_rearm_and_truthful_cancel():
    now, started, release = [0.0], threading.Event(), threading.Event()

    def request(session):
        started.set()
        assert release.wait(1)
        return True

    c = Controller(
        probe=lambda: Evidence(10000, True, True, True, "same"),
        lock=request,
        clock=lambda: now[0],
    )
    c.arm(owner=True, direct_intent=True, session="same")
    for n in range(1, 60):
        now[0] = n * 10
        c.tick()
    now[0] = 600
    thread = threading.Thread(target=c.tick)
    thread.start()
    assert started.wait(1)
    assert c.status()["state"] == "locking"
    assert c.arm(owner=True, direct_intent=True, session="same")["ok"] is False
    reply = IntentGate(c).input(
        "cancel goodnight", owner=True, direct=True, session="same"
    )
    assert "cannot undo" in reply
    release.set()
    thread.join(1)
    assert c.status()["state"] == "locked"
    assert c.status()["token"] is None


def test_final_unknown_conversation_resets_full_quiet_interval(tmp_path):
    now, calls = [0.0], []
    c = Controller(
        probe=lambda: Evidence(10000, True, True, True, "same"),
        lock=lambda _: calls.append(1) or True,
        clock=lambda: now[0],
    )
    c.arm(owner=True, direct_intent=True, session="same")
    for n in range(1, 60):
        now[0] = n * 10
        c.tick()
    now[0] = 600
    c.finalizer = lambda _, commit: commit(
        Evidence(10000, True, None, True, "same"), lambda: calls.append(1)
    )
    c.tick()
    assert c.status()["state"] == "armed"
    c.finalizer = None
    for n in range(1, 60):
        now[0] = 600 + n * 10
        c.tick()
    assert not calls
    now[0] = 1200
    c.tick()
    assert calls == [1]


@pytest.mark.asyncio
async def test_new_session_retires_old_host_and_retries_election(tmp_path, monkeypatch):
    async def local(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", local)
    feature = SimpleNamespace(
        authorized=lambda: True, sensitive_active=False, is_echo=lambda _: False
    )

    def make():
        return Binding(
            Session(),
            feature,
            fence=Fence(tmp_path),
            idle=SimpleNamespace(
                read=lambda: 0, start=lambda: None, close=lambda: None
            ),
            session_lock=SimpleNamespace(identity=lambda: "same", lock=lambda _: True),
        ).install()

    old = make()
    old.host.controller.arm(owner=True, direct_intent=True, session="same")
    new = make()
    assert new.fd is None
    old._step()
    new._step()
    assert old.fd is None and not old.armed()
    assert new.fd is not None and new.allowed()
    await old.close()
    await new.close()


@pytest.mark.asyncio
async def test_failed_observer_cleanup_still_releases_host_fd(tmp_path, monkeypatch):
    async def local(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", local)
    feature = SimpleNamespace(
        authorized=lambda: True, sensitive_active=False, is_echo=lambda _: False
    )
    b = Binding(
        Session(),
        feature,
        fence=Fence(tmp_path),
        idle=SimpleNamespace(read=lambda: 0, start=lambda: None, close=lambda: None),
        session_lock=SimpleNamespace(identity=lambda: "same", lock=lambda _: True),
    ).install()
    monkeypatch.setattr(
        b.conversations, "update", lambda **kw: (_ for _ in ()).throw(TimeoutError())
    )
    with pytest.raises(TimeoutError):
        await b.close()
    assert b.fd is None
    other = Fence(tmp_path)
    import fcntl
    import os

    from goodnight_fence import _open

    fd = _open(other.directory / "host.lock")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


@pytest.mark.asyncio
async def test_installed_sdk_final_event_created_at_fallback(tmp_path, monkeypatch):
    from jarvis_rt import AgentSession
    from jarvis_rt.events import (
        AgentStateChangedEvent,
        UserInputTranscribedEvent,
    )

    async def local(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", local)
    session = AgentSession()  # no start/providers/device operations
    said = []

    async def completed():
        return

    monkeypatch.setattr(
        session,
        "say",
        lambda text: said.append(text) or SimpleNamespace(wait_for_playout=completed),
    )
    feature = SimpleNamespace(
        authorized=lambda: True, sensitive_active=False, is_echo=lambda _: False
    )
    binding = Binding(
        session,
        feature,
        fence=Fence(tmp_path),
        idle=SimpleNamespace(read=lambda: 0, start=lambda: None, close=lambda: None),
        session_lock=SimpleNamespace(identity=lambda: "same", lock=lambda _: True),
    ).install()
    session.emit(
        "agent_state_changed",
        AgentStateChangedEvent(old_state="initializing", new_state="listening"),
    )
    event = UserInputTranscribedEvent(
        is_final=True, transcript="goodnight", created_at=10
    )
    session.emit("user_input_transcribed", event)
    for _ in range(10):
        await asyncio.sleep(0)
    assert binding.armed()
    assert said
    session.emit("user_input_transcribed", event)
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(said) == 1
    await binding.close()


def test_shared_status_requires_live_host_not_just_fresh_file(tmp_path, monkeypatch):
    import fcntl
    import os

    import goodnight_host as gh
    from goodnight_fence import _open

    monkeypatch.setattr(gh, "CURRENT", None)
    monkeypatch.setattr(gh, "elapsed_clock", lambda: 100)
    fence = Fence(tmp_path)
    with fence():
        gh._write(fence.directory / "epoch.json", {"epoch": "current"})
        gh._write(
            fence.directory / "status.json",
            {
                "host": "h",
                "epoch": "current",
                "at": 100,
                "state": "armed",
                "reason": "waiting",
            },
        )
    fd = _open(fence.directory / "host.lock")
    try:
        assert gh.control("status")["ok"] is False
        fcntl.flock(fd, fcntl.LOCK_EX)
        receipt = gh.control("status")
        assert receipt["ok"] is True
        assert receipt["observed_at"] == 100
        assert receipt["say"].startswith("Last reported")
        assert gh.control("cancel")["say"] == "Goodnight cancellation requested."
    finally:
        os.close(fd)


@pytest.fixture(autouse=True)
def supported_test_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_GOODNIGHT_V2", "1")


@pytest.mark.parametrize("stamp", [float("nan"), float("inf"), float("-inf"), True])
def test_invalid_activity_timestamp_never_hidden_by_valid_call(tmp_path, stamp):
    import json

    first = Conversations(Fence(tmp_path), clock=lambda: 1000)
    second = Conversations(first.fence, clock=lambda: 1000)
    for call in (first, second):
        call.update(agent="listening", user="listening", allowed=True)
        call.activity()
    second.activity_path.write_text(json.dumps({"at": stamp}))
    assert first.idle_seconds() is None
    second.path.write_text(
        json.dumps(
            {"at": stamp, "agent": "listening", "user": "listening", "allowed": True}
        )
    )
    assert first.clear() is None


@pytest.mark.asyncio
async def test_retired_call_signal_failure_cannot_leave_winner_clear(
    tmp_path, monkeypatch
):
    import goodnight_host as gh

    async def local(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", local)
    feature = SimpleNamespace(
        authorized=lambda: True, sensitive_active=False, is_echo=lambda _: False
    )

    def binding():
        return Binding(
            Session(),
            feature,
            fence=Fence(tmp_path),
            idle=SimpleNamespace(
                read=lambda: 1000, start=lambda: None, close=lambda: None
            ),
            session_lock=SimpleNamespace(identity=lambda: "same", lock=lambda _: True),
        ).install()

    retired = binding()
    winner = binding()
    old_signal = retired.conversations.activity_path.read_bytes()
    old_row = retired.conversations.path.read_bytes()
    monkeypatch.setattr(
        retired.conversations,
        "activity",
        lambda: (_ for _ in ()).throw(OSError("synthetic write failure")),
    )
    # Winner owns final admission; the old permitted/listening row and idle
    # signal still exist. Epoch mismatch itself must hold before publication.
    with winner.fence():
        retired._signal_activity()
        assert retired.conversations.activity_path.read_bytes() == old_signal
        assert retired.conversations.path.read_bytes() == old_row
        assert winner.conversations.clear() is None
        assert winner.host.probe().conversation_clear is None
    await retired.close()
    assert winner.conversations.clear() is True
    await winner.close()
    assert gh.CURRENT is None
