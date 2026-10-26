"""Visible desktop URL requests; no automation profile or visual-success claims."""

import asyncio
import re
import shutil
from urllib.parse import quote, urlsplit

from livekit.agents.llm import ToolError

import action_guard
import phone_session
from action_outcome import ActionOutcome, parse_outcome
from system import LocalSystemError, require_local
from system.desktop import sandbox_display
from system.launcher import blocked_say, is_blocked, require_launch_allowed
from system.school_tools import loud_guard

_REAPERS = set()
_SAY = "Requested a new tab in your desktop Brave; the page is not visually verified."


def normalize_url(value: str) -> str:
    from browser import BrowserError, BrowserManager
    from tools import resolve_url

    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    value = resolve_url(value)
    try:
        BrowserManager._validate_url(value)
        parsed = urlsplit(value)
        if re.search(r"[\x00-\x20\x7f]", parsed.netloc) or re.search(
            r"[\x00-\x1f\x7f]", value
        ):
            raise BrowserError("That URL is not safe to open.")
        if not parsed.hostname:
            raise BrowserError("That URL is not safe to open.")
        _ = parsed.port
    except (BrowserError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    return quote(value, safe=":/?#[]@!$&'()*+,;=%")


async def launch(url: str, *, ticket=None, identity=None) -> dict:
    url = normalize_url(url)
    args = {"app": "brave", "url": url}
    identity = identity or action_guard.request_id(None)
    source = "visible-url"
    try:
        require_local()
        if phone_session.guest_required():
            raise ToolError("Guest mode refuses owner actions.")
        phone_session.check_approval_effect(ticket, current=True)
        loud_guard("open_app", args)
        if sandbox_display():
            raise ToolError(
                "Visible link requests require the owner's desktop, not a sandbox display."
            )
        import os

        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise ToolError("No visible desktop session is available.")
        binary = shutil.which("brave-browser") or shutil.which("brave")
        if not binary:
            raise ToolError("Desktop Brave is unavailable; I have not opened the link.")
        argv = [binary, "--new-tab", url]
        if blocked := is_blocked(*argv):
            raise ToolError(blocked_say(blocked))
        require_launch_allowed(argv)
        if prior := action_guard.claim("open_app", args, source, request_id=identity):
            # Preserve the recorded outcome, including pending status. Never
            # translate another executor's process launch into visual evidence.
            receipt = parse_outcome(prior.get("outcome"))
            if receipt is None:
                receipt = ActionOutcome(
                    "url.launch",
                    "pending" if prior.get("ok") is None else "dispatched_unverified",
                    "unknown",
                    surface="owner_desktop",
                    request_id=identity,
                )
            return receipt.result(
                status="duplicate_request",
                visually_confirmed=False,
                note="Do not launch again or claim the page is visible.",
                duplicate_of=receipt.action_id,
            )
        spawned = False
        try:
            # Last authority check directly at the effect boundary.
            phone_session.check_approval_effect(ticket, current=True)
            if phone_session.guest_required():
                raise ToolError("Guest mode refuses owner actions.")
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            spawned = True
            reaper = asyncio.create_task(proc.wait())
            _REAPERS.add(reaper)
            reaper.add_done_callback(_REAPERS.discard)
            try:
                rc = await asyncio.wait_for(asyncio.shield(reaper), timeout=0.5)
            except asyncio.TimeoutError:
                rc = None  # Long-lived first browser process; launch only.
            if rc not in (None, 0):
                spawned = False
                raise ToolError(
                    "Desktop Brave rejected the launch; I have not verified an opened page."
                )
            receipt = ActionOutcome(
                "url.launch",
                "launch_requested",
                "started",
                surface="owner_desktop",
                evidence={"launcher_exit_code": rc},
                request_id=identity,
            )
            action_guard.settle(
                "open_app", args, source, True, request_id=identity, outcome=receipt
            )
            return receipt.result(
                **{
                    "say": _SAY,
                    "url": url,
                    "status": "launch_requested",
                    "launcher_exit_code": rc,
                    "visually_confirmed": False,
                    "browser": "desktop_brave",
                }
            )
        except BaseException:
            # Cancellation after dispatch is uncertain, so suppress retries
            # without calling it visually successful or killing the browser.
            action_guard.settle(
                "open_app",
                args,
                source,
                spawned,
                _SAY if spawned else "",
                request_id=identity,
                outcome=ActionOutcome(
                    "url.launch",
                    "dispatched_unverified" if spawned else "failed",
                    "started" if spawned else "not_started",
                    "unavailable" if spawned else "not_attempted",
                    "owner_desktop",
                    reason="launch_interrupted",
                    request_id=identity,
                ),
            )
            raise
    except (LocalSystemError, OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
