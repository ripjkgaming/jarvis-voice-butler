"""Independent camera monitor and fail-open exercise input controller.

The controller accepts an injected input backend for verification and tests.
Its default desktop backend is inert, so a platform must pass explicit
emergency-release and cross-monitor-blocking checks before this code can arm.
"""

from __future__ import annotations

import atexit
import contextlib
import hashlib
import os
import threading
import time
import uuid
from typing import Any, Callable

from aware.exercise_backend import DesktopExerciseBackend


class ExerciseCamera:
    """Fast presence sampler backed by the shared camera hub."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        who: str = "exercise",
        max_age: float = 1.5,
        frozen_after: float = 5.0,
    ) -> None:
        self.clock = clock
        self.wall_clock = wall_clock
        self.who = who
        self.max_age = max_age
        self.frozen_after = frozen_after
        self._last_frame_id: str | None = None
        self._same_frame_since: float | None = None

    def poll(self) -> dict[str, object]:
        """Return present/absent with capture freshness, or an explicit error."""
        now = self.clock()
        try:
            import cv2

            import camera_hub
            import presence

            if not camera_hub.request(self.who, 2.0, fps=8.0):
                return self._failure("camera_lease_failed", now)
            frame = camera_hub.latest_frame(max_age_s=self.max_age)
            meta = camera_hub.latest_meta()
            captured_at = float(meta.get("ts", 0.0))
            age = self.wall_clock() - captured_at
            if frame is None or captured_at <= 0 or age < -0.25 or age > self.max_age:
                return self._failure("camera_frame_stale_or_unreadable", now)

            frame_id = hashlib.blake2b(memoryview(frame), digest_size=12).hexdigest()
            if frame_id == self._last_frame_id:
                if self._same_frame_since is None:
                    self._same_frame_since = now
                if now - self._same_frame_since > self.frozen_after:
                    return self._failure("camera_frame_frozen", now, frame_id)
            else:
                self._last_frame_id = frame_id
                self._same_frame_since = now

            model = presence._cascade()
            if model is None:
                return self._failure("face_detector_unavailable", now, frame_id)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = model.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(presence.MIN_FACE_PX, presence.MIN_FACE_PX),
            )
            observed_at = now - max(0.0, age)
            return {
                "status": "present" if len(faces) else "absent",
                "observed_at": observed_at,
                "timestamp": captured_at,
                "frame_id": frame_id,
                "faces": len(faces),
                "error": "",
            }
        except Exception as exc:
            return self._failure(f"camera_poll_failed:{type(exc).__name__}", now)

    def release(self) -> None:
        """Drop only this component's shared-camera lease."""
        with contextlib.suppress(Exception):
            import camera_hub

            camera_hub.release(self.who)

    @staticmethod
    def _failure(reason: str, now: float, frame_id: str = "") -> dict[str, object]:
        return {
            "status": "unknown",
            "observed_at": now,
            "timestamp": 0.0,
            "frame_id": frame_id,
            "error": reason,
        }


class ExerciseController:
    """Dedicated fail-open input control with a fast camera and watchdog loop.

    ``start`` verifies the emergency shortcut and blocking backend before
    requesting a block. Once armed, camera checks run every 500 ms and a
    separate in-process watchdog worker refreshes a backend-owned expiring
    lease and requests release on stale camera heartbeats or the 180s hard
    limit. Arming requires ``verify_fail_open_lease`` and an initial
    ``refresh_safety_lease`` before blocking. The lease must independently
    release input if this process stalls or dies. Absence releases only after
    prior presence and strictly more than 30 continuous monotonic seconds.
    """

    def __init__(
        self,
        *,
        backend: Any | None = None,
        camera: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
        camera_interval: float = 0.5,
        watchdog_interval: float = 0.25,
        watchdog_timeout: float = 2.0,
        camera_stale_after: float = 2.0,
        frozen_after: float = 5.0,
        absence_seconds: float = 30.0,
        hard_timeout: float = 180.0,
        controls: Callable[[], str] | None = None,
        on_event: Callable[[dict[str, object]], object] | None = None,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.clock = clock
        self.backend = backend or DesktopExerciseBackend()
        self.camera = camera or ExerciseCamera(clock=clock, frozen_after=frozen_after)
        self.camera_interval = max(0.05, float(camera_interval))
        self.watchdog_interval = max(0.05, float(watchdog_interval))
        self.watchdog_timeout = max(0.1, float(watchdog_timeout))
        self.camera_stale_after = max(0.1, float(camera_stale_after))
        self.frozen_after = max(0.1, float(frozen_after))
        self.absence_seconds = max(0.0, float(absence_seconds))
        self.hard_timeout = max(0.1, float(hard_timeout))
        self.controls = controls or _exercise_control_failure
        self.on_event = on_event
        self.wall_clock = wall_clock
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._state = "unlocked"
        self._reason = ""
        self._started_at: float | None = None
        self._session_id: str | None = None
        self._pending_release_reason = ""
        self._release_event_sent = False
        self._heartbeat_at: float | None = None
        self._absence_since: float | None = None
        self._seen_present = False
        self._last_frame_id: str | None = None
        self._same_frame_since: float | None = None
        self._camera_thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None

    def start(self, *, run_workers: bool = True) -> dict[str, object]:
        """Verify safeguards, then arm. Failed checks always leave input free."""
        with self._lock:
            if self._state == "locked":
                return {"armed": True, "state": "locked", "reason": self._reason}
            if self._state == "release_failed":
                if not self._release_backend(self._pending_release_reason):
                    return {
                        "armed": False,
                        "state": "release_failed",
                        "reason": self._reason,
                    }
                self._emit_release(self._pending_release_reason)
                self._state = "unlocked"
            self._reset_unlocked()
            control_failure = self._controls_failure()
            if control_failure:
                self._reason = control_failure
                return {"armed": False, "state": "unlocked", "reason": self._reason}
            try:
                emergency_ok = bool(
                    self.backend.verify_emergency_release(self.emergency_release)
                )
            except Exception:
                emergency_ok = False
            if not emergency_ok:
                self._reason = "emergency_release_unverified"
                self._release_backend(self._reason)
                return {"armed": False, "state": "unlocked", "reason": self._reason}
            try:
                blocking_ok = bool(self.backend.verify_blocking())
            except Exception:
                blocking_ok = False
            if not blocking_ok:
                self._reason = "input_blocking_unverified"
                self._release_backend(self._reason)
                return {"armed": False, "state": "unlocked", "reason": self._reason}
            try:
                lease_ok = bool(self.backend.verify_fail_open_lease())
            except Exception:
                lease_ok = False
            if not lease_ok:
                self._reason = "fail_open_lease_unverified"
                self._release_backend(self._reason)
                return {"armed": False, "state": "unlocked", "reason": self._reason}
            try:
                lease_started = bool(self.backend.refresh_safety_lease())
            except Exception:
                lease_started = False
            if not lease_started:
                self._reason = "fail_open_lease_start_failed"
                self._release_backend(self._reason)
                return {"armed": False, "state": "unlocked", "reason": self._reason}
            try:
                blocked = bool(self.backend.block())
            except Exception:
                blocked = False
            if not blocked:
                self._reason = "input_block_failed"
                self._release_backend(self._reason)
                return {"armed": False, "state": "unlocked", "reason": self._reason}

            now = self.clock()
            self._state = "locked"
            self._reason = ""
            self._started_at = now
            self._session_id = uuid.uuid4().hex
            self._release_event_sent = False
            self._heartbeat_at = now
            self._stop_event.clear()
            atexit.register(self.stop)
            self._emit_event("armed", "", departure_verified=False)
            if run_workers:
                self._camera_thread = threading.Thread(
                    target=self._camera_loop,
                    name="aware-exercise-camera",
                    daemon=True,
                )
                self._watchdog_thread = threading.Thread(
                    target=self._watchdog_loop,
                    name="aware-exercise-watchdog",
                    daemon=True,
                )
                self._camera_thread.start()
                self._watchdog_thread.start()
            return {"armed": True, "state": "locked", "reason": ""}

    def observe(self, reading: dict[str, object]) -> dict[str, object]:
        """Apply one fresh camera result; unknown/failure always unlocks."""
        with self._lock:
            if self._state != "locked":
                return self.status()
            now = self.clock()
            control_failure = self._controls_failure()
            if control_failure:
                return self._release_locked(control_failure)
            if not isinstance(reading, dict):
                return self._release_locked("camera_unreadable")
            status = str(reading.get("status", "unknown")).lower()
            try:
                observed_at = float(reading["observed_at"])
            except (KeyError, TypeError, ValueError):
                return self._release_locked("camera_timestamp_missing")
            age = now - observed_at
            if age < -0.25 or age > self.camera_stale_after:
                return self._release_locked("camera_stale")
            if status not in {"present", "absent"}:
                return self._release_locked("camera_unknown")
            frame_id = str(reading.get("frame_id", ""))
            if not frame_id:
                return self._release_locked("camera_frame_identity_missing")
            if frame_id == self._last_frame_id:
                if self._same_frame_since is None:
                    self._same_frame_since = now
                if now - self._same_frame_since > self.frozen_after:
                    return self._release_locked("camera_frozen")
            else:
                self._last_frame_id = frame_id
                self._same_frame_since = now

            self._heartbeat_at = now
            if status == "present":
                self._seen_present = True
                self._absence_since = None
                return {"state": "present", "reason": ""}
            if not self._seen_present:
                return {"state": "locked_waiting_for_presence", "reason": ""}
            if self._absence_since is None:
                self._absence_since = now
            if now - self._absence_since > self.absence_seconds:
                return self._release_locked("continuous_absence")
            return {"state": "absence_pending", "reason": ""}

    def watchdog_once(self) -> dict[str, object]:
        """Run one watchdog evaluation, also exposed for deterministic tests."""
        with self._lock:
            if self._state == "release_failed":
                if self._release_backend(self._pending_release_reason):
                    self._emit_release(self._pending_release_reason)
                    self._state = "unlocked"
                    return {
                        "state": "released",
                        "reason": self._pending_release_reason,
                    }
                return {"state": "release_failed", "reason": self._reason}
            if self._state != "locked":
                return self.status()
            now = self.clock()
            control_failure = self._controls_failure()
            if control_failure:
                return self._release_locked(control_failure)
            if (
                self._started_at is not None
                and now - self._started_at >= self.hard_timeout
            ):
                return self._release_locked("hard_timeout")
            if (
                self._heartbeat_at is None
                or now - self._heartbeat_at > self.watchdog_timeout
            ):
                return self._release_locked("watchdog_heartbeat_timeout")
            try:
                lease_refreshed = bool(self.backend.refresh_safety_lease())
            except Exception:
                lease_refreshed = False
            if not lease_refreshed:
                return self._release_locked("fail_open_lease_refresh_failed")
            return {"state": "locked", "reason": ""}

    def emergency_release(self) -> dict[str, object]:
        """Callback passed to the separately verified emergency shortcut."""
        with self._lock:
            return self._release_locked("emergency_shortcut")

    def on_error(self, error: BaseException | None = None) -> dict[str, object]:
        """Fail-open entry point for unrecoverable controller errors."""
        _ = error
        with self._lock:
            return self._release_locked("controller_error")

    def stop(self, reason: str = "shutdown") -> dict[str, object]:
        """Stop workers and release any held input; safe to call repeatedly."""
        self._stop_event.set()
        with self._lock:
            result = self._release_locked(reason)
        current = threading.current_thread()
        for worker in (self._camera_thread, self._watchdog_thread):
            if worker and worker is not current and worker.is_alive():
                worker.join(timeout=1.0)
        self._camera_thread = None
        self._watchdog_thread = None
        with contextlib.suppress(Exception):
            atexit.unregister(self.stop)
        return result

    def status(self) -> dict[str, object]:
        """Return current lock state and the last release/verification reason."""
        with self._lock:
            result = {"state": self._state, "reason": self._reason}
            evidence = getattr(self.backend, "evidence", None)
            if isinstance(evidence, dict):
                result["backend"] = dict(evidence)
            return result

    def _camera_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                reading = self.camera.poll()
                self.observe(reading)
            except BaseException as exc:
                self.on_error(exc)
            if self._stop_event.wait(self.camera_interval):
                return

    def _watchdog_loop(self) -> None:
        while True:
            time.sleep(self.watchdog_interval)
            if self._stop_event.is_set() and self.status()["state"] != "release_failed":
                return
            try:
                result = self.watchdog_once()
                if result.get("state") not in {"locked", "release_failed"}:
                    self._stop_event.set()
                    return
            except BaseException:
                self.on_error()
                self._stop_event.set()
                return

    def _release_locked(self, reason: str) -> dict[str, object]:
        was_locked = self._state == "locked"
        self._release_camera()
        self._pending_release_reason = reason
        released = self._release_backend(reason)
        self._state = "unlocked" if released else "release_failed"
        self._reason = reason if released else f"release_failed:{reason}"
        self._absence_since = None
        self._started_at = None
        self._heartbeat_at = None
        if was_locked and released:
            self._stop_event.set()
            self._emit_release(reason)
        watchdog_dead = (
            self._watchdog_thread is None
            or not self._watchdog_thread.is_alive()
            or self._watchdog_thread is threading.current_thread()
        )
        if not released and watchdog_dead and was_locked:
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop,
                name="aware-exercise-watchdog-recovery",
                daemon=True,
            )
            self._watchdog_thread.start()
        return {
            "state": "released" if was_locked and released else self._state,
            "reason": self._reason,
        }

    def _emit_release(self, reason: str) -> None:
        if self._release_event_sent:
            return
        self._release_event_sent = True
        self._emit_event(
            "released",
            reason,
            departure_verified=reason == "continuous_absence",
        )

    def _emit_event(self, event: str, reason: str, *, departure_verified: bool) -> None:
        if not self.on_event or not self._session_id:
            return
        payload = {
            "event": event,
            "session_id": self._session_id,
            "timestamp": float(self.wall_clock()),
            "reason": reason,
            "departure_verified": departure_verified,
        }
        # A persistence/reporting outage cannot keep input blocked.
        with contextlib.suppress(Exception):
            self.on_event(payload)

    def _release_backend(self, reason: str) -> bool:
        try:
            result = self.backend.release(reason)
            return result is not False
        except Exception:
            return False

    def _release_camera(self) -> None:
        release = getattr(self.camera, "release", None)
        if callable(release):
            with contextlib.suppress(Exception):
                release()

    def _controls_failure(self) -> str:
        try:
            return str(self.controls() or "")
        except Exception:
            return "always_aware_controls_unavailable"

    def _reset_unlocked(self) -> None:
        self._state = "unlocked"
        self._reason = ""
        self._started_at = None
        self._session_id = None
        self._pending_release_reason = ""
        self._release_event_sent = False
        self._heartbeat_at = None
        self._absence_since = None
        self._seen_present = False
        self._last_frame_id = None
        self._same_frame_since = None
        self._stop_event.set()


def camera_presence(camera: Any | None = None) -> dict[str, object]:
    """One fresh camera sample for activity eligibility; never infer absence on error.

    ``timestamp`` is wall-clock capture time for activity records,
    ``observed_at`` is monotonic time for freshness and duration decisions.
    """
    source = camera or ExerciseCamera()
    try:
        reading = source.poll()
        if isinstance(reading, dict):
            return reading
    except Exception:
        pass
    finally:
        release = getattr(source, "release", None)
        if callable(release):
            with contextlib.suppress(Exception):
                release()
    return ExerciseCamera._failure("camera_poll_failed", time.monotonic())


def _exercise_control_failure() -> str:
    """Return an Always Aware off/pause/kill reason, checking shared state."""
    if str(os.environ.get("JARVIS_AWARE", "1")).strip().lower() in (
        "0",
        "off",
        "false",
        "no",
    ):
        return "always_aware_disabled_by_environment"
    try:
        from aware import state

        if state.killed():
            return "always_aware_kill_switch"
        current = state.load()
        if not current.get("enabled"):
            return "always_aware_off"
        if state.paused(current, time.time()):
            return "always_aware_paused"
    except Exception:
        return "always_aware_controls_unavailable"
    return ""
