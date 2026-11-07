"""HUD data-channel task events (Region 2 publisher).

Agent publishes small JSON packets on the LiveKit data channel topic
``jarvis-tasks`` around tool invocation::

    {"kind": "tool_start", "tool": "nmap_scan", "label": "scanning network…"}
    {"kind": "tool_finish", "tool": "nmap_scan", "ok": true}
    {"kind": "intent_echo", "echo": "hot rod red → crimson"}

Packets are best-effort: publish failures are swallowed so HUD telemetry
can never break a voice session. Every event is also mirrored to
``~/.jarvis/actions.log`` so the node graph has a last-known-topology
fallback when the channel is silent. Zero inference cost.
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Any

from log_tail import tail_lines

TOPIC = "jarvis-tasks"
_CAPTION_LOCK = RLock()

#: Conversation mirror for HUDs without a LiveKit client (the Tauri
#: webview has no WebRTC, so it can never join a room — it reads these).
CAPTIONS_FILE = "captions.log"
CAPTIONS_KEEP = 200

FRIENDLY_LABELS: dict[str, str] = {
    "nmap_scan": "scanning network…",
    "nikto_sweep": "sweeping web server…",
    "gobuster_dir": "enumerating paths…",
    "open_app": "opening app…",
    "open_url": "opening page…",
    "take_screenshot": "capturing screen…",
    "take_os_screenshot": "capturing screen…",
    "read_screen_text": "reading screen…",
}


def tool_start_payload(tool: str, label: str | None = None, detail: str = "") -> str:
    return json.dumps(
        {
            "kind": "tool_start",
            "tool": tool,
            "label": label or FRIENDLY_LABELS.get(tool, f"{tool}…"),
            "detail": detail[:200],
            "ts": int(time.time() * 1000),
        }
    )


def tool_finish_payload(tool: str, ok: bool = True, detail: str = "") -> str:
    return json.dumps(
        {
            "kind": "tool_finish",
            "tool": tool,
            "ok": ok,
            "detail": detail[:200],
            "ts": int(time.time() * 1000),
        }
    )


def tool_execution_results(event) -> list[tuple[str, bool]]:
    """Read errors from paired SDK outputs, including failed command exit codes."""
    from action_outcome import parse_outcome
    from claim_guard import sdk_tool_result

    results = []
    for call, output in event.zipped():
        name = str(getattr(call, "name", "unknown"))
        ok = output is not None and not bool(getattr(output, "is_error", False))
        data = sdk_tool_result(output)
        if isinstance(data, dict):
            ok = ok and data.get("ok") is not False and not data.get("error")
            if "rc" in data:
                ok = ok and str(data["rc"]) == "0"
            receipt = parse_outcome(data)
            if receipt is not None:
                ok = ok and receipt.accepted
        results.append((name, bool(ok)))
    return results


def intent_echo_payload(resolved: str, raw: str = "") -> str:
    echo = f"{raw} → {resolved}" if raw and raw != resolved else resolved
    return json.dumps(
        {"kind": "intent_echo", "echo": echo[:200], "ts": int(time.time() * 1000)}
    )


async def publish(room: Any, payload: str) -> None:
    """Best-effort data-channel publish. Never raises."""
    try:
        local = getattr(room, "local_participant", None)
        if local is None:
            return
        await local.publish_data(payload, topic=TOPIC)
    except Exception:
        pass


def mirror_to_log(tool: str, kind: str, detail: str = "") -> None:
    """Append to actions.log so the graph has a fallback topology."""
    from phone_session import private

    if private():
        return
    try:
        from system import log_action

        log_action(f"hud:{kind}", f"{tool} {detail[:200]}".strip())
    except Exception:
        pass
    mirror_to_activity(tool, kind, detail)


def jarvis_activity_id(tool: str) -> str:
    """Activity id for a Jarvis tool ("open_app" -> "jarvis-open-app"). Pure."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(tool or "").lower()).strip("-")
    return f"jarvis-{slug or 'tool'}"[:64]


def mirror_to_activity(tool: str, kind: str, detail: str = "") -> None:
    """Jarvis tool runs join the HUD EXECUTION feed (src/activity.py): the
    webview has no WebRTC, so the data-channel copy never reached it."""
    from phone_session import private

    if private():
        return
    try:
        import activity

        item_id = jarvis_activity_id(tool)
        if kind == "tool_start":
            activity.start(
                "task",
                FRIENDLY_LABELS.get(tool, f"{tool}…"),
                detail="Jarvis",
                source="jarvis",
                item_id=item_id,
            )
        elif kind == "tool_finish":
            activity.finish(item_id, ok=detail != "error")
    except Exception:
        pass


#: Live subtitles: a partial transcript earns a caption line when it grew
#: by this many chars or this many seconds passed (overlay shows the tail,
#: so interim lines read as live typing, not lag).
PARTIAL_MIN_CHARS = 8
PARTIAL_MIN_SECONDS = 1.5


def partial_changed(last: str, last_ts: float, text: str, now: float) -> bool:
    """Should an interim transcript line be captioned? Pure."""
    text = " ".join(str(text or "").split())
    if not text or text == last:
        return False
    if len(text) >= len(last) + PARTIAL_MIN_CHARS:
        return True
    return (now - last_ts) >= PARTIAL_MIN_SECONDS


def captions_path() -> Path:
    """Captions file under $JARVIS_HOME (default ~/.jarvis). Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    base = Path(home) if home else Path.home() / ".jarvis"
    return base / CAPTIONS_FILE


def _active_speaker() -> str:
    try:
        try:
            from character_runtime import get_runtime
        except ImportError:
            from src.character_runtime import get_runtime
        return get_runtime().active_id()
    except Exception:
        return "jarvis"


@contextmanager
def _caption_transaction(path: Path):
    """Protect append/prune across threads and separate desktop workers."""
    with _CAPTION_LOCK, path.with_suffix(".lock").open("a") as lock:
        try:
            import fcntl
        except ImportError:  # Non-POSIX clients still serialize local writers.
            yield
        else:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _replace_caption_file(path: Path, text: str) -> None:
    """Publish complete contents using a temporary file unique to this writer."""
    tmp = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as stream:
            tmp = Path(stream.name)
            stream.write(text)
        tmp.replace(path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def caption(role: str, text: str) -> bool:
    """Append one `ts\\trole\\ttext` line, pruning to the last keep window.

    Fail-soft, never raises. Roles: "sir" (user) / "jarvis" (assistant).
    """
    from phone_session import private

    if private():
        return False  # Private phone speech never enters the shared desktop log.
    try:
        text = " ".join(str(text or "").split())
        if not text:
            return False
        path = captions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _caption_transaction(path):
            with path.open("a", encoding="utf-8") as fh:
                fh.write(
                    f"{int(time.time())}\t{role}\t{text[:500]}\t{_active_speaker()}\n"
                )
            lines = tail_lines(path, CAPTIONS_KEEP + 51)
            if len(lines) > CAPTIONS_KEEP + 50:
                _replace_caption_file(path, "\n".join(lines[-CAPTIONS_KEEP:]) + "\n")
        return True
    except OSError:
        return False


#: Jarvis's in-progress line, rewritten word by word as the audio plays
#: (fed by the agent's synced transcription output). One JSON object:
#: {"id", "text", "ts", "done"}.
LIVE_CAPTION_FILE = "caption_live.json"


def live_caption(uid: str, text: str, done: bool = False) -> bool:
    """Atomically replace the live Jarvis line. Fail-soft, never raises."""
    from phone_session import private

    if private():
        return False
    try:
        path = captions_path().with_name(LIVE_CAPTION_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        _replace_caption_file(
            path,
            json.dumps(
                {
                    "id": str(uid),
                    "text": " ".join(str(text or "").split())[:2000],
                    "ts": time.time(),
                    "done": bool(done),
                }
            ),
        )
        return True
    except OSError:
        return False


def read_captions(limit: int = 20) -> list[dict]:
    """Tail of the captions file as [{ts, role, text}]. Never raises."""
    try:
        lines = tail_lines(captions_path(), max(1, limit))
    except OSError:
        return []
    out: list[dict] = []
    for line in lines:
        parts = line.split("\t", 3)
        if len(parts) not in (3, 4):
            continue
        try:
            ts = int(parts[0])
        except ValueError:
            continue
        speaker = parts[3].strip() if len(parts) == 4 and parts[3].strip() else "jarvis"
        out.append({"ts": ts, "role": parts[1], "text": parts[2], "speaker": speaker})
    return out
