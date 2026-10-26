from types import SimpleNamespace

import power_actions as power
import pytest
from action_outcome import ActionOutcome


def test_confirmation_is_scoped_expiring_and_single_use():
    now = [100.0]
    store = power.ConfirmationStore(clock=lambda: now[0])
    ticket = store.issue("shutdown", "owner")
    assert not store.consume(ticket, "restart", "owner")
    assert not store.consume(ticket, "shutdown", "owner")
    ticket = store.issue("restart", "owner")
    assert store.consume(ticket, "restart", "owner")
    assert not store.consume(ticket, "restart", "owner")
    ticket = store.issue("shutdown", "owner")
    now[0] += 3.5
    assert not store.consume(ticket, "shutdown", "owner")
    ticket = store.issue("shutdown", "owner")
    assert not store.consume(ticket, "shutdown", "other")


def test_second_issue_replaces_prior_ticket():
    store = power.ConfirmationStore()
    first = store.issue("restart", "owner")
    second = store.issue("shutdown", "owner")
    assert not store.consume(first, "restart", "owner")
    assert store.consume(second, "shutdown", "owner")


@pytest.mark.parametrize("rc", [1, 124, 126, 127, -15])
def test_failure_never_claims_success_or_retries(rc):
    outcome = power.receipt("shutdown", rc)
    assert not outcome.verified
    assert not outcome.retry_safe
    assert outcome.state.value in {"failed", "dispatched_unverified"}
    assert "initiated" not in outcome.say.lower()


def test_successful_exit_is_only_request_not_effect():
    for action in ("shutdown", "reboot", "suspend", "logout"):
        outcome = power.receipt(action, 0)
        assert outcome.state.value == "dispatched_unverified"
        assert not outcome.verified
        with pytest.raises(ValueError):
            ActionOutcome(
                "power." + action,
                "verified",
                "started",
                "matched",
                "owner_desktop",
                evidence={"launcher_exit_code": 0},
            )


@pytest.mark.parametrize("active", [False, None])
def test_lock_unverified_without_positive_readback(active):
    outcome = power.receipt("lock", 0, active)
    assert not outcome.verified
    assert outcome.say != "Locked."


def test_lock_verified_only_with_positive_readback_and_clean_dispatch():
    outcome = power.receipt("lock", 0, True)
    assert (
        outcome.verified
        and outcome.say == "Locked; KDE reports the screen lock active."
    )
    assert not power.receipt("lock", 1, True).verified
    with pytest.raises(ValueError):
        ActionOutcome(
            "power.lock",
            "verified",
            "not_started",
            "matched",
            "owner_desktop",
            evidence={"launcher_exit_code": 0, "lock_active": True},
        )
    with pytest.raises(ValueError):
        ActionOutcome(
            "power.lock",
            "verified",
            "started",
            "matched",
            "owner_desktop",
            evidence={"launcher_exit_code": 1, "lock_active": True},
        )


@pytest.mark.parametrize(
    "text,want",
    [
        ("(true,)", True),
        ("(false,)", False),
        ("true", None),
        ("(true,) token", None),
        ("", None),
    ],
)
def test_strict_readback(text, want):
    assert power.parse_lock_active(text) is want


def test_sync_failure_never_reads_back_and_redacts_exception():
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stdout="", stderr="PRIVATE-SENTINEL")

    outcome = power.dispatch_sync("lock", run)
    assert len(calls) == 1 and not outcome.verified
    assert "PRIVATE" not in str(outcome.to_dict())

    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE-SENTINEL")

    outcome = power.dispatch_sync("shutdown", fail)
    assert outcome.dispatch.value == "unknown"
    assert "PRIVATE" not in str(outcome.to_dict())


@pytest.mark.asyncio
async def test_core_failure_burns_confirmation(monkeypatch):
    from system.core import SystemTools

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    tools = SystemTools()
    calls = []

    async def run(*argv, **kwargs):
        calls.append(argv)
        return 1, "", "PRIVATE-SENTINEL"

    monkeypatch.setattr("system.core.run_cmd", run)
    await SystemTools.confirm_power_action(tools, None, action="shutdown")
    result = await SystemTools.power_control(tools, None, action="shutdown")
    assert result["outcome"]["state"] == "failed"
    assert "PRIVATE" not in str(result)
    with pytest.raises(Exception, match="confirmation"):
        await SystemTools.power_control(tools, None, action="shutdown")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_core_lock_checks_same_bus(monkeypatch):
    from system.core import SystemTools

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    tools = SystemTools()
    calls = []

    async def run(*argv, **kwargs):
        calls.append(argv)
        return (0, "(true,)", "") if argv[0] == "gdbus" else (0, "", "")

    monkeypatch.setattr("system.core.run_cmd", run)
    result = await SystemTools.power_control(tools, None, action="lock")
    assert result["outcome"]["state"] == "verified"
    assert calls[1] == tuple(power.LOCK_READ_ARGV)


def test_hud_two_phase_and_replay(monkeypatch):
    import taskbar

    monkeypatch.setattr(taskbar, "_power_confirmations", power.ConfirmationStore())
    calls = []

    def dispatch(action):
        calls.append(action)
        return power.receipt("reboot", 0)

    monkeypatch.setattr(taskbar, "dispatch_power", dispatch)
    assert (
        taskbar.handle_power({"action": "restart"}, confirmation_scope="owner")[0]
        == 428
    )
    assert taskbar.handle_power({"action": "restart", "phase": "request"})[0] == 403
    code, issued = taskbar.handle_power(
        {"action": "restart", "phase": "request"}, confirmation_scope="owner"
    )
    assert code == 200 and issued["confirmation_required"] and not calls
    confirm = {
        "action": "restart",
        "phase": "confirm",
        "confirmation": issued["confirmation"],
    }
    assert (
        taskbar.handle_power(confirm, confirmation_scope="owner")[1]["outcome"]["state"]
        == "dispatched_unverified"
    )
    assert calls == ["restart"]
    assert taskbar.handle_power(confirm, confirmation_scope="owner")[0] == 403
    assert calls == ["restart"]


def test_hud_different_action_or_failed_command_consumes_ticket(monkeypatch):
    import taskbar

    monkeypatch.setattr(taskbar, "_power_confirmations", power.ConfirmationStore())
    monkeypatch.setattr(
        taskbar, "dispatch_power", lambda action: power.receipt("shutdown", 1)
    )
    _, issued = taskbar.handle_power(
        {"action": "shutdown", "phase": "request"}, confirmation_scope="owner"
    )
    bad = {
        "action": "restart",
        "phase": "confirm",
        "confirmation": issued["confirmation"],
    }
    assert taskbar.handle_power(bad, confirmation_scope="owner")[0] == 403
    _, issued = taskbar.handle_power(
        {"action": "shutdown", "phase": "request"}, confirmation_scope="owner"
    )
    body = {
        "action": "shutdown",
        "phase": "confirm",
        "confirmation": issued["confirmation"],
    }
    assert taskbar.handle_power(body, confirmation_scope="owner")[1]["ok"] is False
    assert taskbar.handle_power(body, confirmation_scope="owner")[0] == 403


def test_actual_bridge_confirmation_route_and_guest_gate(monkeypatch):
    import bridge
    import taskbar
    import watch_mode
    from test_taskbar import _post

    monkeypatch.setattr(watch_mode, "guest_required", lambda: False)
    calls = []
    monkeypatch.setattr(taskbar, "_power_confirmations", power.ConfirmationStore())
    monkeypatch.setattr(
        taskbar,
        "dispatch_power",
        lambda action: calls.append(action) or power.receipt(action, 0),
    )
    server, _ = bridge._run_in_thread(token="test-only-owner")
    try:
        body = {"action": "shutdown", "phase": "request"}
        assert _post(server, "/power", body)[0] == 401
        assert (
            _post(server, "/power", {"action": "shutdown"}, token="test-only-owner")[0]
            == 428
        )
        code, issued = _post(server, "/power", body, token="test-only-owner")
        assert code == 200 and not calls
        body = {
            "action": "shutdown",
            "phase": "confirm",
            "confirmation": issued["confirmation"],
        }
        monkeypatch.setattr(watch_mode, "guest_required", lambda: True)
        assert _post(server, "/power", body, token="test-only-owner")[0] == 403
        assert not calls
        monkeypatch.setattr(watch_mode, "guest_required", lambda: False)
        assert _post(server, "/power", body, token="test-only-owner")[0] == 403
        _, issued = _post(
            server,
            "/power",
            {"action": "shutdown", "phase": "request"},
            token="test-only-owner",
        )
        body["confirmation"] = issued["confirmation"]
        code, result = _post(server, "/power", body, token="test-only-owner")
        assert code == 200 and result["outcome"]["state"] == "dispatched_unverified"
        assert calls == ["shutdown"]
        assert _post(server, "/power", body, token="test-only-owner")[0] == 403
    finally:
        server.shutdown()
        server.server_close()


def test_concurrent_confirm_only_one_consumes():
    from concurrent.futures import ThreadPoolExecutor

    store = power.ConfirmationStore()
    ticket = store.issue("shutdown", "owner")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: store.consume(ticket, "shutdown", "owner"), range(2))
        )
    assert results.count(True) == 1


def test_dispatch_rechecks_owner_and_never_calls_process_in_guest(monkeypatch):
    import taskbar
    import watch_mode

    monkeypatch.setattr(watch_mode, "guest_required", lambda: True)
    monkeypatch.setattr(
        taskbar, "_default_run", lambda *a, **k: pytest.fail("must not dispatch")
    )
    outcome = taskbar.dispatch_power("shutdown")
    assert outcome.state.value == "blocked" and outcome.dispatch.value == "not_started"
