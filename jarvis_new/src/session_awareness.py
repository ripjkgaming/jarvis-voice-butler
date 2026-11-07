"""Session awareness: date/time, previous-session summary, recent coding jobs.

Everything here is best effort and never raises. Owner-only: never feed the
block to guest instructions.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

MAX_BLOCK = 1200
SUMMARY_MAX = 600
_GAP_S = 30 * 60  # silence that separates sessions in captions.log
_TAIL_BYTES = 64 * 1024
_cached_last: dict | None = None


def _home() -> Path:
    env = os.environ.get("JARVIS_HOME")
    return Path(env) if env else Path.home() / ".jarvis"


def now_line(ts: float | None = None) -> str:
    try:
        dt = datetime.fromtimestamp(ts if ts is not None else time.time()).astimezone()
        off = dt.strftime("%z")
        off = f"UTC{off[:3]}:{off[3:]}" if off else "UTC"
        h = dt.hour % 12 or 12
        return (f"{dt.strftime('%A')} {dt.day} {dt.strftime('%B %Y')}, "
                f"{h}:{dt.minute:02d} {dt.strftime('%p')}, {dt.tzname() or ''} ({off})")
    except Exception:
        return ""


def _read_tail(path: Path, nbytes: int = _TAIL_BYTES) -> str:
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - nbytes))
        return f.read().decode("utf-8", "replace")


def _parse_captions(text: str) -> list[tuple[float, str, str]]:
    out = []
    for line in text.splitlines():
        p = line.split("\t")
        if len(p) < 3:
            continue
        try:
            out.append((float(p[0]), p[1].strip().lower(), p[2].strip()))
        except ValueError:
            continue
    return out


def _last_session_lines(rows):
    """Rows of the final session (trailing run with no gap > _GAP_S)."""
    if not rows:
        return []
    start = len(rows) - 1
    while start > 0 and rows[start][0] - rows[start - 1][0] <= _GAP_S:
        start -= 1
    return rows[start:]


def _summarize(rows) -> dict:
    sir = [t for _, s, t in rows if s == "sir" and t]
    jar = [t for _, s, t in rows if s == "jarvis" and t]
    last6 = [t[:120] for t in sir[-6:]]
    summary = ""
    if last6:
        summary = "Sir asked: " + " | ".join(last6[-4:])
    if jar:
        summary += (" ; Jarvis last said: " + jar[-1][:150]) if summary else "Jarvis last said: " + jar[-1][:150]
    return {"summary": summary[:SUMMARY_MAX], "requests": last6}


def _unfinished(jobs: list[dict]) -> list[str]:
    return [f"{j.get('task') or j.get('id')} ({j.get('state')})"[:100]
            for j in jobs if str(j.get("state", "")).lower() in ("running", "queued", "failed", "error", "started")][:3]


def record_session_end(transcript_tail=None) -> None:
    """Persist last_session.json from captions.log (and optional tail text)."""
    try:
        end = time.time()
        rows = []
        cap = _home() / "captions.log"
        if cap.is_file():
            rows = _last_session_lines(_parse_captions(_read_tail(cap)))
        if not rows and transcript_tail:
            if isinstance(transcript_tail, str):
                transcript_tail = [transcript_tail]
            rows = [(end, "sir", str(t)) for t in list(transcript_tail)[-6:]]
        info = _summarize(rows)
        info["end_epoch"] = end
        info["unfinished"] = _unfinished(recent_code_jobs())
        d = _home()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "last_session.json.tmp"
        tmp.write_text(json.dumps(info), encoding="utf-8")
        os.replace(tmp, d / "last_session.json")
    except Exception:
        pass


def load_last_session() -> dict | None:
    try:
        p = _home() / "last_session.json"
        if p.is_file():
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("end_epoch"):
                return d
        # Fallback (crash / first run): derive from captions.log tail.
        cap = _home() / "captions.log"
        if cap.is_file():
            rows = _last_session_lines(_parse_captions(_read_tail(cap)))
            if rows:
                d = _summarize(rows)
                d["end_epoch"] = rows[-1][0]
                d["unfinished"] = []
                return d
    except Exception:
        pass
    return None


def recent_code_jobs(limit: int = 3) -> list[dict]:
    try:
        d = _home() / "code-jobs"
        if not d.is_dir():
            return []
        files = sorted(d.glob("*.status.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        jobs = []
        for f in files:
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(j, dict):
                    jobs.append(j)
            except Exception:
                continue
        return jobs
    except Exception:
        return []


def _relative(secs: float) -> str:
    secs = max(0, int(secs))
    if secs < 90:
        return "moments"
    m = secs // 60
    if m < 90:
        return f"{m} minutes"
    h = secs // 3600
    if h < 48:
        return f"{h} hours"
    return f"{h // 24} days"


def _build(last, jobs, now) -> str:
    lines = [f"Now: {now_line(now)}."]
    if last:
        end = float(last["end_epoch"])
        lines.append(f"Last session ended {_relative(now - end)} ago ({now_line(end)}).")
        if last.get("summary"):
            lines.append("It: " + str(last["summary"])[:SUMMARY_MAX])
        if last.get("unfinished"):
            lines.append("Unfinished: " + "; ".join(map(str, last["unfinished"])))
    if jobs:
        lines.append("Recent coding jobs:")
        for j in jobs:
            bits = [str(j.get("state") or "?")]
            if j.get("cwd"):
                bits.append(str(j["cwd"]))
            head = str(j.get("task") or j.get("id") or "job")[:80]
            s = str(j.get("summary") or "")[:160]
            lines.append(f"- {head} [{', '.join(bits)}]" + (f": {s}" if s else ""))
    lines.append("Use this to carry on where Sir left off; do not recite it unless relevant.")
    text = "\n".join(lines)
    if len(text) > MAX_BLOCK:
        tail = lines[-1]
        text = text[: MAX_BLOCK - len(tail) - 2].rstrip() + "…\n" + tail
    return text[:MAX_BLOCK]


def build_awareness_block(now: float | None = None) -> str:
    global _cached_last
    try:
        if _cached_last is None:
            _cached_last = load_last_session() or {}
        t = now if now is not None else time.time()
        return "\n\n[Session awareness]\n" + _build(_cached_last or None, recent_code_jobs(), t)
    except Exception:
        return ""


def reset_cache() -> None:
    global _cached_last
    _cached_last = None
