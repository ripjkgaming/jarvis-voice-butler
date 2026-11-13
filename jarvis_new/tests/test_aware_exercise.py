"""Fail-open tests for the dedicated exercise lock controller."""

from __future__ import annotations

import time

from aware.exercise import ExerciseController, camera_presence
from aware.exercise_backend import DesktopExerciseBackend, inspect_desktop_session


class Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class Camera:
    def __init__(self, samples=None) -> None:
        self.samples = list(samples or [])

    def poll(self):
        return (
            self.samples.pop(0)
            if self.samples
            else {
                "status": "unknown",
                "observed_at": 100.0,
                "frame_id": "default",
            }
        )


class Backend:
    def __init__(self, *, emergency=True, blocking=True, lease=True) -> None:
        self.emergency = emergency
        self.blocking = blocking
        self.lease = lease
        self.lease_refreshes = 0
        self.verified_callback = None
        self.blocked = False
        self.releases = []
        self.fail_release = False
        self.calls = []

    def verify_emergency_release(self, callback) -> bool:
        self.calls.append("verify_emergency")
        self.verified_callback = callback
        return self.emergency

    def verify_blocking(self) -> bool:
        self.calls.append("verify_blocking")
        return self.blocking

    def verify_fail_open_lease(self) -> bool:
        self.calls.append("verify_fail_open_lease")
        return self.lease

    def refresh_safety_lease(self) -> bool:
        self.calls.append("refresh_safety_lease")
        self.lease_refreshes += 1
        return self.lease

    def block(self) -> bool:
        self.calls.append("block")
        self.blocked = True
        return True

    def release(self, reason="release") -> None:
        self.blocked = False
        self.releases.append(reason)
        return not self.fail_release


def sample(status, when, frame_id):
    return {"status": status, "observed_at": when, "frame_id": frame_id}


def controller(backend=None, camera=None, clock=None, **kwargs):
    clock = clock or Clock()
    kwargs.setdefault("controls", lambda: "")
    return ExerciseController(
        backend=backend or Backend(),
        camera=camera or Camera(),
        clock=clock,
        **kwargs,
    ), clock


def start(ctl):
    return ctl.start(run_workers=False)


def test_verifies_escape_and_blocking_before_blocking_input():
    backend = Backend(emergency=False)
    ctl, _ = controller(backend=backend)

    result = start(ctl)

    assert result["armed"] is False
    assert result["reason"] == "emergency_release_unverified"
    assert backend.verified_callback is not None
    assert not backend.blocked
    assert backend.calls == ["verify_emergency"]


def test_refuses_to_arm_when_input_backend_is_not_verified():
    backend = Backend(blocking=False)
    ctl, _ = controller(backend=backend)

    result = start(ctl)

    assert result["armed"] is False
    assert result["reason"] == "input_blocking_unverified"
    assert not backend.blocked
    assert backend.calls == ["verify_emergency", "verify_blocking"]


def test_default_desktop_backend_never_arms():
    backend = DesktopExerciseBackend(evidence={"input_blocking_available": False})
    ctl, _ = controller(backend=backend)

    result = start(ctl)

    assert result["armed"] is False
    assert result["reason"] == "emergency_release_unverified"
    assert ctl.status()["backend"]["input_blocking_available"] is False


def test_armer_rejects_backend_without_independent_fail_open_lease():
    backend = Backend(lease=False)
    ctl, _ = controller(backend=backend)

    result = start(ctl)

    assert result["armed"] is False
    assert result["reason"] == "fail_open_lease_unverified"
    assert backend.calls == [
        "verify_emergency",
        "verify_blocking",
        "verify_fail_open_lease",
    ]
    assert not backend.blocked


def test_watchdog_refreshes_lease_and_releases_if_refresh_fails():
    backend = Backend()
    ctl, _ = controller(backend=backend)
    assert start(ctl)["armed"]
    refreshes_after_arm = backend.lease_refreshes
    assert ctl.watchdog_once()["state"] == "locked"
    assert backend.lease_refreshes == refreshes_after_arm + 1

    backend.lease = False
    result = ctl.watchdog_once()
    assert result["state"] == "released"
    assert result["reason"] == "fail_open_lease_refresh_failed"
    assert not backend.blocked


def test_absence_unlock_requires_prior_presence_and_more_than_30_seconds():
    clock = Clock()
    backend = Backend()
    ctl, _ = controller(backend=backend, clock=clock)
    assert start(ctl)["armed"] is True

    assert (
        ctl.observe(sample("absent", clock(), "a"))["state"]
        == "locked_waiting_for_presence"
    )
    assert backend.blocked
    assert ctl.observe(sample("present", clock(), "b"))["state"] == "present"
    assert ctl.observe(sample("absent", clock(), "c"))["state"] == "absence_pending"
    clock.advance(30.0)
    assert ctl.observe(sample("absent", clock(), "d"))["state"] == "absence_pending"
    clock.advance(0.01)
    assert ctl.observe(sample("absent", clock(), "e"))["state"] == "released"
    assert not backend.blocked
    assert backend.releases[-1] == "continuous_absence"


def test_returning_before_threshold_resets_continuous_absence_timer():
    clock = Clock()
    backend = Backend()
    ctl, _ = controller(backend=backend, clock=clock)
    start(ctl)
    ctl.observe(sample("present", clock(), "p1"))
    ctl.observe(sample("absent", clock(), "a1"))
    clock.advance(29.9)
    ctl.observe(sample("present", clock(), "p2"))
    ctl.observe(sample("absent", clock(), "a2"))
    clock.advance(30.0)

    assert ctl.observe(sample("absent", clock(), "a3"))["state"] == "absence_pending"
    clock.advance(0.1)
    assert ctl.observe(sample("absent", clock(), "a4"))["state"] == "released"


def test_unknown_unreadable_stale_or_frozen_camera_releases_immediately():
    for kind, reading in [
        ("unknown", sample("unknown", 100.0, "x")),
        ("unreadable", {"status": "error", "observed_at": 100.0, "frame_id": "x"}),
        ("stale", sample("present", 90.0, "x")),
    ]:
        backend = Backend()
        ctl, _ = controller(backend=backend, camera=Camera())
        start(ctl)
        ctl.observe(sample("present", 100.0, "initial"))
        assert ctl.observe(reading)["state"] == "released", kind
        assert not backend.blocked
        assert backend.releases[-1].startswith("camera_")

    clock = Clock()
    backend = Backend()
    ctl, _ = controller(backend=backend, clock=clock, frozen_after=2.0)
    start(ctl)
    ctl.observe(sample("present", clock(), "same"))
    clock.advance(2.1)
    assert ctl.observe(sample("present", clock(), "same"))["state"] == "released"
    assert backend.releases[-1] == "camera_frozen"


def test_emergency_release_shortcut_callback_releases_input():
    backend = Backend()
    ctl, _ = controller(backend=backend)
    assert start(ctl)["armed"]

    backend.verified_callback()

    assert not backend.blocked
    assert backend.releases[-1] == "emergency_shortcut"


def test_watchdog_releases_after_controller_heartbeat_stalls():
    clock = Clock()
    backend = Backend()
    ctl, _ = controller(backend=backend, clock=clock, watchdog_timeout=2.0)
    start(ctl)
    clock.advance(2.01)

    assert ctl.watchdog_once()["state"] == "released"
    assert backend.releases[-1] == "watchdog_heartbeat_timeout"


def test_hard_timeout_shutdown_and_unrecoverable_error_release():
    for cause, advance in [("hard_timeout", 180.01), ("shutdown", 0.0)]:
        clock = Clock()
        backend = Backend()
        ctl, _ = controller(backend=backend, clock=clock)
        start(ctl)
        clock.advance(advance)
        result = ctl.watchdog_once() if cause == "hard_timeout" else ctl.stop(cause)
        assert result["state"] == "released"
        assert not backend.blocked
    backend = Backend()
    ctl, clock = controller(backend=backend)
    start(ctl)
    ctl.on_error(RuntimeError("camera loop failed"))
    assert not backend.blocked
    assert backend.releases[-1] == "controller_error"


def test_start_after_stop_begins_unlocked_and_requires_new_verification():
    backend = Backend()
    ctl, _ = controller(backend=backend)
    start(ctl)
    ctl.stop("test")
    assert not backend.blocked

    assert ctl.status()["state"] == "unlocked"
    assert start(ctl)["armed"] is True
    assert backend.blocked


def test_always_aware_control_change_releases_from_watchdog():
    control = [""]
    backend = Backend()
    ctl, _ = controller(backend=backend, controls=lambda: control[0])
    assert start(ctl)["armed"]

    control[0] = "always_aware_paused"
    result = ctl.watchdog_once()

    assert result["state"] == "released"
    assert result["reason"] == "always_aware_paused"
    assert not backend.blocked


def test_fresh_camera_presence_api_has_wall_and_monotonic_timestamps():
    reading = sample("present", 55.0, "hash")
    reading["timestamp"] = 1_800_000_000.0

    assert camera_presence(Camera([reading])) == reading
    failed = camera_presence(Camera([{"status": "unknown"}]))
    assert failed["status"] == "unknown"


def test_live_camera_and_watchdog_workers_release_without_parent_polling():
    backend = Backend()
    camera = Camera([{"status": "unknown", "observed_at": 100.0, "frame_id": "x"}])
    ctl, _ = controller(
        backend=backend,
        camera=camera,
        camera_interval=0.01,
        watchdog_interval=0.01,
    )
    result = ctl.start()
    assert result["armed"]
    for _ in range(100):
        if ctl.status()["state"] == "unlocked":
            break
        time.sleep(0.01)

    assert ctl.status()["reason"] == "camera_unknown"
    assert not backend.blocked
    ctl.stop()


def test_installed_session_evidence_keeps_desktop_backend_unverified(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "kglobalshortcutsrc").write_text(
        "[services][jarvis-watch-stop.desktop]\n_launch=Ctrl+Alt+Shift+W\n"
    )
    evidence = inspect_desktop_session(
        environ={"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"},
        config_dir=config,
    )

    assert evidence["watch_mode_shortcut_configured"] is True
    assert evidence["exercise_emergency_shortcut_verified"] is False
    assert evidence["exercise_input_blocking_verified"] is False
    assert evidence["exercise_fail_open_lease_verified"] is False
    assert evidence["input_blocking_available"] is False


def test_release_failure_is_reported_retried_and_blocks_rearming():
    backend = Backend()
    ctl, _ = controller(backend=backend)
    assert start(ctl)["armed"]
    backend.fail_release = True

    result = ctl.on_error(RuntimeError("release test"))

    assert result["state"] == "release_failed"
    assert ctl.status()["state"] == "release_failed"
    assert start(ctl)["armed"] is False
    assert ctl.status()["state"] == "release_failed"

    backend.fail_release = False
    result = ctl.watchdog_once()
    assert result["state"] == "released"
    assert ctl.status()["state"] == "unlocked"


def test_session_events_distinguish_departure_from_emergency_and_are_once():
    events = []
    backend = Backend()
    clock = Clock()
    ctl, _ = controller(
        backend=backend,
        clock=clock,
        on_event=events.append,
        wall_clock=lambda: 1_800_000_000.0,
    )
    start(ctl)
    ctl.observe(sample("present", clock(), "p"))
    ctl.observe(sample("absent", clock(), "a1"))
    clock.advance(30.01)
    ctl.observe(sample("absent", clock(), "a2"))
    ctl.stop("duplicate-stop")

    assert [event["event"] for event in events] == ["armed", "released"]
    assert events[0]["session_id"] == events[1]["session_id"]
    assert events[1]["departure_verified"] is True
    assert events[1]["reason"] == "continuous_absence"
    assert "exercise_completed" not in events[1]

    emergency_events = []
    ctl, _ = controller(backend=Backend(), on_event=emergency_events.append)
    start(ctl)
    ctl.emergency_release()
    assert emergency_events[-1]["departure_verified"] is False
    assert emergency_events[-1]["reason"] == "emergency_shortcut"


def test_event_persistence_errors_do_not_interrupt_release():
    backend = Backend()

    def broken_sink(_event):
        raise OSError("storage unavailable")

    ctl, clock = controller(backend=backend, on_event=broken_sink)
    start(ctl)
    ctl.observe(sample("present", clock(), "p"))
    ctl.emergency_release()

    assert not backend.blocked
    assert ctl.status()["state"] == "unlocked"
