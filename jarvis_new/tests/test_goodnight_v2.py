import pytest
from goodnight_v2 import Controller, Evidence, IntentGate


class Rig:
    def __init__(self):
        self.now = 0.0
        self.idle = 10000.0
        self.tasks = True
        self.conversation = True
        self.owner = True
        self.session = "boot/wayland/session"
        self.calls = []
        self.probes = 0
        self.race = None
        self.c = Controller(
            clock=lambda: self.now,
            probe=self.probe,
            lock=lambda s: self.calls.append(s) or True,
        )

    def probe(self):
        self.probes += 1
        if self.race:
            self.race(self)
        return Evidence(
            self.idle, self.tasks, self.conversation, self.owner, self.session
        )

    def arm(self):
        return self.c.arm(owner=True, direct_intent=True, session=self.session)

    def advance(self, seconds):
        for _ in range(int(seconds / 10)):
            self.now += 10
            self.c.tick()


def test_ten_minutes_from_request_and_idle():
    r = Rig()
    r.arm()
    r.advance(590)
    assert not r.calls
    r.advance(10)
    assert len(r.calls) == 1


def test_input_resets_continuous_timer():
    r = Rig()
    r.arm()
    r.advance(500)
    r.idle = 0
    r.advance(10)
    r.idle = 10000
    r.advance(590)
    assert not r.calls
    r.advance(10)
    assert r.calls


def test_active_task_ending_permits_lock():
    r = Rig()
    r.tasks = False
    r.arm()
    r.advance(800)
    assert not r.calls
    r.tasks = True
    r.advance(10)
    assert r.calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("idle", None),
        ("idle", float("nan")),
        ("tasks", None),
        ("conversation", None),
        ("owner", False),
    ],
)
def test_unknown_fails_closed(field, value):
    r = Rig()
    setattr(r, field, value)
    r.arm()
    r.advance(800)
    assert not r.calls


def test_call_and_speech_reset():
    r = Rig()
    r.arm()
    r.advance(590)
    r.conversation = False
    r.advance(10)
    r.conversation = True
    r.advance(590)
    assert not r.calls
    r.advance(10)
    assert r.calls


def test_new_task_during_final_check():
    r = Rig()
    r.arm()
    r.advance(590)
    n = r.probes
    r.race = lambda rig: setattr(rig, "tasks", False) if rig.probes == n + 2 else None
    r.advance(10)
    assert not r.calls


def test_cancel_during_final_check_and_late_callback():
    r = Rig()
    token = r.arm()["token"]
    r.advance(590)
    n = r.probes
    r.race = lambda rig: rig.c.cancel() if rig.probes == n + 2 else None
    r.advance(10)
    r.c.tick(token)
    assert not r.calls


def test_duplicate_arm_does_not_extend_expiry():
    r = Rig()
    first = r.arm()
    r.advance(100)
    second = r.arm()
    assert first["token"] == second["token"]
    assert first["expires_at"] == second["expires_at"]
    r.advance(590)
    assert not r.calls
    r.advance(10)
    assert r.calls


def test_expiry_no_late_lock_and_restart_disarmed():
    r = Rig()
    r.tasks = False
    token = r.arm()["token"]
    r.advance(3600)
    r.tasks = True
    r.c.tick(token)
    assert not r.calls
    assert r.c.status()["state"] == "expired"
    r.c = Controller(clock=lambda: r.now, probe=r.probe, lock=lambda s: True)
    assert r.c.status()["state"] == "disarmed"


def test_session_change_and_wake():
    r = Rig()
    r.arm()
    r.advance(500)
    r.now += 200
    r.c.tick()
    assert not r.calls
    r.advance(590)
    assert not r.calls
    r.session = "different-session"
    r.advance(10)
    assert not r.calls and r.c.status()["state"] == "cancelled"


def test_cancel_other_thread_while_final_probe_is_blocked():
    import threading

    r = Rig()
    r.arm()
    r.advance(590)
    started, released = threading.Event(), threading.Event()
    n = r.probes

    def race(rig):
        if rig.probes == n + 2:
            started.set()
            assert released.wait(2)

    r.race = race
    r.now += 10
    thread = threading.Thread(target=r.c.tick)
    thread.start()
    assert started.wait(2)
    r.c.cancel()
    released.set()
    thread.join(2)
    assert not thread.is_alive() and not r.calls


def test_progressing_authoritative_idle_does_not_add_extra_ten_minutes():
    r = Rig()
    r.idle = 0
    r.arm()
    for second in range(10, 601, 10):
        r.now = second
        r.idle = second
        r.c.tick()
    assert len(r.calls) == 1


def test_shared_final_fence_can_postpone_without_consuming_arm():
    r = Rig()
    r.c.finalizer = lambda s, commit: None
    r.arm()
    r.advance(600)
    assert r.c.status()["state"] == "armed"
    assert r.c.status()["token"]
    r.c.finalizer = None
    r.c.lock = lambda s: r.calls.append(s) or True
    r.advance(10)
    assert r.calls


@pytest.mark.parametrize(
    "text",
    [
        "say goodnight",
        '"goodnight"',
        "the email says goodnight",
        "I am going to sleep",
        "goodnight; shutdown",
        "goodnight\n",
    ],
)
def test_quoted_or_other_intent_does_not_arm(text):
    r = Rig()
    gate = IntentGate(r.c)
    assert not gate.input(text, owner=True, direct=True, session=r.session)
    assert r.c.status()["state"] == "disarmed"


def test_owner_only_direct_goodnight_and_cancel_status():
    r = Rig()
    gate = IntentGate(r.c)
    assert not gate.input(
        "goodnight jarvis", owner=False, direct=True, session=r.session
    )
    assert not gate.input("goodnight", owner=True, direct=False, session=r.session)
    assert gate.input("Goodnight, Jarvis!", owner=True, direct=True, session=r.session)
    assert gate.input("goodnight status", owner=True, direct=True, session=r.session)
    assert gate.input("cancel goodnight", owner=True, direct=True, session=r.session)
    assert r.c.status()["state"] == "cancelled"
