"""Startup sequencing must not duplicate apps or overlap opening animations."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "session_launcher",
    Path(__file__).parents[1] / "desktop/session-launcher/launcher.py",
)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def window(app, ident="w1"):
    return {
        "id": ident,
        "app": app["identities"][0],
        "caption": app.get("title", ""),
        "geometry": {"x": 40, "y": 50, "width": 1200, "height": 800},
    }


def test_order_waits_for_window_placement_and_animation():
    clock, launched = [0.0], []
    sequence = launcher.Sequence(
        launcher.DEFAULT, "primary", launched.append, lambda: clock[0]
    )
    snap = {"windows": [], "busy": False}
    assert sequence.step(snap) == {}
    assert [a["name"] for a in launched] == ["Brave"]
    clock[0] = 2
    sequence.step(snap)
    assert len(launched) == 1
    snap["windows"] = [window(launcher.DEFAULT["apps"][0])]
    command = sequence.step(snap)
    assert command["place"] and command["primary"] == "primary"
    snap["placement"] = {
        "id": "w1",
        "area": {"x": 0, "y": 0, "width": 1920, "height": 1040},
    }
    sequence.step(snap)
    for t in range(3, 8):
        clock[0] = t
        snap["busy"] = True
        sequence.step(snap)
        assert len(launched) == 1
    snap["busy"] = False
    clock[0] = 8
    sequence.step(snap, overlay_busy=True)
    assert len(launched) == 1
    sequence.step(snap)
    clock[0] = 8.5
    sequence.step(snap)
    sequence.step(snap)
    assert [a["name"] for a in launched] == ["Brave", "ChatGPT"]


def test_existing_and_minimized_windows_never_launch_again():
    launched = []
    sequence = launcher.Sequence(launcher.DEFAULT, "primary", launched.append)
    snap = {
        "windows": [window(a, str(i)) for i, a in enumerate(launcher.DEFAULT["apps"])],
        "busy": False,
    }
    snap["windows"][0]["minimized"] = True
    snap["windows"][0]["hidden"] = True
    for _ in range(4):
        result = sequence.step(snap)
    assert result == {"stop": True}
    assert not launched
    assert len(sequence.results) == 3


def test_timeout_stops_without_launching_next_app():
    clock, launched = [0.0], []
    seq = launcher.Sequence(
        launcher.DEFAULT, "primary", launched.append, lambda: clock[0]
    )
    snap = {"windows": [], "busy": False}
    seq.step(snap)
    clock[0] = 46
    assert seq.step(snap) == {"stop": True}
    assert seq.error and len(launched) == 1
    assert seq.step(snap) == {"stop": True}


def test_title_collisions_and_jarvis_auxiliary_windows():
    brave, _, jarvis = launcher.DEFAULT["apps"]
    assert not launcher.matches(brave, {"app": "other-app", "caption": "Brave"})
    assert not launcher.matches(
        jarvis, {"resourceClass": "Jarvis-shell", "caption": "Jarvis — Drafts"}
    )
    assert launcher.matches(
        jarvis, {"resourceClass": "Jarvis-shell", "caption": "Jarvis"}
    )
    assert not launcher.matches(
        jarvis, {"resourceClass": "Jarvis-shell", "caption": "Jarvis", "hidden": True}
    )


def test_primary_uses_priority_not_screen_order_or_mouse_location():
    assert (
        launcher.primary_output(
            {
                "outputs": [
                    {
                        "name": "laptop",
                        "connected": True,
                        "enabled": True,
                        "priority": 2,
                    },
                    {"name": "off", "connected": True, "enabled": False, "priority": 1},
                    {"name": "main", "connected": True, "enabled": True, "priority": 1},
                ]
            }
        )
        == "main"
    )


def test_clipped_geometry_does_not_advance():
    clock = [0.0]
    seq = launcher.Sequence(
        launcher.DEFAULT, "primary", lambda app: None, lambda: clock[0]
    )
    snap = {"windows": [], "busy": False}
    seq.step(snap)
    snap["windows"] = [window(launcher.DEFAULT["apps"][0])]
    seq.step(snap)
    snap["windows"][0]["geometry"]["x"] = 1800
    snap["placement"] = {
        "id": "w1",
        "area": {"x": 0, "y": 0, "width": 1920, "height": 1040},
    }
    seq.step(snap)
    assert seq.phase == "placing"
    clock[0] = 11
    assert seq.step(snap) == {"stop": True}
    assert "fit" in seq.error
