import threading

import pytest

from event_monitor.core import Event, Store, Worker
from event_monitor_host import EventHost, SDKEngine

NOW = 1800000000.0


class Engine:
    def __init__(self):
        self.started = []
        self.events = []

    def start(self, identity, messages):
        self.started.append(identity)
        self.events = list(messages)

    def append(self, message):
        self.events.append(message)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def no_live_models(monkeypatch):
    # Runtime delivery tests exercise deterministic fallback, never account calls.
    # Tests for optional model selection inject their clients in the summary suite.
    monkeypatch.setenv("JARVIS_MONITOR_SUMMARY", "0")
    monkeypatch.setenv("JARVIS_MONITOR_TRIAGE", "0")


@pytest.fixture
def policy(monkeypatch):
    from proactive import engine

    monkeypatch.setattr(engine, "load_state", dict)
    monkeypatch.setattr(engine, "live_context", lambda *a: engine.Context(idle_s=30))


def make(tmp_path, guard=lambda: "epoch-1"):
    s = Store(tmp_path / "monitors.sqlite")
    mid = s.create(
        owner="owner",
        session_scope="desktop",
        source="jolpica",
        event="results",
        filters={"season": 2025, "round": 1},
        expires=NOW + 600,
        outputs=["session", "activate"],
        now=NOW,
        budget=3,
    )
    e = Engine()
    h = EventHost(tmp_path / "host.sqlite", guard=guard, engine=e, clock=lambda: NOW)
    return s, mid, h, e


def test_actual_dispatch_starts_non_audio_agent_and_durable_replay(tmp_path, policy):
    s, _mid, h, e = make(tmp_path)
    Worker(
        s, {"jolpica": lambda *a: [Event("one", NOW, {"race": "synthetic"})]}
    ).collect(NOW)
    assert h.dispatch(s, NOW + 1, quiet=False) == 1
    assert len(e.started) == 1 and len(e.events) == 1
    assert h.view("owner")["mode"] == "text-event-agent"
    h.close()
    recovered = EventHost(
        tmp_path / "host.sqlite",
        guard=lambda: "epoch-1",
        engine=Engine(),
        clock=lambda: NOW,
    )
    assert recovered.view("owner")["events"] == h.view("owner")["events"]
    assert recovered.dispatch(s, NOW + 2, quiet=False) == 0
    assert recovered.view("guest")["events"] == []
    recovered.close()


@pytest.mark.parametrize("guard", [lambda: None, lambda: False, lambda: 0])
def test_unknown_authority_never_starts(tmp_path, policy, guard):
    s, _, h, e = make(tmp_path, guard)
    Worker(s, {"jolpica": lambda *a: [Event("one", NOW, {})]}).collect(NOW)
    assert h.dispatch(s, NOW + 1, quiet=False) == 0
    assert not e.started
    h.close()


def test_revocation_and_concurrent_stop_block_effect(tmp_path, policy):
    epoch = ["one"]
    s, mid, h, e = make(tmp_path, lambda: epoch[0])
    Worker(s, {"jolpica": lambda *a: [Event("one", NOW, {})]}).collect(NOW)
    s.stop(mid, "owner", NOW + 0.5)
    assert h.dispatch(s, NOW + 1, quiet=False) == 0
    assert not e.events
    h.close()


def test_expiry_quiet_owner_and_fixed_receipt(tmp_path, policy):
    s, _mid, h, e = make(tmp_path)
    Worker(s, {"jolpica": lambda *a: [Event("one", NOW, {})]}).collect(NOW)
    assert h.dispatch(s, NOW + 1, quiet=True) == 0
    assert not e.started
    assert h.dispatch(s, NOW + 601, quiet=False) == 0
    h.close()


@pytest.mark.asyncio
async def test_installed_sdk_has_no_room_models_or_audio(monkeypatch):
    # Actual installed start/aclose and context update; no transport or providers.
    from jarvis_rt.room_io import RoomIO

    def forbidden(*a, **k):
        raise AssertionError("room/network forbidden")

    monkeypatch.setattr(RoomIO, "start", forbidden)
    engine = SDKEngine()
    await engine.open("synthetic", [])
    try:
        session = engine.session
        assert session.input.audio is None and session.output.audio is None
        assert session.current_agent.llm is None
        assert not session.current_agent.tools
        await engine.put({"key": "e1", "title": "Synthetic event", "detail": "{}"})
        assert (
            "Synthetic event" in session.current_agent.chat_ctx.items[-1].text_content
        )
    finally:
        await engine.aclose()
    assert engine.session is None


def test_stop_serializes_with_accepted_dispatch(tmp_path, policy):
    from concurrent.futures import ThreadPoolExecutor

    s, mid, h, e = make(tmp_path)
    Worker(s, {"jolpica": lambda *a: [Event("one", NOW, {})]}).collect(NOW)
    entered = threading.Event()
    release = threading.Event()

    def append(message):
        entered.set()
        assert release.wait(2)
        e.events.append(message)

    e.append = append
    with ThreadPoolExecutor(2) as pool:
        dispatch = pool.submit(h.dispatch, s, NOW + 1, quiet=False)
        assert entered.wait(2)
        stop = pool.submit(s.stop, mid, "owner", NOW + 1.5)
        assert not stop.done()
        release.set()
        assert dispatch.result(3) == 1
        stop.result(3)
    assert s.status(mid, "owner")["state"] == "cancelled"
    assert h.dispatch(s, NOW + 2, quiet=False) == 0
    h.close()


def test_authority_change_during_start_is_drained(tmp_path, policy):
    epoch = ["one"]
    s, _, h, e = make(tmp_path, lambda: epoch[0])
    Worker(s, {"jolpica": lambda *a: [Event("one", NOW, {})]}).collect(NOW)

    def start(identity, messages):
        e.started.append(identity)
        epoch[0] = None

    e.start = start
    assert h.dispatch(s, NOW + 1, quiet=False) == 0
    assert h.provider() is None
    assert h.view("owner")["events"] == []
    h.close()


def test_acceptance_survives_context_projection_failure(tmp_path, policy):
    s, _, h, e = make(tmp_path)
    Worker(s, {"jolpica": lambda *a: [Event("one", NOW, {})]}).collect(NOW)

    def fail(message):
        raise RuntimeError("synthetic context failure")

    e.append = fail
    assert h.dispatch(s, NOW + 1, quiet=False) == 1  # durable text acceptance only
    assert len(h.view("owner")["events"]) == 1
    assert h.provider() is None
    restarted_engine = Engine()
    restored = EventHost(
        tmp_path / "host.sqlite",
        guard=lambda: "epoch-1",
        engine=restarted_engine,
        clock=lambda: NOW,
    )
    assert restored.start("next", "owner", "desktop")
    assert len(restarted_engine.events) == 1
    assert restored.dispatch(s, NOW + 2, quiet=False) == 0
    restored.close()
    h.close()


def test_real_runtime_pass_uses_actual_sdk_and_persistent_api(
    tmp_path, policy, monkeypatch
):
    import asyncio

    # Sandbox wakeup limitation: harness tick only, including the owned SDK loop.
    original = asyncio.new_event_loop

    def ticking_loop():
        loop = original()

        def tick():
            if not loop.is_closed():
                loop.call_later(0.01, tick)

        loop.call_later(0.01, tick)
        return loop

    monkeypatch.setattr(asyncio, "new_event_loop", ticking_loop)
    from event_monitor_bridge import Runtime

    runtime = Runtime(
        tmp_path,
        guard=lambda: "epoch-1",
        adapters={"jolpica": lambda *a: [Event("one", NOW, {"race": "synthetic"})]},
    )
    runtime.api.authorize = lambda *a: "epoch-1"
    runtime.api.clock = lambda: NOW
    definition = {
        "source": "jolpica",
        "event": "results",
        "filters": {"season": 2025, "round": 1},
        "expires": NOW + 600,
        "outputs": ["session", "activate"],
        "session_scope": "desktop",
        "request_id": "synthetic-owner-turn",
        "budget": 3,
        "ttl": 300,
    }
    mid = runtime.api.execute("create", definition)["id"]
    try:
        assert runtime.pass_once(now=NOW + 1, quiet=False) == 1
        assert runtime.host.engine.engine.session.input.audio is None
        assert len(runtime.host.view("owner")["events"]) == 1
        assert runtime.api.execute("status", {"id": mid})["counts"]["sent"] == 1
        assert runtime.pass_once(now=NOW + 2, quiet=False) == 0
    finally:
        assert runtime.close()


@pytest.mark.asyncio
async def test_cancelled_sdk_start_drains_owned_candidate(monkeypatch):
    import asyncio

    import jarvis_rt

    closed = []

    class WaitingSession:
        def __init__(self, **kwargs):
            pass

        async def start(self, **kwargs):
            await asyncio.Event().wait()

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(jarvis_rt, "AgentSession", WaitingSession)
    engine = SDKEngine()
    task = asyncio.create_task(engine.open("synthetic", []))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed and engine.session is None


def test_idle_pass_closes_expired_agent_without_new_batches(tmp_path, policy):
    from event_monitor_bridge import Runtime

    e = Engine()
    closed = []
    e.close = lambda: closed.append(True)
    runtime = Runtime(
        tmp_path, guard=lambda: "epoch", engine=e, adapters={"jolpica": lambda *a: []}
    )
    clock = [NOW]
    runtime.host.clock = lambda: clock[0]
    assert runtime.host.start("request", "owner", "desktop")
    clock[0] = NOW + 301
    runtime.pass_once(now=NOW + 301, quiet=True)
    assert closed and runtime.host.active is None
    runtime.close()


def test_host_shutdown_during_collection_never_activates(tmp_path, policy):
    from concurrent.futures import ThreadPoolExecutor

    from event_monitor_bridge import Runtime

    entered = threading.Event()
    release = threading.Event()
    e = Engine()

    def feed(*a):
        entered.set()
        assert release.wait(2)
        return [Event("one", NOW, {})]

    runtime = Runtime(
        tmp_path, guard=lambda: "epoch", engine=e, adapters={"jolpica": feed}
    )
    runtime.store.create(
        owner="owner",
        session_scope="desktop",
        source="jolpica",
        event="results",
        filters={"season": 2025, "round": 1},
        expires=NOW + 600,
        outputs=["session", "activate"],
        now=NOW,
    )
    with ThreadPoolExecutor(1) as pool:
        task = pool.submit(runtime.pass_once, now=NOW + 1, quiet=False)
        assert entered.wait(2)
        runtime.close()
        release.set()
        assert task.result(3) == 0
    assert not e.started and not e.events


def test_start_key_is_durable_and_cannot_extend_lifetime(tmp_path):
    clock = [NOW]
    first = EventHost(
        tmp_path / "host.sqlite",
        guard=lambda: "epoch",
        engine=Engine(),
        clock=lambda: clock[0],
    )
    identity = first.start("stable-key", "owner", "desktop").identity
    first.close()
    second = EventHost(
        tmp_path / "host.sqlite",
        guard=lambda: "epoch",
        engine=Engine(),
        clock=lambda: clock[0],
    )
    assert second.start("stable-key", "owner", "desktop").identity == identity
    clock[0] = NOW + 301
    assert second.start("stable-key", "owner", "desktop") is None
    assert second.start("fresh-key", "owner", "desktop").identity != identity
    second.close()


def test_shutdown_after_final_worker_check_never_starts(tmp_path, policy):
    from event_monitor_bridge import Runtime

    e = Engine()
    r = Runtime(
        tmp_path,
        guard=lambda: "epoch",
        engine=e,
        adapters={"jolpica": lambda *a: [Event("one", NOW, {})]},
    )
    r.store.create(
        owner="owner",
        session_scope="desktop",
        source="jolpica",
        event="results",
        filters={"season": 2025, "round": 1},
        expires=NOW + 600,
        outputs=["session", "activate"],
        now=NOW,
    )
    original = r.host.dispatch

    def race(*a, **k):
        r.close()
        return original(*a, **k)

    r.host.dispatch = race
    assert r.pass_once(now=NOW, quiet=False) == 0
    assert not e.started and not e.events


def test_monitor_stop_closes_active_text_generation(tmp_path, policy):
    from event_monitor_bridge import Runtime

    e = Engine()
    closed = []
    e.close = lambda: closed.append(True)
    r = Runtime(
        tmp_path,
        guard=lambda: "epoch",
        engine=e,
        adapters={"jolpica": lambda *a: [Event("one", NOW, {})]},
    )
    r.api.authorize = lambda *a: "epoch"
    r.api.clock = lambda: NOW
    mid = r.store.create(
        owner="owner",
        session_scope="desktop",
        source="jolpica",
        event="results",
        filters={"season": 2025, "round": 1},
        expires=NOW + 600,
        outputs=["session", "activate"],
        now=NOW,
    )
    r.host.clock = lambda: NOW
    assert r.pass_once(now=NOW, quiet=False) == 1
    assert r.host.active
    r.api.execute("stop", {"id": mid})
    assert closed and r.host.active is None
    r.close()


def test_twenty_fact_dispatch_json_is_complete(tmp_path, policy):
    import json

    s, _, h, e = make(tmp_path)
    rows = [Event(str(i), NOW, {"message": "x" * 1000}) for i in range(20)]
    Worker(s, {"jolpica": lambda *a: rows}).collect(NOW)
    assert h.dispatch(s, NOW, quiet=False) == 1
    data = json.loads(e.events[0]["detail"])
    assert len(data) == 20 and all(len(row["facts"]["message"]) == 1000 for row in data)
    h.close()


@pytest.mark.parametrize("stop_starter", [False, True])
def test_stop_monitor_in_shared_generation_removes_only_its_projection(
    tmp_path, policy, stop_starter
):
    from event_monitor_bridge import Runtime

    e = Engine()
    closed = []
    e.close = lambda: closed.append(True)
    e.replace = lambda messages: setattr(e, "events", list(messages))
    r = Runtime(
        tmp_path,
        guard=lambda: "epoch",
        engine=e,
        adapters={"jolpica": lambda *a: [Event("one", NOW, {"race": "synthetic"})]},
    )
    r.api.authorize = lambda *a: "epoch"
    r.api.clock = lambda: NOW
    r.host.clock = lambda: NOW
    mids = [
        r.store.create(
            owner="owner",
            session_scope="desktop",
            source="jolpica",
            event="results",
            filters={"season": 2025, "round": n},
            expires=NOW + 600,
            outputs=["session", "activate"],
            now=NOW,
        )
        for n in [1, 2]
    ]
    assert r.pass_once(now=NOW, quiet=False) == 2
    assert len(e.started) == 1 and len(e.events) == 2
    with r.store.db() as db:
        mapping = {
            row["monitor"]: row["id"]
            for row in db.execute("SELECT id,monitor FROM batches")
        }
    with r.host.db() as db:
        started_keys = {row[0] for row in db.execute("SELECT key FROM starts")}
    stopped = next(
        mid for mid in mids if (mapping[mid] in started_keys) == stop_starter
    )
    survivor = next(mid for mid in mids if mid != stopped)
    r.api.execute("stop", {"id": stopped})
    assert r.host.active and not closed
    assert [message["key"] for message in e.events] == [mapping[survivor]]
    assert r.store.status(survivor, "owner")["state"] == "active"
    assert (
        len(r.host.view("owner")["events"]) == 2
    )  # retain factual historical receipts
    r.host.close()
    r.host.start("fresh", "owner", "desktop")
    assert [message["key"] for message in e.events] == [mapping[survivor]]
    r.api.execute("stop", {"id": survivor})
    assert r.host.active is None
    r.close()


@pytest.mark.asyncio
async def test_installed_sdk_replaces_only_cancelled_projection():
    engine = SDKEngine()
    a = {"key": "A", "title": "Synthetic A", "detail": "{}"}
    b = {"key": "B", "title": "Synthetic B", "detail": "{}"}
    await engine.open("shared", [a, b])
    original = engine.session
    try:
        assert (
            sum(
                "Synthetic" in getattr(item, "text_content", "")
                for item in original.current_agent.chat_ctx.items
            )
            == 2
        )
        await engine.replace([a])
        assert engine.session is original
        text = " ".join(
            getattr(item, "text_content", "")
            for item in original.current_agent.chat_ctx.items
        )
        assert "Synthetic A" in text and "Synthetic B" not in text
        assert original.input.audio is None and original.output.audio is None
        assert original.current_agent.llm is None and not original.current_agent.tools
    finally:
        await engine.aclose()


def test_cancelled_projection_failure_drains_without_cancelling_other_definition(
    tmp_path, policy
):
    from event_monitor_bridge import Runtime

    e = Engine()
    closed = []
    e.close = lambda: closed.append(True)

    def failed(messages):
        raise RuntimeError("synthetic projection failure")

    e.replace = failed
    r = Runtime(
        tmp_path,
        guard=lambda: "epoch",
        engine=e,
        adapters={"jolpica": lambda *a: [Event("one", NOW, {"race": "synthetic"})]},
    )
    r.api.authorize = lambda *a: "epoch"
    r.api.clock = lambda: NOW
    r.host.clock = lambda: NOW
    mids = [
        r.store.create(
            owner="owner",
            session_scope="desktop",
            source="jolpica",
            event="results",
            filters={"season": 2025, "round": n},
            expires=NOW + 600,
            outputs=["session", "activate"],
            now=NOW,
        )
        for n in [1, 2]
    ]
    assert r.pass_once(now=NOW, quiet=False) == 2
    r.api.execute("stop", {"id": mids[0]})
    assert closed and r.host.active is None
    assert r.store.status(mids[1], "owner")["state"] == "active"
    assert (
        len(r.host.history("owner")) == 1 and len(r.host.view("owner")["events"]) == 2
    )
    r.close()


def test_shutdown_during_authority_callback_cannot_publish_stale_epoch(tmp_path):
    from event_monitor_bridge import Runtime

    called = [False]

    def source():
        if not called[0]:
            called[0] = True
            r.close()
        return "stale-epoch"

    r = Runtime(
        tmp_path, guard=source, engine=Engine(), adapters={"jolpica": lambda *a: []}
    )
    assert r.guard() is None
    assert r.host.start("late", "owner", "desktop") is None
