"""Voice tools to delegate a task to Meta's Muse agent (see system/muse.py).

Two steps, like every outbound action Jarvis takes: delegate_to_muse
reads the brief back and arms a one-shot confirmation; confirm_muse_send
actually sends it, over email or WhatsApp, and the authorization burns.
Nothing leaves without Sir's confirm.

Senders are injected so the same path is exercised by the tests. In
production they default to the Gmail send API and the WhatsApp watcher.
"""

from __future__ import annotations

import asyncio

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, muse, require_local


def _default_email_sender(to: str, subject: str, body: str) -> None:
    from system.inbox import (
        _gmail_access_token,
        _gmail_send_api,
        _mail_log,
        build_mime_b64,
    )

    token = _gmail_access_token()
    _gmail_send_api(token, build_mime_b64(to, subject, body))
    _mail_log("sent", to=to, subject=subject, body=body)


async def _default_wa_sender(name: str, text: str) -> None:
    from system.whatsapp import send_chat

    res = await send_chat(name, text)
    if not res.get("ok"):
        raise RuntimeError(res.get("err") or "WhatsApp send failed")


class MuseTools:
    """Delegate work to Muse by email or WhatsApp. Register via .tools."""

    def __init__(self, email_sender=None, wa_sender=None) -> None:
        # email_sender(to, subject, body) -> None; wa_sender(name, text) -> awaitable
        self._email_sender = email_sender or _default_email_sender
        self._wa_sender = wa_sender or _default_wa_sender
        # Armed by delegate_to_muse, burned by confirm_muse_send.
        self._pending: tuple[str, str] | None = None  # (channel, clean task)

    @property
    def tools(self) -> list:
        # Opt-in: until Muse is configured the tools are not offered at all,
        # so Jarvis never mentions or depends on it.
        if not muse.enabled():
            return []
        return [self.delegate_to_muse, self.confirm_muse_send]

    @function_tool()
    async def delegate_to_muse(
        self, context: RunContext, task: str, via: str = "auto"
    ) -> dict[str, str]:
        """Hand a task to Meta's Muse agent — READ-BACK step, sends nothing.

        Use when Sir says things like "get Muse to ...", "delegate this to
        Muse", "have Muse draft/research/build ...". This reads the brief
        back and waits; call confirm_muse_send only after Sir confirms.

        Args:
            task: What Muse should do, in full.
            via: email, whatsapp, or auto (email if an address is set).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        if not muse.enabled():
            raise ToolError("Muse isn't set up, Sir.")
        clean = muse.clean_task(task)
        if not clean:
            raise ToolError("What should I ask Muse to do, Sir?")
        channel = muse.pick_channel(via)
        if channel == "email" and not muse.muse_email():
            raise ToolError(
                "I don't have Muse's email address yet, Sir. Set JARVIS_MUSE_EMAIL "
                "or tell me the address, or I can send it over WhatsApp instead."
            )
        self._pending = (channel, clean)
        log_action("muse", f"propose {channel}: {clean[:160]}")
        return {"say": muse.preview(clean, channel)}

    @function_tool()
    async def confirm_muse_send(
        self, context: RunContext, task: str = "", via: str = ""
    ) -> dict[str, str]:
        """Send the delegation Sir just confirmed. Requires delegate_to_muse first.

        Call only after Sir explicitly approves. The armed brief is sent and
        the authorization burns.

        Args:
            task: Ignored unless given; the armed brief is what is sent.
            via: Ignored; the armed channel is what is used.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        if not muse.enabled():
            raise ToolError("Muse isn't set up, Sir.")
        if self._pending is None:
            raise ToolError(
                "Nothing is queued for Muse, Sir. Tell me the task first and "
                "I'll read it back before sending."
            )
        channel, clean = self._pending
        try:
            if channel == "email":
                to = muse.muse_email()
                if not to:
                    raise ToolError("Muse's email address is no longer set, Sir.")
                subject, body = muse.email_brief(clean)
                await asyncio.to_thread(self._email_sender, to, subject, body)
                where = to
            else:
                await self._wa_sender(muse.muse_chat(), muse.wa_brief(clean))
                where = muse.muse_chat()
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(
                f"I couldn't reach Muse over {channel}, Sir: {exc}"
            ) from exc
        finally:
            self._pending = None
        log_action("muse", f"sent {channel} to={where}: {clean[:160]}")
        return {"say": f"Delegated to Muse over {channel} ({where}), Sir."}
