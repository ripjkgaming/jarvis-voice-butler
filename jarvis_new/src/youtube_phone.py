"""Download follow-ups scoped to a phone client; unrelated yes/no stays chat."""

from __future__ import annotations

import re
import threading
import time

_lock = threading.Lock()
_pending = {}
_recent = {}
TOOLS = frozenset(
    {
        "download_youtube_video",
        "confirm_youtube_download",
        "cancel_youtube_download",
        "youtube_download_status",
    }
)


def remember(client_id: str, job: dict) -> None:
    with _lock:
        now = time.monotonic()
        for mapping in (_pending, _recent):
            for key, value in list(mapping.items()):
                if value[0] < now:
                    mapping.pop(key, None)
        if job.get("status") == "confirm":
            _pending[client_id] = (now + 120, job["confirmation_id"])
        else:
            _pending.pop(client_id, None)
        if job.get("id"):
            _recent[client_id] = (now + 86400, job["id"])


def command(text: str, client_id: str = "default"):
    clean = re.sub(
        r"^(?:(?:hey|please|jarvis)[ ,]+)+", "", text.strip(), flags=re.I
    ).strip()
    lower = clean.lower().rstrip(" .!?")
    with _lock:
        pending = _pending.get(client_id)
        recent = _recent.get(client_id)
    if (
        pending
        and pending[0] > time.monotonic()
        and lower
        in {"yes", "yeah", "yep", "yes please", "this one", "no", "nope", "no thanks"}
    ):
        return (
            "confirm_youtube_download",
            {
                "confirmation_id": pending[1],
                "confirmed": lower not in {"no", "nope", "no thanks", "cancel"},
                "_client_id": client_id,
            },
            None,
        )
    if re.fullmatch(r"(?:cancel|stop)(?: the)?(?: youtube)? downloads?", lower) or (
        lower == "cancel" and recent
    ):
        return (
            "cancel_youtube_download",
            {"job_id": recent[1] if recent else "", "_client_id": client_id},
            None,
        )
    if lower in {
        "download status",
        "youtube download status",
        "how is the download",
        "is the download done",
    }:
        if recent and recent[0] > time.monotonic():
            return (
                "youtube_download_status",
                {"job_id": recent[1], "_client_id": client_id},
                None,
            )
        return None
    if re.match(r"^(?:download|save|grab|get me)\s+\S", clean, re.I):
        return "download_youtube_video", {"url": clean, "_client_id": client_id}, None
    return None
