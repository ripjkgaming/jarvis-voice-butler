"""Volatile commands for a simulated suit panel, never hardware control.

Only the bridge's authenticated /suit handler changes its canonical state.
Agent processes may import this module, but send every command back to that
handler; their private copy of the state is never used to open the desktop UI.
"""

from __future__ import annotations

import re
import threading
import time
import uuid


class CommandState:
    """One bridge lifetime. No files, startup restore, timers, or background work."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._intent = 0
        self._value = {
            "op": "close",
            "revision": 0,
            "issued_at": 0,
            "session": uuid.uuid4().hex,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._value)

    def set_open(self, opened: bool) -> dict:
        if type(opened) is not bool:
            raise ValueError("open must be a boolean")
        with self._lock:
            self._intent += 1
            return self._publish(opened)

    def begin_open(self) -> int:
        """Reserve ordering before asking the native host to show its window."""
        with self._lock:
            self._intent += 1
            return self._intent

    def complete_open(self, intent: int) -> tuple[dict, bool]:
        with self._lock:
            if intent != self._intent:
                return dict(self._value), True
            return self._publish(True), False

    def _publish(self, opened: bool) -> dict:
        # Caller holds the lock. Every explicit successful command is fresh,
        # including reopen after a frontend reload ignored the old command.
        self._value = {
            **self._value,
            "op": "open" if opened else "close",
            "revision": self._value["revision"] + 1,
            "issued_at": max(int(time.time() * 1000), self._value["issued_at"] + 1),
        }
        return dict(self._value)


_state = CommandState()


def snapshot() -> dict:
    return _state.snapshot()


def set_open(opened: bool) -> dict:
    """Bridge handler only. All voice executors use request_visibility instead."""
    return _state.set_open(opened)


def _shell_running() -> bool:
    """Probe the existing Tauri instance; never start a shell to show this panel."""
    from active_window import _qdbus

    return (
        _qdbus(
            [
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus.NameHasOwner",
                "dev.jarvis.shell.SingleInstance",
            ],
            timeout=1.0,
        )
        == "true"
    )


def apply_visibility(opened: bool) -> tuple[int, dict]:
    """Authenticated bridge handler only: request show, then publish the command.

    Native show is intentionally outside the state lock: a close remains instant
    even if the shell stalls. An older native reply cannot overwrite that close.
    CLI success acknowledges request acceptance, not compositor rendering.
    """
    import projects

    if type(opened) is not bool:
        raise ValueError("open must be a boolean")
    if not opened:
        return 200, {"ok": True, "suit_diagnostics": set_open(False)}
    intent = _state.begin_open()
    if not _shell_running():
        return 503, {"ok": False, "error": "The Jarvis desktop shell isn't running"}
    try:
        shown = projects.shell_verb("suitshow")
    except Exception:
        shown = False
    if not shown:
        return 503, {"ok": False, "error": "The suit diagnostics window didn't respond"}
    state, superseded = _state.complete_open(intent)
    result = {"ok": True, "suit_diagnostics": state}
    if superseded:
        result.update(
            superseded=True,
            say="A newer panel command superseded that request, Sir.",
        )
    return 200, result


_MENTION = re.compile(r"\bsuit\s+diagnostics?\b", re.IGNORECASE)
_PREFIX = re.compile(
    r"^(?:(?:hey\s+)?(?:jarvis|jeeves|jarves|jervis)\b|please\b|"
    r"(?:can|could|would|will)\s+you\b)[\s,.:;!?\-]*"
)
_COMMAND = re.compile(
    r"(?P<verb>bring up|pull up|show|open|display|dismiss|close|hide|put away)\s+"
    r"(?:(?:the|my)\s+)?(?:simulated\s+)?suit\s+diagnostics?"
    r"(?:\s+panel)?(?:\s+please)?"
)


def mentions_suit(text: str) -> bool:
    """Reserve suit wording so rejected requests never become generic app launches."""
    return isinstance(text, str) and bool(_MENTION.search(text))


def parse_command(text: str) -> bool | None:
    """Exact open/close intent. Negation, questions and compound requests fail closed."""
    if not isinstance(text, str) or len(text) > 500:
        return None
    clean = " ".join(text.lower().strip().strip(".!?,; ").split())
    previous = None
    while previous != clean:
        previous = clean
        clean = _PREFIX.sub("", clean).strip()
    match = _COMMAND.fullmatch(clean)
    if not match:
        return None
    return match["verb"] not in {"dismiss", "close", "hide", "put away"}


def reply_for(opened: bool) -> str:
    return (
        "Simulated suit diagnostics are up, Sir."
        if opened
        else "Suit diagnostics dismissed, Sir."
    )


def request_visibility(opened: bool) -> dict | None:
    """Route agent/phone execution to the canonical bridge, including self-calls.

    The bridge uses ThreadingHTTPServer, so a /route worker can make this
    authenticated /suit call without blocking the server's request loop.
    """
    from system import require_local
    from system.projects_tools import _bridge_call

    require_local()
    if type(opened) is not bool:
        raise ValueError("open must be a boolean")
    result = _bridge_call("POST", "/suit", {"open": opened})
    return result if isinstance(result, dict) else None
