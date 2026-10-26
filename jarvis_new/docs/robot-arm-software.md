# Arm and gripper software preparation

The working implementation is an offline simulator in `src/robotics/`. It has no serial, USB, network, GPIO, or physical robot driver and is not registered as a Jarvis voice/LLM tool. No existing Jarvis service needs to restart. Hardware transports are rejected even if software is enabled.

See [hardware options and commissioning prerequisites](robot-arm-hardware.md). The provisional RoArm-M2-S has a manufacturer-documented power-on centering movement. Its stock emergency-stop and controller-local watchdog behavior remain unverified. Do not energize it based on this simulator or copy these fictional joint limits into its firmware.

## Run the simulator

From `jarvis_new`, using the existing environment:

```bash
uv run --no-sync python -m robotics.cli status
# Reports disabled; no device access.
uv run --no-sync python -m robotics.cli demo --enable-mock --calibrate --arm
uv run --no-sync python -m robotics.cli demo --config config/robot-arm.mock.json --enable-mock --calibrate --arm
```

The explicit demo flags authorize simulated calibration and arming for this invocation only. The CLI checks existing `watch_mode.guest_required()` before commands, rejects guest mode, and denies on guard errors. The current local OS session is the simulator's authority; this is not an authenticated hardware-owner service. A future voice integration must supply the existing server-side verified-owner guard, an independent local enable/deadman, and denial on guest/unknown identities. Never accept a client-supplied `owner=true` or register raw joint commands as LLM tools.

Demo output is JSON listing correlated requests: calibrate, arm, present, half-close/open gripper, home, disarm. A mock acknowledgement is immediate and does not mean that a physical movement completed. Starting another invocation begins disabled unless explicitly enabled; it never restores an armed session.

## Controller API

`ArmController(config, transport, authorize=trusted_owner_guard)` denies by default if no guard is supplied. The `Config` dataclass holds joint degree bounds, per-joint degrees/second limits, complete home position, Cartesian preview bounds in metres, normalized gripper speed, maximum duration, and command/watchdog deadlines. `Config.demo()` and `config/robot-arm.mock.json` are fictional simulator profiles, not measurements of a real kit.

Supported asynchronous calls:

- `connect()` establishes a new random session, leaves the arm disarmed and invalidates calibration. It does not clear a latched stop.
- `calibrate(observed_joint_tuple)` records a mock observed pose only while connected and disarmed. Physical homing, offsets and gripper calibration are unsupported.
- `arm()` deliberately arms after calibration and authority checks; it starts the watchdog.
- `move_joints(complete_tuple, duration_seconds)` validates finite values, all joints, speed and duration before dispatch.
- `gesture("home" | "present" | "wave")` prevalidates the whole fixed sequence and serializes it; checks are repeated on each segment.
- `gripper(opening, duration_seconds)` uses normalized opening 0..1. It does not sense an object, force, slip or successful grasp.
- `heartbeat()` refreshes the watchdog only after a valid acknowledgement. There is no automatic heartbeat thread masking a lost operator.
- `disarm()` stops the armed state deliberately. `disconnect()` invalidates calibration; disconnecting while armed latches a stop.
- `emergency_stop(reason)` immediately latches the software state before awaiting a best-effort transport stop. It requires no owner permission.
- `clear_estop(acknowledge=True)` requires trusted authority, leaves the arm disarmed and uncalibrated, and refuses to erase a newer stop or a revoked-authority event during reset.

`preview_target((x, y, z))` validates Cartesian bounds for planning only. `reach_and_grasp(...)` always refuses: inverse kinematics, forward kinematic workspace enforcement, collision avoidance, torque/force limiting and physical grabbing are unsupported. Joint endpoint bounds do not prove that the end effector or swept arm stays inside a safe volume. Do not represent these checks as collision protection.

For example, a trusted application can await `connect()`, `calibrate(config.home)`, `arm()`, `gesture("present")`, `gripper(0.5, 2)`, `disarm()`, then `disconnect()` in a `finally` block. Only a test harness may substitute `authorize=lambda: True`; real integration must derive authority from verified local state.

## Backend contract and failure handling

`Transport` defines asynchronous `connect`, `send(Request) -> Ack`, and `disconnect`, plus `simulated`. Only simulated transports are currently allowed. The request fields are `session`, monotonically increasing `sequence`, `operation`, `values`, and `duration`. An acknowledgement must explicitly accept and match session and integer sequence. This is an internal guard contract, **not** the Waveshare native JSON wire protocol.

Timeouts, rejected/stale acknowledgements, cancellation, revoked authority while armed and missing heartbeats latch a stop and invalidate calibration. A late acknowledgement cannot rearm or clear a newer stop. The watchdog cannot run while the host/event loop is frozen, and an emergency-stop message cannot be guaranteed over a broken link. The software latch is process-local; every restart remains disabled/disarmed with calibration required, while a physical stop latch must independently survive until a deliberate hardware reset.

Future firmware/driver work must provide an independently enforced enable/deadman, controller-local heartbeat deadline, command expiry, stop priority/preemption, duplicate/stale session rejection, measured position feedback, local travel/current limits and no movement on power-on/reconnect. An acknowledgement of receipt is not an acknowledgement of completion. Transport cancellation must not allow an unobserved command to continue moving. Add feedback/completion monitoring and verify stop/reconnect/reset races before enabling a physical backend.

## Physical commissioning checklist

Do not connect the application to a motor controller until all items have been verified for the selected kit:

1. Confirm payload (including gripper), reach, mounting pattern, supply requirements, motor currents, encoder feedback, joint ranges/units and native protocol. Budget and kit specifications remain pending.
2. Obtain a compatible arm/gripper, correctly rated power supply and cables, rigid base/clamps, guarded test area and an accessible independent physical emergency stop/power cutoff. Plan safe behavior for gravity loads: removing power may let an arm fall or drop an object. Protective braking/support may be required.
3. Verify controller-local safety firmware and stopping behavior with the computer disconnected. Stock RoArm power-on centering and its unverified local stop/watchdog prevent commissioning as-is.
4. Measure/record each joint's calibrated zero, direction and soft/hard limits, safe workspace and speed/current/force limits. Keep hardware profiles separate from the demo profile.
5. Verify power-up, cable disconnect, communication loss, host freeze/reboot, rejected command, duplicate packet, watchdog expiry and physical stop all prevent continued or unexpected motion. Test that restoring power/link never automatically arms or releases a stop.
6. Start with a supported unloaded arm, minimum speed and a guarded single joint. Validate feedback and an independently safe stop before expanding to joint sequences, then IK/collision-aware reach and force-limited grasp.
7. Have a qualified person assess the installation and required protection. These Python checks are not safety-certified and do not make a hobby arm suitable for unrestricted movement near people or pets.

## Validation

The focused tests use only mocked transports, run sequentially, and do not contact hardware:

```bash
nice -n 10 uv run --no-sync pytest tests/test_robot_arm.py -q
nice -n 10 uv run --no-sync ruff check src/robotics tests/test_robot_arm.py
```

Coverage includes disabled default and missing authority, calibration/arm gating, invalid numbers and bounds, gripper/joint speed, guest revocation, timeout/rejected/stale acknowledgements, watchdog, cancellation, reconnect requiring calibration, hardware/IK refusal, whole-gesture prevalidation, stop during motion/connect, and the delayed-reset/new-stop race. Physical firmware, actual stopping time, collision behavior and successful grasp remain untested and unsupported.
