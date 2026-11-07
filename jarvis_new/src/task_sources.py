"""Read-only access to Sir's own data for background task "lookup" steps.

The headless ChatGPT worker has no mail, calendar or notes tools, and it
must not get any: mail text is untrusted. So Jarvis itself runs the reads
here, in-process, and hands the worker only fenced text. Every function in
this module is read-only; nothing here can send, delete, label or write.

Sources: gmail (messages, text/PDF attachments, linked Google Docs),
calendar (upcoming events) and memory (the notes vault).
JARVIS_TASK_LOOKUP=0 disables all of it.
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import re

SOURCES = ("gmail", "calendar", "memory")
MAX_QUERIES = 4
MAX_MESSAGES = 6  # per gmail query
MAX_ATTACHMENTS = 3  # per gmail query
MAX_DOCS = 2  # per gmail query
ATTACHMENT_BYTES = 3 * 1024 * 1024
BODY_CHARS = 4000
ATTACHMENT_CHARS = 8000
BLOCK_CHARS = 24000  # whole lookup payload handed to the worker
_DOC_LINK = re.compile(r"https://docs\.google\.com/document/d/([A-Za-z0-9_-]{20,})")


class SourceReadError(Exception):
    """A source could not be read. The message is safe to speak."""


def enabled() -> bool:
    import os

    return os.environ.get("JARVIS_TASK_LOOKUP", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def _decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _walk(part: dict):
    if isinstance(part, dict):
        yield part
        for sub in part.get("parts", []) or []:
            yield from _walk(sub)


def _plain_text(payload: dict) -> str:
    """All text/plain (else html) body text of a Gmail payload, undecorated."""
    plain, html_parts = [], []
    for part in _walk(payload):
        data = (part.get("body") or {}).get("data")
        if not data or part.get("filename"):
            continue
        mime = part.get("mimeType", "")
        with contextlib.suppress(Exception):
            text = _decode(data).decode(errors="replace")
            if mime == "text/plain":
                plain.append(text)
            elif mime == "text/html":
                html_parts.append(text)
    if plain:
        return "\n".join(plain)
    if html_parts:
        from system.inbox import html_to_text

        return "\n".join(html_to_text(h) for h in html_parts)
    return ""


def attachment_parts(payload: dict) -> list[dict]:
    """Readable-size text/PDF attachments of a payload. Pure."""
    found = []
    for part in _walk(payload):
        name = str(part.get("filename") or "")
        att_id = (part.get("body") or {}).get("attachmentId")
        size = int((part.get("body") or {}).get("size") or 0)
        mime = str(part.get("mimeType") or "")
        if not (name and att_id) or size > ATTACHMENT_BYTES:
            continue
        low = name.lower()
        if low.endswith(".pdf") or mime == "application/pdf":
            found.append({"name": name, "id": att_id, "kind": "pdf"})
        elif low.endswith((".txt", ".md", ".csv")) or mime == "text/plain":
            found.append({"name": name, "id": att_id, "kind": "text"})
    return found


def doc_ids(text: str) -> list[str]:
    """Google Doc ids linked in text, first-seen order, de-duplicated. Pure."""
    seen: list[str] = []
    for match in _DOC_LINK.finditer(text or ""):
        if match[1] not in seen:
            seen.append(match[1])
    return seen


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"[ \t]+", " ", str(text or ""))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text if len(text) <= limit else text[:limit] + " …[cut]"


def _pdf_text(raw: bytes) -> str:
    import librarian_pdf

    pages = librarian_pdf.parse_pdf(raw)
    pages = [p if isinstance(p, str) else str(getattr(p, "text", p)) for p in pages]
    return "\n".join(pages)


def _read_attachment(token: str, msg_id: str, part: dict, api) -> str:
    data = api(f"/messages/{msg_id}/attachments/{part['id']}", token, None)
    raw = _decode(str((data or {}).get("data") or ""))
    if part["kind"] == "pdf":
        return _pdf_text(raw)
    return raw.decode(errors="replace")


def _header(full: dict, name: str) -> str:
    for h in (full.get("payload") or {}).get("headers", []) or []:
        if isinstance(h, dict) and h.get("name", "").lower() == name:
            return str(h.get("value", ""))
    return ""


def gmail(query: str, *, api=None, token=None, docs_read=None) -> list[str]:
    """Search Gmail read-only. One text block per message (+ attachments, docs)."""
    from system import inbox

    api = api or inbox._gmail_api
    try:
        token = token or inbox._gmail_access_token()
    except Exception as exc:
        raise SourceReadError(str(exc) or "Gmail is not connected.") from exc
    q = " ".join(str(query or "").split())[:200]
    if not q:
        raise SourceReadError("Empty Gmail query.")
    try:
        listed = api("/messages", token, {"q": q, "maxResults": MAX_MESSAGES})
    except Exception as exc:
        raise SourceReadError(str(exc) or "Gmail did not respond.") from exc
    blocks: list[str] = []
    budget_att, budget_docs = MAX_ATTACHMENTS, MAX_DOCS
    for msg in (listed.get("messages") or [])[:MAX_MESSAGES]:
        try:
            full = api(f"/messages/{msg['id']}", token, {"format": "full"})
        except Exception:
            continue
        payload = full.get("payload") or {}
        body = _plain_text(payload)
        blocks.append(
            "EMAIL\n"
            f"Subject: {_header(full, 'subject')[:200]}\n"
            f"From: {_header(full, 'from')[:120]}\n"
            f"Date: {_header(full, 'date')[:60]}\n"
            f"Body: {_clip(inbox.strip_quoted(body), BODY_CHARS)}"
        )
        for part in attachment_parts(payload):
            if budget_att <= 0:
                break
            budget_att -= 1
            try:
                text = _read_attachment(token, msg["id"], part, api)
                blocks.append(
                    f"ATTACHMENT {part['name'][:80]} (on that email)\n"
                    f"{_clip(text, ATTACHMENT_CHARS)}"
                )
            except Exception:
                blocks.append(
                    f"ATTACHMENT {part['name'][:80]} (on that email): unreadable"
                )
        for doc_id in doc_ids(body):
            if budget_docs <= 0:
                break
            budget_docs -= 1
            try:
                reader = docs_read or _docs_read
                blocks.append(
                    f"LINKED GOOGLE DOC {doc_id[:8]}\n"
                    f"{_clip(reader(doc_id), ATTACHMENT_CHARS)}"
                )
            except Exception:
                blocks.append(f"LINKED GOOGLE DOC {doc_id[:8]}: unreadable")
    return blocks


def _docs_read(doc_id: str) -> str:
    import google_api

    return google_api.docs_read(doc_id)


def calendar(days_ahead: int = 30, days_back: int = 0, *, lister=None) -> list[str]:
    """Calendar events in a window around now, read-only."""
    days_ahead = max(0, min(366, int(days_ahead or 0)))
    days_back = max(0, min(60, int(days_back or 0)))
    now = dt.datetime.now(dt.timezone.utc)
    lo = (now - dt.timedelta(days=days_back)).isoformat()
    hi = (now + dt.timedelta(days=days_ahead)).isoformat()
    try:
        if lister is None:
            import google_api

            lister = google_api.calendar_list
        events = lister(lo, hi)
    except Exception as exc:
        raise SourceReadError(str(exc) or "Calendar is not reachable.") from exc
    rows = []
    for ev in events[:60]:
        when = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get(
            "date", ""
        )
        end = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get(
            "date", ""
        )
        rows.append(
            f"EVENT {when} to {end}: {str(ev.get('summary') or '(untitled)')[:120]}"
            + (f" @ {str(ev['location'])[:100]}" if ev.get("location") else "")
        )
    return rows


def memory(query: str, *, recaller=None) -> list[str]:
    """Notes vault paragraphs matching the query, read-only."""
    if recaller is None:
        import memory as mem

        recaller = mem.recall
    try:
        hits = recaller(str(query or "")[:200], 8)
    except Exception as exc:
        raise SourceReadError(str(exc) or "Memory is not readable.") from exc
    return [f"NOTE {h.get('source', '')}: {h.get('text', '')}" for h in hits]


def parse_queries(raw: str) -> list[dict]:
    """Model JSON -> validated [{source, query|days_ahead|days_back}]. Pure."""
    import json

    text = str(raw or "").strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(match[0]) if match else None
    except ValueError:
        data = None
    items = data.get("queries") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict) or item.get("source") not in SOURCES:
            continue
        spec: dict = {"source": item["source"]}
        if item["source"] == "calendar":
            for key in ("days_ahead", "days_back"):
                with contextlib.suppress(TypeError, ValueError):
                    spec[key] = int(item.get(key) or 0)
        else:
            query = " ".join(str(item.get("query") or "").split())[:200]
            if not query:
                continue
            spec["query"] = query
        if spec not in out:
            out.append(spec)
        if len(out) >= MAX_QUERIES:
            break
    return out


def fetch(queries: list[dict], *, handlers=None) -> tuple[str, list[str], int]:
    """Run validated queries. Returns (text, error messages, hit count).

    A failing source is reported, never swallowed, and never aborts the
    other queries.
    """
    handlers = handlers or {}
    errors: list[str] = []
    sections: list[str] = []
    hits = 0
    for spec in queries:
        source = spec["source"]
        try:
            if source == "gmail":
                items = handlers.get("gmail", gmail)(spec["query"])
            elif source == "memory":
                items = handlers.get("memory", memory)(spec["query"])
            else:
                items = handlers.get("calendar", calendar)(
                    spec.get("days_ahead", 30), spec.get("days_back", 0)
                )
        except SourceReadError as exc:
            errors.append(f"{source}: {exc}")
            continue
        except Exception as exc:  # a source must never crash the task
            errors.append(f"{source}: {type(exc).__name__}")
            continue
        label = spec.get("query") or "upcoming events"
        if items:
            hits += len(items)
            sections.append(f"## {source} search: {label}\n" + "\n\n".join(items))
        else:
            sections.append(f"## {source} search: {label}\nNO RESULTS")
    text = "\n\n".join(sections)
    if len(text) > BLOCK_CHARS:
        text = text[:BLOCK_CHARS] + "\n…[cut]"
    return text, errors, hits
