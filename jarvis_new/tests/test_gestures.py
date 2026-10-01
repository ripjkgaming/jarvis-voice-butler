"""Tests for Hollow Hands (src/gestures.py). Hermetic: scripted
recognizer output and a fake wake socket — no camera, no models."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

import gestures


@pytest.fixture()
def home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis"))


def test_debounce_hold_and_cooldown() -> None:
    d = gestures.GestureDebounce(hold_s=0.8, cooldown_s=3.0)
    assert d.update("Open_Palm", 0.0) is None
    assert d.update("Open_Palm", 0.5) is None
    assert d.update("Open_Palm", 0.9) == "Open_Palm"  # held 0.9 s
    assert d.update("Open_Palm", 1.0) is None  # fresh hold required
    assert d.update("Open_Palm", 2.0) is None  # hold ok, cooldown blocks
    assert d.update("Open_Palm", 4.0) == "Open_Palm"  # hold + cooldown passed


def test_debounce_resets_on_change_and_gap() -> None:
    d = gestures.GestureDebounce(hold_s=0.8, cooldown_s=3.0)
    assert d.update("Open_Palm", 0.0) is None
    assert d.update("Victory", 0.5) is None  # switch restarts the hold
    assert d.update("Victory", 1.0) is None
    assert d.update("Victory", 1.4) == "Victory"
    d2 = gestures.GestureDebounce(hold_s=0.8)
    assert d2.update("Open_Palm", 0.0) is None
    assert d2.update(None, 5.0) is None  # hand gone: hold forgotten
    assert d2.update("Open_Palm", 5.1) is None


def test_fist_needs_long_hold() -> None:
    d = gestures.GestureDebounce()
    assert d.update("Closed_Fist", 0.0) is None
    assert d.update("Closed_Fist", 1.5) is None
    assert d.update("Closed_Fist", 2.1) == "Closed_Fist"


def test_load_mapping_defaults_and_override(home) -> None:
    assert gestures.load_mapping()["Open_Palm"] == "mic_toggle"
    gestures.config_path().parent.mkdir(parents=True, exist_ok=True)
    gestures.config_path().write_text(json.dumps({"Victory": "confirm"}))
    assert gestures.load_mapping()["Victory"] == "confirm"
    gestures.config_path().write_text(
        json.dumps({"Victory": "explode", "Nope": "talk"})
    )
    assert gestures.load_mapping()["Victory"] == "talk"  # invalid ignored
    gestures.config_path().write_text("not json")
    assert gestures.load_mapping()["Victory"] == "talk"


def test_top_gesture() -> None:
    good = SimpleNamespace(
        gestures=[[SimpleNamespace(category_name="Victory", score=0.9)]]
    )
    assert gestures.top_gesture(good) == "Victory"
    assert gestures.top_gesture(SimpleNamespace(gestures=[])) is None
    assert (
        gestures.top_gesture(
            SimpleNamespace(
                gestures=[[SimpleNamespace(category_name="None", score=0.9)]]
            )
        )
        is None
    )
    assert gestures.top_gesture(object()) is None


def test_send_wake_no_listener(home) -> None:
    assert gestures.send_wake({"status": True}) is None


def _monitor(script, **kw):
    """Monitor fed by a scripted gesture list, one per poll."""
    it = iter(script)
    clock = [100.0]
    kw.setdefault("frame", lambda: np.zeros((60, 80, 3), dtype=np.uint8))
    kw.setdefault("recognize", lambda frame: next(it, None))
    kw.setdefault("now", lambda: clock[0])
    mon = gestures.GestureMonitor(**kw)
    return mon, clock


def test_monitor_fires_talk_and_confirm(home) -> None:
    fired: list = []
    mon, clock = _monitor(
        ["Victory"] * 12,
        sender=lambda payload: fired.append(payload) or {"ok": True},
        speaker=lambda text, source: None,
        debounce=gestures.GestureDebounce(hold_s=0.8, cooldown_s=3.0),
    )
    for _ in range(12):
        mon.poll()
        clock[0] += 0.125  # 8 fps
    assert {"talk": True} in fired


def test_monitor_confirm_logs(home, monkeypatch: pytest.MonkeyPatch) -> None:
    import system

    logged: list = []
    monkeypatch.setattr(
        system, "log_action", lambda cat, detail: logged.append((cat, detail))
    )
    mon, clock = _monitor(
        ["Thumb_Up"] * 12,
        sender=lambda payload: {"ok": True},
        speaker=lambda text, source: None,
        debounce=gestures.GestureDebounce(hold_s=0.8, cooldown_s=3.0),
    )
    for _ in range(12):
        mon.poll()
        clock[0] += 0.125
    assert any(cat == "gesture" and "confirm" in detail for cat, detail in logged)


def test_monitor_mic_toggle(home) -> None:
    import speak

    sent: list = []
    said: list = []
    mon, clock = _monitor(
        ["Open_Palm"] * 12,
        sender=lambda payload: sent.append(payload) or {"ok": True},
        speaker=lambda text, source: said.append(text),
        debounce=gestures.GestureDebounce(hold_s=0.8, cooldown_s=3.0),
    )
    # Fake an unmuted mic: toggle must ask for mute.
    import unittest.mock as mock

    with mock.patch.object(speak, "wake_status", return_value={"muted": False}):
        for _ in range(12):
            mon.poll()
            clock[0] += 0.125
    assert {"mute": True} in sent
    assert said


def test_monitor_fist_stops(home) -> None:
    mon, clock = _monitor(
        ["Closed_Fist"] * 30,
        sender=lambda payload: {"ok": True},
        speaker=lambda text, source: None,
        debounce=gestures.GestureDebounce(),
    )
    for _ in range(30):
        mon.poll()
        clock[0] += 0.125
        if mon.should_stop:
            break
    assert mon.should_stop
