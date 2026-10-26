import asyncio
from dataclasses import replace

import pytest

from robotics.arm import ArmController, ArmError, Config, MockTransport, State


def make(**changes):
    t = MockTransport()
    return ArmController(
        replace(Config.demo(), enabled=True, **changes), t, authorize=lambda: True
    ), t


async def ready(c):
    await c.connect()
    await c.calibrate(c.config.home)
    await c.arm()


@pytest.mark.asyncio
async def test_disabled_and_authority_fail_closed():
    t = MockTransport()
    for cfg in (Config.demo(), replace(Config.demo(), enabled=True)):
        with pytest.raises(ArmError):
            await ArmController(cfg, t).connect()
    assert not t.requests


@pytest.mark.asyncio
async def test_calibration_and_motion_bounds():
    c, t = make()
    await c.connect()
    with pytest.raises(ArmError):
        await c.arm()
    await c.calibrate(c.config.home)
    with pytest.raises(ArmError):
        await c.move_joints(c.config.home, 2)
    await c.arm()
    for target, duration in [
        ((999, 40, -30), 2),
        ((0, 40, -30), 0),
        ((float("nan"), 40, -30), 2),
        ((0, 70, -30), 0.01),
        ((0, 40), 2),
    ]:
        before = len(t.requests)
        with pytest.raises(ArmError):
            await c.move_joints(target, duration)
        assert len(t.requests) == before
    await c.gesture("present")
    await c.gripper(0.5, 2)
    assert c.gripper_position == 0.5
    await c.disconnect()


@pytest.mark.asyncio
async def test_latched_stop_reconnect_and_recalibration():
    c, _t = make()
    await ready(c)
    await c.emergency_stop()
    assert c.state is State.ESTOP
    with pytest.raises(ArmError):
        await c.move_joints(c.config.home, 2)
    await c.disconnect()
    await c.connect()
    assert c.state is State.ESTOP
    with pytest.raises(ArmError):
        await c.clear_estop(acknowledge=False)
    await c.clear_estop(acknowledge=True)
    with pytest.raises(ArmError):
        await c.arm()
    await c.calibrate(c.config.home)
    await c.arm()
    await c.disconnect()


@pytest.mark.asyncio
async def test_authority_revocation_latches_stop():
    c, t = make()
    await ready(c)
    c.authorize = lambda: False
    with pytest.raises(ArmError):
        await c.gesture("home")
    assert c.state is State.ESTOP
    assert t.requests[-1].operation == "stop"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "bad_ack", "rejected"])
async def test_transport_faults_latch(failure):
    c, t = make(command_timeout=0.03, watchdog_timeout=0.1)
    await ready(c)
    t.failure = failure
    with pytest.raises(ArmError):
        await c.gripper(0.5, 2)
    assert c.state is State.ESTOP
    await c.disconnect()


@pytest.mark.asyncio
async def test_watchdog_stops_without_heartbeat():
    c, t = make(command_timeout=0.03, watchdog_timeout=0.1)
    await ready(c)
    await asyncio.sleep(0.16)
    assert c.state is State.ESTOP
    assert t.requests[-1].operation == "stop"
    await c.disconnect()


@pytest.mark.asyncio
async def test_hardware_ik_and_workspace_fail_closed():
    c, t = make()
    t.simulated = False
    with pytest.raises(ArmError):
        await c.connect()
    assert not t.requests
    c, t = make()
    await ready(c)
    assert c.preview_target((0.1, 0, 0.1)) == (0.1, 0, 0.1)
    with pytest.raises(ArmError):
        c.preview_target((100, 0, 0))
    with pytest.raises(ArmError):
        await c.reach_and_grasp((0.1, 0, 0.1))
    await c.disconnect()


@pytest.mark.asyncio
async def test_invalid_later_gesture_segment_dispatches_nothing():
    from robotics.arm import JointLimit

    c, t = make()
    c.config = replace(
        c.config, joints=(JointLimit("base", 0, 30, 15), *c.config.joints[1:])
    )
    await ready(c)
    before = len(t.requests)
    with pytest.raises(ArmError):
        await c.gesture("wave")
    assert len(t.requests) == before
    await c.disconnect()


@pytest.mark.asyncio
async def test_late_ack_cannot_override_estop():
    class Delayed(MockTransport):
        async def send(self, request):
            if request.operation == "move_joints":
                await asyncio.sleep(0.03)
            return await super().send(request)

    c, _ = make()
    c.transport = Delayed()
    await ready(c)
    task = asyncio.create_task(c.move_joints(c.config.home, 2))
    await asyncio.sleep(0.01)
    await c.emergency_stop()
    with pytest.raises(ArmError):
        await task
    assert c.state is State.ESTOP
    await c.disconnect()


@pytest.mark.asyncio
async def test_cancelled_command_latches_stop():
    c, t = make()
    await ready(c)
    t.failure = "timeout"
    task = asyncio.create_task(c.gripper(0.5, 2))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert c.state is State.ESTOP
    await c.disconnect()


def test_cli_guest_guard_denies_even_with_enable(monkeypatch):
    import argparse

    from robotics import cli

    monkeypatch.setattr(cli, "local_owner", lambda: False)
    args = argparse.Namespace(
        action="demo", config=None, enable_mock=True, calibrate=True, arm=True
    )
    with pytest.raises(ArmError):
        asyncio.run(cli.run(args))


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["stop", "authority"])
async def test_reset_ack_cannot_clear_new_stop_or_revoked_authority(change):
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedReset(MockTransport):
        async def send(self, request):
            if request.operation == "reset_stop":
                entered.set()
                await release.wait()
            return await super().send(request)

    c, _ = make()
    c.transport = DelayedReset()
    await ready(c)
    await c.emergency_stop("first stop")
    task = asyncio.create_task(c.clear_estop(acknowledge=True))
    await entered.wait()
    if change == "stop":
        await c.emergency_stop("new operator stop")
    else:
        c.authorize = lambda: False
    release.set()
    with pytest.raises(ArmError):
        await task
    assert c.state is State.ESTOP
    assert not c.calibrated
    assert c.transport.requests[-1].operation == "stop"
    if change == "stop":
        assert c.fault == "new operator stop"
    await c.disconnect()


@pytest.mark.asyncio
async def test_stop_during_connect_is_not_cleared():
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedConnect(MockTransport):
        async def connect(self):
            entered.set()
            await release.wait()
            await super().connect()

    c, _ = make()
    c.transport = DelayedConnect()
    task = asyncio.create_task(c.connect())
    await entered.wait()
    await c.emergency_stop("operator stopped during connect")
    release.set()
    with pytest.raises(ArmError):
        await task
    assert c.state is State.ESTOP
    await c.disconnect()
