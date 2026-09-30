"""Sidecar wiring for the bridge: mail + WhatsApp watchers (no real threads)."""

from __future__ import annotations

import importlib
import sys

sys.path.insert(0, "src")

import bridge


def test_sidecar_threads_include_mail_and_wa() -> None:
    mapping = dict(bridge.SIDECAR_THREADS)
    assert mapping.get("mail_watch_job") == "JARVIS_MAIL_WATCH"
    assert mapping.get("wa_autoreply") == "JARVIS_WA_WATCH"


def test_sidecar_threads_include_notify_queue() -> None:
    assert ("notify", "JARVIS_NOTIFY_QUEUE") in bridge.SIDECAR_THREADS


def test_start_sidecar_threads_skips_disabled_watchers(monkeypatch) -> None:
    imported: list[str] = []
    started: list[str] = []

    class _FakeMod:
        def __init__(self, name: str) -> None:
            self._name = name

        def start_thread(self):
            started.append(self._name)
            return (None, None)

    def fake_import(name: str):
        imported.append(name)
        return _FakeMod(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    monkeypatch.setenv("JARVIS_MAIL_WATCH", "0")
    monkeypatch.setenv("JARVIS_WA_WATCH", "0")
    bridge.start_sidecar_threads()
    assert "mail_watch_job" not in imported
    assert "wa_autoreply" not in imported
    assert "mail_watch_job" not in started
    assert "wa_autoreply" not in started


def test_start_sidecar_threads_starts_enabled_watchers(monkeypatch) -> None:
    imported: list[str] = []

    class _FakeMod:
        def __init__(self, name: str) -> None:
            self._name = name

        def start_thread(self):
            imported.append(self._name)
            return (None, None)

    monkeypatch.setattr(importlib, "import_module", lambda name: _FakeMod(name))
    monkeypatch.setenv("JARVIS_MAIL_WATCH", "1")
    monkeypatch.setenv("JARVIS_WA_WATCH", "1")
    bridge.start_sidecar_threads()
    assert "mail_watch_job" in imported
    assert "wa_autoreply" in imported
