"""Notion REST client (urllib only, fail-soft).

Token NOTION_TOKEN lives in $JARVIS_HOME/keys.env (internal integration).
Every public function raises NotionError with a speakable message on
failure, never a traceback. Pass `opener=` in tests to fake the network.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

NOTION_VERSION = "2022-06-28"
BASE = "https://api.notion.com/v1"

SETUP_HINT = (
    "Notion isn't connected yet, Sir — run scripts/notion_setup.py once to connect it."
)

MAX_BLOCKS = 200  # recursive read cap so a huge page can't flood the LLM


class NotionError(Exception):
    """User-facing Notion failure (speakable message)."""


def jarvis_home() -> Path:
    """Base dir honoring $JARVIS_HOME (default ~/.jarvis). Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def notion_token() -> str:
    """NOTION_TOKEN from env or keys.env (never printed/logged)."""
    direct = os.environ.get("NOTION_TOKEN", "").strip()
    if direct:
        return direct
    try:
        for line in (jarvis_home() / "keys.env").read_text().splitlines():
            line = line.strip()
            if line.startswith("NOTION_TOKEN="):
                return line.partition("=")[2].strip().strip("'\"")
    except OSError:
        pass
    return ""


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _api(
    path: str,
    token: str,
    method: str = "GET",
    payload: dict | None = None,
    opener=None,
) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers=_headers(token),
    )
    call = opener or urllib.request.urlopen
    try:
        with call(req, timeout=20) as resp:
            got = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise NotionError(
                "Notion refused access, Sir — the token may be wrong or the page "
                "isn't shared with the integration."
            ) from exc
        if exc.code == 404:
            raise NotionError("I could not find that Notion page, Sir.") from exc
        raise NotionError(f"Notion returned an error ({exc.code}).") from exc
    except Exception as exc:
        raise NotionError(f"Notion did not respond ({exc}).") from exc
    if not isinstance(got, dict):
        raise NotionError("Notion answered with garbage.")
    return got


def _require_token(opener=None) -> str:
    _ = opener  # kept for a uniform signature across helpers
    token = notion_token()
    if not token:
        raise NotionError(SETUP_HINT)
    return token


def search(query: str, opener=None) -> list[dict]:
    """Search page/database titles. Returns [{id, title, type}]."""
    token = _require_token(opener)
    query = (query or "").strip()[:200]
    if not query:
        raise NotionError("What should I search Notion for, Sir?")
    got = _api(
        "/search",
        token,
        "POST",
        {"query": query, "page_size": 10},
        opener=opener,
    )
    out = []
    for item in got.get("results", []) or []:
        if not isinstance(item, dict):
            continue
        title = "".join(
            t.get("plain_text", "")
            for t in (
                item.get("properties", {}).get("title", {}).get("title", []) or []
            )
            if isinstance(t, dict)
        ) or _page_title_fallback(item)
        out.append(
            {
                "id": item.get("id", ""),
                "title": title[:150],
                "type": item.get("object", ""),
            }
        )
    return out


def _page_title_fallback(item: dict) -> str:
    """Best title guess for databases / untitled pages. Pure."""
    try:
        for prop in (item.get("properties") or {}).values():
            if isinstance(prop, dict) and prop.get("type") == "title":
                return "".join(
                    t.get("plain_text", "") for t in prop.get("title", []) or []
                )
    except AttributeError:
        pass
    return "(untitled)"


def _rich_text(block: dict) -> str:
    """One block -> its plain text. Pure."""
    data = block.get(block.get("type", ""), {}) if isinstance(block, dict) else {}
    if not isinstance(data, dict):
        return ""
    return "".join(t.get("plain_text", "") for t in data.get("rich_text", []) or [])


def blocks_text(blocks: list[dict]) -> str:
    """Blocks -> readable plain text (headings kept as lines). Pure."""
    lines = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        kind, text = block.get("type", ""), _rich_text(block).strip()
        if not text:
            continue
        if kind.startswith("heading_"):
            lines.append(f"# {text}")
        elif kind == "bulleted_list_item" or kind == "numbered_list_item":
            lines.append(f"- {text}")
        elif kind == "to_do":
            tick = "x" if (block.get("to_do") or {}).get("checked") else " "
            lines.append(f"- [{tick}] {text}")
        else:
            lines.append(text)
    return "\n".join(lines).strip()[:8000]


def _read_children(page_id: str, token: str, opener, budget: list) -> list[dict]:
    """Block children, recursing into nested blocks within budget. Capped."""
    got = _api(f"/blocks/{page_id}/children?page_size=100", token, opener=opener)
    blocks = [b for b in got.get("results", []) or [] if isinstance(b, dict)]
    out: list[dict] = []
    for block in blocks:
        if len(budget) >= MAX_BLOCKS:
            break
        budget.append(1)
        out.append(block)
        if block.get("has_children"):
            out.extend(_read_children(block.get("id", ""), token, opener, budget))
    return out


def read_page(page_id: str, opener=None) -> str:
    """Page -> plain text from blocks (recursive, capped)."""
    token = _require_token(opener)
    page_id = (page_id or "").strip()
    if not page_id:
        raise NotionError("Which Notion page, Sir?")
    return blocks_text(_read_children(page_id, token, opener, []))


def markdown_to_blocks(text: str) -> list[dict]:
    """Markdown-ish text -> Notion blocks (headings, bullets, to-dos). Pure."""
    blocks: list[dict] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        stripped = stripped[:1000]
        if stripped.startswith("### "):
            blocks.append(_block("heading_3", stripped[4:]))
        elif stripped.startswith("## "):
            blocks.append(_block("heading_2", stripped[3:]))
        elif stripped.startswith("# "):
            blocks.append(_block("heading_1", stripped[2:]))
        elif re.match(r"^- \[[ xX]\] ", stripped):
            checked = stripped[3].lower() == "x"
            blocks.append(
                {
                    "type": "to_do",
                    "to_do": {
                        "rich_text": [{"text": {"content": stripped[6:]}}],
                        "checked": checked,
                    },
                }
            )
        elif stripped.startswith(("- ", "* ")):
            blocks.append(_block("bulleted_list_item", stripped[2:]))
        elif re.match(r"^\d+[.)] ", stripped):
            blocks.append(
                _block("numbered_list_item", re.sub(r"^\d+[.)] ", "", stripped))
            )
        else:
            blocks.append(_block("paragraph", stripped))
    return blocks[:100]


def _block(kind: str, text: str) -> dict:
    return {"type": kind, kind: {"rich_text": [{"text": {"content": text}}]}}


def create_page(
    parent_id: str,
    title: str,
    text: str = "",
    *,
    is_database: bool = False,
    opener=None,
) -> dict:
    """New page under a page (or new row in a database). Returns {id}."""
    token = _require_token(opener)
    title = (title or "").strip()[:200]
    if not title:
        raise NotionError("The page needs a title, Sir.")
    if not (parent_id or "").strip():
        raise NotionError("Which Notion page or database, Sir?")
    parent = (
        {"database_id": parent_id.strip()}
        if is_database
        else {"page_id": parent_id.strip()}
    )
    payload: dict = {
        "parent": parent,
        "properties": {"title": {"title": [{"text": {"content": title}}]}},
    }
    children = markdown_to_blocks(text)
    if children and not is_database:
        payload["children"] = children
    got = _api("/pages", token, "POST", payload, opener=opener)
    if not got.get("id"):
        raise NotionError("The Notion page did not stick, Sir.")
    return {"id": got["id"], "title": title}


def append_to_page(page_id: str, text: str, opener=None) -> dict:
    """Append markdown-ish text as blocks. Returns {appended}."""
    token = _require_token(opener)
    page_id = (page_id or "").strip()
    if not page_id:
        raise NotionError("Which Notion page, Sir?")
    children = markdown_to_blocks(text)
    if not children:
        raise NotionError("There is nothing to add, Sir.")
    _api(
        f"/blocks/{page_id}/children",
        token,
        "PATCH",
        {"children": children},
        opener=opener,
    )
    return {"appended": len(children)}


def query_database(database_id: str, filter_text: str = "", opener=None) -> list[dict]:
    """Query a database, optionally filtering by title text. Capped at 20."""
    token = _require_token(opener)
    database_id = (database_id or "").strip()
    if not database_id:
        raise NotionError("Which Notion database, Sir?")
    payload: dict = {"page_size": 20}
    if (filter_text or "").strip():
        payload["filter"] = {
            "property": "title",
            "title": {"contains": filter_text.strip()[:200]},
        }
    got = _api(f"/databases/{database_id}/query", token, "POST", payload, opener=opener)
    rows = []
    for item in got.get("results", []) or []:
        if isinstance(item, dict):
            rows.append(
                {"id": item.get("id", ""), "title": _page_title_fallback(item)[:150]}
            )
    return rows[:20]
