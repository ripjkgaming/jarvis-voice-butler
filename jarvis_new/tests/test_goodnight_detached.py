import asyncio
import os
from types import SimpleNamespace

import pytest
from goodnight_detached import Binding
from goodnight_supervisor import Client, Supervisor

from goodnight_fence import Fence
from goodnight_host import _write


class Session:
    agent_state = user_state = "listening"

    def __init__(self):
        self.handlers = {}
        self.said = []

    def on(self, n, c):
        self.handlers.setdefault(n, []).append(c)

    def off(self, n, c):
        self.handlers[n].remove(c)

    def say(self, text):
        self.said.append(text)

        async def done():
            pass

        return SimpleNamespace(wait_for_playout=done)

    def emit(self, name, event):
        for callback in list(self.handlers[name]):
            callback(event)


@pytest.fixture
def host(tmp_path):
    now = [0.0]
    allowed = [True]
    busy = [False]
    idle = [10000.0]
    effects = []
    s = Supervisor(
        fence=Fence(tmp_path),
        clock=lambda: now[0],
        authorized=lambda: allowed[0],
        idle=SimpleNamespace(
            read=lambda: idle[0], start=lambda: None, close=lambda: None
        ),
        inventory=SimpleNamespace(clear=lambda: not busy[0]),
        session_lock=SimpleNamespace(
            identity=lambda: "owner-session",
            lock=lambda identity: effects.append(identity) or True,
        ),
    ).start(background=False)
    yield s, now, allowed, busy, idle, effects
    s.close()


def packet(s, now, operation="arm", call=None, generation=None):
    client = Client(s.fence, clock=lambda: now[0])
    if call is None:
        call = "a" * 32
        path = s.calls.directory / (call + ".json")
        _write(
            path,
            {"at": now[0], "agent": "listening", "user": "listening", "allowed": True},
        )
        _write(path.with_suffix(".activity"), {"at": now[0]})
    return client.packet(
        operation,
        call=call,
        turn="trusted-final",
        generation=generation,
        owner=f"{os.getuid()}/owner-session",
    )


def wait(s, now, seconds=605):
    for _ in range(seconds // 5):
        now[0] += 5
        s.step()


@pytest.mark.asyncio
async def test_normal_call_close_transfers_plan_and_locks_without_session(
    host, monkeypatch
):
    s, now, _allowed, _busy, _idle, effects = host

    async def local(f, *a, **kw):
        return f(*a, **kw)

    monkeypatch.setattr(asyncio, "to_thread", local)

    class Transport:
        def send(self, operation, **options):
            p = packet(
                s,
                now,
                operation,
                call=options["call"],
                generation=options.get("generation"),
            )
            return s.accept(p)

    session = Session()
    b = Binding(
        session,
        SimpleNamespace(
            authorized=lambda: True, sensitive_active=False, is_echo=lambda _: False
        ),
        fence=s.fence,
        client=Transport(),
        clock=lambda: now[0],
        owner=lambda: True,
    ).install()
    session.emit(
        "user_input_transcribed",
        SimpleNamespace(is_final=True, transcript="goodnight", item_id="turn1"),
    )
    for _ in range(12):
        await asyncio.sleep(0)
    assert s.host.controller.status()["state"] == "armed"
    expiry = s.host.controller.status()["expires_at"]
    assert session.said and "even after this conversation ends" in session.said[0]
    session.emit("close", SimpleNamespace())
    for _ in range(12):
        await asyncio.sleep(0)
    await b.close()
    import json

    assert json.loads(b.path.read_text())["closed"] is True
    assert all(not cs for cs in session.handlers.values())
    assert b.poll_task.done() and all(t.done() for t in b.inputs)
    assert s.host.controller.status()["expires_at"] == expiry
    assert not b.armed()  # no retained mic/session prevention
    wait(s, now)
    assert effects == ["owner-session"]
    assert s.host.controller.status()["state"] == "locked"


@pytest.mark.parametrize("block", ["task", "input", "speech"])
def test_new_work_input_speech_postpone_without_extending_expiry(host, block):
    s, now, _allowed, busy, idle, effects = host
    p = packet(s, now)
    s.accept(p)
    path = s.calls.directory / (p["data"]["call"] + ".json")
    path.unlink()
    path.with_suffix(".activity").unlink()
    expiry = s.host.controller.status()["expires_at"]
    wait(s, now, 595)
    if block == "task":
        busy[0] = True
    elif block == "input":
        idle[0] = 0
    else:
        q = packet(s, now, "status")
        path = s.calls.directory / (q["data"]["call"] + ".json")
        row = {"at": now[0], "agent": "speaking", "user": "listening", "allowed": True}
        _write(path, row)
    now[0] += 5
    s.step()
    assert not effects and s.host.controller.status()["state"] == "armed"
    assert s.host.controller.status()["expires_at"] == expiry


def test_cancel_during_handoff_blocks_delayed_arm_and_does_not_revive(host):
    s, now, _, _, _, effects = host
    p = packet(s, now)
    s.control("cancel")
    with pytest.raises(ValueError):
        s.accept(p)
    wait(s, now)
    assert not effects


def test_generation_cancel_cannot_cancel_new_plan(host):
    s, now, *_ = host
    p = packet(s, now)
    r = s.accept(p)
    old = r["current"]["token"]
    cancel = packet(s, now, "cancel", generation=old)
    s.control("cancel")
    now[0] += 1
    new = packet(s, now)
    s.accept(new)
    assert s.accept(cancel)["ok"] is False
    assert s.host.controller.status()["token"] == new["data"]["generation"]


def test_tamper_replay_expiry_and_owner_generation(host):
    s, now, allowed, _, _, effects = host
    p = packet(s, now)
    s.accept(p)
    now[0] += 1
    assert s.accept(p)["replay"] is True
    assert s.host.controller.status()["expires_at"] == 3600
    tampered = {
        "data": dict(p["data"], owner="other-owner"),
        "signature": p["signature"],
    }
    with pytest.raises(ValueError):
        s.accept(tampered)
    (s.directory / "revoked").touch(mode=0o600)
    s.step()
    allowed[0] = False
    s.step()
    allowed[0] = True
    s.step()
    assert s.host.controller.status()["state"] == "cancelled"
    assert not effects
    with pytest.raises(ValueError):
        s.accept(packet(s, now))


def test_restart_rejects_old_handoff_and_never_restores(host):
    s, now, *_ = host
    p = packet(s, now)
    s.accept(p)
    s.close()
    newer = Supervisor(
        fence=s.fence,
        clock=lambda: now[0],
        authorized=lambda: True,
        idle=s.host.idle,
        inventory=s.host.inventory,
        session_lock=s.host.session_lock,
    ).start(background=False)
    try:
        with pytest.raises(ValueError):
            newer.accept(p)
        assert newer.host.controller.status()["state"] == "disarmed"
    finally:
        newer.close()


def test_expiry_across_suspend_drops_plan(host):
    s, now, *_ = host
    s.accept(packet(s, now))
    now[0] = 3601
    s.step()
    assert s.host.controller.status()["state"] == "expired"


def test_unknown_call_timestamp_and_stale_row_hold(host):
    s, now, *_ = host
    p = packet(s, now)
    s.accept(p)
    wait(s, now)
    assert s.host.controller.status()["state"] == "armed"
    path = s.calls.directory / (p["data"]["call"] + ".json")
    path.write_text('{"at":NaN,"allowed":true,"agent":"listening","user":"listening"}')
    assert s.calls.sample() == (None, None)


def clear_call(s, p, now):
    path = s.calls.directory / (p["data"]["call"] + ".json")
    _write(path, {"at": now[0], "closed": True})
    _write(path.with_suffix(".activity"), {"at": now[0]})


def test_original_callback_time_is_immutable_through_dispatch(host):
    s, now, *_ = host
    c = Client(s.fence, clock=lambda: now[0])
    now[0] = 9
    p = c.packet(
        "arm", call="b" * 32, turn="trusted-original", issued=0, generation="c" * 32
    )
    _write(
        s.calls.directory / ("b" * 32 + ".json"),
        {"at": 9, "agent": "listening", "user": "listening", "allowed": True},
    )
    _write(s.calls.directory / ("b" * 32 + ".activity"), {"at": 9})
    r = s.accept(p)
    assert r["current"]["armed_at"] == 0 and r["current"]["expires_at"] == 3600
    now[0] = 20
    p = c.packet("arm", call="b" * 32, turn="late", issued=0)
    with pytest.raises(ValueError):
        s.accept(p)


@pytest.mark.parametrize("race", ["voice_cancel", "task", "input"])
def test_race_before_final_commit(host, race):
    import threading

    s, now, _allowed, busy, idle, effects = host
    p = packet(s, now)
    s.accept(p)
    clear_call(s, p, now)
    wait(s, now, 595)
    now[0] += 5
    arrived = threading.Event()
    release = threading.Event()
    original = s.host.controller.finalizer

    def paused(expected, commit):
        arrived.set()
        assert release.wait(2)
        return original(expected, commit)

    s.host.controller.finalizer = paused
    t = threading.Thread(target=s.host.tick)
    t.start()
    assert arrived.wait(2)
    with s.fence():
        if race == "voice_cancel":
            cancel = packet(
                s,
                now,
                "cancel",
                call=p["data"]["call"],
                generation=p["data"]["generation"],
            )
            _write(s.directory / "cancels" / (cancel["data"]["id"] + ".json"), cancel)
        elif race == "task":
            busy[0] = True
        else:
            idle[0] = 0
        release.set()
    t.join(2)
    assert not t.is_alive() and not effects
    assert s.host.controller.status()["state"] == (
        "cancelled" if race == "voice_cancel" else "armed"
    )


@pytest.mark.parametrize("change", ["expiry", "speech"])
def test_slow_final_probe_cannot_consume_pending_plan_early(host, change):
    s, now, *rest = host
    effects = rest[-1]
    p = packet(s, now)
    s.accept(p)
    clear_call(s, p, now)
    wait(s, now, 595)
    now[0] += 5
    original = s.host.probe
    reads = [0]

    def delayed():
        reads[0] += 1
        if reads[0] == 3:  # third sample is final under-fence slow probe
            if change == "expiry":
                now[0] = 3601
            else:
                path = s.calls.directory / (p["data"]["call"] + ".json")
                _write(path.with_suffix(".activity"), {"at": now[0]})
        return original()

    s.host.probe = delayed
    s.host.controller.probe = delayed
    s.host.tick()
    assert not effects
    assert s.host.controller.status()["state"] == (
        "expired" if change == "expiry" else "armed"
    )


def test_closed_speech_activity_persists_after_session_settlement(host):
    s, now, *rest = host
    effects = rest[-1]
    p = packet(s, now)
    s.accept(p)
    now[0] = 20
    clear_call(s, p, now)
    wait(s, now, 595)
    assert not effects
    now[0] += 5
    s.step()
    assert effects == ["owner-session"]


@pytest.mark.asyncio
async def test_actual_trusted_voice_cancel_publishes_urgent_without_status_roundtrip(
    host, monkeypatch
):
    import threading

    s, now, *rest = host
    effects = rest[-1]
    p = packet(s, now)
    s.accept(p)
    clear_call(s, p, now)
    wait(s, now, 595)
    now[0] += 5

    async def local(f, *a, **kw):
        return f(*a, **kw)

    monkeypatch.setattr(asyncio, "to_thread", local)
    session = Session()
    b = Binding(
        session,
        SimpleNamespace(
            authorized=lambda: True, sensitive_active=False, is_echo=lambda _: False
        ),
        fence=s.fence,
        client=Client(s.fence, clock=lambda: now[0]),
        clock=lambda: now[0],
        owner=lambda: True,
    ).install()
    entered = threading.Event()
    release = threading.Event()
    original = s.host.controller.finalizer

    def paused(expected, commit):
        entered.set()
        assert release.wait(6)
        return original(expected, commit)

    # Call is quiet but its activity signal is recent. Make it old enough only
    # for this synthetic race; real call start correctly postpones the plan.
    _write(b.activity_path, {"at": 0})
    s.host.controller.finalizer = paused
    t = threading.Thread(target=s.host.tick)
    t.start()
    assert entered.wait(2)
    try:
        with s.fence():
            session.emit(
                "user_input_transcribed",
                SimpleNamespace(
                    is_final=True, transcript="cancel goodnight", item_id="real-cancel"
                ),
            )
            for _ in range(15):
                await asyncio.sleep(0)
            urgent = list((s.directory / "cancels").glob("*.json"))
            assert urgent  # actual _dispatch→Client.cancel→Client.send mailbox
            assert not list((s.directory / "inbox").glob("*.json"))  # no status RPC
            release.set()
        t.join(2)
        assert not t.is_alive() and not effects
        assert s.host.controller.status()["state"] == "cancelled"
    finally:
        release.set()
        t.join(2)
        await b.close()


@pytest.mark.asyncio
async def test_unavailable_supervisor_consumes_command_and_never_claims_arm(
    tmp_path, monkeypatch
):
    async def local(f, *a, **kw):
        return f(*a, **kw)

    monkeypatch.setattr(asyncio, "to_thread", local)
    session = Session()
    b = Binding(
        session,
        SimpleNamespace(
            authorized=lambda: True, sensitive_active=False, is_echo=lambda _: False
        ),
        fence=Fence(tmp_path),
        owner=lambda: True,
    ).install()
    session.emit(
        "user_input_transcribed",
        SimpleNamespace(is_final=True, transcript="goodnight", item_id="unavailable"),
    )
    for _ in range(12):
        await asyncio.sleep(0)
    assert b.consume(SimpleNamespace(text_content="goodnight"))
    assert session.said and "not confirmed" in session.said[0]
    assert not b.armed()
    await b.close()


def test_startup_publication_failure_releases_election_fd(tmp_path, monkeypatch):
    import goodnight_supervisor as gs

    s = Supervisor(
        fence=Fence(tmp_path),
        authorized=lambda: True,
        idle=SimpleNamespace(read=lambda: 0, start=lambda: None, close=lambda: None),
        inventory=SimpleNamespace(clear=lambda: True),
        session_lock=SimpleNamespace(identity=lambda: "owner-session"),
    )
    original = gs._write
    monkeypatch.setattr(
        gs,
        "_write",
        lambda *args: (_ for _ in ()).throw(OSError("synthetic publication failure")),
    )
    with pytest.raises(OSError):
        s.start(background=False)
    assert s.fd is None
    monkeypatch.setattr(gs, "_write", original)
    new = Supervisor(
        fence=s.fence,
        authorized=lambda: True,
        idle=s.host.idle,
        inventory=s.host.inventory,
        session_lock=s.host.session_lock,
    ).start(background=False)
    new.close()
