import json

import pytest
from livekit.agents.llm import ToolError

import system.reddit as reddit_mod
from system.reddit import RedditTools, _clean_html, parse_reddit_feed

ATOM_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>r/technology</title>
<entry><title>New battery breakthrough doubles range</title>
<link href="https://www.reddit.com/r/technology/comments/abc/example/"/>
<content type="html">&lt;p&gt;Scientists report &lt;b&gt;big&lt;/b&gt; gains.&lt;/p&gt;</content></entry>
<entry><title>Second post here</title>
<link href="https://www.reddit.com/r/technology/comments/def/second/"/>
</entry>
</feed>
"""

RSS_FIXTURE = b"""<?xml version="1.0"?>
<rss><channel><title>r/all</title>
<item><title>Front page post &amp; more</title>
<link>https://www.reddit.com/r/pics/comments/xyz/pic/</link>
<description>Look at this</description></item>
</channel></rss>
"""


def test_clean_html_strips_tags_and_entities() -> None:
    assert _clean_html("<p>A &amp; B</p>") == "A & B"


def test_parse_atom_entries() -> None:
    items = parse_reddit_feed(ATOM_FIXTURE, 5)
    assert len(items) == 2
    assert items[0]["title"] == "New battery breakthrough doubles range"
    assert items[0]["link"].endswith("/example/")
    assert "big gains" in items[0]["text"]
    assert items[1]["text"] == ""


def test_parse_rss_items() -> None:
    items = parse_reddit_feed(RSS_FIXTURE, 5)
    assert len(items) == 1
    assert items[0]["title"] == "Front page post & more"


def test_parse_malformed_xml_salvages_titles() -> None:
    raw = b"<rss><channel><item><title>Broken & <tag></title><link>https://x.test/1</link>"
    items = parse_reddit_feed(raw, 5)
    assert items and "Broken" in items[0]["title"]


def test_parse_garbage_returns_empty() -> None:
    assert parse_reddit_feed(b"definitely not xml <oops", 5) == []


def test_reddit_tools_register_expected_ids() -> None:
    assert [tool.id for tool in RedditTools().tools] == ["reddit_front"]


@pytest.mark.asyncio
async def test_reddit_refuses_when_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_LOCAL", raising=False)
    with pytest.raises(ToolError, match="only available"):
        await RedditTools.reddit_front(RedditTools(), None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_reddit_rejects_bad_subreddit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    with pytest.raises(ToolError, match="not a valid subreddit"):
        await RedditTools.reddit_front(RedditTools(), None, subreddit="../../etc")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_reddit_uses_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.reddit.DATA_DIR", tmp_path)
    (tmp_path / "reddit-technology.json").write_text(
        json.dumps([{"title": "Cached post", "link": "", "text": ""}])
    )
    result = await RedditTools.reddit_front(RedditTools(), None, subreddit="technology")  # type: ignore[arg-type]
    assert "Cached post" in result["say"]


@pytest.mark.asyncio
async def test_reddit_falls_back_to_stale_cache_on_throttle(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.reddit.DATA_DIR", tmp_path)
    stale = tmp_path / "reddit-all.json"
    stale.write_text(json.dumps([{"title": "Stale post", "link": "", "text": ""}]))
    # Age it past TTL so a fresh fetch is attempted...
    import os
    import time

    old = time.time() - 7200
    os.utime(stale, (old, old))

    def _throttled(subreddit: str) -> bytes:
        raise ToolError("Reddit is throttling me (429). Try again in a bit.")

    monkeypatch.setattr(reddit_mod, "_fetch_feed", _throttled)
    result = await RedditTools.reddit_front(RedditTools(), None, subreddit="")  # type: ignore[arg-type]
    assert "Stale post" in result["say"]
    assert "cache" in result["say"]
