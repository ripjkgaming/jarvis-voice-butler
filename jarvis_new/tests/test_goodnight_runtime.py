from types import SimpleNamespace

from goodnight_runtime import Host, SessionLock


def test_lock_request_requires_getactive_readback(monkeypatch):
    requests = []

    def run(argv, **kwargs):
        requests.append(argv)
        return SimpleNamespace(
            returncode=0, stdout="b false\n" if argv[0] == "busctl" else ""
        )

    lock = SessionLock("2", run=run)
    monkeypatch.setattr(lock, "identity", lambda: "same")
    monkeypatch.setattr("goodnight_runtime.time.sleep", lambda n: None)
    assert lock.lock("same") is False
    assert requests[0] == ["loginctl", "lock-session", "2"]
    requests.clear()
    lock.run = lambda *a, **k: SimpleNamespace(returncode=0, stdout="b true\n")
    assert lock.lock("same") is True


def test_no_matching_session_never_requests_lock(monkeypatch):
    lock = SessionLock("2", run=lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(lock, "identity", lambda: None)
    assert lock.lock("other") is False


def test_missing_launch_fence_holds_and_does_not_start_on_import():
    host = Host(
        idle=SimpleNamespace(read=lambda: 10000),
        inventory=SimpleNamespace(clear=lambda: True),
        session_lock=SimpleNamespace(identity=lambda: "session", lock=lambda s: True),
        owner_allowed=lambda: True,
        conversation_clear=lambda: True,
    )
    assert host.controller.status()["state"] == "disarmed"
    assert host.probe().tasks_clear is None


def test_untrusted_ingress_cannot_arm_or_start_helper():
    host = Host(
        idle=SimpleNamespace(start=lambda: (_ for _ in ()).throw(AssertionError())),
        inventory=None,
        session_lock=SimpleNamespace(identity=lambda: "session"),
        owner_allowed=lambda: True,
        conversation_clear=lambda: True,
    )
    assert host.input("goodnight", direct_owner=False) is None


def test_nonfinite_conversation_idle_cannot_be_hidden_by_min():
    for bad in (float("nan"), float("inf"), float("-inf"), True):
        for idle, quiet in ((1000, bad), (bad, 1000)):
            host = Host(
                idle=SimpleNamespace(read=lambda value=idle: value),
                inventory=SimpleNamespace(clear=lambda: True),
                session_lock=SimpleNamespace(identity=lambda: "same"),
                owner_allowed=lambda: True,
                conversation_clear=lambda: True,
                conversation_idle=lambda value=quiet: value,
                launch_fence=lambda: None,
            )
            assert host.probe().idle_seconds is None
