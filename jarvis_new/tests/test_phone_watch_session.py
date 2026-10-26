"""Offline watch-phone ownership tests: no camera, ring, audio or cloud calls."""

import asyncio
import base64
import json
import time
from types import SimpleNamespace

import pytest

import bridge
import hud_events
import mint_token
import phone_api
import watch_mode


@pytest.fixture
def creds(monkeypatch):
    c = {
        "LIVEKIT_URL": "wss://test",
        "LIVEKIT_API_KEY": "test",
        "LIVEKIT_API_SECRET": "a" * 40,
    }
    monkeypatch.setattr(mint_token, "load_creds", lambda: c)
    return c


def claims(payload):
    return json.loads(
        base64.urlsafe_b64decode(payload["participantToken"].split(".")[1] + "==")
    )


def test_private_token_is_unique_signed_and_single_dispatch(creds):
    import phone_session as ps

    a = mint_token.mint_token_payload(private_phone=True)
    b = mint_token.mint_token_payload(private_phone=True)
    assert a["roomName"] != b["roomName"]
    p = claims(a)
    assert p["sub"] == ps.identity_for(a["roomName"])
    assert p["video"]["room"] == a["roomName"]
    assert p["video"]["canUpdateOwnMetadata"] is False
    assert p["attributes"] == {"jarvis.phone_owner": a["roomName"]}
    dispatch = p["roomConfig"]["agents"]
    assert len(dispatch) == 1
    assert ps.verify(dispatch[0]["metadata"], a["roomName"]) == p["sub"]
    with pytest.raises(ValueError):
        mint_token.mint_token_payload(a["roomName"], False)


@pytest.mark.parametrize(
    "mutation", ["room", "identity", "signature", "expired", "future"]
)
def test_forged_unmatched_expired_dispatch_fails_closed(creds, mutation):
    import phone_session as ps

    room = ps.new_room()
    raw = json.loads(ps.sign(room))
    if mutation in {"room", "identity", "signature"}:
        raw[mutation] = "forged"
    elif mutation == "expired":
        raw = json.loads(ps.sign(room, now=time.time() - 901))
    else:
        raw = json.loads(ps.sign(room, now=time.time() + 30))
    with pytest.raises(ValueError):
        ps.verify(json.dumps(raw), room)


class FakeContext:
    def __init__(self, room, metadata, matched=True):
        self.room = FakeRoom(room)
        self.job = SimpleNamespace(metadata=metadata)
        self.shutdowns = []
        self.matched = matched
        self.connected = 0

    async def connect(self):
        self.connected += 1

    async def wait_for_participant(self, identity):
        return SimpleNamespace(
            identity=identity,
            attributes={
                "jarvis.phone_owner": self.room.name if self.matched else "wrong"
            },
        )

    def add_shutdown_callback(self, cb):
        self.shutdowns.append(cb)


@pytest.mark.asyncio
async def test_concurrent_guest_private_owner_and_cleanup_replay(creds):
    import phone_session as ps

    watch_mode.enter_guest()
    room = ps.new_room()
    ctx = FakeContext(room, ps.sign(room))
    observed = []

    @ps.isolated_job
    async def job(context):
        assert ps.identity() == ps.identity_for(room)
        assert not ps.guest_required()
        assert not bridge._effective_guest({})
        assert bridge._effective_guest({"guest": True})
        assert not hud_events.caption("sir", "private words")
        assert not hud_events.live_caption("1", "private live words")
        assert ps.has_private_calls()
        # Reconnection within this job retains owner identity without a new dispatch.
        await asyncio.sleep(0)
        observed.append(ps.identity())

    await job(ctx)
    assert ctx.connected == 1
    assert observed == [ps.identity_for(room)]
    assert ps.identity() == ""
    assert ps.guest_required()
    assert bridge._effective_guest({"guest": False})
    assert not hud_events.captions_path().exists()
    assert ps.has_private_calls()
    for cb in ctx.shutdowns:
        await cb()
    assert not ps.has_private_calls()
    with pytest.raises(RuntimeError, match="already claimed"):
        await job(ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancel", "identity", "error"])
async def test_failed_or_cancelled_job_cleans_ownership(creds, failure):
    import phone_session as ps

    watch_mode.enter_guest()
    room = ps.new_room()
    ctx = FakeContext(room, ps.sign(room), matched=failure != "identity")

    @ps.isolated_job
    async def job(context):
        if failure == "cancel":
            raise asyncio.CancelledError()
        raise RuntimeError("failed start")

    with pytest.raises((RuntimeError, asyncio.CancelledError)):
        await job(ctx)
    assert ps.identity() == ""
    assert ps.guest_required()
    assert not ps.has_private_calls()


def test_private_session_api_rejects_guest_and_watch_join():
    seen = []

    def mint(*args):
        seen.append(args)
        return {}

    assert (
        phone_api.session(
            {"api_version": 1, "mode": "new", "guest": True},
            lambda: None,
            lambda: False,
            mint,
        )[0]
        == 403
    )
    assert seen == []


def test_caption_filter_is_only_during_guest_or_active_private_call(monkeypatch):
    import phone_session as ps

    monkeypatch.setattr(watch_mode, "guest_required", lambda: False)
    monkeypatch.setattr(ps, "has_private_calls", lambda: False)
    assert not ps.hide_shared_captions("100.64.0.2")
    monkeypatch.setattr(ps, "has_private_calls", lambda: True)
    assert ps.hide_shared_captions("100.64.0.2")
    assert not ps.hide_shared_captions("127.0.0.1")
    assert not ps.hide_shared_captions("::1")


def handler_request(
    path, body=None, *, token="test-owner", configured="test-owner", peer="100.64.0.2"
):
    """Run the real HTTP handler without a socket or runtime service."""
    import io
    from email.message import Message

    handler = object.__new__(bridge._Handler)
    handler.token = configured
    handler.path = path
    handler.client_address = (peer, 1234)
    handler.headers = Message()
    handler.headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else b""
    handler.headers["Content-Length"] = str(len(data))
    handler.rfile = io.BytesIO(data)
    result = []
    handler._send = lambda code, payload: result.append((code, payload))
    if body is not None:
        handler.do_POST()
    else:
        handler.do_GET()
    return result[0]


def test_native_manual_call_during_watch_and_auth_guards(creds, monkeypatch):
    import phone_session as ps

    watch_mode.enter_guest()
    monkeypatch.setattr(bridge, "read_hud_room", lambda: "jarvis-local-guest")
    body = {"api_version": 1, "mode": "new"}
    code, payload = handler_request("/phone/v1/session", body)
    assert code == 200
    assert ps.private_room(payload["roomName"])
    assert watch_mode.guest_required()
    assert handler_request("/phone/v1/session", body, token="wrong")[0] == 401
    assert handler_request("/phone/v1/session", body, configured="")[0] == 403
    assert handler_request("/phone/v1/session", {**body, "guest": True})[0] == 403
    assert handler_request("/phone/v1/session", {**body, "mode": "laptop"})[0] == 403
    assert handler_request("/phone/v1/session", {**body, "api_version": 2})[0] == 426
    # Incoming invitations retain their independent guest/quiet-hours restrictions.
    assert handler_request("/phone/v1/calls")[0] == 403
    assert (
        handler_request("/token", {"room": payload["roomName"], "dispatch": True})[0]
        == 403
    )
    watch_mode.restore_owner()
    assert (
        handler_request("/token", {"room": payload["roomName"], "dispatch": False})[0]
        == 403
    )


def test_normal_phone_new_is_private_and_laptop_join_is_unchanged(creds, monkeypatch):
    monkeypatch.setattr(watch_mode, "guest_required", lambda: False)
    monkeypatch.setattr(bridge, "read_hud_room", lambda: "jarvis-local-owner")
    code, new = handler_request("/phone/v1/session", {"api_version": 1, "mode": "new"})
    assert code == 200
    assert new["roomName"].startswith("jarvis-private-phone-")
    assert claims(new)["attributes"]["jarvis.phone_owner"] == new["roomName"]
    code, joined = handler_request(
        "/phone/v1/session", {"api_version": 1, "mode": "laptop"}
    )
    assert code == 200
    assert joined["roomName"] == "jarvis-local-owner"
    assert "roomConfig" not in claims(joined)


def test_shared_caption_routes_hide_remote_only_then_recover(monkeypatch):
    import phone_session as ps

    monkeypatch.setattr(bridge, "read_captions", lambda n: [{"text": "local words"}])
    monkeypatch.setattr(bridge, "read_live_caption", lambda: {"text": "local live"})
    watch_mode.enter_guest()
    assert handler_request("/captions")[1]["captions"] == []
    assert handler_request("/caption/live")[1]["live"] is None
    assert handler_request("/captions", peer="127.0.0.1")[1]["captions"]
    watch_mode.restore_owner()
    monkeypatch.setattr(ps, "has_private_calls", lambda: True)
    assert handler_request("/captions")[1]["captions"] == []
    monkeypatch.setattr(ps, "has_private_calls", lambda: False)
    assert handler_request("/captions")[1]["captions"] == [{"text": "local words"}]


@pytest.mark.asyncio
async def test_private_tool_authorization_and_background_callback_scope(creds):
    from livekit.agents.llm import ToolError

    import agent
    import phone_session as ps

    watch_mode.enter_guest()
    calls = []

    async def private_tool():
        calls.append("owner tool")
        return {"ok": True}

    tool = SimpleNamespace(id="read_clipboard", _func=private_tool)
    agent._wrap_tools_with_timing([tool])
    with pytest.raises(ToolError, match="Guest mode"):
        await tool._func()
    room = ps.new_room()
    ctx = FakeContext(room, ps.sign(room))
    released = asyncio.Event()
    background = []

    @ps.isolated_job
    async def job(context):
        async def callback():
            await released.wait()
            assert ps.identity() == ps.identity_for(room)
            assert not hud_events.caption("jarvis", "private callback")
            return await tool._func()

        background.append(asyncio.create_task(callback()))

    await job(ctx)
    assert ps.guest_required()
    released.set()
    await background[0]
    assert calls == ["owner tool"]
    with pytest.raises(ToolError):
        await tool._func()
    for cb in ctx.shutdowns:
        await cb()


def test_stale_worker_marker_does_not_hide_normal_captions(tmp_path, monkeypatch):
    import phone_session as ps

    directory = tmp_path / "phone_sessions"
    monkeypatch.setattr(ps, "_state_dir", lambda: directory)
    directory.mkdir()
    (directory / "dead.active").write_text(
        json.dumps({"pid": 99999999, "start": "old"})
    )
    assert not ps.has_private_calls()
    (directory / "corrupt.active").write_text("bad")
    assert ps.has_private_calls()


@pytest.mark.asyncio
async def test_revoked_private_callbacks_cannot_act_or_publish(creds, monkeypatch):
    import phone_session as ps

    watch_mode.enter_guest()
    room = ps.new_room()
    ctx = FakeContext(room, ps.sign(room))
    go = asyncio.Event()
    tasks = []

    @ps.isolated_job
    async def ordinary(context):
        assert not ps.private()
        assert ps.guest_required()

    @ps.isolated_job
    async def job(context):
        await ordinary(FakeContext("jarvis-local-guest", ""))
        assert not ps.guest_required()

        async def late_callback():
            await go.wait()
            assert ps.private()
            assert ps.identity() == ""
            assert (
                ps.guest_required()
            )  # Revoked private jobs fail closed even after watch stops.
            assert not hud_events.caption("sir", "late private words")

        tasks.append(asyncio.create_task(late_callback()))

    await job(ctx)
    for cb in ctx.shutdowns:
        await cb()
    watch_mode.restore_owner()
    assert not ps.guest_required()
    go.set()
    await tasks[0]


@pytest.mark.asyncio
async def test_concurrent_dispatch_claim_is_atomic(creds):
    import phone_session as ps

    room = ps.new_room()
    raw = ps.sign(room)
    contexts = [FakeContext(room, raw) for _ in range(2)]
    started = []

    @ps.isolated_job
    async def job(context):
        started.append(context)
        await asyncio.sleep(0)

    results = await asyncio.gather(
        *(job(ctx) for ctx in contexts), return_exceptions=True
    )
    assert len(started) == 1
    assert sum(isinstance(r, RuntimeError) for r in results) == 1
    for ctx in contexts:
        for cb in ctx.shutdowns:
            await cb()
    assert not ps.has_private_calls()


@pytest.mark.asyncio
async def test_cancel_while_waiting_for_phone_clears_context(creds):
    import phone_session as ps

    room = ps.new_room()
    ctx = FakeContext(room, ps.sign(room))
    waiting = asyncio.Event()

    async def wait(identity):
        waiting.set()
        await asyncio.Event().wait()

    ctx.wait_for_participant = wait

    @ps.isolated_job
    async def job(context):
        pytest.fail("Cancelled join must not start the agent")

    task = asyncio.create_task(job(ctx))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ps.identity() == ""
    assert not ps.has_private_calls()


def test_expired_nonce_ledger_is_pruned_before_fresh_job(creds):
    import os

    import phone_session as ps

    directory = ps._state_dir()
    directory.mkdir(parents=True)
    old = directory / "expired.claimed"
    old.touch()
    os.utime(old, (time.time() - ps.TTL - 120,) * 2)
    room = ps.new_room()
    ctx = FakeContext(room, ps.sign(room))

    @ps.isolated_job
    async def job(context):
        assert not old.exists()

    asyncio.run(job(ctx))
    for cb in ctx.shutdowns:
        asyncio.run(cb())


class FakeRoom:
    def __init__(self, name):
        self.name = name
        self.listeners = {}

    def on(self, event, callback):
        self.listeners.setdefault(event, []).append(callback)

    def off(self, event, callback):
        self.listeners[event].remove(callback)

    def emit(self, event, *args):
        for callback in list(self.listeners.get(event, [])):
            callback(*args)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal",
    [
        "CLIENT_INITIATED",
        "ROOM_DELETED",
        "USER_REJECTED",
        "PARTICIPANT_REMOVED",
        "ROOM_CLOSED",
        "DUPLICATE_IDENTITY",
        "room",
        "close_soon",
        "aclose",
    ],
)
async def test_terminal_event_revokes_before_shutdown_and_sdk_drain(creds, terminal):
    from livekit import rtc
    from livekit.agents import AgentSession
    from livekit.agents.voice.events import CloseReason

    import phone_session as ps

    room = ps.new_room()
    ctx = FakeContext(room, ps.sign(room))
    ctx.room = FakeRoom(room)
    drain_saw_revocation = []

    @ps.isolated_job
    async def job(context):
        session = AgentSession()
        # Exercise the real installed SDK close entrypoints, without starting media.
        session._started = True

        async def drain(**kwargs):
            drain_saw_revocation.append(ps.guest_required())
            assert ps.identity() == ""

        session._teardown_activity = drain
        ps.bind_session(session)
        assert ps.identity()
        if terminal not in {"room", "close_soon", "aclose"}:
            ctx.room.emit(
                "participant_disconnected",
                SimpleNamespace(
                    identity=ps.identity(),
                    disconnect_reason=getattr(rtc.DisconnectReason, terminal),
                ),
            )
        elif terminal == "room":
            ctx.room.emit("disconnected", rtc.DisconnectReason.ROOM_DELETED)
        elif terminal == "close_soon":
            session._close_soon(reason=CloseReason.USER_INITIATED)
        else:
            await session.aclose()
        # SDK job callbacks have deliberately not run yet.
        assert ps.guest_required()
        assert ps.identity() == ""
        assert ps.private()
        ctx.room.emit("reconnected")
        assert ps.guest_required()  # Unverified events never resurrect authority.
        if terminal == "close_soon":
            await session._closing_task
        elif terminal != "aclose":
            await session.aclose()

    await job(ctx)
    assert drain_saw_revocation == [True]
    for cb in ctx.shutdowns:
        await cb()
    assert not ps.has_private_calls()


@pytest.mark.asyncio
async def test_transient_reconnect_and_other_participant_do_not_end_owner(creds):
    from livekit import rtc

    import phone_session as ps

    room = ps.new_room()
    ctx = FakeContext(room, ps.sign(room))
    ctx.room = FakeRoom(room)

    @ps.isolated_job
    async def job(context):
        owner = ps.identity()
        ctx.room.emit("reconnecting")
        ctx.room.emit(
            "participant_disconnected",
            SimpleNamespace(
                identity="unrelated",
                disconnect_reason=rtc.DisconnectReason.CLIENT_INITIATED,
            ),
        )
        ctx.room.emit(
            "participant_disconnected",
            SimpleNamespace(
                identity=owner, disconnect_reason=rtc.DisconnectReason.UNKNOWN_REASON
            ),
        )
        ctx.room.emit("reconnected")
        assert ps.identity() == owner

    await job(ctx)
    for cb in ctx.shutdowns:
        await cb()


@pytest.mark.asyncio
async def test_sensitive_private_publishers_never_enter_any_shared_feed(
    creds, monkeypatch
):
    import activity
    import hud_panels
    import phone_session as ps
    import system

    marker = "PRIVATE-phone-query-path-result-12345"
    system.log_action("guest", "normal guest event")
    activity.start("task", "normal guest event", item_id="guest-event")
    hud_panels.show("system")
    monkeypatch.setitem(
        hud_panels.GATHER, "system", lambda _: {"items": ["normal guest event"]}
    )
    watch_mode.enter_guest()
    room = ps.new_room()
    ctx = FakeContext(room, ps.sign(room))

    @ps.isolated_job
    async def job(context):
        system.log_action("private", marker)
        await asyncio.to_thread(system.log_action, "files", marker)
        assert (
            activity.start("task", marker, detail=marker, item_id="private-event") == ""
        )
        await asyncio.to_thread(
            activity.start, "task", marker, item_id="private-thread-event"
        )
        hud_events.mirror_to_log(marker, "tool_start", marker)
        hud_events.mirror_to_activity(marker, "tool_start", marker)
        with pytest.raises(PermissionError, match="private phone"):
            hud_panels.show("mail")
        with pytest.raises(PermissionError, match="private phone"):
            hud_panels.set_insights("usage")
        for route in ["/actions", "/activity", "/panels"]:
            code, payload = handler_request(route, peer="127.0.0.1")
            assert code == 200
            assert marker not in json.dumps(payload)
            assert "normal guest event" in json.dumps(payload)

    await job(ctx)
    for cb in ctx.shutdowns:
        await cb()
    assert marker not in system.LOG_PATH.read_text()
    assert hud_panels.visible() == ["system"]


@pytest.mark.asyncio
async def test_private_notify_speak_queue_fail_before_context_losing_threads(
    creds, monkeypatch
):
    import notify
    import phone_session as ps
    import speak

    marker = "PRIVATE-raw-thread-and-queued-content-98765"
    # No real toast, synthesis or thread may run in this regression.
    monkeypatch.setattr(
        speak.threading,
        "Thread",
        lambda *a, **k: pytest.fail("private raw thread spawned"),
    )
    monkeypatch.setattr(
        notify, "_toast", lambda *a: pytest.fail("private toast published")
    )
    room = ps.new_room()
    ctx = FakeContext(room, ps.sign(room))
    delayed = []
    go = asyncio.Event()

    async def assert_refused():
        with pytest.raises(PermissionError, match="private phone"):
            notify.send(marker)
        assert not notify.enqueue({"text": marker})
        assert not speak.speak(marker)
        speak.speak_local(marker)
        assert not notify.queue_path().exists()

    @ps.isolated_job
    async def job(context):
        await assert_refused()
        assert bridge.run_phone_tool("notify", {"body": marker})["ok"] is False

        async def completion():
            await go.wait()
            await assert_refused()

        delayed.append(asyncio.create_task(completion()))

    await job(ctx)
    for cb in ctx.shutdowns:
        await cb()
    go.set()
    await delayed[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_id",
    [
        "start_task",
        "approve_task",
        "start_workshop",
        "research_project",
        "find_cheaper_alternative",
        "code_project",
        "run_tests",
        "notify",
        "show_panel",
        "open_projects",
    ],
)
async def test_private_unsafe_tools_are_refused_before_origin(creds, tool_id):
    from livekit.agents.llm import ToolError

    import agent
    import phone_session as ps

    async def unsafe(*a, **kw):
        pytest.fail("private detached/output tool reached its origin")

    tool = SimpleNamespace(id=tool_id, _func=unsafe)
    agent._wrap_tools_with_timing([tool])
    room = ps.new_room()
    ctx = FakeContext(room, ps.sign(room))

    @ps.isolated_job
    async def job(context):
        with pytest.raises(ToolError, match="private phone"):
            await tool._func()

    await job(ctx)
    for cb in ctx.shutdowns:
        await cb()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["fast_path", "goals"])
@pytest.mark.parametrize("phase", ["before", "during", "unsafe"])
async def test_direct_class_dispatch_rechecks_private_authority_and_policy(
    creds, route, phase
):
    import phone_session as ps
    from intent import fast_path, goals

    room = ps.new_room()
    ctx = FakeContext(room, ps.sign(room))
    invoked = []

    class Tools:
        async def read_clipboard(self, context):
            invoked.append(True)
            if phase == "during":
                ctx.room.emit("disconnected", None)
                await asyncio.sleep(0)
            return {"say": "PRIVATE-direct-route-result"}

        async def find_cheaper_alternative(self, context):
            pytest.fail("private detached research origin invoked")

    @ps.isolated_job
    async def job(context):
        if phase == "before":
            ctx.room.emit("disconnected", None)
        tool = "find_cheaper_alternative" if phase == "unsafe" else "read_clipboard"
        if route == "fast_path":
            ok, say = await fast_path.execute(Tools(), tool, {})
        else:
            ok, say = await goals.execute([Tools()], tool, {})
        assert not ok
        assert "PRIVATE-direct-route-result" not in say
        assert invoked == ([True] if phase == "during" else [])

    await job(ctx)
    for cb in ctx.shutdowns:
        await cb()


@pytest.mark.asyncio
async def test_local_turn_does_not_say_result_after_hangup(creds, monkeypatch):
    from livekit.agents import StopResponse

    import agent
    import phone_session as ps
    from intent import fast_path

    room = ps.new_room()
    ctx = FakeContext(room, ps.sign(room))

    async def goodnight(text):
        return False

    async def match(text):
        return "read_clipboard", {}, "test"

    async def execute(*args):
        ctx.room.emit("disconnected", None)
        return True, "PRIVATE-response-after-hangup"

    monkeypatch.setattr(agent, "_pipeline_name", lambda: "local")
    monkeypatch.setattr(fast_path, "enabled", lambda: True)
    monkeypatch.setattr(fast_path, "match_async", match)
    monkeypatch.setattr(fast_path, "execute", execute)
    assistant = SimpleNamespace(
        _goodnight=goodnight,
        system_tools=object(),
        session=SimpleNamespace(
            say=lambda *a: pytest.fail("private response after hangup")
        ),
    )

    @ps.isolated_job
    async def job(context):
        with pytest.raises(StopResponse):
            await agent.Assistant.on_user_turn_completed(
                assistant,
                None,
                SimpleNamespace(text_content="read clipboard", id="private-turn"),
            )

    await job(ctx)
    for cb in ctx.shutdowns:
        await cb()


@pytest.mark.asyncio
async def test_revoked_private_guest_controls_cannot_dispatch(creds, monkeypatch):
    from livekit.agents.llm import ToolError

    import agent
    import phone_session as ps

    async def volume(*a, **kw):
        pytest.fail("ended private session changed desktop volume")

    tool = SimpleNamespace(id="set_volume", _func=volume)
    agent._wrap_tools_with_timing([tool])
    monkeypatch.setattr(
        bridge, "_run", lambda *a: pytest.fail("ended private bridge action")
    )
    room = ps.new_room()
    ctx = FakeContext(room, ps.sign(room))

    @ps.isolated_job
    async def job(context):
        ctx.room.emit("disconnected", None)
        with pytest.raises(ToolError, match="call has ended"):
            await tool._func()
        assert not bridge.run_phone_tool("volume_get", {})["ok"]

    await job(ctx)
    for cb in ctx.shutdowns:
        await cb()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["", "wrong_invitation", "expired", "quiet"])
async def test_accepted_incoming_private_owner_bound_opener_once(creds, tmp_path, monkeypatch, failure):
    import phone_calls
    import phone_session as ps

    q = phone_calls.CallQueue(tmp_path / "incoming.db")
    monkeypatch.setattr(phone_calls, "queue", lambda: q)
    now = time.time()
    monkeypatch.setattr(phone_calls, "quiet", lambda n=None: False)
    q.configure(True, True, True, now)
    assert q.offer("update", "incoming", "Server opener", now)
    invitation = q.pending(now)["invitation"]["id"]
    assert q.claim(invitation, "accept", now)
    payload = phone_calls.mint_phone_token(invitation)
    attrs = claims(payload)["attributes"]
    metadata = claims(payload)["roomConfig"]["agents"][0]["metadata"]
    ctx = FakeContext(payload["roomName"], metadata)

    async def participant(identity):
        return SimpleNamespace(identity=identity, attributes=attrs)

    ctx.wait_for_participant = participant
    if failure == "wrong_invitation":
        attrs["jarvis.phone_call"] = "f" * 32
    elif failure == "expired":
        monkeypatch.setattr(phone_calls.time, "time", lambda: now + 46)
    elif failure == "quiet":
        monkeypatch.setattr(phone_calls, "quiet", lambda n=None: True)

    @ps.isolated_job
    async def job(context):
        assert ps.invitation_id() == invitation
        assert phone_calls.consume_announcement(invitation, "jarvis-phone-" + invitation) == ""
        assert phone_calls.consume_announcement(invitation, ps.identity()) == "Server opener"
        assert phone_calls.consume_announcement(invitation, ps.identity()) == ""
        assert phone_calls.consume_announcement("f" * 32, ps.identity()) == ""
        assert not hud_events.caption("sir", "private incoming text")

    if failure:
        with pytest.raises(RuntimeError, match="invitation"):
            await job(ctx)
        assert not ps.identity()
    else:
        await job(ctx)
        for callback in ctx.shutdowns:
            await callback()
