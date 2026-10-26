"""Bounded mock arm control; physical commissioning and motion are unsupported."""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ArmError(RuntimeError):
    """A command was refused or a transport fault was latched."""


class State(str, Enum):
    DISABLED = "disabled"
    DISCONNECTED = "disconnected"
    DISARMED = "disarmed"
    ARMED = "armed"
    ESTOP = "estop"


def finite(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ArmError("Expected a finite numeric value")
    return float(value)


@dataclass(frozen=True)
class JointLimit:
    name: str
    minimum: float
    maximum: float
    speed: float  # degrees/second; illustrative mock units


@dataclass(frozen=True)
class Config:
    joints: tuple[JointLimit, ...]
    home: tuple[float, ...]
    workspace: tuple[tuple[float, float], ...]
    enabled: bool = False
    command_timeout: float = 0.5
    watchdog_timeout: float = 2.0
    gripper_speed: float = 0.5  # normalized opening/second
    maximum_duration: float = 30.0

    @classmethod
    def demo(cls) -> Config:
        """Fictional simulator limits, NOT a hardware calibration profile."""
        return cls(
            joints=(
                JointLimit("base", -30, 30, 15),
                JointLimit("shoulder", 10, 70, 15),
                JointLimit("elbow", -60, 0, 15),
            ),
            home=(0, 40, -30),
            workspace=((-0.2, 0.2), (-0.2, 0.2), (0, 0.3)),
        )

    def __post_init__(self) -> None:
        if (
            type(self.enabled) is not bool
            or not self.joints
            or len(self.home) != len(self.joints)
        ):
            raise ArmError("Invalid enable flag, joints or home position")
        if len({j.name for j in self.joints}) != len(self.joints):
            raise ArmError("Joint names must be unique")
        for joint, home in zip(self.joints, self.home, strict=True):
            lo, hi, speed, value = map(
                finite, (joint.minimum, joint.maximum, joint.speed, home)
            )
            if lo >= hi or speed <= 0 or not lo <= value <= hi:
                raise ArmError("Invalid joint limits, speed or home")
        if len(self.workspace) != 3:
            raise ArmError("Workspace must specify three axes")
        for lo, hi in self.workspace:
            if finite(lo) >= finite(hi):
                raise ArmError("Invalid workspace interval")
        for value in (
            self.command_timeout,
            self.watchdog_timeout,
            self.gripper_speed,
            self.maximum_duration,
        ):
            if finite(value) <= 0:
                raise ArmError("Timeouts, speed and duration must be positive")
        if self.command_timeout >= self.watchdog_timeout:
            raise ArmError("Command deadline must be shorter than watchdog deadline")


@dataclass(frozen=True)
class Request:
    session: str
    sequence: int
    operation: str
    values: tuple[float, ...] = ()
    duration: float = 0


@dataclass(frozen=True)
class Ack:
    session: str
    sequence: int
    accepted: bool


class Transport(Protocol):
    simulated: bool

    async def connect(self) -> None: ...
    async def send(self, request: Request) -> Ack: ...
    async def disconnect(self) -> None: ...


class MockTransport:
    """Immediate simulated acknowledgement, never opens devices or sockets."""

    simulated = True

    def __init__(self) -> None:
        self.connected = False
        self.requests: list[Request] = []
        self.failure: str | None = None

    async def connect(self) -> None:
        self.connected = True

    async def send(self, request: Request) -> Ack:
        if not self.connected:
            raise ArmError("Mock transport is disconnected")
        self.requests.append(request)
        if request.operation != "stop":
            if self.failure == "timeout":
                await asyncio.sleep(3600)
            if self.failure == "bad_ack":
                return Ack("stale-session", request.sequence, True)
            if self.failure == "rejected":
                return Ack(request.session, request.sequence, False)
        return Ack(request.session, request.sequence, True)

    async def disconnect(self) -> None:
        self.connected = False


class ArmController:
    def __init__(
        self,
        config: Config,
        transport: Transport,
        *,
        authorize: Callable[[], bool] | None = None,
    ) -> None:
        self.config, self.transport, self.authorize = config, transport, authorize
        self.state = State.DISCONNECTED if config.enabled else State.DISABLED
        self.calibrated = False
        self.position = config.home
        self.gripper_position = 1.0
        self.fault: str | None = None
        self._connected = False
        self._session = ""
        self._sequence = 0
        self._stop_epoch = 0
        self._last_contact = 0.0
        self._watchdog: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def _authorized(self) -> bool:
        try:
            return bool(self.authorize and self.authorize() is True)
        except Exception:
            return False

    async def _guard(self, *, armed: bool = False) -> None:
        if not self.config.enabled:
            raise ArmError("Arm is explicitly disabled")
        if not self._authorized():
            if self.state is State.ARMED:
                await self.emergency_stop("Authority revoked")
            raise ArmError("Verified local owner authority is required")
        if self.transport.simulated is not True:
            raise ArmError("Physical transport commissioning is unsupported; mock only")
        if self.state is State.ESTOP:
            raise ArmError("Emergency stop is latched")
        if armed and (self.state is not State.ARMED or not self.calibrated):
            raise ArmError("Deliberate calibration and arming are required")

    async def _rpc(
        self, operation: str, values: tuple[float, ...] = (), duration: float = 0
    ) -> None:
        self._sequence += 1
        request = Request(self._session, self._sequence, operation, values, duration)
        try:
            ack = await asyncio.wait_for(
                self.transport.send(request), self.config.command_timeout
            )
            if (
                not isinstance(ack, Ack)
                or ack.accepted is not True
                or type(ack.sequence) is not int
                or ack.session != request.session
                or ack.sequence != request.sequence
            ):
                raise ArmError("Rejected, stale or mismatched acknowledgement")
        except asyncio.CancelledError:
            await self.emergency_stop("Command cancelled")
            raise
        except Exception as exc:
            await self.emergency_stop(f"Transport fault: {type(exc).__name__}")
            raise ArmError(self.fault) from exc
        self._last_contact = time.monotonic()

    async def connect(self) -> None:
        # Connecting never clears a stop latch or restores calibration/arming.
        async with self._lock:
            latched = self.state is State.ESTOP
            stop_epoch = self._stop_epoch
            if latched:
                if (
                    not self.config.enabled
                    or not self._authorized()
                    or self.transport.simulated is not True
                ):
                    raise ArmError("Mock local owner authority is required")
            else:
                await self._guard()
            if self._connected:
                raise ArmError("Already connected; no duplicate session")
            try:
                await asyncio.wait_for(
                    self.transport.connect(), self.config.command_timeout
                )
            except Exception as exc:
                self.state, self.calibrated = State.ESTOP, False
                self.fault = "Connection failed"
                raise ArmError(self.fault) from exc
            self._connected = True
            self._session = uuid.uuid4().hex
            self.calibrated = False
            if self._stop_epoch != stop_epoch or not self._authorized():
                await self.emergency_stop(
                    self.fault or "Stop or authority changed while connecting"
                )
                raise ArmError(self.fault)
            self.state = State.ESTOP if latched else State.DISARMED

    def _target(self, values: tuple[float, ...]) -> tuple[float, ...]:
        if len(values) != len(self.config.joints):
            raise ArmError("Every joint must be specified")
        target = tuple(finite(value) for value in values)
        if any(
            not joint.minimum <= value <= joint.maximum
            for joint, value in zip(self.config.joints, target, strict=True)
        ):
            raise ArmError("Joint target exceeds configured bounds")
        return target

    def _duration(self, value: float) -> float:
        result = finite(value)
        if not 0 < result <= self.config.maximum_duration:
            raise ArmError("Duration exceeds configured bounds")
        return result

    async def calibrate(self, observed: tuple[float, ...]) -> None:
        async with self._lock:
            await self._guard()
            if self.state is not State.DISARMED or not self._connected:
                raise ArmError("Calibration requires a connected disarmed arm")
            target = self._target(observed)
            await self._rpc("calibrate", target)
            if self.state is State.ESTOP:
                raise ArmError("Stopped during calibration")
            self.position, self.gripper_position, self.calibrated = target, 1.0, True

    async def arm(self) -> None:
        async with self._lock:
            await self._guard()
            if self.state is not State.DISARMED or not self.calibrated:
                raise ArmError("Calibration is required before deliberate arming")
            await self._rpc("arm")
            if self.state is State.ESTOP:
                raise ArmError("Stopped during arming")
            if not self._authorized():
                await self.emergency_stop("Authority revoked while arming")
                raise ArmError(self.fault)
            self.state = State.ARMED
            self._watchdog = asyncio.create_task(
                self._watch(), name="mock-arm-watchdog"
            )

    async def heartbeat(self) -> None:
        async with self._lock:
            await self._guard(armed=True)
            await self._rpc("heartbeat")

    async def _watch(self) -> None:
        while self.state is State.ARMED:
            await asyncio.sleep(min(0.1, self.config.watchdog_timeout / 4))
            if (
                not self._authorized()
                or time.monotonic() - self._last_contact >= self.config.watchdog_timeout
            ):
                await self.emergency_stop("Authority revoked or heartbeat expired")
                return

    def _checked_motion(
        self, values: tuple[float, ...], duration: float, origin: tuple[float, ...]
    ) -> tuple[tuple[float, ...], float]:
        target, seconds = self._target(values), self._duration(duration)
        if any(
            abs(v - p) / seconds > joint.speed
            for joint, v, p in zip(self.config.joints, target, origin, strict=True)
        ):
            raise ArmError("Joint speed exceeds configured limit")
        return target, seconds

    async def _move_locked(self, values: tuple[float, ...], duration: float) -> None:
        await self._guard(armed=True)
        target, seconds = self._checked_motion(values, duration, self.position)
        await self._rpc("move_joints", target, seconds)
        if self.state is State.ESTOP:
            raise ArmError("Stopped while moving")
        await self._guard(armed=True)
        self.position = target

    async def move_joints(self, values: tuple[float, ...], duration: float) -> None:
        async with self._lock:
            await self._move_locked(values, duration)

    async def gripper(self, opening: float, duration: float) -> None:
        async with self._lock:
            await self._guard(armed=True)
            value, seconds = finite(opening), self._duration(duration)
            if (
                not 0 <= value <= 1
                or abs(value - self.gripper_position) / seconds
                > self.config.gripper_speed
            ):
                raise ArmError("Gripper opening or speed exceeds bounds")
            await self._rpc("gripper", (value,), seconds)
            if self.state is State.ESTOP:
                raise ArmError("Stopped while gripping")
            await self._guard(armed=True)
            self.gripper_position = value

    async def gesture(self, name: str) -> None:
        # Only a fixed illustrative set, with every segment revalidated.
        if len(self.config.joints) != 3:
            raise ArmError("Demo gestures require the illustrative three-joint profile")
        poses = {
            "home": (self.config.home,),
            "present": ((0, 50, -40),),
            "wave": ((15, 40, -30), (-15, 40, -30), self.config.home),
        }
        if name not in poses:
            raise ArmError("Unknown gesture")
        async with self._lock:
            await self._guard(armed=True)
            origin = self.position
            # Validate the entire sequence before dispatching any segment.
            for pose in poses[name]:
                origin, _ = self._checked_motion(pose, 3, origin)
            for pose in poses[name]:
                await self._move_locked(pose, 3)

    def preview_target(self, xyz: tuple[float, float, float]) -> tuple[float, ...]:
        if len(xyz) != 3:
            raise ArmError("Target requires x/y/z metres")
        target = tuple(finite(value) for value in xyz)
        if any(
            not lo <= value <= hi
            for value, (lo, hi) in zip(target, self.config.workspace, strict=True)
        ):
            raise ArmError("Cartesian target outside configured preview bounds")
        return target

    async def reach_and_grasp(self, xyz: tuple[float, float, float]) -> None:
        self.preview_target(xyz)
        raise ArmError(
            "IK, collision checking, force feedback and physical grasping are unsupported"
        )

    async def emergency_stop(self, reason: str = "Operator emergency stop") -> None:
        # Latch BEFORE awaits; a late motion acknowledgement cannot rearm.
        self._stop_epoch += 1
        self.state, self.calibrated, self.fault = State.ESTOP, False, reason
        if self._watchdog and self._watchdog is not asyncio.current_task():
            self._watchdog.cancel()
        if self._connected:
            self._sequence += 1
            request = Request(self._session, self._sequence, "stop")
            # Latch remains even when the best-effort stop cannot be delivered.
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(
                    self.transport.send(request), self.config.command_timeout
                )

    async def clear_estop(self, *, acknowledge: bool) -> None:
        async with self._lock:
            if (
                acknowledge is not True
                or not self.config.enabled
                or not self._authorized()
                or self.transport.simulated is not True
            ):
                raise ArmError("Explicit local-owner acknowledgement is required")
            if self.state is not State.ESTOP:
                raise ArmError("No latched stop to clear")
            stop_epoch = self._stop_epoch
            if self._connected:
                await self._rpc("reset_stop")
            if self._stop_epoch != stop_epoch or not self._authorized():
                # Reassert stop after a late reset acknowledgement; never clear a newer latch.
                await self.emergency_stop(
                    self.fault or "Authority revoked while clearing stop"
                )
                raise ArmError("Stop or authority changed while clearing stop")
            self.fault, self.calibrated = None, False
            self.state = State.DISARMED if self._connected else State.DISCONNECTED

    async def disarm(self) -> None:
        async with self._lock:
            await self._guard()
            if not self._connected:
                raise ArmError("Not connected")
            await self._rpc("disarm")
            if self.state is not State.ESTOP:
                self.state = State.DISARMED
            if self._watchdog:
                self._watchdog.cancel()

    async def disconnect(self) -> None:
        async with self._lock:
            if self.state is State.ARMED:
                await self.emergency_stop("Disconnected while armed")
            if self._watchdog:
                self._watchdog.cancel()
            if self._connected:
                try:
                    await asyncio.wait_for(
                        self.transport.disconnect(), self.config.command_timeout
                    )
                except Exception:
                    self.state, self.fault = State.ESTOP, "Disconnect failed"
            self._connected, self.calibrated = False, False
            if self.state not in (State.ESTOP, State.DISABLED):
                self.state = State.DISCONNECTED
