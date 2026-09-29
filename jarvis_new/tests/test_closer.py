"""close_app: a site closes as its Brave tab(s), anything else its window."""

import json
import subprocess

import pytest

import bridge
from system import closer, kwin_windows


def _page(tid, url, title=""):
    return {"type": "page", "id": tid, "url": url, "title": title}


TABS = [
    _page("yt", "https://www.youtube.com/watch?v=x", "Lofi - YouTube"),
    _page("myt", "https://m.youtube.com/", "YouTube"),
    _page("nb", "https://notebook.google.com/n/1", "Physics Mark Schemes"),
    _page("gm", "https://mail.google.com/mail/u/0", "Inbox"),
    _page("ext", "chrome://settings", "youtube settings"),
    {"type": "service_worker", "id": "sw", "url": "https://www.youtube.com/sw.js"},
]


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _opener(tabs, closed):
    def open_fn(url, timeout=0):
        if url.endswith("/json/list"):
            return _Resp(json.dumps(tabs).encode())
        closed.append(url.rsplit("/", 1)[-1])
        return _Resp(b"Target is closing")

    return open_fn


def _dead_opener(url, timeout=0):
    raise OSError("connection refused")


@pytest.mark.parametrize(
    "raw,want",
    [
        ("the YouTube tab", "youtube"),
        ("my steam app.", "steam"),
        ("Dolphin window", "dolphin"),
        ("  GitHub website! ", "github"),
    ],
)
def test_clean_name(raw, want):
    assert closer.clean_name(raw) == want


def test_match_tabs_site_by_exact_host():
    ids = [t["id"] for t in closer.match_tabs(TABS, "youtube")]
    assert ids == ["yt", "myt"]  # chrome:// and service workers ignored


def test_match_tabs_google_spares_subdomains():
    assert closer.match_tabs(TABS, "google") == []


def test_match_tabs_free_form_label_and_title():
    assert [t["id"] for t in closer.match_tabs(TABS, "notebook")] == ["nb"]
    assert [t["id"] for t in closer.match_tabs(TABS, "physics mark")] == ["nb"]


def test_close_target_closes_only_matching_tabs():
    closed: list[str] = []
    out = closer.close_target("the youtube tab", opener=_opener(TABS, closed))
    assert out["ok"] and out["closed"] == "tab"
    assert closed == ["yt", "myt"]
    assert "2 youtube tabs" in out["say"]


def test_close_target_falls_back_to_window(monkeypatch):
    monkeypatch.setattr(kwin_windows, "available", lambda env=None: True)
    monkeypatch.setattr(
        kwin_windows, "find", lambda q, run=None: [("{u1}", "Home — Dolphin")]
    )
    acted = []
    monkeypatch.setattr(
        kwin_windows, "act_on", lambda a, u, run=None: acted.append((a, u))
    )
    out = closer.close_target("dolphin", opener=_opener(TABS, []))
    assert out == {"ok": True, "say": "Closed Home — Dolphin.", "closed": "window"}
    assert acted == [("close", "{u1}")]


def test_site_name_never_closes_a_brave_window(monkeypatch):
    monkeypatch.setattr(kwin_windows, "available", lambda env=None: True)
    monkeypatch.setattr(
        kwin_windows, "find", lambda q, run=None: [("{b}", "Google Search - Brave")]
    )
    acted = []
    monkeypatch.setattr(kwin_windows, "act_on", lambda *a, **k: acted.append(a))
    out = closer.close_target("google", opener=_dead_opener)
    assert not out["ok"] and acted == []


def test_close_target_refusals(monkeypatch):
    monkeypatch.setattr(kwin_windows, "available", lambda env=None: False)
    assert not closer.close_target("jarvis")["ok"]
    assert closer.close_target("  ")["say"] == "Close what, Sir?"

    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, "", "")

    out = closer.close_target("steam", opener=_dead_opener, run=run)
    assert out == {"ok": False, "say": "Nothing called steam is open, Sir."}


@pytest.mark.parametrize(
    "text,want",
    [
        ("close youtube", ("close_app", {"name": "youtube"}, None)),
        ("hey jarvis, quit steam", ("close_app", {"name": "steam"}, None)),
        ("close it", None),
        ("close the helper", None),
        ("close deep research", None),
    ],
)
def test_bridge_routes_close(text, want):
    assert bridge._match_voice_tool(text) == want


def test_bridge_remote_close_still_remote_stop():
    assert bridge._match_voice_tool("close the remote session")[0] == "remote_stop"


def test_bridge_close_reply():
    reply = bridge._dynamic_voice_reply("close_app", {}, {"say": "Closed YouTube."})
    assert reply == "Closed YouTube, Sir."
