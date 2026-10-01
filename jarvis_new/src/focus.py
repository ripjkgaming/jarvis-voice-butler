"""Focus Mode: lock onto a window or Brave tab, track drifts, review work.

Sir says "lock on to this": the current active window (or active Brave
tab) becomes the target. A background loop polls every second: leaving
the target for more than 8 s counts one drift (a butler nudge via
src/speak.py, at most one reminder if still away after 2 more minutes).
When the timer ends Jarvis speaks a summary; every session lands in
``$JARVIS_HOME/focus/history.jsonl`` and mirrors into src/activity.py.

``look_at_locked_work()`` screenshots the locked work for "what do you
see" via the free Gemini vision REST API (same urllib pattern as
eyes.py). Everything outside the pure helpers is fail-soft.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

#: Leaving the target this long counts one drift.
GRACE_S = 8.0
#: Second nudge when still away this long after the grace crossing.
REMINDER_S = 120.0
#: Poll cadence of the background loop.
POLL_S = 1.0
#: Cap per-tick accrual (sleep/wake jumps don't inflate the books).
MAX_DT_S = 5.0


#: Brave CDP port (Sir's Brave runs with remote debugging; override via env).
def _cdp_port() -> int:
    try:
        return int(os.environ.get("JARVIS_BRAVE_CDP_PORT", "9222") or 9222)
    except ValueError:
        return 9222


CDP_PORT = _cdp_port()
#: Active-tab CDP detail is cached this long (websocket checks are slow).
TAB_CACHE_S = 2.0
#: Activity feed id for the running session.
ACTIVITY_ID = "focus-session"

DRIFT_LINES = (
    "Sir, you've drifted. Back to {label}.",
    "Eyes on {label}, Sir.",
    "Drifting, Sir — back to {label}.",
    "Sir. {label}.",
)


def jarvis_home() -> Path:
    """Base dir honoring $JARVIS_HOME (default ~/.jarvis). Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def focus_dir() -> Path:
    """$JARVIS_HOME/focus (created on demand). Never raises."""
    try:
        path = jarvis_home() / "focus"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return jarvis_home() / "focus"


def state_path() -> Path:
    """Running-session file. Pure."""
    return focus_dir() / "state.json"


def history_path() -> Path:
    """Append-only session log. Pure."""
    return focus_dir() / "history.jsonl"


# --- pure helpers ------------------------------------------------------------


def is_brave(app: str, title: str = "") -> bool:
    """Is this window Brave? Pure."""
    if "brave" in (app or "").casefold():
        return True
    return (title or "").strip().casefold().endswith(" - brave")


def url_prefix(url: str) -> str:
    """Lockable prefix of a URL: scheme://host + path, no query. Pure."""
    try:
        parts = urllib.parse.urlparse((url or "").strip())
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return ""
        return f"{parts.scheme}://{parts.netloc}{parts.path or '/'}"
    except Exception:
        return ""


def url_matches(locked_prefix: str, current_url: str) -> bool:
    """Same site section as the locked tab? Pure."""
    if not locked_prefix:
        return False
    current = url_prefix(current_url)
    if not current:
        return False
    # Either direction: navigating deeper keeps the lock; landing back on
    # the section root (shorter path) does too.
    return current.startswith(locked_prefix) or locked_prefix.startswith(current)


def _title_head(text: str) -> str:
    """Title minus counters ("(2)") and the trailing " - App" bit. Pure."""
    text = re.sub(r"\s*\([^)]*\)", "", text)
    text = re.sub(r"\s+[-\u2013\u2014·|]\s+\S+$", "", text)
    return text.strip()


def title_matches(locked_title: str, current_title: str) -> bool:
    """Same document, tolerating suffix churn (" - Brave", counters). Pure."""
    locked = _title_head(" ".join((locked_title or "").split()).casefold())
    current = _title_head(" ".join((current_title or "").split()).casefold())
    if not locked or not current:
        return False
    return locked == current or locked in current or current in locked


def pick_tab(targets: list, visibility: dict | None = None) -> dict | None:
    """CDP /json/list rows -> {"url", "title"} of the active tab. Pure.

    Prefers the target whose visibilityState is "visible"; falls back to
    the first http(s) page entry (usually the focused one).
    """
    pages = [
        t
        for t in (targets or [])
        if isinstance(t, dict)
        and t.get("type") == "page"
        and str(t.get("url") or "").startswith(("http://", "https://"))
    ]
    if not pages:
        return None
    if visibility:
        for page in pages:
            if visibility.get(page.get("webSocketDebuggerUrl")) == "visible":
                return {
                    "url": str(page.get("url") or ""),
                    "title": " ".join(str(page.get("title") or "").split()),
                }
    first = pages[0]
    return {
        "url": str(first.get("url") or ""),
        "title": " ".join(str(first.get("title") or "").split()),
    }


_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty"}


def number_word(n: int) -> str:
    """0..59 -> "forty-five"; larger stays digits. Pure."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if 0 <= n < 20:
        return _ONES[n]
    if 20 <= n < 60 and n % 10 == 0:
        return _TENS[n]
    if 20 <= n < 60:
        return f"{_TENS[n - n % 10]}-{_ONES[n % 10]}"
    return str(n)


def count_word(n: int, singular: str) -> str:
    """2 -> "Two drifts"; 1 -> "One drift". Pure."""
    word = number_word(n).capitalize() if n <= 10 else str(n)
    return f"{word} {singular}" if n == 1 else f"{word} {singular}s"


def on_task(target: dict, where: dict | None) -> bool:
    """Is Sir on the locked target right now? Pure."""
    if not target or not where:
        return False
    if target.get("kind") == "tab":
        if not is_brave(where.get("app", ""), where.get("title", "")):
            return False
        return url_matches(target.get("url_prefix", ""), where.get("tab_url", ""))
    # Window: same KWin id wins; else same app with a matching title.
    if target.get("win_id") and where.get("id") and target["win_id"] == where["id"]:
        return True
    app = " ".join(str(target.get("app") or "").split()).casefold()
    wapp = " ".join(str(where.get("app") or "").split()).casefold()
    return (
        bool(app)
        and app == wapp
        and title_matches(target.get("title", ""), where.get("title", ""))
    )


def drift_line(label: str, n: int) -> str:
    """Butler nudge, rotating with the drift count. Pure."""
    return DRIFT_LINES[max(0, n - 1) % len(DRIFT_LINES)].format(label=label or "it")


def session_stats(session: dict) -> dict:
    """Elapsed/planned/drifts/on-task % for status + summary. Pure."""
    focused = float(session.get("focused_s", 0.0))
    away = float(session.get("away_s", 0.0))
    total = focused + away
    pct = round(100.0 * focused / total) if total > 0 else 100
    planned = session.get("minutes") or 0
    return {
        "focused_s": focused,
        "away_s": away,
        "drifts": int(session.get("drifts", 0)),
        "pct": pct,
        "planned_min": planned,
    }


def summary_say(session: dict, reason: str) -> str:
    """Spoken session summary. Pure."""
    stats = session_stats(session)
    label = session.get("label") or "it"
    planned = session.get("minutes") or 0
    if planned >= 1:
        head = f"{number_word(round(planned)).capitalize()} minutes done, Sir."
    elif reason == "stopped":
        head = "Focus over, Sir."
    else:
        head = "Done, Sir."
    tail = f"{count_word(stats['drifts'], 'drift')}, {stats['pct']}% on task."
    if stats["drifts"] == 0 and stats["pct"] == 100 and session.get("focused_s", 0) > 0:
        return f"{head} Flawless — locked on {label} the whole way."
    return f"{head} {tail}"


def status_say(session: dict | None) -> str:
    """Spoken "how am I doing". Pure."""
    if not session:
        return "We're not in focus mode, Sir."
    stats = session_stats(session)
    label = session.get("label") or "it"
    mins = round(stats["focused_s"] / 60)
    return (
        f"{number_word(mins).capitalize()} minutes on {label}, Sir. "
        f"{count_word(stats['drifts'], 'drift')}, {stats['pct']}% on task."
    )


# --- persistence -------------------------------------------------------------


def load_state() -> dict | None:
    """Running session from state.json, or None. Never raises."""
    try:
        data = json.loads(state_path().read_text())
        return data if isinstance(data, dict) and data.get("started") else None
    except (OSError, ValueError):
        return None


def save_state(session: dict | None) -> None:
    """Persist (or clear) the running session. Never raises."""
    try:
        if session is None:
            with contextlib.suppress(OSError):
                state_path().unlink()
            return
        tmp = state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(session))
        os.replace(tmp, state_path())
    except (OSError, ValueError):
        pass


def append_history(entry: dict) -> None:
    """One JSON line per finished session. Never raises."""
    try:
        with history_path().open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


# --- Brave tab awareness -----------------------------------------------------


def _cdp_list(port: int, opener=None, timeout: float = 5.0) -> list:
    open_fn = opener or urllib.request.urlopen
    with open_fn(f"http://127.0.0.1:{port}/json/list", timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf8", "replace"))
    return data if isinstance(data, list) else []


async def _cdp_visibility(wsurl: str, timeout: float = 5.0) -> str | None:
    import websockets

    async with websockets.connect(wsurl, max_size=2_000_000, open_timeout=5) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": "document.visibilityState",
                        "returnByValue": True,
                    },
                }
            )
        )
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if msg.get("id") == 1:
                result = msg.get("result", {}).get("result", {})
                return result.get("value")


async def _cdp_visibilities(wsurls: list[str]) -> dict:
    out: dict = {}
    for wsurl in wsurls:
        try:
            out[wsurl] = await _cdp_visibility(wsurl)
        except Exception:
            continue
    return out


def brave_tab(port: int = CDP_PORT, opener=None) -> dict | None:
    """Sir's live Brave: active tab {"url", "title"} or None. Fail-soft.

    Uses Chromium's /json/list on the remote-debugging port plus a
    document.visibilityState check per page (first page entry fallback).
    Needs Brave launched with --remote-debugging-port; otherwise None
    and callers fall back to the window title.
    """
    try:
        targets = _cdp_list(port, opener=opener)
    except Exception:
        return None
    pages = [
        t
        for t in targets
        if isinstance(t, dict)
        and t.get("type") == "page"
        and str(t.get("url") or "").startswith(("http://", "https://"))
    ]
    if not pages:
        return None
    visibility: dict = {}
    try:
        wsurls = [
            p["webSocketDebuggerUrl"] for p in pages if p.get("webSocketDebuggerUrl")
        ]
        if wsurls:
            visibility = asyncio.run(_cdp_visibilities(wsurls))
    except Exception:
        visibility = {}
    return pick_tab(targets, visibility or None)


# --- screenshots + Gemini vision ---------------------------------------------


def _parse_keys_file(path: Path) -> dict:
    out: dict = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            out[name.strip()] = value
    except OSError:
        pass
    return out


def google_key() -> str:
    """GOOGLE_API_KEY from env, else $JARVIS_HOME/keys.env. Never logs it."""
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if key:
        return key
    return (
        _parse_keys_file(jarvis_home() / "keys.env").get("GOOGLE_API_KEY", "").strip()
    )


def gemini_models() -> list[str]:
    """Vision model chain: primary then fallbacks. Pure (env)."""
    primary = os.environ.get("JARVIS_TEXT_MODEL", "").strip() or "gemini-3.8-flash"
    chain = [primary, "gemini-3.7-flash", "gemini-3.6-flash"]
    seen: set[str] = set()
    return [m for m in chain if not (m in seen or seen.add(m))]


def build_vision_body(
    image: bytes, mime: str, question: str, model: str
) -> tuple[str, bytes]:
    """(url, json body) for generateContent with one inline image. Pure."""
    prompt = (
        "You are Jarvis, a sharp but kind British butler looking at Sir's "
        "screen while he works. Give brief, useful feedback on the work "
        "visible — 2 to 4 sentences, concrete, no flattery. "
        "If it looks like leisure (games, feeds, shopping), say so plainly."
    )
    question = " ".join((question or "").split())
    if question:
        prompt += f" Sir asks: {question[:300]} Answer that first, then the feedback."
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    body = json.dumps(
        {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": mime,
                                "data": base64.b64encode(image).decode(),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {"maxOutputTokens": 300},
        }
    ).encode()
    return url, body


def parse_gemini_text(payload: dict) -> str:
    """First text part of a generateContent reply, '' when absent. Pure."""
    try:
        for part in payload["candidates"][0]["content"]["parts"]:
            text = part.get("text", "")
            if text and text.strip():
                return " ".join(text.split())
    except (KeyError, TypeError, IndexError, AttributeError):
        pass
    return ""


def _gemini_post(
    url: str, body: bytes, key: str, opener=None, timeout: float = 30.0
) -> dict:
    req = urllib.request.Request(
        f"{url}?key={key}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    open_fn = opener or urllib.request.urlopen
    with open_fn(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def take_screenshot(run=None) -> bytes | None:
    """Fullscreen PNG via spectacle. None on any failure. Never raises."""
    runner = run or subprocess.run
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            out = tmp.name
        proc = runner(
            ["spectacle", "-b", "-n", "-f", "-o", out],
            capture_output=True,
            timeout=15,
        )
        if getattr(proc, "returncode", 1) != 0:
            return None
        data = Path(out).read_bytes()
        return data or None
    except Exception:
        return None
    finally:
        with contextlib.suppress(Exception):
            Path(out).unlink()


def jpeg_downscale(png: bytes, max_side: int = 1280) -> tuple[bytes, str]:
    """PNG -> smaller JPEG when cv2 is around, else the PNG as-is. Pure-ish."""
    try:
        import cv2
        import numpy as np

        frame = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return png, "image/png"
        h, w = frame.shape[:2]
        scale = min(1.0, max_side / max(h, w))
        if scale < 1.0:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if ok:
            return bytes(buf), "image/jpeg"
    except Exception:
        pass
    return png, "image/png"


# --- session tracker ---------------------------------------------------------


def capture_target(kind: str, where: dict, tab: dict | None) -> dict:
    """Freeze the lock target from the live window/tab. Pure."""
    if kind == "tab" and tab and tab.get("url"):
        return {
            "kind": "tab",
            "url_prefix": url_prefix(tab["url"]),
            "title": " ".join(str(tab.get("title") or "").split())[:200],
        }
    return {
        "kind": "window",
        "app": where.get("app", ""),
        "title": where.get("title", ""),
        "win_id": where.get("id", ""),
    }


def default_label(target: dict) -> str:
    """Human lock label from a target ("this tab", app name...). Pure."""
    if target.get("kind") == "tab":
        try:
            host = urllib.parse.urlparse(target.get("url_prefix", "")).netloc.replace(
                "www.", ""
            )
        except Exception:
            host = ""
        title = target.get("title", "")
        return (
            f"{title[:60]} ({host})"
            if host and title
            else host or title[:60] or "this tab"
        )
    return target.get("app", "") or target.get("title", "")[:60] or "this"


class FocusTracker:
    """One focus session: lock, poll ticks, drift nudges, summaries.

    Inject fakes for tests (clock, window feed, speech); defaults run
    the real machine. The background loop never raises.
    """

    def __init__(
        self,
        *,
        now=None,
        active_fn=None,
        tab_fn=None,
        speak_fn=None,
        shot_fn=None,
    ) -> None:
        self._now = now or time.time
        self._active_fn = active_fn
        self._tab_fn = tab_fn
        self._speak_fn = speak_fn
        self._shot_fn = shot_fn
        self.session: dict | None = None
        self._tab_cache: dict = {"at": 0.0, "value": None}
        self._last_mirror = 0.0
        self._last_save = 0.0

    # -- inputs ---------------------------------------------------------

    def _active(self) -> dict | None:
        try:
            if self._active_fn is not None:
                return self._active_fn()
            import active_window

            return active_window.active()
        except Exception:
            return None

    def _tab(self) -> dict | None:
        try:
            now = self._now()
            if now - self._tab_cache["at"] <= TAB_CACHE_S:
                return self._tab_cache["value"]
            value = (self._tab_fn or brave_tab)()
            self._tab_cache.update(at=now, value=value)
            return value
        except Exception:
            return None

    def _where(self) -> dict | None:
        where = self._active()
        if not where:
            return None
        where = dict(where)
        if is_brave(where.get("app", ""), where.get("title", "")):
            tab = self._tab()
            if tab:
                where["tab_url"] = tab.get("url", "")
                where["tab_title"] = tab.get("title", "")
        return where

    def _speak(self, text: str, min_gap_s: float = 60.0) -> None:
        try:
            if self._speak_fn is not None:
                self._speak_fn(text)
                return
            import speak

            speak.speak(text, source="focus", min_gap_s=min_gap_s)
        except Exception:
            pass

    # -- activity mirror --------------------------------------------------

    def _mirror_start(self) -> None:
        try:
            import activity

            session = self.session or {}
            activity.start(
                "task",
                f"Focus: {session.get('label', 'work')}"[:120],
                detail="Locked on",
                source="focus",
                item_id=ACTIVITY_ID,
            )
        except Exception:
            pass

    def _mirror_poll(self, force: bool = False) -> None:
        try:
            if not self.session:
                return
            now = self._now()
            if not force and now - self._last_mirror < 5.0:
                return
            self._last_mirror = now
            import activity

            stats = session_stats(self.session)
            planned = self.session.get("minutes") or 0
            progress = (
                min(99.9, 100.0 * (now - self.session["started"]) / (planned * 60.0))
                if planned
                else None
            )
            activity.update(
                ACTIVITY_ID,
                detail=f"{stats['drifts']} drifts · {stats['pct']}% on task"[:200],
                progress=progress,
            )
        except Exception:
            pass

    def _mirror_finish(self, detail: str) -> None:
        try:
            import activity

            activity.finish(ACTIVITY_ID, ok=True, detail=detail[:200])
        except Exception:
            pass

    # -- session control --------------------------------------------------

    def is_active(self) -> bool:
        """Is a focus session running?"""
        return self.session is not None

    def lock(self, kind: str = "auto", minutes: float = 0, label: str = "") -> dict:
        """Lock onto the current window/tab. Returns {"ok", "say"}."""
        where = self._where()
        if not where:
            return {"ok": False, "say": "I can't see any active window, Sir."}
        tab = None
        if kind == "auto":
            if is_brave(where.get("app", ""), where.get("title", "")):
                tab = self._tab()
            kind = "tab" if tab and tab.get("url") else "window"
        elif kind == "tab":
            if not is_brave(where.get("app", ""), where.get("title", "")):
                return {
                    "ok": False,
                    "say": "Brave isn't the active window, Sir — bring the tab up first.",
                }
            tab = self._tab()
            if not tab or not tab.get("url"):
                # CDP unreachable: fall back to the Brave window itself.
                kind = "window"
        if kind not in ("window", "tab"):
            return {"ok": False, "say": "Lock onto a window or a tab, Sir?"}
        target = capture_target(kind, where, tab)
        if target["kind"] == "tab" and not target.get("url_prefix"):
            return {"ok": False, "say": "I couldn't read that tab, Sir."}
        try:
            mins = float(minutes or 0)
        except (TypeError, ValueError):
            mins = 0.0
        mins = max(0.0, min(480.0, mins))
        now = self._now()
        self.session = {
            "kind": target["kind"],
            "label": " ".join((label or "").split())[:80] or default_label(target),
            "minutes": mins or None,
            "started": now,
            "last_poll": now,
            "target": target,
            "focused_s": 0.0,
            "away_s": 0.0,
            "drifts": 0,
            "drift_log": [],
            "away_since": None,
            "excursion_open": False,
            "reminded": False,
        }
        save_state(self.session)
        self._last_save = now
        self._mirror_start()
        self._mirror_poll(force=True)
        mins_say = ""
        if mins >= 1:
            mins_say = f" for {number_word(round(mins))} minutes"
        elif mins > 0:
            mins_say = f" for {round(mins * 60)} seconds"
        return {
            "ok": True,
            "say": f"Locked on, Sir{mins_say}.",
        }

    def poll(self, now: float | None = None) -> None:
        """One 1-second tick: accrue time, count drifts, end the timer."""
        try:
            if not self.session:
                return
            now = self._now() if now is None else now
            last = self.session.get("last_poll", now)
            dt = max(0.0, min(MAX_DT_S, now - last))
            self.session["last_poll"] = now
            where = self._where()
            target = self.session["target"]
            if on_task(target, where):
                self.session["focused_s"] += dt
                self.session["away_since"] = None
                self.session["excursion_open"] = False
                self.session["reminded"] = False
            else:
                self.session["away_s"] += dt
                if self.session.get("away_since") is None:
                    self.session["away_since"] = now
                away_for = now - self.session["away_since"]
                if away_for > GRACE_S and not self.session.get("excursion_open"):
                    self.session["excursion_open"] = True
                    self.session["drifts"] += 1
                    self.session["drift_log"].append(
                        {
                            "t": round(now - self.session["started"], 1),
                            "app": str((where or {}).get("app", ""))[:60],
                            "title": str((where or {}).get("title", ""))[:120],
                            "url": str((where or {}).get("tab_url", ""))[:200],
                        }
                    )
                    self._speak(
                        drift_line(self.session["label"], self.session["drifts"])
                    )
                    self._mirror_poll(force=True)
                elif (
                    self.session.get("excursion_open")
                    and not self.session.get("reminded")
                    and away_for > GRACE_S + REMINDER_S
                ):
                    self.session["reminded"] = True
                    self._speak(
                        f"Still away, Sir. {self.session['label']} is waiting.",
                        min_gap_s=0,
                    )
            planned = self.session.get("minutes") or 0
            if planned and now - self.session["started"] >= planned * 60.0:
                self.end(reason="done")
                return
            self._mirror_poll()
            if now - self._last_save > 10.0:
                self._last_save = now
                save_state(self.session)
        except Exception:
            pass

    def run(self, stop: threading.Event, interval: float = POLL_S) -> None:
        """Loop poll() until stop is set. Never raises."""
        while not stop.is_set():
            with contextlib.suppress(Exception):
                self.poll()
            stop.wait(interval)

    def status(self) -> dict:
        """Machine-readable session status ({} when idle). Never raises."""
        try:
            session = self.session or load_state()
            if not session:
                return {"active": False}
            stats = session_stats(session)
            return {
                "active": self.session is not None,
                "kind": session.get("kind"),
                "label": session.get("label"),
                "drifts": stats["drifts"],
                "pct": stats["pct"],
                "focused_s": round(stats["focused_s"], 1),
                "away_s": round(stats["away_s"], 1),
                "planned_min": stats["planned_min"],
                "elapsed_s": round(self._now() - session["started"], 1),
            }
        except Exception:
            return {"active": False}

    def end(self, reason: str = "stopped") -> dict:
        """Finish the session: summary, history, activity. Never raises."""
        try:
            session = self.session
            self.session = None
            save_state(None)
            if not session:
                return {"ok": False, "say": "We're not in focus mode, Sir."}
            say = summary_say(session, reason)
            stats = session_stats(session)
            append_history(
                {
                    "ended": time.time(),
                    "label": session.get("label"),
                    "kind": session.get("kind"),
                    "planned_min": session.get("minutes"),
                    "focused_s": round(stats["focused_s"], 1),
                    "away_s": round(stats["away_s"], 1),
                    "drifts": stats["drifts"],
                    "pct": stats["pct"],
                    "reason": reason,
                }
            )
            self._mirror_finish(f"{stats['drifts']} drifts · {stats['pct']}% on task")
            self._speak(say, min_gap_s=0)
            return {"ok": True, "say": say}
        except Exception:
            return {"ok": False, "say": "Focus over, Sir."}

    def look(self, question: str = "", opener=None) -> dict:
        """Screenshot the locked work + brief Gemini feedback. Fail-soft."""
        try:
            if not self.session:
                return {"ok": False, "say": "We're not locked on, Sir."}
            where = self._where()
            if not on_task(self.session["target"], where):
                return {
                    "ok": False,
                    "say": (
                        f"That's not in front of me, Sir — bring "
                        f"{self.session['label']} up and I'll look."
                    ),
                }
            shot_fn = self._shot_fn or take_screenshot
            try:
                png = shot_fn()
            except Exception:
                png = None
            if not png:
                return {"ok": False, "say": "I couldn't capture the screen, Sir."}
            key = google_key()
            if not key:
                return {
                    "ok": False,
                    "say": "I need a GOOGLE_API_KEY in keys.env to see it, Sir.",
                }
            image, mime = jpeg_downscale(png)
            last_err = ""
            for model in gemini_models():
                url, body = build_vision_body(image, mime, question, model)
                try:
                    text = parse_gemini_text(
                        _gemini_post(url, body, key, opener=opener)
                    )
                except Exception as exc:
                    last_err = str(exc)[:120]
                    continue
                if text:
                    return {"ok": True, "say": text[:1200]}
            return {
                "ok": False,
                "say": f"I couldn't read the screen, Sir ({last_err or 'no answer'}).",
            }
        except Exception:
            return {"ok": False, "say": "I couldn't look just now, Sir."}


_tracker: FocusTracker | None = None


def get_tracker() -> FocusTracker:
    """Process-wide tracker (created on first use)."""
    global _tracker
    if _tracker is None:
        _tracker = FocusTracker()
    return _tracker


def is_active() -> bool:
    """Is a focus session running? For other modules. Never raises."""
    try:
        return get_tracker().is_active()
    except Exception:
        return False


def spoken_status() -> str:
    """Spoken status for the live session, else the persisted one."""
    try:
        tracker = get_tracker()
        return status_say(tracker.session or load_state())
    except Exception:
        return "We're not in focus mode, Sir."


def start_thread(interval: float = POLL_S) -> tuple[threading.Thread, threading.Event]:
    """Run the focus loop in a daemon thread. Returns (thread, stop event)."""
    tracker = get_tracker()
    stop = threading.Event()

    def _loop() -> None:
        tracker.run(stop, interval)

    thread = threading.Thread(target=_loop, name="focus-watch", daemon=True)
    thread.start()
    return thread, stop
