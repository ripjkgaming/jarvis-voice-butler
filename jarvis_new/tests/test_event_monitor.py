import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from event_monitor.core import Event, Session, Store, Worker


@pytest.fixture(autouse=True)
def isolated_proactive_context(monkeypatch):
    from proactive import engine

    monkeypatch.setattr(engine, "load_state", lambda: {})
    monkeypatch.setattr(engine, "live_context", lambda *a: engine.Context(idle_s=30))


NOW = 1800000000.0


def setup(tmp_path, output="session", budget=3):
    s = Store(tmp_path / "monitors.sqlite")
    mid = s.create(
        owner="owner",
        session_scope="desktop",
        source="jolpica",
        event="results",
        filters={"season": 2025, "round": 1},
        expires=NOW + 600,
        outputs=[output],
        now=NOW,
        budget=budget,
    )
    return s, mid


def sample(key="one", ts=NOW):
    return Event(key, ts, {"race": "Australian Grand Prix", "position": "1"})


def worker(s, rows, triage=None):
    return Worker(s, {"jolpica": lambda m, cursor, now: rows}, triage=triage)


def test_full_replay_restart_and_idempotent_dispatch(tmp_path):
    s, mid = setup(tmp_path)
    w = worker(s, [sample(), sample(), sample("old", NOW - 1000)])
    w.collect(NOW)
    s = Store(tmp_path / "monitors.sqlite")
    seen = []

    def sink(key, session, signal):
        return seen.append((key, signal)) or True

    def session():
        return Session("owner", "desktop", "session-a", True, False, False)

    assert s.dispatch(NOW + 2, session, sink, quiet=False) == 1
    worker(s, [sample()]).collect(NOW + 3)
    assert s.dispatch(NOW + 4, session, sink, quiet=False) == 0
    assert len(seen) == 1
    assert s.status(mid, "owner")["counts"]["sent"] == 1


@pytest.mark.parametrize(
    "state",
    [
        Session("guest", "desktop", "x", False, False, False),
        Session("owner", "phone", "x", True, False, False),
        Session("owner", "desktop", "x", True, True, False),
        Session("owner", "desktop", "x", True, False, True),
        None,
    ],
)
def test_owner_scope_call_watch_gate(tmp_path, state):
    s, _ = setup(tmp_path)
    worker(s, [sample()]).collect(NOW)
    assert (
        s.dispatch(
            NOW + 2, lambda: state, lambda *a: pytest.fail("must not send"), quiet=False
        )
        == 0
    )


def test_quiet_expiry_and_cancel(tmp_path):
    s, mid = setup(tmp_path)
    worker(s, [sample()]).collect(NOW)

    def session():
        return Session("owner", "desktop", "a", True, False, False)

    assert s.dispatch(NOW + 2, session, lambda *a: pytest.fail(), quiet=True) == 0
    assert s.dispatch(NOW + 700, session, lambda *a: pytest.fail(), quiet=False) == 0
    assert s.status(mid, "owner")["state"] == "expired"
    s, mid = setup(tmp_path / "other")
    worker(s, [sample()]).collect(NOW)
    s.stop(mid, "owner", NOW + 1)
    assert s.dispatch(NOW + 2, session, lambda *a: pytest.fail(), quiet=False) == 0


def test_coalesce_budget_races_and_out_of_order(tmp_path):
    s, mid = setup(tmp_path, budget=1)
    w = worker(s, [sample("two", NOW - 1), sample("one", NOW - 2)])
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(w.collect, [NOW, NOW]))
    seen = []

    def sess():
        return Session("owner", "desktop", "a", True, False, False)

    with ThreadPoolExecutor(2) as pool:
        sent = list(
            pool.map(
                lambda _: s.dispatch(
                    NOW + 2, sess, lambda *a: seen.append(a) or True, quiet=False
                ),
                range(2),
            )
        )
    assert sum(sent) == 1 and len(seen) == 1
    assert s.status(mid, "owner")["counts"]["sent"] == 2
    worker(s, [sample("three")]).collect(NOW + 3)
    assert s.dispatch(NOW + 4, sess, lambda *a: pytest.fail(), quiet=False) == 0


@pytest.mark.parametrize(
    "result", ["bad json", '{"ids":["invented"]}', '{"ids":["one"],"call":true}']
)
def test_invalid_model_falls_back_to_deterministic(tmp_path, result):
    s, _ = setup(tmp_path)
    worker(s, [sample()], lambda rows: result).collect(NOW)
    assert s.pending_count() == 1


def test_quota_failure_does_not_stop_collection(tmp_path):
    s, _ = setup(tmp_path)

    def fail(rows):
        raise TimeoutError()

    w = worker(s, [sample()], fail)
    w.collect(NOW)
    assert s.pending_count() == 1
    assert s.reserve_model(NOW, daily=1, minute=20)
    assert not s.reserve_model(NOW + 1, daily=1, minute=20)
    assert not Store(tmp_path / "monitors.sqlite").reserve_model(
        NOW + 2, daily=1, minute=20
    )


def test_reconnect_no_duplicate_model_and_collection_errors(tmp_path):
    s, _ = setup(tmp_path)
    calls = []
    w = worker(
        s, [sample()], lambda rows: calls.append(rows) or json.dumps({"ids": ["one"]})
    )
    w.collect(NOW)
    w.collect(NOW + 1)
    assert len(calls) == 1
    Worker(s, {"jolpica": lambda *a: (_ for _ in ()).throw(OSError())}).collect(NOW + 2)
    assert s.pending_count() == 1


def test_explicit_outputs_and_scope_required(tmp_path):
    s, _ = setup(tmp_path)
    with pytest.raises(ValueError):
        s.create(
            owner="owner",
            session_scope="desktop",
            source="openf1",
            event="race_control",
            filters={"session_key": "latest"},
            expires=NOW + 10,
            outputs=["call"],
            now=NOW,
        )
    with pytest.raises(PermissionError):
        s.list("guest", authorized=False)


def test_cancel_before_inflight_collection_commit(tmp_path):
    s, mid = setup(tmp_path)

    def adapter(*a):
        s.stop(mid, "owner", NOW + 1)
        return [sample()]

    Worker(s, {"jolpica": adapter}).collect(NOW)
    assert s.pending_count() == 0


def test_durable_pending_activation_default_off_and_synthetic_start(tmp_path):
    s = Store(tmp_path / "a.sqlite")
    s.create(
        owner="owner",
        session_scope="desktop",
        source="jolpica",
        event="results",
        filters={"season": 2025, "round": 1},
        expires=NOW + 600,
        outputs=["session", "activate"],
        now=NOW,
    )
    worker(s, [sample()]).collect(NOW)
    active = [None]
    starts, delivered = [], []

    def start(key, owner, scope):
        starts.append(key)
        active[0] = Session(owner, scope, "new-session", True, False, False)
        return active[0]

    assert (
        s.dispatch(
            NOW + 1,
            lambda: active[0],
            lambda *a: False,
            quiet=False,
            start_session=start,
        )
        == 0
    )
    with s.db() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM activations WHERE state='pending'"
            ).fetchone()[0]
            == 1
        )
    assert starts == []
    restarted = Store(tmp_path / "a.sqlite")
    assert (
        restarted.dispatch(
            NOW + 2,
            lambda: active[0],
            lambda *a: delivered.append(a) or True,
            quiet=False,
            start_session=start,
            live_effects=True,
            activation_guard=lambda *a: True,
        )
        == 1
    )
    assert len(starts) == len(delivered) == 1
    assert (
        restarted.dispatch(
            NOW + 3, lambda: active[0], lambda *a: pytest.fail(), quiet=False
        )
        == 0
    )


def test_delivery_retry_membership_survives_new_arrivals(tmp_path):
    s, _ = setup(tmp_path)
    worker(s, [sample()]).collect(NOW)

    def sess():
        return Session("owner", "desktop", "s", True, False, False)

    keys = []
    s.dispatch(NOW + 1, sess, lambda key, *a: keys.append(key) or False, quiet=False)
    worker(s, [sample("two", NOW + 61)]).collect(NOW + 61)
    s.dispatch(NOW + 62, sess, lambda key, *a: keys.append(key) or True, quiet=False)
    assert keys[0] == keys[1]


def test_registry_cancel_and_expiry_recovery(tmp_path):
    import jobs

    registry = jobs.Registry(tmp_path / "registry", clock=lambda: NOW)
    s, mid = setup(tmp_path / "store")
    w = Worker(s, {"jolpica": lambda *a: [sample()]}, registry=registry)
    w.collect(NOW)
    job = registry.by_native("event-monitor", mid)
    registry.request_cancel(job["id"])
    w.collect(NOW + 61)
    assert s.status(mid, "owner")["state"] == "cancelled"
    assert registry.get(job["id"])["lifecycle"] == "settled"


def test_adapter_bounds_and_paid_live_refusal(tmp_path):
    from event_monitor.adapters import F1Adapters, public_json

    with pytest.raises(ValueError):
        public_json("http://127.0.0.1/secret")
    calls = []
    a = F1Adapters(lambda url: calls.append(url) or [])
    with pytest.raises(ValueError):
        a.openf1(
            {
                "event": "race_control",
                "filters": {"session_key": 9158, "start": NOW - 60, "end": NOW},
            },
            0,
            NOW,
        )
    assert calls == []


def test_model_uses_only_free_router_existing_client(tmp_path, monkeypatch):
    from event_monitor.triage import FreeTriage

    monkeypatch.setenv("JARVIS_MONITOR_TRIAGE", "1")

    class Client:
        @staticmethod
        def api_key():
            return "synthetic-never-sent"

        @staticmethod
        def _one_model(prompt, system, model, key, timeout, opener):
            assert model == "openrouter/free" and timeout == 8
            assert "synthetic-never-sent" not in prompt
            return '{"ids":["one"]}', None

    s, _ = setup(tmp_path)
    t = FreeTriage(s, client=Client, clock=lambda: NOW)
    worker(s, [sample()], t).collect(NOW)
    assert s.pending_count() == 1


def test_owner_api_explicit_definition_and_foreign_scope(tmp_path):
    from event_monitor.api import MonitorAPI

    s = Store(tmp_path / "api.sqlite")
    api = MonitorAPI(s, authorize=lambda *a: "ticket", clock=lambda: NOW)
    body = {
        "source": "jolpica",
        "event": "schedule",
        "filters": {"season": 2025, "round": 1},
        "expires": NOW + 600,
        "outputs": ["status"],
        "session_scope": "desktop",
        "request_id": "synthetic-1",
        "budget": 2,
        "ttl": 300,
    }
    mid = api.execute("create", body)["id"]
    assert api.execute("create", body)["id"] == mid
    assert len(api.execute("list")) == 1
    with pytest.raises(ValueError):
        api.execute("create", {"source": "jolpica"})
    foreign = MonitorAPI(s, owner="guest", authorize=lambda *a: "ticket")
    with pytest.raises(ValueError):
        foreign.execute("status", {"id": mid})
    api.execute("stop", {"id": mid})
    assert api.execute("status", {"id": mid})["state"] == "cancelled"


@pytest.mark.asyncio
async def test_voice_requires_exact_current_definition(tmp_path, monkeypatch):
    from event_monitor.api import MonitorAPI
    from event_monitor.voice import BuildMonitorTools, MonitorTools

    s = Store(tmp_path / "voice.sqlite")
    api = MonitorAPI(s, authorize=lambda *a: "ticket", clock=lambda: NOW)
    tools = MonitorTools(api)
    body = {
        "source": "jolpica",
        "event": "schedule",
        "filters": {"season": 2025, "round": 1},
        "expires": NOW + 600,
        "outputs": ["status"],
        "session_scope": "desktop",
        "request_id": "synthetic-voice",
        "budget": 2,
        "ttl": 300,
    }
    monkeypatch.setattr(BuildMonitorTools, "_input", lambda context: ("turn", "hello"))
    assert (await tools.monitor_create(None, json.dumps(body)))[
        "status"
    ] == "needs_explicit_definition"
    monkeypatch.setattr(
        BuildMonitorTools,
        "_input",
        lambda context: ("turn", "monitor " + json.dumps(body)),
    )
    result = await tools.monitor_create(None, json.dumps(body))
    assert result["status"] == "created"
    assert (await tools.monitor_stop(None, result["id"]))[
        "status"
    ] == "needs_explicit_stop"
    monkeypatch.setattr(
        BuildMonitorTools,
        "_input",
        lambda context: ("turn", "stop monitor " + result["id"]),
    )
    assert (await tools.monitor_stop(None, result["id"]))["state"] == "cancelled"


def test_actual_adapter_fixture_collect_triage_dispatch(tmp_path):
    from event_monitor.adapters import F1Adapters

    fixture = {
        "MRData": {
            "RaceTable": {
                "Races": [
                    {
                        "season": "2025",
                        "round": "1",
                        "raceName": "Australian Grand Prix",
                        "date": "2025-03-16",
                        "Results": [
                            {
                                "position": "1",
                                "Driver": {"driverId": "norris"},
                                "status": "Finished",
                            }
                        ],
                    }
                ]
            }
        }
    }
    urls = []
    adapter = F1Adapters(lambda url: urls.append(url) or fixture)
    s, _ = setup(tmp_path)
    w = Worker(s, adapter.mapping(), triage=lambda rows: {"ids": [r.key for r in rows]})
    w.collect(NOW)
    w.collect(NOW + 61)
    seen = []
    assert (
        s.dispatch(
            NOW + 62,
            lambda: Session("owner", "desktop", "s", True, False, False),
            lambda *a: seen.append(a) or True,
            quiet=False,
        )
        == 1
    )
    assert len(seen) == 1 and "/2025/1/results/" in urls[0]


def test_openf1_historical_replay_is_labeled_and_scoped(tmp_path):
    from event_monitor.adapters import F1Adapters

    start, end = NOW - 7200, NOW - 7140
    import datetime

    def iso(t):
        return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()

    def fetch(url):
        if "/sessions?" in url:
            return [{"session_key": 9158, "date_end": iso(end)}]
        return [
            {
                "session_key": 9158,
                "date": iso(start + 1),
                "category": "Flag",
                "flag": "YELLOW",
                "message": "Yellow flag",
            }
        ]

    s = Store(tmp_path / "replay.sqlite")
    s.create(
        owner="owner",
        session_scope="desktop",
        source="openf1",
        event="race_control",
        filters={"session_key": 9158, "start": start, "end": end},
        expires=NOW + 600,
        outputs=["session"],
        now=NOW,
    )
    Worker(s, F1Adapters(fetch).mapping()).collect(NOW)
    assert s.pending_count() == 1
    with s.db() as db:
        assert (
            "historical-replay" in db.execute("SELECT facts FROM events").fetchone()[0]
        )


def test_session_revoked_between_policy_and_sink(tmp_path):
    s, _ = setup(tmp_path)
    worker(s, [sample()]).collect(NOW)
    seq = iter([Session("owner", "desktop", "s", True, False, False), None])
    assert (
        s.dispatch(NOW + 1, lambda: next(seq), lambda *a: pytest.fail(), quiet=False)
        == 0
    )


def test_model_minute_cap_and_next_day(tmp_path):
    s, _ = setup(tmp_path)
    for _ in range(20):
        assert s.reserve_model(NOW)
    assert not s.reserve_model(NOW)
    assert s.reserve_model(NOW + 61)
    assert s.reserve_model(NOW + 86400)


def test_dnd_blocks_activation_before_start(tmp_path, monkeypatch):
    from proactive import engine

    monkeypatch.setattr(
        engine, "live_context", lambda *a: engine.Context(dnd=True, idle_s=30)
    )
    s = Store(tmp_path / "dnd.sqlite")
    s.create(
        owner="owner",
        session_scope="desktop",
        source="jolpica",
        event="results",
        filters={"season": 2025, "round": 1},
        expires=NOW + 600,
        outputs=["session", "activate"],
        now=NOW,
    )
    worker(s, [sample()]).collect(NOW)
    assert (
        s.dispatch(
            NOW + 1,
            lambda: None,
            lambda *a: pytest.fail(),
            quiet=False,
            live_effects=True,
            start_session=lambda *a: pytest.fail(),
            activation_guard=lambda *a: True,
        )
        == 0
    )
