# Exercise camera and input verification

The exercise component is intentionally fail-open. Its current default input
backend is inert: it reports capabilities, but will not acquire desktop input
or arm a lock. Do not infer that Watch Mode's fullscreen card or stop shortcut
blocks input for this exercise mode.

## API

- `aware.exercise.camera_presence(camera=None) -> dict` takes one sample for
  the activity policy. `status` is `present`, `absent`, or `unknown`;
  `timestamp` is the camera frame's wall-clock capture time, `observed_at` is
  monotonic time, and `frame_id` is a digest of the frame. Missing, old, or
  frozen frames return `unknown`, never `absent`.
- `aware.exercise.ExerciseCamera.poll() -> dict` uses the shared camera hub at
  8 fps and the local face detector. It makes no snapshot file and does not
  open a second camera reader.
- `aware.exercise.ExerciseController(backend=None, camera=None, ...)` exposes
  `start()`, `observe(reading)`, `watchdog_once()`, `emergency_release()`,
  `on_error(error)`, `stop(reason='shutdown')`, and `status()`. `start()` checks
  Always Aware state, verifies the escape callback, then verifies actual input
  blocking and an independently expiring fail-open lease, starts that lease,
  and only then invokes `backend.block()`. The watchdog refreshes it while the
  camera is healthy. The lease must release input if this process stalls or
  exits. The backend contract is `verify_fail_open_lease()` followed by
  `refresh_safety_lease()` before blocking and on each watchdog cycle. Its
  production default refuses these checks. `run_workers=False` is
  for deterministic tests only; production starts a camera worker and a
  separate watchdog worker.
- `aware.exercise_backend.inspect_desktop_session()` reports session and
  read-only device-access evidence. `DesktopExerciseBackend` never blocks.

After arming with a verified injected backend, camera checks run independently
of the slower Always Aware poll. The controller must first observe presence;
only continuous, fresh `absent` results for **more than** 30 monotonic seconds
release input. Presence resets the timer. Unknown, unreadable, stale, frozen,
controller-error, Always Aware off/pause/kill, watchdog heartbeat timeout, and
the 180 second hard timeout all release. A fresh start clears session state,
so process restart begins unlocked. The watchdog is a separate thread in the
controller process; the independent safety guarantee comes from the required
backend-owned expiring lease, not from this thread. The installed backend does
not implement that lease, so it cannot arm.

## Installed session findings

Read-only inspection on 2026-11-12 found KDE Plasma on Wayland with XWayland
available. The config file contains `Ctrl+Alt+Shift+W` for the existing
`jarvis-watch-stop.desktop` action. This confirms only a Watch Mode config
entry; it does not prove that an exercise release handler is registered or
that the shortcut reaches this controller.

The session listed 30 `/dev/input/event*` nodes, but the current user could
open only 3; `/dev/uinput` was accessible. System Python has `evdev`, but the
project's `uv` environment does not. Those facts are not enough to claim that
every keyboard and pointer is covered, or that all input is released if the
controlling process stalls. This build therefore has no evdev grab backend or
expiring lease and remains unavailable. No camera lock or input grab was
activated during implementation.

To recheck the read-only capability report in this checkout:

```console
uv run python -c 'from aware.exercise_backend import inspect_desktop_session; print(inspect_desktop_session())'
```

`ExerciseController.status()` also includes the backend evidence after the
controller is constructed. Enabling input control requires a separately
reviewed platform backend that can enumerate and cover every seat keyboard and
pointer, deliver `Ctrl+Alt+Shift+W` from inside its input capture, release all
captures on controller death, and prove those paths on this desktop. Until
that verification is completed, the exercise input lock is unavailable. Do
not reuse Watch Mode's OS-lock behavior for this feature.
