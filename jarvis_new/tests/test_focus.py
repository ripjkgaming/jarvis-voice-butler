"""Tests for src/focus.py: matching, drift machine, persistence, look."""

import json

import pytest

import focus
from focus import FocusTracker


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    return tmp_path


class Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def step(self, s):
        self.t += s
        return self.t


def make_tracker(clock, box, tab=None, speaks=None):
    active_fn = lambda: dict(box["where"]) if box["where"] else None  # noqa: E731
    tab_fn = lambda: dict(tab["tab"]) if tab and tab["tab"] else None  # noqa: E731
    return FocusTracker(
        now=clock.now,
        active_fn=active_fn,
        tab_fn=tab_fn,
        speak_fn=(speaks.append if speaks is not None else (lambda s: None)),
    )


KONSOLE = {
    "title": "~ : claude — Konsole",
    "app": "org.kde.konsole",
    "pid": 1,
    "id": "k1",
}
BRAVE = {
    "title": "Chat - Claude - Brave",
    "app": "brave-browser",
    "pid": 2,
    "id": "b1",
}
CLAUDE_TAB = {"url": "https://claude.ai/chat/abc123", "title": "Chat - Claude"}


# --- pure helpers ------------------------------------------------------------


def test_url_prefix_and_matches():
    assert (
        focus.url_prefix("https://claude.ai/chat/abc?x=1")
        == "https://claude.ai/chat/abc"
    )
    assert focus.url_prefix("not a url") == ""
    assert focus.url_matches("https://claude.ai/chat/abc", "https://claude.ai/chat/abc")
    assert focus.url_matches(
        "https://claude.ai/chat/abc", "https://claude.ai/chat/abc/def"
    )
    assert not focus.url_matches(
        "https://claude.ai/chat/abc", "https://gemini.google.com/x"
    )


def test_title_matches():
    assert focus.title_matches("Chat - Claude - Brave", "Chat - Claude - Brave")
    assert focus.title_matches("Chat - Claude - Brave", "Chat - Claude (2) - Brave")
    assert not focus.title_matches("Chat - Claude", "Inbox - Gmail")
    assert not focus.title_matches("", "Inbox")


def test_is_brave():
    assert focus.is_brave("brave-browser")
    assert focus.is_brave("something", "Docs - Brave")
    assert not focus.is_brave("org.kde.konsole", "~ : Konsole")


def test_pick_tab_prefers_visible():
    targets = [
        {
            "type": "page",
            "url": "https://a.example/",
            "title": "A",
            "webSocketDebuggerUrl": "ws://1",
        },
        {
            "type": "page",
            "url": "https://b.example/",
            "title": "B",
            "webSocketDebuggerUrl": "ws://2",
        },
        {"type": "iframe", "url": "https://a.example/f", "title": ""},
    ]
    assert focus.pick_tab(targets, {"ws://2": "visible"})["url"] == "https://b.example/"
    assert focus.pick_tab(targets)["url"] == "https://a.example/"
    assert focus.pick_tab([]) is None


def test_number_words():
    assert focus.number_word(45) == "forty-five"
    assert focus.number_word(5) == "five"
    assert focus.count_word(1, "drift") == "One drift"
    assert focus.count_word(2, "drift") == "Two drifts"


# --- locking -----------------------------------------------------------------


def test_lock_window(tmp_path):
    clock, box, speaks = Clock(), {"where": KONSOLE}, []
    tracker = make_tracker(clock, box, speaks=speaks)
    got = tracker.lock("window", 45, "thesis")
    assert got["ok"] and "Locked on" in got["say"] and "forty-five" in got["say"]
    assert tracker.session["target"]["kind"] == "window"
    assert json.loads((tmp_path / "home" / "focus" / "state.json").read_text())["label"]


def test_lock_auto_tab():
    clock, box = Clock(), {"where": dict(BRAVE)}
    tracker = make_tracker(clock, box, tab={"tab": dict(CLAUDE_TAB)}, speaks=[])
    got = tracker.lock("auto", 0, "")
    assert got["ok"] and tracker.session["target"]["kind"] == "tab"
    assert tracker.session["target"]["url_prefix"] == "https://claude.ai/chat/abc123"
    assert tracker.session["minutes"] is None  # open-ended


def test_lock_no_window():
    tracker = make_tracker(Clock(), {"where": None}, speaks=[])
    assert tracker.lock("window", 10)["ok"] is False


def test_lock_tab_needs_brave():
    tracker = make_tracker(Clock(), {"where": dict(KONSOLE)}, speaks=[])
    got = tracker.lock("tab", 10)
    assert got["ok"] is False and "Brave" in got["say"]


def test_lock_tab_cdp_down_falls_back_to_window():
    clock, box = Clock(), {"where": dict(BRAVE)}
    tracker = make_tracker(clock, box, tab={"tab": None}, speaks=[])
    got = tracker.lock("tab", 10, "docs")
    assert got["ok"] and tracker.session["target"]["kind"] == "window"


# --- drift machine -----------------------------------------------------------


def away_box():
    return {
        "where": {
            "title": "Inbox - Gmail - Brave",
            "app": "brave-browser",
            "pid": 2,
            "id": "b1",
            "tab_url": "https://mail.google.com/mail/u/0",
            "tab_title": "Inbox - Gmail",
        }
    }


def test_grace_single_drift_per_excursion():
    clock = Clock()
    box = {"where": dict(KONSOLE)}
    speaks: list = []
    tracker = make_tracker(clock, box, speaks=speaks)
    assert tracker.lock("window", 0, "thesis")["ok"]
    # 7 s away: no drift yet.
    box.update(away_box())
    for _ in range(7):
        tracker.poll(clock.step(1))
    assert tracker.session["drifts"] == 0 and speaks == []
    # 10 s away (past the 8 s grace): exactly one drift + one nudge.
    for _ in range(3):
        tracker.poll(clock.step(1))
    assert tracker.session["drifts"] == 1 and len(speaks) == 1
    assert "thesis" in speaks[0]
    # Still away: no second drift for the same excursion.
    for _ in range(30):
        tracker.poll(clock.step(1))
    assert tracker.session["drifts"] == 1
    # Back, then away again: second drift.
    box["where"] = dict(KONSOLE)
    tracker.poll(clock.step(1))
    box.update(away_box())
    for _ in range(10):
        tracker.poll(clock.step(1))
    assert tracker.session["drifts"] == 2 and len(speaks) == 2


def test_reminder_after_two_more_minutes():
    clock = Clock()
    box = {"where": dict(KONSOLE)}
    speaks: list = []
    tracker = make_tracker(clock, box, speaks=speaks)
    tracker.lock("window", 0, "thesis")
    box.update(away_box())
    for _ in range(10):
        tracker.poll(clock.step(1))
    assert len(speaks) == 1
    for _ in range(120):
        tracker.poll(clock.step(1))
    assert len(speaks) == 2 and "Still away" in speaks[1]


def test_drift_log_truncates(tmp_path):
    clock = Clock()
    box = {"where": dict(KONSOLE)}
    tracker = make_tracker(clock, box, speaks=[])
    tracker.lock("window", 0, "x" * 200)
    box["where"] = {
        "title": "T" * 500,
        "app": "A" * 200,
        "pid": 3,
        "id": "z",
        "tab_url": "U" * 500,
    }
    for _ in range(10):
        tracker.poll(clock.step(1))
    entry = tracker.session["drift_log"][0]
    assert len(entry["title"]) <= 120 and len(entry["app"]) <= 60


def test_timer_end_summarizes_and_logs_history(tmp_path):
    clock = Clock()
    box = {"where": dict(KONSOLE)}
    speaks: list = []
    tracker = make_tracker(clock, box, speaks=speaks)
    tracker.lock("window", 0.05, "thesis")  # 3 s timer
    for _ in range(5):
        tracker.poll(clock.step(1))
    assert tracker.session is None  # ended
    assert any("minutes done" in s or "Done" in s for s in speaks)
    lines = (tmp_path / "home" / "focus" / "history.jsonl").read_text().splitlines()
    entry = json.loads(lines[-1])
    assert entry["label"] == "thesis" and entry["reason"] == "done"


def test_summary_math_and_status():
    clock = Clock()
    box = {"where": dict(KONSOLE)}
    tracker = make_tracker(clock, box, speaks=[])
    tracker.lock("window", 0, "thesis")
    for _ in range(90):  # 90 s on task
        tracker.poll(clock.step(1))
    box.update(away_box())
    for _ in range(10):  # 10 s away -> one drift
        tracker.poll(clock.step(1))
    got = tracker.end("stopped")
    assert "One drift" in got["say"] and "90% on task" in got["say"]


def test_status_idle_and_active():
    tracker = make_tracker(Clock(), {"where": None}, speaks=[])
    assert tracker.status() == {"active": False}
    assert focus.status_say(None) == "We're not in focus mode, Sir."


def test_on_task_id_and_title():
    target = {
        "kind": "window",
        "app": "org.kde.konsole",
        "title": "~ : claude — Konsole",
        "win_id": "k1",
    }
    assert focus.on_task(target, dict(KONSOLE, id="k1", title="totally different"))
    assert focus.on_task(
        target,
        {
            "title": "~ : claude — Konsole",
            "app": "org.kde.konsole",
            "pid": 9,
            "id": "other",
        },
    )
    assert focus.on_task(
        target,
        {
            "title": "~ : claude (remote) — Konsole",
            "app": "org.kde.konsole",
            "pid": 9,
            "id": "other",
        },
    )
    assert not focus.on_task(target, dict(BRAVE))
    tab_target = {
        "kind": "tab",
        "url_prefix": "https://claude.ai/chat/abc",
        "title": "c",
    }
    assert focus.on_task(tab_target, dict(BRAVE, tab_url="https://claude.ai/chat/abc"))
    assert not focus.on_task(tab_target, dict(BRAVE, tab_url="https://x.example/"))


# --- look --------------------------------------------------------------------


class FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def gemini_opener(payload: dict):
    def open_fn(req, timeout=30.0):
        return FakeResp(json.dumps(payload).encode())

    return open_fn


def test_look_no_session():
    tracker = make_tracker(Clock(), {"where": None}, speaks=[])
    assert tracker.look()["ok"] is False


def test_look_off_target():
    clock = Clock()
    box = {"where": dict(KONSOLE)}
    tracker = make_tracker(clock, box, speaks=[])
    tracker.lock("window", 0, "thesis")
    box.update(away_box())
    got = tracker.look()
    assert got["ok"] is False and "not in front" in got["say"]


def test_look_happy(monkeypatch):
    clock = Clock()
    box = {"where": dict(KONSOLE)}
    tracker = make_tracker(clock, box, speaks=[])
    tracker._shot_fn = lambda: b"\x89PNG-fake-bytes"
    monkeypatch.setattr(focus, "google_key", lambda: "key")
    monkeypatch.setattr(
        focus, "jpeg_downscale", lambda png, max_side=1280: (png, "image/png")
    )
    tracker.lock("window", 0, "thesis")
    payload = {
        "candidates": [{"content": {"parts": [{"text": "Looking sharp, Sir."}]}}]
    }
    got = tracker.look("is this right?", opener=gemini_opener(payload))
    assert got["ok"] and got["say"] == "Looking sharp, Sir."


def test_look_needs_key(monkeypatch):
    clock = Clock()
    box = {"where": dict(KONSOLE)}
    tracker = make_tracker(clock, box, speaks=[])
    tracker._shot_fn = lambda: b"png"
    monkeypatch.setattr(focus, "google_key", lambda: "")
    tracker.lock("window", 0, "thesis")
    assert "GOOGLE_API_KEY" in tracker.look()["say"]


def test_look_shot_failure():
    clock = Clock()
    box = {"where": dict(KONSOLE)}
    tracker = make_tracker(clock, box, speaks=[])
    tracker._shot_fn = lambda: None
    tracker.lock("window", 0, "thesis")
    assert "capture" in tracker.look()["say"]
