"""Voice tools for email drafts: list, read, revise, discard, send.

Drafts come from draft_engine (stored in ~/.jarvis/drafts). Nothing here
sends on its own: draft_send reads the draft back and only proceeds when
Sir has authorised that exact to/subject/body through
InboxTools.confirm_email_action, then goes through the existing
gmail_reply path, so the single-use confirm gate is never weakened.
"""

from __future__ import annotations

import asyncio

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

import draft_engine
from system import LocalSystemError, log_action, require_local
from system.inbox import InboxTools, _draft_key

LIVE = ("pending", "announced")


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

    @property
    def tools(self) -> list:
        return [
            self.draft_list,
            self.draft_read,
            self.draft_revise,
            self.draft_discard,
            self.draft_send,
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
        say, _opened = drafts_ui.open_drafts()
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
