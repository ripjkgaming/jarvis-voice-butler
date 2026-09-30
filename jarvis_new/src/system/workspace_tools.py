"""Voice tools for Google Docs/Sheets/Drive, Notion, and document generation.

Reads are direct; any write that touches existing external data (sheet
rows, Notion notes) is confirm-gated like email in inbox.py: the agent
reads the change back, Sir approves, confirm_workspace_action authorizes
exactly one matching write. Creating brand-new docs/pages/invoices is
safe and reversible, so it skips the gate.
"""

from __future__ import annotations

import asyncio
import contextlib
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
        # Events the backend model read out of a schedule file, waiting
        # for Sir's go-ahead before they touch his calendar.
        self._pending_events: list[dict] = []
        # Backend-model jobs run as background tasks so the voice model
        # keeps talking; hold references so they aren't garbage-collected.
        self._tasks: set[asyncio.Task] = set()
        self._importing = False

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
            self.import_schedule_to_calendar,
            self.confirm_calendar_import,
            self.calendar_upcoming,
            self.email_events,
            self.confirm_email_event,
            self.dismiss_email_event,
            self.ask_backend,
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

    def _in_background(self, context: RunContext, job, *, fallback: str) -> None:
        """Run `job` (async -> instructions or None) off the voice turn.

        When it finishes, the voice model is prompted with the returned
        instructions so the result joins the conversation in Jarvis's own
        words; if the realtime model balks, a fixed line is spoken instead.
        """
        session = getattr(context, "session", None)

        async def run() -> None:
            try:
                instructions = await job()
            except Exception as exc:  # never let a background job die silently
                instructions = f"Tell Sir briefly that the background job failed: {str(exc)[:200]}"
            if not instructions or session is None:
                return
            try:
                await session.generate_reply(instructions=instructions)
            except Exception:
                with contextlib.suppress(Exception):
                    await session.say(fallback)

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @function_tool()
    async def import_schedule_to_calendar(
        self, context: RunContext, file_hint: str = "", only: str = ""
    ) -> dict[str, str]:
        """Read an exam/mock/school schedule file and prepare its dates for Sir's Google Calendar.

        Runs in the BACKGROUND on the backend model (Sonnet 5.5): this returns
        at once, so carry on the conversation normally. When the file has
        been read you'll be prompted to read the preview to Sir; nothing is
        added until Sir says yes and you call confirm_calendar_import.

        Args:
            file_hint: File name words or a path ("mock exam timetable"); empty = newest exam/mock/timetable file in Downloads.
            only: Optional filter, e.g. "just my subjects: maths, physics".
        """
        _guard()
        import backend_model

        if self._importing:
            return {"say": "I'm still reading the last schedule, Sir; I'll tell you when it's done."}
        path = await asyncio.to_thread(backend_model.find_schedule_file, file_hint)
        if path is None:
            raise ToolError(
                "I can't find that schedule, Sir. Save it to Downloads, or tell me the file name."
            )
        self._importing = True

        async def job() -> str:
            try:
                events, warning = await asyncio.to_thread(
                    backend_model.extract_events, path, focus=only
                )
            finally:
                self._importing = False
            if warning:
                return f"Tell Sir briefly that reading {path.name} failed: {warning}."
            self._pending_events = events
            log_action("calendar", f"extracted {len(events)} from {path.name[:60]}")
            saved = 0
            with contextlib.suppress(Exception):
                import exams

                saved = exams.import_events(events)
            kept = (
                f" Mention that {saved} exams are also saved to your exam schedule, "
                "so Sir can ask about them any time."
                if saved
                else ""
            )
            return (
                f"The schedule {path.name} has been read. Tell Sir, briefly and naturally, "
                f"that you found {len(events)} entries: {describe_events(events)}. "
                "Then ask whether to add them to his calendar. If he says yes, call "
                "confirm_calendar_import." + kept
            )

        self._in_background(
            context, job, fallback="I've read your schedule, Sir. Shall I add it to your calendar?"
        )
        return {"say": f"Reading {path.name} now, Sir. Carry on; I'll tell you what I find."}

    @function_tool()
    async def confirm_calendar_import(self, context: RunContext) -> dict[str, str]:
        """Add the previewed schedule entries to Sir's Google Calendar.

        Call ONLY after Sir explicitly says yes to the import_schedule_to_calendar preview.
        Entries already on the calendar (same title and day) are skipped.
        """
        _guard()
        import google_api

        events, self._pending_events = self._pending_events, []
        if not events:
            raise ToolError("There's nothing waiting to be added, Sir.")
        dates = sorted(e["date"] for e in events)
        tz_day = "T00:00:00Z"
        try:
            import datetime as dt

            last = (dt.date.fromisoformat(dates[-1]) + dt.timedelta(days=2)).isoformat()
            first = (dt.date.fromisoformat(dates[0]) - dt.timedelta(days=1)).isoformat()
            existing = await asyncio.to_thread(
                google_api.calendar_list, first + tz_day, last + tz_day
            )
        except google_api.GoogleError as exc:
            self._pending_events = events
            raise ToolError(str(exc)) from exc
        have = set()
        for item in existing:
            start = item.get("start") or {}
            day = str(start.get("date") or start.get("dateTime") or "")[:10]
            have.add(_event_key(str(item.get("summary", "")), day))
        added, skipped, failed = 0, 0, []
        for e in events:
            if _event_key(e["title"], e["date"]) in have:
                skipped += 1
                continue
            try:
                await asyncio.to_thread(google_api.calendar_create, e)
                added += 1
            except google_api.GoogleError as exc:
                failed.append(e)
                if added == 0 and len(failed) == 1 and "calendar" in str(exc).lower():
                    self._pending_events = events
                    raise ToolError(str(exc)) from exc
        log_action("calendar", f"import added={added} skipped={skipped} failed={len(failed)}")
        say = f"Added {added} to your calendar, Sir."
        if skipped:
            say += f" {skipped} were already there."
        if failed:
            say += f" {len(failed)} failed: {describe_events(failed, 3)}."
        return {"say": say}

    @function_tool()
    async def email_events(self, context: RunContext) -> dict[str, str]:
        """Events Jarvis found in Sir's email and offered to add to his
        calendar, still waiting for a yes or no ("any events from my mail?")."""
        _guard()
        import event_extractor

        rows = event_extractor.pending()
        if not rows:
            return {"say": "No calendar suggestions waiting, Sir."}
        lines = [
            f"{r['event']['title']}, {event_extractor.when_text(r['event'])} (id {r['id']})"
            for r in rows[:5]
        ]
        return {"say": f"{len(rows)} waiting: " + "; ".join(lines)}

    @function_tool()
    async def confirm_email_event(
        self, context: RunContext, msg_id: str = ""
    ) -> dict[str, str]:
        """Add an event Jarvis found in an email to Sir's calendar.

        Call ONLY after Sir explicitly says yes to the "shall I add it to your
        calendar?" offer. Empty msg_id = the most recent pending suggestion.

        Args:
            msg_id: Suggestion id from email_events; empty for the latest.
        """
        _guard()
        import event_extractor
        import google_api
        from proactive.sources import calendar_source

        record = _pick_suggestion(msg_id)
        try:
            await asyncio.to_thread(
                google_api.calendar_create,
                record["event"],
                calendar_source.calendar_id(),
                calendar_source.calendar_account(),
            )
        except google_api.GoogleError as exc:
            raise ToolError(str(exc)) from exc
        event_extractor.set_status(record["id"], "added")
        log_action("calendar", f"email-event added {record['id'][:16]}")
        ev = record["event"]
        return {"say": f"Added {ev['title']}, {event_extractor.when_text(ev)}, to your calendar."}

    @function_tool()
    async def dismiss_email_event(
        self, context: RunContext, msg_id: str = ""
    ) -> dict[str, str]:
        """Sir said no to adding an email's event to his calendar.

        Args:
            msg_id: Suggestion id; empty for the latest pending one.
        """
        _guard()
        import event_extractor

        record = _pick_suggestion(msg_id)
        event_extractor.set_status(record["id"], "dismissed")
        log_action("calendar", f"email-event dismissed {record['id'][:16]}")
        return {"say": f"Very well, I'll leave {record['event']['title']} off the calendar."}

    @function_tool()
    async def calendar_upcoming(self, context: RunContext, days: int = 7) -> dict[str, str]:
        """What's on Sir's Google Calendar over the next few days.

        Args:
            days: How many days ahead to look (1-60).
        """
        _guard()
        import datetime as dt

        import google_api

        days = max(1, min(60, int(days or 7)))
        now = dt.datetime.now(dt.timezone.utc)
        try:
            items = await asyncio.to_thread(
                google_api.calendar_list,
                now.isoformat().replace("+00:00", "Z"),
                (now + dt.timedelta(days=days)).isoformat().replace("+00:00", "Z"),
            )
        except google_api.GoogleError as exc:
            raise ToolError(str(exc)) from exc
        if not items:
            return {"say": f"Your calendar is clear for the next {days} days, Sir."}
        rows = []
        for item in items[:10]:
            start = item.get("start") or {}
            raw = str(start.get("dateTime") or start.get("date") or "")
            try:
                when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
                label = when.strftime("%a %d %b %H:%M") if "T" in raw else when.strftime("%a %d %b")
            except ValueError:
                label = raw
            rows.append(f"{item.get('summary', '(untitled)')}, {label}")
        return {"say": ("Coming up: " + "; ".join(rows))[:1200]}

    @function_tool()
    async def ask_backend(self, context: RunContext, task: str) -> dict[str, str]:
        """Hand a slow, careful job to the backend model (Sonnet 5.5): planning, careful reasoning, long rewrites.

        Runs in the BACKGROUND: this returns at once, so keep talking with Sir
        normally. When the backend finishes you'll be prompted with its answer
        to pass on. Not for web research (use research_project) or chit-chat.

        Args:
            task: The full task with every detail the backend needs.
        """
        _guard()
        import backend_model

        task = (task or "").strip()
        if len(task) < 3:
            raise ToolError("What should the backend work on, Sir?")

        async def job() -> str:
            reply, warning = await asyncio.to_thread(backend_model.backend_think, task)
            if warning:
                return f"Tell Sir briefly that the backend job failed: {warning}."
            log_action("backend", task[:60])
            return (
                "The backend model has finished a job Sir asked for. Task: "
                f"{task[:300]}\n\nIts answer:\n{reply[:6000]}\n\n"
                "Give Sir the gist conversationally in a few sentences and offer detail."
            )

        self._in_background(context, job, fallback="The backend has finished, Sir. Shall I go through it?")
        return {"say": "On it, Sir. That's with the backend; I'll come back to you when it's done."}


def describe_events(events: list[dict], limit: int = 8) -> str:
    """Speakable one-line-per-event preview. Pure."""
    import datetime as dt

    lines = []
    for e in events[:limit]:
        try:
            day = dt.date.fromisoformat(e["date"]).strftime("%a %d %b")
        except (KeyError, ValueError):
            day = e.get("date", "?")
        when = f" at {e['start']}" if e.get("start") else ""
        lines.append(f"{e.get('title', '?')}, {day}{when}")
    more = len(events) - limit
    tail = f"; and {more} more" if more > 0 else ""
    return "; ".join(lines) + tail


def _pick_suggestion(msg_id: str) -> dict:
    """A pending email-event suggestion by id, or the newest. ToolError if none."""
    import event_extractor

    if (msg_id or "").strip():
        record = event_extractor.get(msg_id.strip())
        if record is None or record.get("status") != "pending":
            raise ToolError("I have no pending calendar suggestion with that id, Sir.")
        return record
    rows = event_extractor.pending()
    if not rows:
        raise ToolError("There's no calendar suggestion waiting, Sir.")
    return rows[-1]


def _event_key(title: str, date: str) -> str:
    """Dedupe key: title words + day. Pure."""
    words = re.sub(r"\W+", " ", (title or "").casefold()).strip()
    return f"{words}|{date}"
