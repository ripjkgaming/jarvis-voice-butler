"""The backend model: Sonnet 5.5 on headless Claude Code, behind the voice model.

Jarvis runs two tiers. The conversation model (Gemini realtime for voice,
Ling on OpenRouter for text) talks to Sir and must answer in well under a
second. The backend model (claude_cli.BACKEND_MODEL, Sonnet 5.5 via `claude
-p` on Sir's subscription) gets the slow, careful jobs the conversation
model hands off: reading a document and turning it into structured data,
multi-step planning, anything that needs real thought. It never speaks to
Sir directly; its answer comes back through a voice tool.

Fail-soft like claude_cli: public functions return (result, warning).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path

import claude_cli

BACKEND_SYSTEM = (
    "You are the backend reasoning engine for Jarvis, a personal assistant. "
    "Another model handles conversation; you do careful work it hands you. "
    "Be accurate and concise. Never invent facts that are not in the input."
)

#: File types Claude Code's Read tool understands natively.
READABLE = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".txt", ".md", ".csv", ".ics"}

EXTRACT_PROMPT = """Today is {today}. Read the file ./{name} with the Read tool.
It is a schedule (for example an exam or mock timetable). Extract EVERY dated
entry that belongs on a calendar{focus}.

Reply with ONLY a JSON array, no prose, no code fence. Each item:
{{"title": str, "date": "YYYY-MM-DD", "start": "HH:MM" or "", "end": "HH:MM" or "",
  "location": str, "notes": str}}

Rules:
- title: short and specific, e.g. "Mock: Maths Paper 2".
- If a year is missing, choose the next occurrence on or after today.
- 24-hour times. Leave start/end "" when the file gives no time.
- notes: paper code, duration, seat or room details the file gives; else "".
- Skip entries you cannot date. Do not guess dates."""


def backend_think(task: str, *, timeout: float = 180.0, runner=None) -> tuple[str, str | None]:
    """One careful backend answer for a task the voice model handed off."""
    task = (task or "").strip()
    if not task:
        return "", "No task given"
    return claude_cli.claude_reply(
        task[:20000],
        model=claude_cli.BACKEND_MODEL,
        system=BACKEND_SYSTEM,
        timeout=timeout,
        runner=runner,
    )


def parse_events(reply: str) -> list[dict]:
    """Backend reply -> clean event dicts (drops malformed items). Pure."""
    text = (reply or "").strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except ValueError:
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        event = {k: str(item.get(k) or "").strip() for k in ("title", "date", "start", "end", "location", "notes")}
        if not event["title"] or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", event["date"]):
            continue
        try:
            dt.date.fromisoformat(event["date"])
        except ValueError:
            continue
        out.append(event)
    return out


def stage_file(path: Path) -> Path:
    """Copy a file into Claude's neutral cwd so Read can reach it."""
    inbox = claude_cli.claude_cwd() / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", path.name)[:120] or "file"
    dest = inbox / safe
    shutil.copyfile(path, dest)
    return dest


def extract_events(
    path: str | Path,
    *,
    focus: str = "",
    today: dt.date | None = None,
    timeout: float = 240.0,
    runner=None,
) -> tuple[list[dict], str | None]:
    """Read a schedule file with the backend model -> (events, warning)."""
    src = Path(path).expanduser()
    if not src.is_file():
        return [], f"No file at {src}"
    if src.suffix.lower() not in READABLE:
        return [], f"I can read PDFs, images and text files, not {src.suffix or 'that'} files"
    try:
        staged = stage_file(src)
    except OSError as exc:
        return [], f"Could not stage the file: {exc}"[:200]
    prompt = EXTRACT_PROMPT.format(
        today=(today or dt.date.today()).isoformat(),
        name=f"inbox/{staged.name}",
        focus=f" (only: {focus.strip()[:200]})" if focus.strip() else "",
    )
    try:
        reply, warning = claude_cli.claude_reply(
            prompt,
            model=claude_cli.BACKEND_MODEL,
            system=BACKEND_SYSTEM,
            tools="Read",
            timeout=timeout,
            runner=runner,
        )
    finally:
        try:
            staged.unlink()
        except OSError:
            pass
    if warning:
        return [], warning
    events = parse_events(reply)
    if not events:
        return [], "The backend found no dated entries in that file"
    return events, None


def find_schedule_file(hint: str = "", search_dirs: list[Path] | None = None) -> Path | None:
    """Resolve a spoken file hint to a local file. Pure-ish (filesystem).

    A real path wins. Otherwise the newest readable file in Downloads /
    Desktop / Documents whose name contains every hint word; with no hint,
    the newest file whose name mentions exam/mock/timetable/schedule.
    """
    hint = (hint or "").strip()
    if hint:
        direct = Path(hint).expanduser()
        if direct.is_file():
            return direct
    dirs = search_dirs or [Path.home() / d for d in ("Downloads", "Desktop", "Documents")]
    words = [w for w in re.split(r"\W+", hint.lower()) if len(w) > 1 and w not in {"the", "my", "file", "pdf"}]
    default_words = ("exam", "mock", "timetable", "schedule")
    best: tuple[float, Path] | None = None
    for base in dirs:
        try:
            files = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in READABLE]
        except OSError:
            continue
        for p in files:
            name = p.name.lower()
            ok = all(w in name for w in words) if words else any(w in name for w in default_words)
            if not ok:
                continue
            mtime = p.stat().st_mtime
            if best is None or mtime > best[0]:
                best = (mtime, p)
    return best[1] if best else None
