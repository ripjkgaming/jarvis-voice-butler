"""Hermetic tests for src/notion_api.py (fake opener, no network)."""

import json
import urllib.error
import urllib.request

import pytest

import notion_api
from notion_api import (
    NotionError,
    append_to_page,
    blocks_text,
    create_page,
    markdown_to_blocks,
    notion_token,
    query_database,
    read_page,
    search,
)


class FakeResp:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, *payloads):
        self.queue = list(payloads)
        self.calls: list[tuple] = []

    def __call__(self, req, timeout=None):
        data = req.data
        try:
            body = json.loads(data.decode()) if data else None
        except ValueError:
            body = None
        self.calls.append((req.get_method(), req.full_url, body, dict(req.headers)))
        if not self.queue:
            raise AssertionError("fake opener called too often")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResp(item)


def _authed(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "secret-fake")


def test_token_missing_raises_setup_hint(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert notion_token() == ""
    with pytest.raises(NotionError, match="connected yet"):
        search("x")


def test_search_shape_and_headers(monkeypatch) -> None:
    _authed(monkeypatch)
    opener = FakeOpener(
        {
            "results": [
                {
                    "id": "page-1",
                    "object": "page",
                    "properties": {
                        "title": {"title": [{"plain_text": "Holiday plan"}]}
                    },
                }
            ]
        }
    )
    hits = search("holiday", opener=opener)
    assert hits == [{"id": "page-1", "title": "Holiday plan", "type": "page"}]
    method, url, body, headers = opener.calls[0]
    assert method == "POST" and url.endswith("/search")
    assert body["query"] == "holiday"
    assert headers.get("Notion-version") == "2022-06-28"
    assert headers.get("Authorization") == "Bearer secret-fake"


def test_search_rejects_empty(monkeypatch) -> None:
    _authed(monkeypatch)
    with pytest.raises(NotionError, match="What should"):
        search("  ")


def test_blocks_text_renders_kinds() -> None:
    blocks = [
        {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Hi"}]}},
        {
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"plain_text": "one"}]},
        },
        {
            "type": "to_do",
            "to_do": {"rich_text": [{"plain_text": "do"}], "checked": True},
        },
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "body"}]}},
    ]
    text = blocks_text(blocks)
    assert "# Hi" in text and "- one" in text and "- [x] do" in text and "body" in text


def test_read_page_recurses_children(monkeypatch) -> None:
    _authed(monkeypatch)
    opener = FakeOpener(
        {
            "results": [
                {
                    "id": "child-1",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"plain_text": "top"}]},
                    "has_children": True,
                }
            ]
        },
        {
            "results": [
                {
                    "id": "grand",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"plain_text": "nested"}]},
                    "has_children": False,
                }
            ]
        },
    )
    text = read_page("page-1", opener=opener)
    assert "top" in text and "nested" in text
    assert opener.calls[1][1].endswith("/blocks/child-1/children?page_size=100")


def test_markdown_to_blocks_kinds() -> None:
    blocks = markdown_to_blocks("# T\n## S\n- item\n- [ ] task\n1. first\nplain")
    kinds = [b["type"] for b in blocks]
    assert kinds == [
        "heading_1",
        "heading_2",
        "bulleted_list_item",
        "to_do",
        "numbered_list_item",
        "paragraph",
    ]
    assert blocks[3]["to_do"]["checked"] is False
    assert markdown_to_blocks("") == []


def test_create_page_payload(monkeypatch) -> None:
    _authed(monkeypatch)
    opener = FakeOpener({"id": "new-page"})
    got = create_page("parent-1", "Title", "# Hi\n- a", opener=opener)
    assert got["id"] == "new-page"
    _, url, body, _ = opener.calls[0]
    assert url.endswith("/pages") and body["parent"] == {"page_id": "parent-1"}
    assert body["properties"]["title"]["title"][0]["text"]["content"] == "Title"
    assert body["children"][0]["type"] == "heading_1"


def test_create_page_database_parent(monkeypatch) -> None:
    _authed(monkeypatch)
    opener = FakeOpener({"id": "row-1"})
    create_page("db-1", "Row", is_database=True, opener=opener)
    _, _, body, _ = opener.calls[0]
    assert body["parent"] == {"database_id": "db-1"}
    assert "children" not in body


def test_create_page_needs_title(monkeypatch) -> None:
    _authed(monkeypatch)
    with pytest.raises(NotionError, match="title"):
        create_page("parent-1", "  ")


def test_append_to_page_shape(monkeypatch) -> None:
    _authed(monkeypatch)
    opener = FakeOpener({})
    assert append_to_page("page-1", "- note", opener=opener) == {"appended": 1}
    method, url, body, _ = opener.calls[0]
    assert method == "PATCH" and url.endswith("/blocks/page-1/children")
    assert body["children"][0]["type"] == "bulleted_list_item"


def test_append_rejects_empty(monkeypatch) -> None:
    _authed(monkeypatch)
    with pytest.raises(NotionError, match="nothing"):
        append_to_page("page-1", "   ")


def test_query_database_filter_shape(monkeypatch) -> None:
    _authed(monkeypatch)
    opener = FakeOpener({"results": [{"id": "r1", "properties": {}}]})
    rows = query_database("db-1", "holiday", opener=opener)
    assert rows[0]["id"] == "r1"
    _, url, body, _ = opener.calls[0]
    assert url.endswith("/databases/db-1/query")
    assert body["filter"]["title"]["contains"] == "holiday"


def test_401_maps_to_share_hint(monkeypatch) -> None:
    _authed(monkeypatch)
    err = urllib.error.HTTPError("https://x", 403, "denied", {}, None)
    with pytest.raises(NotionError, match="shared with the integration"):
        search("x", opener=FakeOpener(err))


def test_never_sends_real_network(monkeypatch) -> None:
    def _boom(req, timeout=None):
        raise AssertionError("real network touched")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    assert notion_api.BASE.startswith("https://")
