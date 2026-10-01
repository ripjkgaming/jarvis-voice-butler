"""Hermetic tests for src/notify.py (no notify-send, no TTS, no network)."""

from __future__ import annotations

import time

import pytest

import claude_cli
import notify


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    notify._seen.clear()
    monkeypatch.setattr(notify, "_input_state", lambda: "active")
    monkeypatch.setattr(notify, "_in_quiet", lambda now: False)
    monkeypatch.setattr(notify, "_log", lambda detail: None)
    yield
    notify._seen.clear()


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis-home"))


@pytest.mark.parametrize(
    ("state", "allow_speech", "quiet", "expected"),
    [
        ("active", True, False, "announce"),
        ("idle", True, False, "notification"),
        ("unknown", True, False, "notification"),
        ("active", False, False, "notification"),
        ("active", True, True, "notification"),
        ("idle", False, True, "notification"),
    ],
)
def test_choose_route_table(state, allow_speech, quiet, expected) -> None:
    assert (
        notify.choose_route(state, allow_speech=allow_speech, quiet=quiet) == expected
    )


def test_send_announces_via_speaker_when_active() -> None:
    calls: list = []

    def speaker(text, **kw):
        calls.append((text, kw))
        return True

    result = notify.send("hello sir", speaker=speaker, toaster=lambda *a: True)
    assert result["route"] == "announce"
    assert calls and calls[0][0] == "hello sir"


@pytest.mark.parametrize("state", ["idle", "unknown"])
def test_send_uses_toaster_when_not_active(state, monkeypatch) -> None:
    monkeypatch.setattr(notify, "_input_state", lambda: state)
    toasted: list = []
    spoke: list = []
    result = notify.send(
        "hello sir",
        speaker=lambda *a, **k: spoke.append(a) or True,
        toaster=lambda *a: toasted.append(a) or True,
    )
    assert result["route"] == "notification"
    assert toasted and not spoke


def test_send_uses_toaster_in_quiet_hours(monkeypatch) -> None:
    monkeypatch.setattr(notify, "_in_quiet", lambda now: True)
    toasted: list = []
    result = notify.send(
        "hello sir",
        speaker=lambda *a, **k: True,
        toaster=lambda *a: toasted.append(a) or True,
    )
    assert result["route"] == "notification"
    assert toasted


def test_send_allow_speech_false_never_announces() -> None:
    toasted: list = []
    result = notify.send(
        "hello sir",
        allow_speech=False,
        speaker=lambda *a, **k: True,
        toaster=lambda *a: toasted.append(a) or True,
    )
    assert result["route"] == "notification"
    assert toasted


def test_send_speaker_false_falls_back_to_toaster() -> None:
    toasted: list = []
    result = notify.send(
        "hello sir",
        speaker=lambda *a, **k: False,
        toaster=lambda *a: toasted.append(a) or True,
    )
    assert result["route"] == "notification"
    assert toasted


def test_send_speaker_raising_never_raises() -> None:
    def bad_speaker(*a, **k):
        raise RuntimeError("tts down")

    toasted: list = []
    result = notify.send(
        "hello sir", speaker=bad_speaker, toaster=lambda *a: toasted.append(a) or True
    )
    assert result["route"] == "notification"
    assert toasted


def test_send_dedupes_same_fingerprint_within_hour() -> None:
    kw = {
        "kind": "email-draft",
        "source": "mail",
        "fingerprint": "draft:abc",
        "speaker": lambda *a, **k: True,
        "toaster": lambda *a: True,
    }
    assert notify.send("draft ready", now=1_000.0, **kw)["route"] in (
        "announce",
        "notification",
    )
    assert notify.send("draft ready", now=1_000.0 + 3_599.0, **kw)["route"] == "deduped"
    assert (
        notify.send("different body", now=1_000.0 + 3_599.0, **kw)["route"] == "deduped"
    )
    assert notify.send("draft ready", fingerprint="draft:other", now=1_100.0)[
        "route"
    ] in ("announce", "notification")


def test_send_dedupe_window_expires() -> None:
    kw = {
        "fingerprint": "fp1",
        "speaker": lambda *a, **k: True,
        "toaster": lambda *a: True,
    }
    assert notify.send("hi", now=0.0, **kw)["route"] != "deduped"
    assert notify.send("hi", now=3_601.0, **kw)["route"] != "deduped"


def test_send_empty_text_returns_empty() -> None:
    assert notify.send("   ")["route"] == "empty"
    assert notify.send("")["route"] == "empty"


def test_toast_maps_urgent_to_critical(monkeypatch) -> None:
    argv: list = []

    class _Proc:
        returncode = 0

    def fake_run(cmd, **kw):
        argv.append(cmd)
        return _Proc()

    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    assert notify._toast("T", "body", "urgent") is True
    assert argv[0][argv[0].index("-u") + 1] == "critical"


def test_toast_maps_info_to_normal(monkeypatch) -> None:
    argv: list = []

    class _Proc:
        returncode = 0

    def fake_run(cmd, **kw):
        argv.append(cmd)
        return _Proc()

    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    assert notify._toast("T", "body", "info") is True
    assert argv[0][argv[0].index("-u") + 1] == "normal"


def test_toast_missing_binary_returns_false(monkeypatch) -> None:
    def missing(*a, **k):
        raise FileNotFoundError("no notify-send")

    monkeypatch.setattr(notify.subprocess, "run", missing)
    assert notify._toast("T", "body", "info") is False


# --- queue roundtrip and cap ---


def test_enqueue_load_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis-home"))
    item = {"ts": 1_000.0, "kind": "email", "text": "hello"}
    assert notify.enqueue(item) is True
    loaded = notify.load_queue()
    assert len(loaded) == 1
    assert loaded[0]["text"] == "hello"


def test_queue_50_item_cap(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis-home"))
    for i in range(55):
        notify.enqueue({"ts": float(i), "kind": "x", "text": f"msg {i}"})
    loaded = notify.load_queue()
    assert len(loaded) == 50
    assert loaded[0]["text"] == "msg 5"


def test_queue_empty_load_returns_list(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis-home"))
    assert notify.load_queue() == []


# --- idle send enqueues and toasts; active send speaks ---


def test_idle_send_enqueues_and_toasts(monkeypatch) -> None:
    monkeypatch.setattr(notify, "_input_state", lambda: "idle")
    toasted: list = []
    enqueued: list = []
    monkeypatch.setattr(notify, "enqueue", lambda item: enqueued.append(item) or True)
    result = notify.send("hello sir", toaster=lambda *a: toasted.append(a) or True)
    assert result["route"] == "notification"
    assert toasted
    assert len(enqueued) == 1
    assert enqueued[0]["kind"] == "info"


def test_active_send_speaks_and_does_not_enqueue(monkeypatch) -> None:
    monkeypatch.setattr(notify, "_input_state", lambda: "active")
    spoke: list = []
    enqueued: list = []
    monkeypatch.setattr(notify, "enqueue", lambda item: enqueued.append(item) or True)
    result = notify.send(
        "hello sir",
        speaker=lambda *a, **k: spoke.append(a) or True,
        toaster=lambda *a: True,
    )
    assert result["route"] == "announce"
    assert spoke
    assert not enqueued


def test_allow_speech_false_never_enqueues(monkeypatch) -> None:
    enqueued: list = []
    monkeypatch.setattr(notify, "enqueue", lambda item: enqueued.append(item) or True)
    result = notify.send(
        "hello sir",
        allow_speech=False,
        toaster=lambda *a: True,
    )
    assert result["route"] == "notification"
    assert not enqueued


def test_queue_false_skips_enqueue(monkeypatch) -> None:
    monkeypatch.setattr(notify, "_input_state", lambda: "idle")
    monkeypatch.setattr(notify, "_in_quiet", lambda now: False)
    enqueued: list = []
    monkeypatch.setattr(notify, "enqueue", lambda item: enqueued.append(item) or True)
    result = notify.send(
        "hello sir",
        queue=False,
        toaster=lambda *a: True,
    )
    assert result["route"] == "notification"
    assert not enqueued


# --- drain ---


def test_drain_each_mode_speaks_one_by_one(monkeypatch) -> None:
    items = [
        {
            "ts": 1_000.0,
            "text": "a",
            "speak_text": "a",
            "kind": "x",
            "source": "s",
            "title": "T",
            "urgency": "info",
        },
        {
            "ts": 1_000.0,
            "text": "b",
            "speak_text": "b",
            "kind": "x",
            "source": "s",
            "title": "T",
            "urgency": "info",
        },
    ]
    monkeypatch.setattr(notify, "load_queue", lambda: items)
    spoke: list = []
    result = notify.drain(now=2_000.0, speaker=lambda *a, **k: spoke.append(a) or True)
    assert result["spoken"] == 2
    assert result["mode"] == "each"
    assert len(spoke) == 2


def test_drain_summary_mode_with_fake_summarizer(monkeypatch) -> None:
    items = [
        {
            "ts": 1_000.0,
            "text": f"msg {i}",
            "speak_text": f"msg {i}",
            "kind": "x",
            "source": "s",
            "title": "T",
            "urgency": "info",
        }
        for i in range(5)
    ]
    monkeypatch.setattr(notify, "load_queue", lambda: items)
    calls: list = []

    def fake_summarizer(items, runner=None):
        calls.append(items)
        return "summary line"

    spoke: list = []
    result = notify.drain(
        now=2_000.0,
        speaker=lambda *a, **k: spoke.append(a) or True,
        summarizer=fake_summarizer,
    )
    assert result["spoken"] == 5
    assert result["mode"] == "summary"
    assert len(calls) == 1
    assert len(calls[0]) == 5


def test_drain_speaker_false_keeps_items(monkeypatch) -> None:
    items = [
        {
            "ts": 1_000.0,
            "text": "a",
            "speak_text": "a",
            "kind": "x",
            "source": "s",
            "title": "T",
            "urgency": "info",
        },
    ]
    monkeypatch.setattr(notify, "load_queue", lambda: items)
    result = notify.drain(now=2_000.0, speaker=lambda *a, **k: False)
    assert result["spoken"] == 0
    assert result["mode"] == "each"
    assert len(notify.load_queue()) == 1


def test_drain_expiry_drops_old_items(monkeypatch) -> None:
    items = [
        {
            "ts": 1_000.0,
            "text": "old",
            "speak_text": "old",
            "kind": "x",
            "source": "s",
            "title": "T",
            "urgency": "info",
        },
        {
            "ts": 9_000_000_000.0,
            "text": "new",
            "speak_text": "new",
            "kind": "x",
            "source": "s",
            "title": "T",
            "urgency": "info",
        },
    ]
    monkeypatch.setattr(notify, "load_queue", lambda: items)
    spoke: list = []
    result = notify.drain(
        now=2_000_000_000.0, speaker=lambda *a, **k: spoke.append(a) or True
    )
    assert result["spoken"] == 1
    assert result["mode"] == "each"
    assert spoke[0][0] == "new"


def test_drain_empty_queue(monkeypatch) -> None:
    monkeypatch.setattr(notify, "load_queue", lambda: [])
    result = notify.drain(now=1_000.0)
    assert result["spoken"] == 0
    assert result["mode"] == "none"


# --- summarise ---


def test_summarise_uses_haiku_model(monkeypatch) -> None:
    calls: list = []

    def fake_reply(prompt, **kw):
        calls.append(kw)
        return ("Sir, you have messages.", None)

    monkeypatch.setattr(claude_cli, "claude_reply", fake_reply)
    items = [{"kind": "email", "text": "hello"}]
    result = notify.summarise(items)
    assert result == "Sir, you have messages."
    assert calls[0].get("model") == "claude-haiku-4-5"


def test_summarise_env_overrides_model(monkeypatch) -> None:
    calls: list = []

    def fake_reply(prompt, **kw):
        calls.append(kw)
        return ("summary", None)

    monkeypatch.setenv("JARVIS_NOTIFY_MODEL", "claude-opus-4-6")
    monkeypatch.setattr(claude_cli, "claude_reply", fake_reply)
    items = [{"kind": "email", "text": "hello"}]
    notify.summarise(items)
    assert calls[0].get("model") == "claude-opus-4-6"


def test_summarise_fallback_on_warning(monkeypatch) -> None:
    monkeypatch.setattr(
        claude_cli, "claude_reply", lambda *a, **k: ("reply", "rate limit")
    )
    items = [{"kind": "x", "text": "y"}]
    result = notify.summarise(items)
    assert "1 notification" in result


def test_summarise_fallback_on_exception(monkeypatch) -> None:
    monkeypatch.setattr(
        claude_cli,
        "claude_reply",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    items = [{"kind": "x", "text": "y"}]
    result = notify.summarise(items)
    assert "1 notification" in result


# --- Announcer ---


def test_announcer_empty_queue(monkeypatch) -> None:
    monkeypatch.setattr(notify, "load_queue", lambda: [])
    a = notify.Announcer()
    assert a.tick(now=1_000.0) == "empty"


def test_announcer_quiet_hours(monkeypatch) -> None:
    monkeypatch.setattr(notify, "load_queue", lambda: [{"ts": 1_000.0}])
    monkeypatch.setattr(notify, "_in_quiet", lambda now: True)
    a = notify.Announcer()
    assert a.tick(now=1_000.0) == "quiet-hours"


def test_announcer_idle_state(monkeypatch) -> None:
    monkeypatch.setattr(notify, "load_queue", lambda: [{"ts": 1_000.0}])
    monkeypatch.setattr(notify, "_in_quiet", lambda now: False)
    monkeypatch.setattr(notify, "_input_state", lambda: "idle")
    a = notify.Announcer()
    assert a.tick(now=1_000.0) == "waiting-idle"


def test_announcer_drains_on_3rd_consecutive_recent_poll(monkeypatch) -> None:
    monkeypatch.setattr(notify, "load_queue", lambda: [{"ts": 1_000.0}])
    monkeypatch.setattr(notify, "_in_quiet", lambda now: False)
    monkeypatch.setattr(notify, "_input_state", lambda: "active")
    drained: list = []

    def fake_drain(now=None):
        drained.append(now)

    a = notify.Announcer()
    assert (
        a.tick(now=1_000.0, recent=True, do_drain=fake_drain)
        == "waiting-sustained-input"
    )
    assert (
        a.tick(now=1_005.0, recent=True, do_drain=fake_drain)
        == "waiting-sustained-input"
    )
    assert a.tick(now=1_010.0, recent=True, do_drain=fake_drain) == "drained"
    assert len(drained) == 1


def test_announcer_streak_resets_on_recent_false(monkeypatch) -> None:
    monkeypatch.setattr(notify, "load_queue", lambda: [{"ts": 1_000.0}])
    monkeypatch.setattr(notify, "_in_quiet", lambda now: False)
    monkeypatch.setattr(notify, "_input_state", lambda: "active")
    a = notify.Announcer()
    assert a.tick(now=1_000.0, recent=True) == "waiting-sustained-input"
    assert a.tick(now=1_005.0, recent=False) == "waiting-sustained-input"
    assert a.tick(now=1_010.0, recent=True) == "waiting-sustained-input"


def test_announcer_recent_none_reads_input_idle(monkeypatch) -> None:
    monkeypatch.setattr(notify, "load_queue", lambda: [{"ts": 1_000.0}])
    monkeypatch.setattr(notify, "_in_quiet", lambda now: False)
    monkeypatch.setattr(notify, "_input_state", lambda: "active")
    a = notify.Announcer()
    assert a.tick(now=1_000.0, recent=None) == "waiting-sustained-input"


def test_start_thread_runs_and_stops(monkeypatch) -> None:
    import threading

    ticks: list = []
    monkeypatch.setattr(
        notify,
        "Announcer",
        lambda: type("A", (), {"tick": lambda self, **kw: ticks.append(kw)})(),
    )
    thread, stop = notify.start_thread(interval=0.05)
    assert isinstance(thread, threading.Thread)
    assert isinstance(stop, threading.Event)
    time.sleep(0.15)
    stop.set()
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert len(ticks) >= 2


# --- DND: past 22:30 AND idle 30+ minutes ---


def test_in_quiet_delegates_to_dnd(monkeypatch) -> None:
    import dnd

    monkeypatch.undo()  # drop the autouse _in_quiet stub
    monkeypatch.setattr(dnd, "active", lambda now: True)
    assert notify._in_quiet(1.0) is True
    monkeypatch.setattr(dnd, "active", lambda now: False)
    assert notify._in_quiet(1.0) is False
