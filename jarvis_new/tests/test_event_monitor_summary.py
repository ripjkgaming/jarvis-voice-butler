import json

import pytest
from event_monitor.core import Event, Store, Worker
from event_monitor_summary import SummaryProcessor

NOW = 1800000000.0


class Client:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or {"task": "summarize", "evidence_ids": ["one"]}

    def api_key(self):
        return "synthetic-key"

    def _one_model(self, prompt, system, model, key, timeout, *args):
        self.calls.append((json.loads(prompt), model, timeout))
        return json.dumps(self.response), None


def make(tmp_path, client=None):
    store = Store(tmp_path / "monitors.sqlite")
    mid = store.create(
        owner="owner",
        session_scope="desktop",
        source="jolpica",
        event="results",
        filters={"season": 2025, "round": 1},
        expires=NOW + 600,
        outputs=["session", "activate", "desktop-alert"],
        now=NOW,
    )
    Worker(
        store,
        {
            "jolpica": lambda *a: [
                Event(
                    "one",
                    NOW,
                    {
                        "race": "Synthetic GP",
                        "results": [
                            {
                                "driver": "driver-a",
                                "position": "1",
                                "status": "Finished",
                            }
                        ],
                    },
                )
            ]
        },
    ).collect(NOW)
    processor = SummaryProcessor(
        tmp_path / "results.sqlite", store, client=client or Client(), clock=lambda: NOW
    )
    return store, mid, processor


def test_free_model_completes_grounded_result_and_no_replay(tmp_path):
    c = Client()
    _s, mid, p = make(tmp_path, c)
    assert p.process_pending(NOW) == 1
    result = p.recent("owner", mid)[0]
    assert result["state"] == "completed" and result["mode"] == "free-model"
    assert "driver-a" in result["summary"] and "1" in result["summary"]
    assert len(c.calls) == 1 and c.calls[0][1] == "openrouter/free"
    assert "owner" not in c.calls[0][0]
    assert p.process_pending(NOW + 1) == 0
    assert (
        SummaryProcessor(
            tmp_path / "results.sqlite", _s, client=c, clock=lambda: NOW
        ).process_pending(NOW + 2)
        == 0
    )
    assert p.recent("guest", mid) == []


@pytest.mark.parametrize(
    "response",
    [
        {"task": "send_whatsapp", "evidence_ids": ["one"]},
        {"task": "summarize", "evidence_ids": ["invented"]},
        {"task": "summarize", "evidence_ids": ["one"], "tool": "exec"},
    ],
)
def test_invalid_model_plan_is_honest_factual_fallback(tmp_path, response):
    _, mid, p = make(tmp_path, Client(response))
    p.process_pending(NOW)
    r = p.recent("owner", mid)[0]
    assert (
        r["mode"] == "deterministic-fallback"
        and r["model_status"] == "invalid-response"
    )
    assert "Synthetic GP" in r["summary"]


def test_quota_unavailable_keeps_usable_summary(tmp_path, monkeypatch):
    s, mid, p = make(tmp_path)
    monkeypatch.setattr(s, "reserve_model", lambda *a: False)
    p.process_pending(NOW)
    r = p.recent("owner", mid)[0]
    assert r["model_status"] == "quota-or-backoff" and r["summary"]


def test_full_twenty_large_events_remain_complete(tmp_path):
    s, mid, p = make(tmp_path)
    s.stop(mid, "owner", NOW)
    mid = s.create(
        owner="owner",
        session_scope="desktop",
        source="jolpica",
        event="results",
        filters={"season": 2025, "round": 1},
        expires=NOW + 600,
        outputs=["session", "activate"],
        now=NOW,
    )
    rows = [
        Event(str(i), NOW, {"race": "Synthetic GP", "message": "x" * 1000})
        for i in range(20)
    ]
    Worker(s, {"jolpica": lambda *a: rows}).collect(NOW)
    p.process_pending(NOW)
    r = p.recent("owner", mid)[0]
    assert (
        len(r["evidence"]) == 20 and len(r["evidence"][-1]["facts"]["message"]) == 1000
    )


@pytest.fixture
def policy(monkeypatch):
    from proactive import engine

    monkeypatch.setattr(engine, "load_state", dict)
    monkeypatch.setattr(engine, "live_context", lambda *a: engine.Context(idle_s=30))


def test_expiry_during_model_is_discarded(tmp_path):
    c = Client()
    _s, mid, p = make(tmp_path, c)
    clock = [NOW]
    p.clock = lambda: clock[0]
    original = c._one_model

    def delayed(*args):
        clock[0] = NOW + 601
        return original(*args)

    c._one_model = delayed
    p.process_pending()
    assert p.recent("owner", mid)[0]["state"] == "discarded"


def test_status_keeps_completed_results_and_triage_status(tmp_path, policy):
    from event_monitor_bridge import Runtime, endpoint
    from test_event_monitor_host import Engine

    r = Runtime(
        tmp_path,
        guard=lambda: "epoch",
        engine=Engine(),
        summary_client=Client(),
        adapters={"jolpica": lambda *a: [Event("one", NOW, {"race": "Synthetic GP"})]},
    )
    r.api.authorize = lambda *a: "epoch"
    r.api.clock = lambda: NOW
    definition = {
        "source": "jolpica",
        "event": "results",
        "filters": {"season": 2025, "round": 1},
        "expires": NOW + 600,
        "outputs": ["session", "activate"],
        "session_scope": "desktop",
        "request_id": "explicit-owner",
        "budget": 3,
        "ttl": 300,
    }
    mid = r.api.execute("create", definition)["id"]
    r.pass_once(now=NOW, quiet=False)
    result = r.api.execute("status", {"id": mid})
    assert (
        result["recent_results"][0]["summary"]
        == 'Synthetic GP: {"race":"Synthetic GP"}'
    )
    assert result["processing_status"]["triage"]["status"] == "disabled"
    code, view = endpoint(
        "GET",
        "/event-monitor/session",
        {},
        None,
        authorized=True,
        peer="127.0.0.1",
        api_factory=lambda: r.api,
        host_factory=lambda: r.host,
    )
    assert code == 200 and view["recent_results"][0]["state"] == "completed"
    r.close()


def test_alert_grant_and_policy_no_replay(tmp_path, policy):
    from event_monitor_host import EventHost
    from event_monitor_summary import deliver_alerts
    from test_event_monitor_host import Engine

    s, mid, p = make(tmp_path)
    p.process_pending(NOW)
    h = EventHost(
        tmp_path / "host.sqlite",
        guard=lambda: "epoch",
        engine=Engine(),
        processor=p,
        clock=lambda: NOW,
    )
    assert h.dispatch(s, NOW, quiet=False) == 1
    sent = []
    alert = lambda k, r: sent.append((k, r["summary"])) or "submitted"
    assert deliver_alerts(p, NOW, lambda: None, alert, quiet=False) == 0
    assert deliver_alerts(p, NOW, lambda: "epoch", alert, quiet=True) == 0
    assert deliver_alerts(p, NOW, lambda: "epoch", alert, quiet=False) == 1
    assert deliver_alerts(p, NOW, lambda: "epoch", alert, quiet=False) == 0
    assert len(sent) == 1 and p.recent("owner", mid)[0]["alert_state"] == "submitted"
    h.close()


def test_ordinary_session_grant_never_grants_visible_alert(tmp_path, policy):
    from event_monitor_host import EventHost
    from event_monitor_summary import deliver_alerts
    from test_event_monitor_host import Engine

    s, mid, p = make(tmp_path)
    with s.db() as db:
        db.execute(
            "UPDATE monitors SET outputs=? WHERE id=?",
            (json.dumps(["session", "activate"]), mid),
        )
    p.process_pending(NOW)
    h = EventHost(
        tmp_path / "host.sqlite",
        guard=lambda: "epoch",
        engine=Engine(),
        processor=p,
        clock=lambda: NOW,
    )
    assert h.dispatch(s, NOW, quiet=False) == 1
    assert (
        deliver_alerts(
            p,
            NOW,
            lambda: "epoch",
            lambda *a: pytest.fail("no alert grant"),
            quiet=False,
        )
        == 0
    )
    h.close()


def test_desktop_transport_is_not_seen_proof_and_redacts_error():
    import subprocess

    from event_monitor_summary import DesktopAlert, screen_unlocked

    calls = []

    def transport(argv, **kwargs):
        calls.append(argv)
        return type("R", (), {"returncode": 0})()

    alert = DesktopAlert(guard=lambda: "epoch", run=transport)
    result = {
        "mode": "deterministic-fallback",
        "model_status": "unavailable",
        "summary": "<Synthetic> update",
    }
    assert alert("id", result) == "submitted"
    assert calls[0][0] == "notify-send" and "&lt;Synthetic&gt;" in calls[0][-1]

    def failing(*a, **k):
        raise subprocess.TimeoutExpired("private-sentinel", 3)

    assert DesktopAlert(guard=lambda: "epoch", run=failing)("id", result) == "uncertain"
    assert not screen_unlocked(failing)
    assert screen_unlocked(
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "(false,)"})()
    )
    assert not screen_unlocked(
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "(true,)"})()
    )


def test_provider_and_resolver_errors_keep_fixed_labels(tmp_path):
    c = Client()

    def fail(*a, **k):
        raise RuntimeError("PRIVATE-PROVIDER-SENTINEL")

    c._one_model = fail
    _, mid, p = make(tmp_path, c)
    p.process_pending(NOW)
    result = p.recent("owner", mid)[0]
    assert result["model_status"] == "unavailable" and "SENTINEL" not in json.dumps(
        result
    )
    c.api_key = fail
    plan, status = p._choose([{"id": "one", "facts": {}}], NOW)
    assert status == "unavailable-existing-credential" and plan["task"] == "summarize"


def test_model_comparison_and_ignore_remain_bounded(tmp_path):
    from event_monitor_summary import factual_summary

    before = {
        "id": "before",
        "facts": {
            "race": "Synthetic GP",
            "results": [{"driver": "A", "position": "3"}],
        },
    }
    after = {
        "id": "after",
        "facts": {
            "race": "Synthetic GP",
            "results": [{"driver": "A", "position": "1"}],
        },
    }
    assert (
        factual_summary([before, after], "compare_results")
        == "Result snapshot comparison: A: position 3 to 1."
    )
    after["facts"]["race"] = "Different GP"
    with pytest.raises(ValueError):
        factual_summary([before, after], "compare_results")
    _, mid, p = make(tmp_path, Client({"task": "ignore", "evidence_ids": []}))
    p.process_pending(NOW)
    result = p.recent("owner", mid)[0]
    assert result["task"] == "ignore"
    with p.store.db() as db:
        assert (
            db.execute("SELECT state FROM batches WHERE monitor=?", (mid,)).fetchone()[
                0
            ]
            == "suppressed"
        )


def test_cancel_during_model_discards_result(tmp_path):
    c = Client()
    s, mid, p = make(tmp_path, c)
    original = c._one_model

    def stop(*args):
        s.stop(mid, "owner", NOW)
        return original(*args)

    c._one_model = stop
    p.process_pending(NOW)
    assert p.recent("owner", mid)[0]["state"] == "discarded"


def test_claimed_alert_recovers_uncertain_without_retry(tmp_path, policy):
    from event_monitor_host import EventHost
    from event_monitor_summary import deliver_alerts
    from test_event_monitor_host import Engine

    s, mid, p = make(tmp_path)
    p.process_pending(NOW)
    h = EventHost(
        tmp_path / "host.sqlite",
        guard=lambda: "epoch",
        engine=Engine(),
        processor=p,
        clock=lambda: NOW,
    )
    assert h.dispatch(s, NOW, quiet=False) == 1
    result = p.recent("owner", mid)[0]
    result["alert_state"] = "claimed"
    with p.db() as db:
        db.execute(
            "UPDATE results SET payload=? WHERE key=?",
            (json.dumps(result), result["key"]),
        )
    assert (
        deliver_alerts(
            p,
            NOW,
            lambda: "epoch",
            lambda *a: pytest.fail("uncertain replay"),
            quiet=False,
        )
        == 0
    )
    assert p.recent("owner", mid)[0]["alert_state"] == "uncertain"
    h.close()


def test_default_authority_requires_unlocked_known_desktop(tmp_path, monkeypatch):
    import event_monitor_bridge as bridge
    import phone_session
    import watch_mode

    monkeypatch.setattr(bridge, "local_owner_guard", lambda *a: None)
    monkeypatch.setattr(phone_session, "has_private_calls", lambda: False)
    monkeypatch.setattr(phone_session, "approval_ticket", lambda: None)
    monkeypatch.setattr(watch_mode, "guest_required", lambda: False)
    monkeypatch.setattr(bridge, "screen_unlocked", lambda: False)
    assert bridge.authority(tmp_path) is None
    monkeypatch.setattr(bridge, "screen_unlocked", lambda: True)
    assert bridge.authority(tmp_path) == "desktop-owner"
    monkeypatch.setattr(watch_mode, "guest_required", lambda: True)
    assert bridge.authority(tmp_path) is None
    monkeypatch.setattr(watch_mode, "guest_required", lambda: False)
    monkeypatch.setattr(phone_session, "has_private_calls", lambda: True)
    assert bridge.authority(tmp_path) is None


def test_explicit_desktop_alert_requires_desktop_session(tmp_path):
    s = Store(tmp_path / "monitors.sqlite")
    for scope, outputs in [
        ("desktop", ["desktop-alert"]),
        ("private-owner", ["session", "desktop-alert"]),
    ]:
        with pytest.raises(ValueError):
            s.create(
                owner="owner",
                session_scope=scope,
                source="jolpica",
                event="results",
                filters={"season": 2025, "round": 1},
                expires=NOW + 600,
                outputs=outputs,
                now=NOW,
            )


def test_shutdown_does_not_start_next_model_task(tmp_path):
    c = Client()
    s, _, p = make(tmp_path, c)
    s.create(
        owner="owner",
        session_scope="desktop",
        source="jolpica",
        event="results",
        filters={"season": 2025, "round": 2},
        expires=NOW + 600,
        outputs=["session", "activate"],
        now=NOW,
    )
    Worker(
        s, {"jolpica": lambda *a: [Event("one", NOW, {"race": "Synthetic GP"})]}
    ).collect(NOW)
    stopped = [False]
    p.cancelled = lambda: stopped[0]
    original = c._one_model

    def finish(*args):
        stopped[0] = True
        return original(*args)

    c._one_model = finish
    assert p.process_pending(NOW) == 1 and len(c.calls) == 1


def test_shutdown_waits_for_alert_transport_and_durable_receipt(tmp_path, policy):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from event_monitor_bridge import Runtime
    from event_monitor_summary import DesktopAlert
    from test_event_monitor_host import Engine

    entered = threading.Event()
    release = threading.Event()
    closing = threading.Event()
    returned = threading.Event()

    def transport(*a, **k):
        entered.set()
        assert release.wait(3)
        return type("R", (), {"returncode": 0})()

    r = Runtime(
        tmp_path,
        guard=lambda: "epoch",
        engine=Engine(),
        summary_client=Client(),
        adapters={"jolpica": lambda *a: [Event("one", NOW, {"race": "Synthetic"})]},
    )
    r.host.clock = lambda: NOW
    r.alert = DesktopAlert(guard=r.guard, run=transport)
    mid = r.store.create(
        owner="owner",
        session_scope="desktop",
        source="jolpica",
        event="results",
        filters={"season": 2025, "round": 1},
        expires=NOW + 600,
        outputs=["session", "activate", "desktop-alert"],
        now=NOW,
    )

    def close():
        closing.set()
        settled = r.close()
        returned.set()
        return settled, r.processor.recent("owner", mid)[0]["alert_state"]

    with ThreadPoolExecutor(2) as pool:
        work = pool.submit(r.pass_once, now=NOW, quiet=False)
        assert entered.wait(3)
        closing_work = pool.submit(close)
        assert closing.wait(3)
        assert r.stop.wait(1)
        try:
            assert not returned.wait(0.1), (
                "shutdown returned while alert transport was still pending"
            )
        finally:
            release.set()
        assert work.result(3) == 1
        assert closing_work.result(3) == (True, "submitted")
    assert r.stop.is_set()


def test_closed_runtime_never_begins_alert_transport(tmp_path, policy):
    from event_monitor_bridge import Runtime
    from event_monitor_summary import DesktopAlert, deliver_alerts
    from test_event_monitor_host import Engine

    r = Runtime(
        tmp_path,
        guard=lambda: "epoch",
        engine=Engine(),
        summary_client=Client(),
        alert=lambda *a: "held",
        adapters={"jolpica": lambda *a: [Event("one", NOW, {"race": "synthetic"})]},
    )
    r.host.clock = lambda: NOW
    r.store.create(
        owner="owner",
        session_scope="desktop",
        source="jolpica",
        event="results",
        filters={"season": 2025, "round": 1},
        expires=NOW + 600,
        outputs=["session", "activate", "desktop-alert"],
        now=NOW,
    )
    # Initially hold the visible effect, but accept the durable text result.
    assert r.pass_once(now=NOW, quiet=False) == 1
    result = r.processor.recent("owner")[0]
    # A policy-held transport has had no effect and can later retry if authorized.
    with r.processor.db() as db:
        result["alert_state"] = "held"
        db.execute(
            "UPDATE results SET payload=? WHERE key=?",
            (json.dumps(result), result["key"]),
        )
    r.close()
    alert = DesktopAlert(
        guard=r.guard,
        run=lambda *a: pytest.fail("late transport"),
        lifecycle_lock=r.host.lock,
    )
    assert (
        deliver_alerts(
            r.processor, NOW, r.guard, alert, quiet=False, lifecycle_lock=r.host.lock
        )
        == 0
    )


def test_alert_epoch_change_refuses_transport():
    from event_monitor_summary import DesktopAlert

    epochs = iter(["epoch-one", "epoch-two"])
    alert = DesktopAlert(
        guard=lambda: next(epochs), run=lambda *a: pytest.fail("epoch changed")
    )
    assert alert("key", {"mode": "free-model", "summary": "Synthetic"}) == "held"
