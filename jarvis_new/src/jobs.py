"""Tiny registry of long-running jobs (tests, builds, terminal commands).

Each job is ~/.jarvis/jobs/<id>.json: {id, title, status, started,
finished, summary, failures}. Tools that start work call start(); when it
ends they call finish(). The proactive jobs source announces finished
jobs ("the tests finished, 2 failures"). Never raises.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from pathlib import Path


def jobs_dir() -> Path:
    h = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(h) if h else Path.home() / ".jarvis") / "jobs"


def _path(job_id: str) -> Path:
    return jobs_dir() / f"{re.sub(r'[^A-Za-z0-9_-]', '_', job_id)[:80]}.json"


def _write(job: dict) -> None:
    with contextlib.suppress(OSError):
        jobs_dir().mkdir(parents=True, exist_ok=True)
        tmp = _path(job["id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(job))
        os.replace(tmp, _path(job["id"]))


def start(title: str, kind: str = "job", now: float | None = None) -> str:
    now = time.time() if now is None else now
    job_id = f"{kind}-{int(now * 1000)}"
    _write(
        {
            "id": job_id,
            "kind": kind,
            "title": title[:120],
            "status": "running",
            "started": now,
        }
    )
    return job_id


def finish(
    job_id: str,
    ok: bool,
    summary: str = "",
    failures: int = 0,
    now: float | None = None,
) -> None:
    job = get(job_id) or {"id": job_id, "title": job_id, "started": now or time.time()}
    job.update(
        status="done" if ok else "failed",
        finished=time.time() if now is None else now,
        summary=" ".join(str(summary).split())[:400],
        failures=int(failures),
    )
    _write(job)


def get(job_id: str) -> dict | None:
    try:
        data = json.loads(_path(job_id).read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def recent(limit: int = 30) -> list[dict]:
    out = []
    with contextlib.suppress(OSError):
        for p in sorted(
            jobs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )[:limit]:
            with contextlib.suppress(OSError, ValueError):
                data = json.loads(p.read_text())
                if isinstance(data, dict):
                    out.append(data)
    return out
