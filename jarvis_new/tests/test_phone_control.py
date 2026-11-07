"""phone_control: /phone/v1/control actions, step-up, readbacks, guest exemption."""

import subprocess
import sys

import pytest

sys.path.insert(0, "src")

import phone_control as pc
import phone_session
from phone_device import Principal


class Fake:
    """Records every side effect; laptop state is plain attributes."""

    def __init__(self):
        self.guest = False
        self.locked = False
        self.lock_readable = True
        self.screens_on = True
        self.muted = False
        self.argv: list[list[str]] = []
        self.tools: list[tuple[str, dict, bool]] = []
        self.logs: list[tuple[str, str]] = []
        self.calls: list[str] = []
        self.fail_argv = False

    # subprocess.run shape
    def run(self, argv, **kw):
        self.argv.append(list(argv))
        if argv[0] == "gdbus":
            if not self.lock_readable:
                return subprocess.CompletedProcess(argv, 1, "", "no bus")
            return subprocess.CompletedProcess(
                argv, 0, "(true,)" if self.locked else "(false,)", ""
            )
        if self.fail_argv:
            return subprocess.CompletedProcess(argv, 1, "", "boom")
        if argv[:2] == ["loginctl", "lock-sessions"]:
            self.locked = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    def run_phone_tool(self, tool, args):
        self.tools.append((tool, args, phone_session.private()))
        if tool == "unlock":
            self.locked = False
            return {"ok": True}
        if tool == "screen_off":
            self.screens_on = False
            return {"ok": True}
        if tool == "screens_restore":
            self.screens_on = True
            return {"ok": True}
        if tool == "screens_state":
            return {
                "ok": True,
                "outputs": [{"name": "eDP-1", "enabled": self.screens_on}],
            }
        return {"ok": False, "error": "unknown"}

    def wake_rpc(self, payload):
        if "mute" in payload:
            self.muted = payload["mute"]
        return {"ok": True, "muted": self.muted}

    def enter_guest(self):
        self.calls.append("enter_guest")
        self.guest = True

    def request_stop(self):
        self.calls.append("request_stop")

    def restore_owner(self):
        self.calls.append("restore_owner")
        self.guest = False

    def deps(self, **over):
        base = {
            "run": self.run,
            "run_phone_tool": self.run_phone_tool,
            "wake_rpc": self.wake_rpc,
            "laptop_power": lambda: {"battery": 87, "status": "Charging", "ac": True},
            "voice": lambda: {
                "call_live": False,
                "voice_owner": None,
                "agent_state": None,
                "waking": False,
            },
            "guest_required": lambda: self.guest,
            "enter_guest": self.enter_guest,
            "request_stop": self.request_stop,
            "restore_owner": self.restore_owner,
            "presence": lambda: {"status": "present"},
            "log": lambda cat, detail: self.logs.append((cat, detail)),
            "sleep": lambda s: None,
            "host": "ripjk",
            "dbus_ok": lambda: True,
        }
        base.update(over)
        return pc.Deps(**base)


DEVICE = Principal(kind="device", gate="open", device={"id": "jl-0000aaaa"})


@pytest.fixture
def fake():
    return Fake()


@pytest.fixture
def ctl(fake):
    return pc.PhoneControl(fake.deps(), presence_enabled=False)


def control(ctl, action, stepup=lambda p, a, x: False, principal=DEVICE, **extra):
    return ctl.control(principal, {"action": action, **extra}, stepup)


ALLOW = lambda p, a, x: x == {"sig": a}  # noqa: E731


# --- argv per action ----------------------------------------------------------------


def test_lock_uses_lock_sessions_and_verifies(ctl, fake):
    status, body = control(ctl, "lock")
    assert status == 200 and body["ok"]
    assert body["status"] == "verified" and body["readback"] == {"locked": True}
    assert ["loginctl", "lock-sessions"] in fake.argv
    assert body["state"]["locked"] is True


def test_sleep_restart_shutdown_argv(ctl, fake):
    assert control(ctl, "sleep")[1]["status"] == "dispatched_unverified"
    control(ctl, "restart", ALLOW, assertion={"sig": "restart"})
    control(ctl, "shutdown", ALLOW, assertion={"sig": "shutdown"})
    assert ["systemctl", "suspend"] in fake.argv
    assert ["systemctl", "reboot"] in fake.argv
    assert ["systemctl", "poweroff"] in fake.argv


def test_screens_toggle(ctl, fake):
    _, body = control(ctl, "screens_off")
    assert body["status"] == "verified" and body["readback"] == {"screens": "off"}
    _status, body = control(ctl, "screens_on")
    assert body["status"] == "verified" and body["readback"] == {"screens": "on"}
    assert [t[0] for t in fake.tools if t[0] != "screens_state"] == [
        "screen_off",
        "screens_restore",
    ]


def test_mic_mute_and_unmute(ctl, fake):
    _, body = control(ctl, "mic_mute")
    assert body["status"] == "verified" and body["readback"] == {"mic_muted": True}
    _status, body = control(ctl, "mic_unmute")
    assert body["readback"] == {"mic_muted": False}


def test_mic_unavailable_fails(fake):
    ctl = pc.PhoneControl(fake.deps(wake_rpc=lambda p: None), presence_enabled=False)
    _status, body = control(ctl, "mic_mute")
    assert not body["ok"] and body["status"] == "failed"


def test_guest_on_has_no_camera_and_verifies(ctl, fake):
    _status, body = control(ctl, "guest_on")
    assert body["status"] == "verified" and body["readback"] == {"guest": True}
    assert fake.calls == ["enter_guest"]


def test_guest_off_stops_then_restores(ctl, fake):
    fake.guest = True
    _status, body = control(ctl, "guest_off", ALLOW, assertion={"sig": "guest_off"})
    assert body["status"] == "verified" and body["readback"] == {"guest": False}
    assert fake.calls == ["request_stop", "restore_owner"]


def test_unknown_action_400(ctl):
    status, body = control(ctl, "logout")
    assert status == 400 and not body["ok"]


# --- step-up --------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["guest_off", "unlock", "restart", "shutdown"])
def test_stepup_required(ctl, fake, action):
    status, body = control(ctl, action)
    assert status == 403
    assert body == {"ok": False, "error": "stepup", "stepup": action}
    status, body = control(ctl, action, ALLOW, assertion={"sig": "other"})
    assert status == 403
    assert not any(a[0] == "systemctl" for a in fake.argv)
    assert not fake.tools and not fake.calls


@pytest.mark.parametrize(
    "action", ["guest_on", "lock", "screens_off", "sleep", "mic_mute"]
)
def test_instant_actions_need_no_stepup(ctl, action):
    status, _body = control(ctl, action)
    assert status == 200


def test_bearer_needs_no_stepup(ctl, fake):
    bearer = Principal(kind="bearer", gate="open")
    status, body = ctl.control(
        bearer, {"action": "unlock"}, lambda p, a, x: p.kind == "bearer"
    )
    assert status == 200 and body["status"] == "verified"


# --- guest exemption -------------------------------------------------------------------


def test_guest_mode_does_not_block_owner_actions(ctl, fake):
    fake.guest = True
    assert control(ctl, "lock")[1]["status"] == "verified"
    _, body = control(ctl, "unlock", ALLOW, assertion={"sig": "unlock"})
    assert body["status"] == "verified"
    _status, body = control(ctl, "guest_off", ALLOW, assertion={"sig": "guest_off"})
    assert body["status"] == "verified" and fake.guest is False


def test_run_phone_tool_called_outside_private_context(ctl, fake):
    owner = phone_session._OWNER.set(object())
    try:
        assert phone_session.private()
        control(ctl, "unlock", ALLOW, assertion={"sig": "unlock"})
    finally:
        phone_session._OWNER.reset(owner)
    assert fake.tools and all(private is False for _, _, private in fake.tools)


# --- readback statuses ---------------------------------------------------------------------


def test_lock_mismatched_when_still_unlocked(fake):
    def run(argv, **kw):
        fake.argv.append(list(argv))
        if argv[0] == "gdbus":
            return subprocess.CompletedProcess(argv, 0, "(false,)", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    ctl = pc.PhoneControl(fake.deps(run=run), presence_enabled=False)
    _status, body = control(ctl, "lock")
    assert body["status"] == "mismatched" and body["ok"] is True
    assert sum(1 for a in fake.argv if a[0] == "gdbus") >= 3  # polled 3x


def test_lock_unverified_when_unreadable(ctl, fake):
    fake.lock_readable = False
    _status, body = control(ctl, "lock")
    assert body["status"] == "dispatched_unverified"
    assert body["readback"] == {"locked": None}


def test_lock_failed_command(ctl, fake):
    fake.fail_argv = True
    _status, body = control(ctl, "lock")
    assert body["status"] == "failed" and body["ok"] is False


def test_no_dbus_gives_unverified_lock(fake):
    ctl = pc.PhoneControl(fake.deps(dbus_ok=lambda: False), presence_enabled=False)
    _status, body = control(ctl, "lock")
    assert body["status"] == "dispatched_unverified"


# --- audit --------------------------------------------------------------------------------


def test_every_call_is_audited(ctl, fake):
    control(ctl, "lock")
    control(ctl, "unlock")  # refused step-up
    assert ("phone_control", "lock verified") in fake.logs
    assert ("phone_control", "unlock stepup") in fake.logs
