"""Local mock CLI. Explicit flags are required; no hardware or voice integration."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict, replace

from robotics.arm import ArmController, ArmError, Config, MockTransport


def local_owner() -> bool:
    # Trust the local OS session, not a flag supplied by an LLM/client request.
    # Existing guest/watch state remains authoritative. Import/check failure denies.
    try:
        import watch_mode

        return os.getuid() == os.geteuid() and not watch_mode.guest_required()
    except Exception:
        return False


async def run(args: argparse.Namespace) -> int:
    cfg = Config.demo()
    if args.config:
        with open(args.config) as handle:
            data = json.load(handle)
        from robotics.arm import JointLimit

        cfg = Config(
            joints=tuple(JointLimit(**j) for j in data["joints"]),
            home=tuple(data["home"]),
            workspace=tuple(tuple(axis) for axis in data["workspace"]),
            command_timeout=data.get("command_timeout", 0.5),
            watchdog_timeout=data.get("watchdog_timeout", 2.0),
            gripper_speed=data.get("gripper_speed", 0.5),
            maximum_duration=data.get("maximum_duration", 30),
        )
    transport = MockTransport()
    controller = ArmController(
        replace(cfg, enabled=args.enable_mock), transport, authorize=local_owner
    )
    if args.action == "status":
        print(
            json.dumps(
                {
                    "mode": "mock-only",
                    "state": controller.state.value,
                    "calibrated": False,
                    "hardware_supported": False,
                }
            )
        )
        return 0
    if not args.enable_mock or not args.calibrate or not args.arm:
        raise ArmError("Demo requires all of --enable-mock --calibrate --arm")
    try:
        await controller.connect()
        await controller.calibrate(cfg.home)
        await controller.arm()
        await controller.gesture("present")
        await controller.gripper(0.5, 2)
        await controller.gripper(1.0, 2)
        await controller.gesture("home")
        await controller.disarm()
    finally:
        await controller.disconnect()
    print(
        json.dumps(
            {
                "mode": "mock-only",
                "state": controller.state.value,
                "requests": [asdict(r) for r in transport.requests],
            },
            indent=2,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "demo"))
    parser.add_argument("--enable-mock", action="store_true")
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--arm", action="store_true")
    parser.add_argument(
        "--config", help="JSON mock bounds profile; never a hardware driver"
    )
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except (ArmError, ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"Refused: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
