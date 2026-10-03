"""Only the user's foreground YouTube video becomes the download target."""

import json

import pytest

import youtube_history as history


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.youtube.com/watch?v=aqz-KE-bpKQ&t=40&list=secret",
            "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
        ),
        (
            "https://youtu.be/aqz-KE-bpKQ?si=tracking",
            "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
        ),
        (
            "https://m.youtube.com/shorts/aqz-KE-bpKQ",
            "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
        ),
        (
            "https://youtube.com/live/aqz-KE-bpKQ",
            "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
        ),
        ("https://www.youtube.com/results?search_query=private", None),
        ("https://www.youtube.com/@creator", None),
        ("https://youtube.com.evil.example/watch?v=aqz-KE-bpKQ", None),
        ("https://evil.example/watch?v=aqz-KE-bpKQ", None),
        ("https://user:password@youtube.com/watch?v=aqz-KE-bpKQ", None),
        ("https://youtube.com:9999/watch?v=aqz-KE-bpKQ", None),
        ("https://www.youtube.com/watch?v=bad", None),
    ],
)
def test_canonical_video_url(url, expected):
    assert history.canonical_video_url(url) == expected


def test_chooses_foreground_video_not_first_background_tab():
    targets = [
        {
            "type": "page",
            "url": "https://youtube.com/watch?v=aaaaaaaaaaa",
            "title": "Older",
            "webSocketDebuggerUrl": "old",
        },
        {
            "type": "page",
            "url": "https://youtube.com/watch?v=bbbbbbbbbbb",
            "title": "Watching",
            "webSocketDebuggerUrl": "new",
        },
    ]
    states = {
        "old": {"visible": True, "focused": False},
        "new": {"visible": True, "focused": True},
    }
    assert history.pick_foreground_video(targets, states, None)["url"].endswith(
        "bbbbbbbbbbb"
    )
    assert (
        history.pick_foreground_video(targets, {"old": {"visible": True}}, None) is None
    )


def test_window_title_disambiguates_visible_browser_windows():
    targets = [
        {
            "type": "page",
            "url": "https://youtube.com/watch?v=aaaaaaaaaaa",
            "title": "Older",
            "webSocketDebuggerUrl": "old",
        },
        {
            "type": "page",
            "url": "https://youtube.com/watch?v=bbbbbbbbbbb",
            "title": "Watching - YouTube",
            "webSocketDebuggerUrl": "new",
        },
    ]
    states = {"old": {"visible": True}, "new": {"visible": True}}
    active = {"app": "brave-browser", "title": "Watching - YouTube - Brave"}
    assert history.pick_foreground_video(targets, states, active)["url"].endswith(
        "bbbbbbbbbbb"
    )
    assert (
        history.pick_foreground_video(
            targets, states, {"app": "sober", "title": "Sober"}
        )
        is None
    )


def test_recent_video_survives_switching_and_closing_and_logs_only_video(tmp_path):
    tracker = history.YouTubeHistory(tmp_path)
    current = {
        "url": "https://youtube.com/watch?v=aaaaaaaaaaa&list=secret",
        "title": "My film - YouTube",
    }
    assert tracker.observe(current, now=10)
    assert not tracker.observe(
        {"url": "https://mail.google.com/private", "title": "Private mail"}, now=11
    )
    assert not tracker.observe(None, now=12)
    assert history.YouTubeHistory(tmp_path).resolve(None)["url"].endswith("aaaaaaaaaaa")
    assert "mail.google" not in "".join(p.read_text() for p in tmp_path.iterdir())
    assert "list=secret" not in "".join(p.read_text() for p in tmp_path.iterdir())
    assert not tracker.observe(current, now=13)
    assert len(tracker.history_path.read_text().splitlines()) == 1
    assert json.loads(tracker.state_path.read_text())["viewed_at"] == 10


def test_current_video_overrides_history_and_invalid_explicit_url_is_rejected(tmp_path):
    tracker = history.YouTubeHistory(tmp_path)
    tracker.observe({"url": "https://youtube.com/watch?v=aaaaaaaaaaa"}, now=1)
    current = {"url": "https://youtube.com/watch?v=bbbbbbbbbbb", "title": "New video"}
    assert tracker.resolve(current)["source"] == "current"
    assert tracker.resolve(None, "https://youtu.be/ccccccccccc")["source"] == "explicit"
    with pytest.raises(ValueError, match="YouTube video URL"):
        tracker.resolve(None, "https://evil.example/video")
    with pytest.raises(ValueError, match="recent YouTube video"):
        history.YouTubeHistory(tmp_path / "empty").resolve(None)


def test_history_is_bounded_and_private(tmp_path):
    tracker = history.YouTubeHistory(tmp_path)
    for n in range(history.HISTORY_LIMIT + 3):
        tracker.observe({"url": f"https://youtube.com/watch?v={n:011d}"}, now=n)
    assert len(tracker.history_path.read_text().splitlines()) == history.HISTORY_LIMIT
    assert tracker.state_path.stat().st_mode & 0o777 == 0o600
    assert tracker.history_path.stat().st_mode & 0o777 == 0o600


def test_return_to_previous_video_counts_as_new_view_and_instances_share_lock(tmp_path):
    first = history.YouTubeHistory(tmp_path)
    second = history.YouTubeHistory(tmp_path)
    assert first._lock is second._lock
    for tracker, video_id in [
        (first, "aaaaaaaaaaa"),
        (second, "bbbbbbbbbbb"),
        (first, "aaaaaaaaaaa"),
    ]:
        tracker.observe({"url": f"https://youtube.com/watch?v={video_id}"})
    rows = [json.loads(line) for line in first.history_path.read_text().splitlines()]
    assert [row["url"].split("=")[-1] for row in rows] == [
        "aaaaaaaaaaa",
        "bbbbbbbbbbb",
        "aaaaaaaaaaa",
    ]


def test_concurrent_instances_do_not_lose_history_entries(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    def observe(number):
        return history.YouTubeHistory(tmp_path).observe(
            {"url": f"https://youtube.com/watch?v={number:011d}"}
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(observe, range(40)))
    rows = [
        json.loads(line)
        for line in history.YouTubeHistory(tmp_path)
        .history_path.read_text()
        .splitlines()
    ]
    assert len(rows) == 40
    assert len({row["url"] for row in rows}) == 40
