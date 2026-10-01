"""Hermetic tests for src/wa_headless.py (no flatpak, no network)."""

from __future__ import annotations

import wa_headless


def test_launch_is_headless_with_debug_port() -> None:
    joined = " ".join(wa_headless.LAUNCH)
    assert "QT_QPA_PLATFORM=offscreen" in joined
    assert "--remote-debugging-port=9223" in joined


def test_ensure_already_up_no_spawn(monkeypatch) -> None:
    monkeypatch.setattr(wa_headless.wa, "alive", lambda: True)
    monkeypatch.setattr(wa_headless.wa, "page_present", lambda: True)

    def no_spawn(*a, **k):
        raise AssertionError("must not spawn when WhatsApp is up")

    assert wa_headless.ensure(spawn=no_spawn, sleep=lambda s: None) is True


def test_ensure_spawns_then_true_once_probes_flip(monkeypatch) -> None:
    calls = {"alive": 0}

    def fake_alive() -> bool:
        calls["alive"] += 1
        return calls["alive"] > 1  # down on the pre-spawn check, up after

    monkeypatch.setattr(wa_headless.wa, "alive", fake_alive)
    monkeypatch.setattr(wa_headless.wa, "page_present", lambda: True)
    spawns: list = []

    def fake_spawn(argv, **kw):
        spawns.append((argv, kw))
        return object()

    assert (
        wa_headless.ensure(spawn=fake_spawn, sleep=lambda s: None, timeout_s=5.0)
        is True
    )
    assert len(spawns) == 1
    assert spawns[0][0] == wa_headless.LAUNCH


def test_ensure_timeout_false(monkeypatch) -> None:
    monkeypatch.setattr(wa_headless.wa, "alive", lambda: False)
    monkeypatch.setattr(wa_headless.wa, "page_present", lambda: False)
    spawns: list = []
    sleeps: list = []

    def fake_spawn(argv, **kw):
        spawns.append(argv)
        return object()

    assert (
        wa_headless.ensure(spawn=fake_spawn, sleep=sleeps.append, timeout_s=0.05)
        is False
    )
    assert len(spawns) == 1
    assert sleeps != []


def test_ensure_spawn_raises_false(monkeypatch) -> None:
    monkeypatch.setattr(wa_headless.wa, "alive", lambda: False)
    monkeypatch.setattr(wa_headless.wa, "page_present", lambda: False)

    def bad_spawn(*a, **k):
        raise RuntimeError("no flatpak here")

    assert wa_headless.ensure(spawn=bad_spawn, sleep=lambda s: None) is False
