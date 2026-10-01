"""Voice-only Drafts window's command bus.

Modelled on the Project Archive bus in projects.py: the Tauri shell owns a
window labelled ``drafts`` with CLI verbs ``draftsshow`` / ``draftshide`` /
``draftsstate``. Those verbs run through :func:`projects.shell_verb`, which
is generic (it just appends the verb to the shell binary argv).

Nothing here touches Gmail or the network: drafts come from draft_engine.
Everything is fail-soft and the shell runner is injectable for tests.
"""

from __future__ import annotations

import contextlib
import re
from typing import Callable

LIVE = ("pending", "announced")

_OPEN = re.compile(
    r"\b(?:open|show|pull\s+up|bring\s+up)\b"
    r".*?\b(?:my\s+)?(?:email\s+|reply\s+)?drafts\b"
)
# "open drafts and approve all": the window verb alone would silently drop
# the rest of the request and report success. Anything that also asks to
# send/approve/discard/revise belongs to the agent, which has the tools.
_ACTS = re.compile(
    r"\b(?:approve|approved|send|sent|respond|discard|delete|"
    r"reject|revise|rewrite|edit|read|accept|confirm|ok|okay)\b"
)
_CLOSE = re.compile(
    r"\b(?:close|hide|dismiss|put\s+away)\b"
    r".*?\b(?:my\s+)?(?:email\s+|reply\s+)?drafts\b"
)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower()).split())


def pending_drafts() -> list[dict]:
    """Drafts with status pending or announced, oldest first. Never raises."""
    try:
        import draft_engine
    except Exception:
        return []
    try:
        rows = [d for s in LIVE for d in draft_engine.list_drafts(s)]
    except Exception:
        return []
    rows.sort(key=lambda d: float(d.get("created") or 0))
    return rows


def parse_voice(text: str) -> dict | None:
    """Spoken Drafts-window command -> {"op": ...}, or None if not ours.

    Open: open/show/pull up/bring up + drafts (also "my drafts",
    "email drafts", "reply drafts"). Close: close/hide/dismiss/put away
    + drafts. Pure. Never matches project phrases, singular-draft
    phrases like "draft a whatsapp" / "draft an email", or compound
    requests ("open drafts and approve all") that need more than the window.
    """
    norm = _norm(text)
    if not norm or "drafts" not in norm:
        return None
    # Another surface owns these: never steal them.
    if "project" in norm or "archive" in norm or "whatsapp" in norm:
        return None
    if _ACTS.search(norm):
        return None
    open_m = _OPEN.search(norm)
    close_m = _CLOSE.search(norm)
    if open_m is None and close_m is None:
        return None
    if open_m is not None and close_m is not None:
        return {"op": "open"} if open_m.start() <= close_m.start() else {"op": "close"}
    return {"op": "open"} if open_m is not None else {"op": "close"}


def _shell(verb: str, run=None) -> bool:
    import projects

    try:
        if run is None:
            return bool(projects.shell_verb(verb))
        return bool(projects.shell_verb(verb, run))
    except Exception:
        return False


def open_drafts(run: Callable | None = None) -> tuple[str, bool]:
    """Show the Drafts window. (say, opened). Never raises.

    Empty (no pending/announced drafts) answers "No drafts, Sir." and
    never calls the shell.
    """
    try:
        rows = pending_drafts()
    except Exception:
        rows = []
    if not rows:
        return "No drafts, Sir.", False
    n = len(rows)
    say = "One draft, Sir." if n == 1 else f"{n} drafts, Sir."
    with contextlib.suppress(Exception):
        opened = _shell("draftsshow", run)
        return say, bool(opened)
    return say, False


def close_drafts(run: Callable | None = None) -> bool:
    """Hide the Drafts window. Never raises."""
    with contextlib.suppress(Exception):
        return _shell("draftshide", run)
    return False


def sync_close(run: Callable | None = None) -> bool:
    """Hide the window when no live drafts remain. Never raises.

    Returns True only when there were no pending/announced drafts
    (the window should be closed); False leaves it alone. Used after
    a send/discard so the window closes itself.
    """
    try:
        if pending_drafts():
            return False
    except Exception:
        return False
    with contextlib.suppress(Exception):
        _shell("draftshide", run)
    return True
