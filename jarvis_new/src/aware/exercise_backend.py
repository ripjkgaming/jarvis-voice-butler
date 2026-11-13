"""Fail-open input-control backend for Always Aware exercise breaks.

This module deliberately provides no input grab. A platform backend must
prove its global emergency shortcut and cross-monitor input blocking before
it can be used by :class:`aware.exercise.ExerciseController`.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Callable

EMERGENCY_SHORTCUT = "Ctrl+Alt+Shift+W"


def inspect_desktop_session(
    *, environ: dict[str, str] | None = None, config_dir: Path | None = None
) -> dict[str, object]:
    """Return read-only desktop/input capability evidence for this session."""
    env = os.environ if environ is None else environ
    config = config_dir or Path(env.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    session_type = str(env.get("XDG_SESSION_TYPE", "unknown")).lower()
    desktop = str(env.get("XDG_CURRENT_DESKTOP", env.get("DESKTOP_SESSION", "unknown")))
    shortcut_file = config / "kglobalshortcutsrc"
    try:
        shortcut_text = shortcut_file.read_text()
        shortcut_configured = (
            "[services][jarvis-watch-stop.desktop]" in shortcut_text
            and f"_launch={EMERGENCY_SHORTCUT}" in shortcut_text
        )
    except OSError:
        shortcut_configured = False
    event_nodes = list(Path("/dev/input").glob("event*"))
    accessible_nodes = [
        node for node in event_nodes if os.access(node, os.R_OK | os.W_OK)
    ]
    complete_device_access = len(event_nodes) == len(accessible_nodes)
    reason = "no verified fail-open desktop input backend is installed"
    if event_nodes and not complete_device_access:
        reason = (
            "input device access is incomplete: "
            f"{len(accessible_nodes)} of {len(event_nodes)} event devices are accessible"
        )
    elif session_type == "wayland":
        reason = "Wayland whole-seat input interception is not verified"
    return {
        "session_type": session_type,
        "desktop": desktop,
        "wayland_display": bool(env.get("WAYLAND_DISPLAY")),
        "x11_display": bool(env.get("DISPLAY")),
        "watch_mode_shortcut_configured": shortcut_configured,
        "event_devices": len(event_nodes),
        "accessible_event_devices": len(accessible_nodes),
        "complete_event_device_access": complete_device_access,
        "uinput_accessible": os.access("/dev/uinput", os.R_OK | os.W_OK),
        "evdev_installed": importlib.util.find_spec("evdev") is not None,
        "exercise_emergency_shortcut_verified": False,
        "exercise_input_blocking_verified": False,
        "exercise_fail_open_lease_verified": False,
        "input_blocking_available": False,
        "reason": reason,
    }


class DesktopExerciseBackend:
    """Default inert backend; it never locks the desktop.

    Existing KDE shortcuts and Watch Mode controls are not proof of an
    exercise-specific release path, so this backend always fails closed
    before acquiring any input control.
    """

    def __init__(self, *, evidence: dict[str, object] | None = None) -> None:
        self.evidence = evidence or inspect_desktop_session()

    def verify_emergency_release(self, release_callback: Callable[[], object]) -> bool:
        """Refuse to claim that a separately dispatched global key works."""
        _ = release_callback
        return False

    def verify_blocking(self) -> bool:
        """No blocking method is present until a platform backend is vetted."""
        return False

    def verify_fail_open_lease(self) -> bool:
        """No external watchdog or expiring backend lease is implemented."""
        return False

    def refresh_safety_lease(self) -> bool:
        """A missing lease cannot be refreshed; callers must release input."""
        return False

    def block(self) -> bool:
        """Never block input from this unverified backend."""
        return False

    def release(self, reason: str = "release") -> None:
        """No input state is held by the inert backend."""
        _ = reason
