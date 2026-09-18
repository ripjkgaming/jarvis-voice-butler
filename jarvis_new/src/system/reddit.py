"""Logged-out Reddit reading via public RSS (no login, no key).

Why RSS and not the browser: Reddit serves automation a
"blocked by network security" wall on both old and www front pages,
so browser reading is a dead end. The public .rss feeds work logged-out
but throttle hard (a second quick request earned a 429), hence:
30-minute cache per feed, stale-cache fallback on any HTTP failure.

Scope is deliberately headlines + self-post text. Full comment threads
need authenticated API access and are out of scope.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local
from system.core import DATA_DIR

REDDIT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)
REDDIT_CACHE_TTL = 30 * 60
SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{1,30}$")


def _clean_html(s: str) -> str:
    """Strip tags/entities for speech. Pure."""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _entry_link(entry: ET.Element) -> str:
    for link in entry.iter():
        tag = link.tag.rsplit("}", 1)[-1] if "}" in link.tag else link.tag
        if tag != "link":
            continue
        href = (link.get("href") or "").strip()
        if href:
            return href
        if (link.text or "").strip().startswith("http"):
            return link.text.strip()
    return ""


def parse_reddit_feed(raw: bytes, n: int) -> list[dict]:
    """Reddit .rss bytes -> [{title, link, text}]. Pure.

    Handles Atom <entry> (www.reddit) and RSS <item> (old.reddit,
    whose escaping is notoriously malformed -> best-effort regex).
    """
    items: list[dict] = []
    try:
        root = ET.fromstring(raw)
    except Exception:
        # old.reddit emits badly-escaped XML; salvage titles via regex.
        text = raw.decode(errors="ignore")
        for m in re.finditer(r"<title>(.*?)</title>.*?<link>(.*?)</link>", text, re.S):
            title = _clean_html(m.group(1))
            if title and not title.lower().startswith("r/"):
                items.append(
                    {"title": title[:200], "link": m.group(2).strip(), "text": ""}
                )
            if len(items) >= n:
                break
        return items
    for entry in root.iter():
        local = entry.tag.rsplit("}", 1)[-1] if "}" in entry.tag else entry.tag
        if local not in ("entry", "item"):
            continue
        title = _clean_html(_child_text(entry, "title"))
        if not title or len(items) >= n:
            if title and len(items) >= n:
                break
            continue
        content = ""
        for tag in ("content", "description", "summary"):
            got = _child_text(entry, tag)
            if got:
                content = _clean_html(got)[:800]
                break
        items.append(
            {"title": title[:200], "link": _entry_link(entry), "text": content}
        )
        if len(items) >= n:
            break
    return items


def _child_text(entry: ET.Element, local: str) -> str:
    """First child text matching a namespace-agnostic tag name. Pure."""
    for child in entry:
        tag = child.tag.rsplit("}", 1)[-1] if "}" in child.tag else child.tag
        if tag == local and child.text:
            return child.text
    return ""


def _cache_path(subreddit: str) -> Path:
    key = re.sub(r"[^a-z0-9]+", "_", subreddit.lower())[:30] or "all"
    return DATA_DIR / f"reddit-{key}.json"


def _fetch_feed(subreddit: str) -> bytes:
    path = f"r/{subreddit}" if subreddit else "r/all"
    url = f"https://www.reddit.com/{path}/.rss"
    req = urllib.request.Request(url, headers={"User-Agent": REDDIT_UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()
    except Exception as exc:
        raise ToolError(
            f"Reddit is throttling me ({exc}). Try again in a bit."
        ) from exc


class RedditTools:
    """Subreddit headlines + self-post text. Register via .tools."""

    @property
    def tools(self) -> list:
        return [self.reddit_front]

    @function_tool()
    async def reddit_front(
        self, context: RunContext, subreddit: str = "", n: int = 5
    ) -> dict[str, str]:
        """Top posts from Reddit, optionally one subreddit (logged-out).

        Args:
            subreddit: Name without r/, e.g. "technology". Empty for r/all.
            n: How many (1-10).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        sub = (subreddit or "").strip().lstrip("r/").strip()[:30]
        if sub and not SUBREDDIT_RE.match(sub):
            raise ToolError(f"'{sub}' is not a valid subreddit name.")
        n = max(1, min(10, int(n or 5)))
        cache = _cache_path(sub)
        try:
            if (
                cache.exists()
                and time.time() - cache.stat().st_mtime < REDDIT_CACHE_TTL
            ):
                data = json.loads(cache.read_text())
                if isinstance(data, list) and data:
                    return {"say": _speak(sub, data[:n])}
        except Exception:
            pass
        try:
            raw = await asyncio.to_thread(_fetch_feed, sub)
            items = parse_reddit_feed(raw, n)
        except ToolError:
            # Throttled: stale cache beats failure.
            try:
                data = json.loads(cache.read_text())
                if isinstance(data, list) and data:
                    log_action("reddit", f"stale {sub or 'all'}")
                    return {"say": _speak(sub, data[:n]) + " (from cache)"}
            except Exception:
                pass
            raise
        if not items:
            return {"say": "Nothing on Reddit right now."}
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(items))
        except Exception:
            pass
        log_action("reddit", f"{sub or 'all'} n={len(items)}")
        return {"say": _speak(sub, items)}


def _speak(sub: str, items: list[dict]) -> str:
    label = f"r/{sub}" if sub else "r/all"
    heads = "; ".join(i["title"] for i in items)
    return f"{label}: {heads}"[:1000]
