"""Voice tools for email drafts: list, read, revise, discard, send.

Drafts come from draft_engine (stored in ~/.jarvis/drafts). Nothing here
sends on its own: draft_send reads the draft back and only proceeds when
Sir has authorised that exact to/subject/body through
InboxTools.confirm_email_action, then goes through the existing
gmail_reply path, so the single-use confirm gate is never weakened.
"""

from __future__ import annotations

import asyncio
import contextlib

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

import draft_engine
from system import LocalSystemError, log_action, require_local
from system.inbox import InboxTools, _draft_key

LIVE = ("pending", "announced")
MAX_BULK = 15


def _label(record: dict) -> str:
    return f"{record.get('sender', 'someone')}: {record.get('subject', '')}"[:120]


def _live(msg_id: str) -> dict:
    """A draft that can still be acted on, or a ToolError."""
    record = draft_engine.read_draft(msg_id)
    if record is None:
        raise ToolError("I have no draft with that id.")
    if record.get("status") not in LIVE:
        raise ToolError(f"That draft is already {record.get('status')}.")
    return record


class DraftTools:
    """Draft voice tools. Shares the agent's InboxTools so the confirm gate is one."""

    def __init__(self, inbox: InboxTools) -> None:
        self._inbox = inbox
        self._bulk_lock = asyncio.Lock()

    @property
    def tools(self) -> list:
        return [
            self.draft_list,
            self.draft_read,
            self.draft_revise,
            self.draft_discard,
            self.draft_send,
            self.draft_approve_all,
            self.open_drafts_ui,
            self.close_drafts_ui,
        ]

    @function_tool()
    async def draft_list(self, context: RunContext) -> dict[str, str]:
        """List the email reply drafts waiting for Sir's review."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        rows = [d for s in LIVE for d in draft_engine.list_drafts(s)]
        if not rows:
            return {"say": "No reply drafts are waiting, Sir."}
        listing = "; ".join(f"{d['id']} ({_label(d)})" for d in rows[:5])
        return {
            "say": f"{len(rows)} draft{'s' if len(rows) != 1 else ''}: {listing}"[:900]
        }

    @function_tool()
    async def draft_read(self, context: RunContext, id: str) -> dict[str, str]:  # noqa: A002
        """Read one drafted reply aloud: recipient, subject, summary and body.

        Args:
            id: Draft id from draft_list or the announcement.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        record = _live(id)
        return {
            "say": (
                f"Reply to {record['to']}, subject {record['subject']}. "
                f"They wrote: {record.get('summary', '')}. My draft: {record['body']}"
            )[:1500]
        }

    @function_tool()
    async def draft_revise(
        self,
        context: RunContext,
        id: str,  # noqa: A002
        instruction: str,
    ) -> dict[str, str]:
        """Revise a drafted reply as Sir instructs (calls the backend model).

        Args:
            id: Draft id.
            instruction: What to change, e.g. "shorter and decline politely".
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        record = _live(id)
        body, warning = await asyncio.to_thread(
            draft_engine.revise_reply, record, instruction
        )
        if body is None:
            raise ToolError(warning or "I could not revise that draft.")
        record["body"] = body
        record["status"] = "announced"
        if draft_engine.write_draft(record) is None:
            raise ToolError("I could not save the revised draft.")
        log_action("drafts", f"revised id={str(id)[:20]}")
        return {"say": f"Revised. New draft: {body}"[:1500]}

    @function_tool()
    async def draft_discard(self, context: RunContext, id: str) -> dict[str, str]:  # noqa: A002
        """Discard a drafted reply without sending it.

        Args:
            id: Draft id.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        record = _live(id)
        draft_engine.set_status(str(record["id"]), "discarded")
        log_action("drafts", f"discarded id={str(id)[:20]}")
        import mail_log

        mail_log.record(
            "discarded", id=record["id"], to=record["to"], subject=record["subject"]
        )
        try:
            import drafts_ui

            drafts_ui.sync_close()
        except Exception:
            pass
        return {"say": f"Discarded the draft to {record.get('sender', 'them')}."}

    @function_tool()
    async def draft_send(self, context: RunContext, id: str) -> dict[str, str]:  # noqa: A002
        """Send an approved draft. Never sends unless Sir has confirmed it.

        First call reads the draft back. Only after Sir says yes, call
        confirm_email_action with exactly the to, subject and body read
        back, then call draft_send again.

        Args:
            id: Draft id.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        record = _live(id)
        key = _draft_key(record["to"], record["subject"], record["body"])
        if self._inbox._confirmed_draft != key:
            raise ToolError(
                "Not authorized yet. Read this back to Sir and ask whether to send it. "
                f"To: {record['to']}. Subject: {record['subject']}. Body: {record['body']} "
                "If he approves, call confirm_email_action with exactly that to, subject "
                "and body, then draft_send again."
            )
        # The existing gate re-checks the exact match and burns the authorization.
        result = await InboxTools.gmail_reply(
            self._inbox, context, str(record["id"]), record["body"]
        )
        draft_engine.set_status(str(record["id"]), "sent")
        log_action("drafts", f"sent id={str(id)[:20]}")
        try:
            import drafts_ui

            drafts_ui.sync_close()
        except Exception:
            pass
        return result

    @function_tool()
    async def draft_approve_all(
        self, context: RunContext, ids: str = ""
    ) -> dict[str, str]:
        """Send every waiting reply draft because Sir said "approve all".

        ONLY for Sir's explicit bulk command: "approve all", "send all the
        drafts", "approve them all". His saying so is the authorization,
        so do not ask again. For one draft, or "approve the one to X", use
        draft_read + confirm_email_action + draft_send. Never call this to
        be helpful on your own. Each draft is sent through the normal
        confirm gate and marked sent only when Gmail accepted it; the
        reply lists exactly what went and what didn't, so report that
        verbatim and never say "approved" for anything it lists as failed.

        Args:
            ids: Optional comma-separated draft ids; empty = every waiting draft.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        wanted = {i.strip() for i in (ids or "").split(",") if i.strip()}
        async with self._bulk_lock:  # a repeated call must not double-send
            rows = [d for s in LIVE for d in draft_engine.list_drafts(s)]
            rows.sort(key=lambda d: float(d.get("created") or 0))
            if wanted:
                rows = [d for d in rows if str(d.get("id")) in wanted]
            if not rows:
                return {"say": "There are no drafts waiting to approve, Sir."}
            sent: list[str] = []
            failed: list[tuple[str, str]] = []
            for record in rows[:MAX_BULK]:
                label = str(record.get("sender") or record.get("to") or "them")
                try:
                    # Sir's "approve all" arms this one draft; the gate in
                    # gmail_reply re-checks the exact to/subject/body.
                    self._inbox._confirmed_draft = _draft_key(
                        record["to"], record["subject"], record["body"]
                    )
                    await InboxTools.gmail_reply(
                        self._inbox, context, str(record["id"]), record["body"]
                    )
                except Exception as exc:
                    failed.append((label, str(exc)[:140] or "send failed"))
                    log_action("drafts", f"bulk fail id={str(record['id'])[:20]}")
                else:
                    draft_engine.set_status(str(record["id"]), "sent")
                    sent.append(label)
                    log_action("drafts", f"bulk sent id={str(record['id'])[:20]}")
                finally:
                    self._inbox._confirmed_draft = None  # never leave it armed
            skipped = max(0, len(rows) - MAX_BULK)
        with contextlib.suppress(Exception):
            import drafts_ui

            drafts_ui.sync_close()
        parts = []
        if sent:
            parts.append(f"Sent {len(sent)}: {', '.join(sent)}.")
        if failed:
            parts.append(
                f"{len(failed)} NOT sent: "
                + "; ".join(f"{who} ({why})" for who, why in failed)
                + "."
            )
        if skipped:
            parts.append(f"{skipped} more left for the next batch.")
        say = " ".join(parts)[:900]
        if not sent:
            raise ToolError(say)
        return {"say": say}

    @function_tool()
    async def open_drafts_ui(self, context: RunContext) -> dict[str, str]:
        """Open the Drafts window when Sir asks to open, show or pull up his drafts.

        Use this for "open my drafts" / "show my email drafts" and the like.
        Says "No drafts, Sir." and opens nothing when no drafts are waiting.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        import drafts_ui

        if not drafts_ui.pending_drafts():
            return {"say": "No drafts, Sir."}
        say, opened = drafts_ui.open_drafts()
        if not opened:
            # The drafts exist but the window never appeared: say so rather
            # than letting "opened" be assumed.
            log_action("drafts", "open failed: shell did not respond")
            return {
                "say": f"{say} I couldn't open the Drafts window though, Sir; "
                "I can read them out instead."
            }
        return {"say": say}

    @function_tool()
    async def close_drafts_ui(self, context: RunContext) -> dict[str, str]:
        """Close the Drafts window when Sir asks to close, hide or dismiss his drafts.

        Use this for "close drafts" / "hide my drafts" and the like.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        import drafts_ui

        drafts_ui.close_drafts()
        return {"say": "Drafts closed."}
