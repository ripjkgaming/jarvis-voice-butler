"""Voice tools for Google Docs/Sheets/Drive, Notion, and document generation.

Reads are direct; any write that touches existing external data (sheet
rows, Notion notes) is confirm-gated like email in inbox.py: the agent
reads the change back, Sir approves, confirm_workspace_action authorizes
exactly one matching write. Creating brand-new docs/pages/invoices is
safe and reversible, so it skips the gate.
"""

from __future__ import annotations

import asyncio
import html
import re

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local


def _guard() -> None:
    try:
        require_local()
    except LocalSystemError as exc:
        raise ToolError(str(exc)) from exc


def _draft_key(kind: str, target: str, content: str) -> str:
    """Exact-match fingerprint for the workspace confirm gate. Pure."""

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip().casefold())

    return f"{norm(kind)}|{norm(target)}|{norm(content)}"


def parse_rows_text(rows_text: str) -> list[list[str]]:
    """'a; b\\nc; d' -> rows of cells (semicolons, else commas). Pure."""
    rows = []
    for line in (rows_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        sep = ";" if ";" in line else ","
        rows.append([cell.strip()[:200] for cell in line.split(sep)])
    return rows[:100]


async def _share_for_school(meta: dict, done: str) -> dict[str, str]:
    """Share a freshly made file with Sir's school account; say how it went."""
    import google_api
    import google_apps

    try:
        email = await asyncio.to_thread(google_api.share_with_school, str(meta.get("id", "")))
    except google_api.GoogleError as exc:
        return {"say": f"{done}, Sir, but sharing it with your school account failed: {exc}"}
    log_action("drive", f"shared {meta.get('id', '')} with school")
    url = google_apps.with_authuser(meta.get("webViewLink", ""), email) if meta.get("webViewLink") else ""
    return {
        "say": f"{done} and shared it with your school account, Sir. {google_api.SCHOOL_SHARE_NOTE}",
        "url": url,
    }


class WorkspaceTools:
    """Google + Notion + docgen tools. Register via .tools on the SystemAgent."""

    def __init__(self) -> None:
        # Single-use write authorization (mirrors the email confirm gate):
        # confirm_workspace_action stores a fingerprint; gated writes only
        # fire on an exact match, then clear it.
        self._confirmed_write: str | None = None

    @property
    def tools(self) -> list:
        return [
            self.drive_find,
            self.read_google_doc,
            self.create_google_doc,
            self.read_sheet,
            self.add_sheet_rows,
            self.notion_find,
            self.notion_read,
            self.notion_add_note,
            self.confirm_workspace_action,
            self.generate_document,
        ]

    @function_tool()
    async def drive_find(self, context: RunContext, query: str) -> dict[str, str]:
        """Find Sir's Drive files by name ("find the holiday budget sheet").

        Args:
            query: Words from the file name or its contents.
        """
        _guard()
        import google_api

        try:
            hits = await asyncio.to_thread(google_api.drive_search, query.strip())
        except google_api.GoogleError as exc:
            raise ToolError(str(exc)) from exc
        if not hits:
            return {"say": f"Nothing in Drive matches {query.strip()[:60]}, Sir."}
        rows = "; ".join(f"{h.get('name', '?')}" for h in hits[:5])
        log_action("drive", f"find {query.strip()[:40]} n={len(hits)}")
        return {"say": f"In Drive: {rows}"[:600]}

    @function_tool()
    async def read_google_doc(
        self, context: RunContext, name_or_id: str
    ) -> dict[str, str]:
        """Read a Google Doc by name or id ("read the meeting notes doc").

        Args:
            name_or_id: File name (resolved via Drive search) or file id.
        """
        _guard()
        import google_api

        try:
            meta = await asyncio.to_thread(google_api.resolve_file, name_or_id)
            if "google-apps.document" not in str(meta.get("mimeType", "")):
                text = await asyncio.to_thread(google_api.drive_export, str(meta["id"]))
                text = text.decode(errors="replace")[:2000]
            else:
                text = await asyncio.to_thread(google_api.docs_read, str(meta["id"]))
        except google_api.GoogleError as exc:
            raise ToolError(str(exc)) from exc
        log_action("drive", f"read {str(meta.get('name', ''))[:60]}")
        say = (f"{meta.get('name', 'Document')}: {text.strip()[:1200]}").strip()
        return {"say": say[:1500]}

    @function_tool()
    async def create_google_doc(
        self, context: RunContext, title: str, content: str, account: str = "personal"
    ) -> dict[str, str]:
        """Create a new Google Doc (safe: brand-new files need no confirm).

        "...for school" / "on my school account": account="school" — the
        doc is made on Sir's main Drive and shared with his school account
        as an editor (his school blocks third-party apps).

        Args:
            title: Document title.
            content: Body text; blank lines become paragraphs.
            account: "personal" (default) or "school".
        """
        _guard()
        import google_api

        title, content = (title or "").strip(), (content or "").strip()
        if not title:
            raise ToolError("The document needs a title, Sir.")
        if not content:
            raise ToolError("There is no content for that document, Sir.")
        paras = "".join(
            f"<p>{html.escape(p)}</p>" for p in content.split("\n") if p.strip()
        )
        page_html = f"<html><body><h1>{html.escape(title)}</h1>{paras}</body></html>"
        try:
            meta = await asyncio.to_thread(google_api.docs_create, title, page_html)
        except google_api.GoogleError as exc:
            raise ToolError(str(exc)) from exc
        log_action("drive", f"create doc {title[:60]}")
        if google_api.normalize_account(account) != "school":
            return {"say": f"Created {title[:100]} in Drive, Sir.", "url": meta.get("webViewLink", "")}
        return await _share_for_school(meta, f"Created {title[:100]}")

    @function_tool()
    async def read_sheet(
        self, context: RunContext, name_or_id: str, sheet_range: str = "A1:Z50"
    ) -> dict[str, str]:
        """Read a Google Sheet by name or id ("read the budget sheet").

        Args:
            name_or_id: Spreadsheet name (resolved via Drive search) or id.
            sheet_range: Cell range, e.g. "A1:C20".
        """
        _guard()
        import google_api

        try:
            meta = await asyncio.to_thread(google_api.resolve_file, name_or_id)
            rows = await asyncio.to_thread(
                google_api.sheets_read, str(meta["id"]), sheet_range.strip() or "A1:Z50"
            )
        except google_api.GoogleError as exc:
            raise ToolError(str(exc)) from exc
        if not rows:
            return {"say": f"{meta.get('name', 'The sheet')} is empty, Sir."}
        lines = [" | ".join(str(c) for c in row)[:150] for row in rows[:12]]
        log_action("sheets", f"read {str(meta.get('name', ''))[:60]}")
        return {"say": f"{meta.get('name', 'Sheet')}: " + "; ".join(lines)[:1200]}

    @function_tool()
    async def confirm_workspace_action(
        self, context: RunContext, kind: str, target: str, content: str
    ) -> str:
        """Authorize ONE workspace write exactly as discussed with the user.

        Call only after the user explicitly approves what goes where. The
        next add_sheet_rows/notion_add_note must match kind, target and
        content exactly, then the authorization burns.

        Args:
            kind: "sheet_rows" or "notion_note".
            target: Sheet or page name Sir approved.
            content: Rows text / note text Sir approved.
        """
        _guard()
        kind = (kind or "").strip().lower()
        if kind not in ("sheet_rows", "notion_note"):
            raise ToolError("I can only confirm sheet rows or a Notion note.")
        if not (target or "").strip() or not (content or "").strip():
            raise ToolError("Tell me both where and what, Sir.")
        self._confirmed_write = _draft_key(kind, target, content)
        return f"Authorized one {kind} write to {target.strip()[:60]}."

    def _consume_confirm(self, kind: str, target: str, content: str) -> None:
        if self._confirmed_write != _draft_key(kind, target, content):
            raise ToolError(
                "That change is not authorized. Read it back and ask Sir "
                "to confirm it before writing."
            )
        self._confirmed_write = None

    @function_tool()
    async def add_sheet_rows(
        self, context: RunContext, name_or_id: str, rows_text: str
    ) -> dict[str, str]:
        """Append rows to a sheet. Requires confirm_workspace_action first.

        Args:
            name_or_id: Spreadsheet name or id (must match the confirmed target).
            rows_text: One row per line, cells separated by ";" (must match).
        """
        _guard()
        import google_api

        rows = parse_rows_text(rows_text)
        if not rows:
            raise ToolError("There are no rows in that, Sir.")
        self._consume_confirm("sheet_rows", name_or_id, rows_text)
        try:
            meta = await asyncio.to_thread(google_api.resolve_file, name_or_id)
            await asyncio.to_thread(
                google_api.sheets_append, str(meta["id"]), "A1", rows
            )
        except google_api.GoogleError as exc:
            raise ToolError(str(exc)) from exc
        log_action("sheets", f"append {str(meta.get('name', ''))[:60]} n={len(rows)}")
        return {
            "say": f"Added {len(rows)} rows to {meta.get('name', 'the sheet')}, Sir."
        }

    @function_tool()
    async def notion_find(self, context: RunContext, query: str) -> dict[str, str]:
        """Find Sir's Notion pages by title ("find the holiday plan page").

        Args:
            query: Words from the page title.
        """
        _guard()
        import notion_api

        try:
            hits = await asyncio.to_thread(notion_api.search, query.strip())
        except notion_api.NotionError as exc:
            raise ToolError(str(exc)) from exc
        if not hits:
            return {"say": f"Nothing in Notion matches {query.strip()[:60]}, Sir."}
        rows = "; ".join(h.get("title", "?") for h in hits[:5])
        log_action("notion", f"find {query.strip()[:40]} n={len(hits)}")
        return {"say": f"In Notion: {rows}"[:600]}

    @function_tool()
    async def notion_read(self, context: RunContext, name_or_id: str) -> dict[str, str]:
        """Read a Notion page by title or id ("read the holiday plan").

        Args:
            name_or_id: Page title (resolved via Notion search) or page id.
        """
        _guard()
        import notion_api

        ref = (name_or_id or "").strip()
        if not ref:
            raise ToolError("Which Notion page, Sir?")
        try:
            if re.fullmatch(r"[A-Za-z0-9-]{10,}", ref):
                page_id, title = ref, "Notion page"
            else:
                hits = await asyncio.to_thread(notion_api.search, ref)
                if not hits:
                    raise ToolError(f"Nothing in Notion matches {ref[:60]}, Sir.")
                page_id, title = hits[0]["id"], hits[0]["title"]
            text = await asyncio.to_thread(notion_api.read_page, page_id)
        except notion_api.NotionError as exc:
            raise ToolError(str(exc)) from exc
        log_action("notion", f"read {title[:60]}")
        return {"say": f"{title}: {text.strip()[:1200]}"[:1500]}

    @function_tool()
    async def notion_add_note(
        self, context: RunContext, page: str, text: str
    ) -> dict[str, str]:
        """Append a note to a Notion page. Requires confirm_workspace_action first.

        Args:
            page: Page title or id (must match the confirmed target).
            text: Note text, markdown-ish (must match the confirmed content).
        """
        _guard()
        import notion_api

        text = (text or "").strip()
        if not text:
            raise ToolError("There is no note to add, Sir.")
        self._consume_confirm("notion_note", page, text)
        try:
            if re.fullmatch(r"[A-Za-z0-9-]{10,}", (page or "").strip()):
                page_id = page.strip()
            else:
                hits = await asyncio.to_thread(notion_api.search, page.strip())
                if not hits:
                    raise ToolError(
                        f"Nothing in Notion matches {page.strip()[:60]}, Sir."
                    )
                page_id = hits[0]["id"]
            await asyncio.to_thread(notion_api.append_to_page, page_id, text)
        except notion_api.NotionError as exc:
            raise ToolError(str(exc)) from exc
        log_action("notion", f"note {page.strip()[:60]}")
        return {"say": "Noted in Notion, Sir."}

    @function_tool()
    async def generate_document(
        self, context: RunContext, kind: str = "invoice", request: str = "", account: str = "personal"
    ) -> dict[str, str]:
        """Generate a brand document from Sir's words (new files need no confirm).

        For "invoice Acme for 3 logo designs at 400 each": drafts the line
        items, fills the brand template, saves a PDF and uploads a Google
        Doc. Without Google connected, the local PDF is still produced.

        Args:
            kind: Document kind ("invoice" for now).
            request: Sir's words, verbatim: client, items, quantities, prices.
            account: "personal" (default) or "school" (shared to his school account).
        """
        _guard()
        import docgen

        kind = (kind or "invoice").strip().lower() or "invoice"
        try:
            fields = await asyncio.to_thread(
                docgen.draft_fields_from_request, kind, request.strip()
            )
            result = await asyncio.to_thread(docgen.deliver, kind, fields)
        except docgen.DocgenError as exc:
            raise ToolError(str(exc)) from exc
        log_action("docgen", f"{kind} {result['title'][:80]}")
        import google_api

        if result.get("drive_id") and google_api.normalize_account(account) == "school":
            shared = await _share_for_school({"id": result["drive_id"]}, result["title"])
            return {"say": f"{result['say']} {shared['say']}", "pdf": result["pdf"]}
        return {"say": result["say"], "pdf": result["pdf"]}
