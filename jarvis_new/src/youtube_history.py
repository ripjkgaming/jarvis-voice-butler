"""Remember only foreground YouTube videos, so voice 'this video' stays useful.

The bridge polls Sir's existing Brave CDP endpoint. Background tabs are never
chosen by their order in /json/list, and unrelated browsing is never persisted.
Only a canonical video URL, its title and the observation time are stored.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HISTORY_LIMIT = 100
POLL_S = 0.75
VIDEO_ID = re.compile(r"^[a-zA-Z0-9_-]{11}$")
_path_locks: dict[Path, threading.RLock] = {}
_path_locks_guard = threading.Lock()


def canonical_video_url(url: str) -> str | None:
    """Accept exact YouTube video hosts/paths and discard tracking parameters."""
    try:
        parts = urlparse(str(url or "").strip())
        if parts.scheme not in {"https", "http"} or parts.username or parts.password:
            return None
        if parts.port not in (None, 80, 443):
            return None
        host = (parts.hostname or "").lower()
        path = parts.path.strip("/").split("/")
        video_id = ""
        if host in {"youtu.be", "www.youtu.be"} and len(path) == 1:
            video_id = path[0]
        elif host in {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com",
        }:
            if path == ["watch"]:
                video_id = parse_qs(parts.query).get("v", [""])[0]
            elif len(path) == 2 and path[0] in {"shorts", "live", "embed"}:
                video_id = path[1]
        if VIDEO_ID.fullmatch(video_id):
            return f"https://www.youtube.com/watch?v={video_id}"
    except (ValueError, TypeError):
        pass
    return None


def _browser_window(active: dict | None) -> bool:
    if not active:
        return False
    app = str(active.get("app", "")).lower()
    title = str(active.get("title", "")).lower()
    return any(
        name in app for name in ("brave", "chromium", "chrome")
    ) or title.endswith(" - brave")


def pick_foreground_video(
    targets: list, states: dict, active: dict | None
) -> dict | None:
    """Select a focused video, or match a visible page to the active window."""
    if active and not _browser_window(active):
        return None
    candidates = []
    for target in targets:
        if not isinstance(target, dict) or target.get("type") != "page":
            continue
        url = canonical_video_url(target.get("url", ""))
        if not url:
            continue
        state = states.get(target.get("webSocketDebuggerUrl"), {})
        if not isinstance(state, dict) or not state.get("visible"):
            continue
        title = " ".join(str(target.get("title", "")).split())[:240]
        candidates.append(({"url": url, "title": title}, state))
    focused = [video for video, state in candidates if state.get("focused")]
    if len(focused) == 1:
        return focused[0]
    if active and _browser_window(active):
        caption = str(active.get("title", "")).removesuffix(" - Brave").strip()
        matches = [
            video
            for video, _ in candidates
            if video["title"] and video["title"] == caption
        ]
        if len(matches) == 1:
            return matches[0]
    return None


async def _page_state(wsurl: str) -> dict:
    """Small read-only query; reject remote websocket targets."""
    import websockets

    if urlparse(wsurl).hostname not in {"127.0.0.1", "localhost", "::1"}:
        return {}
    async with websockets.connect(wsurl, max_size=100_000, open_timeout=0.75) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": "({visible: document.visibilityState === 'visible', focused: document.hasFocus()})",
                        "returnByValue": True,
                    },
                }
            )
        )
        async with asyncio.timeout(0.75):
            while True:
                message = json.loads(await ws.recv())
                if message.get("id") == 1:
                    value = message.get("result", {}).get("result", {}).get("value", {})
                    return value if isinstance(value, dict) else {}


async def _states(targets: list) -> dict:
    pages = [
        row
        for row in targets
        if isinstance(row, dict)
        and row.get("type") == "page"
        and canonical_video_url(row.get("url", ""))
        and row.get("webSocketDebuggerUrl")
    ]
    results = await asyncio.gather(
        *(_page_state(row["webSocketDebuggerUrl"]) for row in pages),
        return_exceptions=True,
    )
    return {
        row["webSocketDebuggerUrl"]: state
        for row, state in zip(pages, results)
        if isinstance(state, dict)
    }


def foreground_video() -> dict | None:
    """Current watched video in the live Brave profile, never an arbitrary tab."""
    try:
        import active_window
        import focus

        active = active_window.active()
        if active and not _browser_window(active):
            return None
        targets = focus._cdp_list(focus._cdp_port(), timeout=0.75)
        return pick_foreground_video(targets, asyncio.run(_states(targets)), active)
    except Exception:
        return None


def _atomic_json(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(content)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(temporary)


class YouTubeHistory:
    def __init__(self, directory: Path | None = None) -> None:
        base = Path(os.environ.get("JARVIS_HOME") or Path.home() / ".jarvis")
        self.directory = Path(directory) if directory is not None else base / "youtube"
        self.state_path = self.directory / "recent.json"
        self.history_path = self.directory / "history.jsonl"
        with _path_locks_guard:
            self._lock = _path_locks.setdefault(
                self.directory.resolve(), threading.RLock()
            )

    def recent(self) -> dict | None:
        with self._lock:
            return self._recent_unlocked()

    def _recent_unlocked(self) -> dict | None:
        try:
            state = json.loads(self.state_path.read_text())
            url = canonical_video_url(state.get("url", ""))
            if url:
                return {
                    "url": url,
                    "title": str(state.get("title", ""))[:240],
                    "viewed_at": state.get("viewed_at"),
                }
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return None

    def observe(self, video: dict | None, now: float | None = None) -> bool:
        if not isinstance(video, dict) or not (
            url := canonical_video_url(video.get("url", ""))
        ):
            return False
        with self._lock:
            previous = self.recent()
            if previous and previous["url"] == url:
                return False
            state = {
                "url": url,
                "title": " ".join(str(video.get("title", "")).split())[:240],
                "viewed_at": time.time() if now is None else now,
            }
            try:
                lines = (
                    self.history_path.read_text().splitlines()[-(HISTORY_LIMIT - 1) :]
                    if self.history_path.exists()
                    else []
                )
                lines.append(json.dumps(state))
                _atomic_json(self.history_path, "\n".join(lines) + "\n")
                _atomic_json(self.state_path, json.dumps(state))
                return True
            except OSError:
                return False

    def resolve(self, current: dict | None, explicit_url: str = "") -> dict:
        if explicit_url:
            url = canonical_video_url(explicit_url)
            if not url:
                raise ValueError("Please provide a valid YouTube video URL, Sir.")
            return {"url": url, "title": "YouTube video", "source": "explicit"}
        if isinstance(current, dict) and canonical_video_url(current.get("url", "")):
            self.observe(current)
            return {
                "url": canonical_video_url(current["url"]),
                "title": str(current.get("title", "YouTube video"))[:240],
                "source": "current",
            }
        recent = self.recent()
        if recent:
            return {**recent, "source": "recent"}
        raise ValueError(
            "I haven't seen a recent YouTube video, Sir. Open the video or give me its YouTube link."
        )


_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


def start_thread(interval: float = POLL_S) -> tuple[threading.Thread, threading.Event]:
    """Bridge-owned observer; only local, read-only video URL tracking."""
    global _thread
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return _thread, _thread._youtube_stop
        stop = threading.Event()
        history = YouTubeHistory()

        def watch() -> None:
            while not stop.is_set():
                with contextlib.suppress(Exception):
                    history.observe(foreground_video())
                stop.wait(interval)

        _thread = threading.Thread(target=watch, name="youtube-history", daemon=True)
        _thread._youtube_stop = stop
        _thread.start()
        return _thread, stop
