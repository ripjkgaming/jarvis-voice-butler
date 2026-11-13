"""Start a detached Claude Code coding job (see claude_code_runner.py)."""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

SRC = Path(__file__).resolve().parent
SELF_WORDS = re.compile(
    r"\b(jarvis|yourself|your own|restart|reboot yourself|your code)\b", re.I
)


_HARD = re.compile(
    r"\b(refactor|rewrite|migrat\w*|architect\w*|redesign|debug\w*|race condition|"
    r"performance|optimi[sz]\w*|entire|whole (?:codebase|project|repo)|"
    r"multiple files|multi[- ]file|from scratch|full[- ]stack|end[- ]to[- ]end|"
    r"fix (?:the |this |a )?(?:bug|crash|failing)|investigate)\b",
    re.IGNORECASE,
)
_TRIVIAL = re.compile(
    r"\b(hello[, ]?world|fizz ?buzz|tiny|trivial|one[- ]liner|rename|"
    r"(?:html|web)\s?page|landing page|simple (?:script|page|function|program|calculator)|"
    r"(?:small|short|quick|basic) (?:script|program|function|page|file)|"
    r"single[- ]file|one file|typo|add a comment)\b",
    re.IGNORECASE,
)
_MULTI = re.compile(
    r"\b(modules|packages?|tests? for each|several|multiple|\d+ (?:files|modules))\b",
    re.I,
)
HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"


def route(task: str) -> tuple[str, str, str]:
    """(model, effort, tier). Pure. JARVIS_CODE_MODEL / JARVIS_CODE_EFFORT override."""
    t = task or ""
    if len(t) > 280 or _HARD.search(t):
        model, effort, tier = SONNET, "high", "hard"
    elif len(t) <= 160 and _TRIVIAL.search(t) and not _MULTI.search(t):
        model, effort, tier = HAIKU, "low", "trivial"
    else:
        model, effort, tier = SONNET, "medium", "normal"
    model = os.environ.get("JARVIS_CODE_MODEL", "").strip() or model
    effort = os.environ.get("JARVIS_CODE_EFFORT", "").strip() or effort
    return model, effort, tier


@functools.lru_cache(maxsize=1)
def claude_binary() -> str:
    return shutil.which("claude") or str(Path.home() / ".local/bin/claude")


@functools.lru_cache(maxsize=1)
def _has_systemd_run() -> str:
    return shutil.which("systemd-run") or ""


def jobs_dir() -> Path:
    home = os.environ.get("JARVIS_HOME", "").strip()
    d = (Path(home) if home else Path.home() / ".jarvis") / "code-jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pick_cwd(task: str, directory: str) -> Path:
    if directory.strip():
        return Path(directory).expanduser()
    if SELF_WORDS.search(task):
        return SRC.parent
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")[:40] or "task"
    return Path.home() / "jarvis-projects" / f"{slug}-{time.strftime('%m%d%H%M')}"


def launch(task: str, directory: str = "") -> dict:
    try:
        t0 = time.time()
        job_id = "cc-" + uuid.uuid4().hex[:10]
        model, effort, tier = route(task)
        spec = {
            "id": job_id,
            "task": task.strip(),
            "cwd": str(pick_cwd(task, directory)),
            "model": model,
            "effort": effort,
            "tier": tier,
            "binary": claude_binary(),
            "launched_at": t0,
        }
        if os.environ.get("JARVIS_CODE_LEGACY"):
            spec.update(legacy=True, model=SONNET, effort="", tier="legacy")
        path = jobs_dir() / f"{job_id}.json"
        path.write_text(json.dumps(spec))  # no fsync: the runner reads it back at once
        _spawn(path, f"jarvis-code-{job_id}")
        return {"ok": True, "id": job_id}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def _spawn(spec_path: Path, unit: str) -> None:
    argv = [sys.executable, str(SRC / "claude_code_runner.py"), str(spec_path)]
    if _has_systemd_run():
        # Own transient unit (measured ~10 ms): survives a restart of Jarvis.
        argv = [
            "systemd-run",
            "--user",
            "--collect",
            "--quiet",
            f"--unit={unit}",
            *argv,
        ]
    subprocess.Popen(
        argv,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def list_jobs(limit: int = 10) -> list[dict]:
    """Newest first: id, task, cwd, state, summary, finished_at."""
    d = jobs_dir()
    rows = []
    for spec_path in d.glob("cc-*.json"):
        name = spec_path.name
        if name.count(".") != 1:  # skip .status.json and .<n>.json run specs
            continue
        jid = name[:-5]
        spec = _read(spec_path)
        st = _read(d / f"{jid}.status.json")
        try:
            created = spec_path.stat().st_mtime
        except OSError:
            continue
        rows.append(
            (
                float(st.get("ts") or created),
                {
                    "id": jid,
                    "task": str(st.get("task") or spec.get("task") or "")[:120],
                    "cwd": st.get("cwd") or spec.get("cwd") or "",
                    "state": st.get("state") or "unknown",
                    "summary": st.get("summary") or st.get("error") or "",
                    "finished_at": st.get("finished_at"),
                },
            )
        )
    rows.sort(key=lambda r: r[0], reverse=True)
    return [r for _, r in rows[: max(1, int(limit))]]


def latest_job() -> dict | None:
    jobs = list_jobs(1)
    return jobs[0] if jobs else None


def followup(job_id: str, text: str) -> dict:
    """Continue an earlier job's Claude Code conversation with a new prompt."""
    try:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "What should I change, Sir?"}
        job_id = (job_id or "").strip()
        if job_id in ("", "latest"):
            job = latest_job()
            if not job:
                return {"ok": False, "error": "There are no earlier coding jobs."}
            job_id = job["id"]
        d = jobs_dir()
        spec = _read(d / f"{job_id}.json")
        st = _read(d / f"{job_id}.status.json")
        if not spec:
            return {"ok": False, "error": f"No coding job {job_id}."}
        if (
            st.get("state") in ("running", "queued")
            and time.time() - float(st.get("ts") or 0) < 600
        ):
            return {"ok": False, "error": "That job is still running."}
        sid = st.get("session_id")
        if not sid:
            return {
                "ok": False,
                "error": "That job has no saved conversation to continue.",
            }
        run = int(st.get("run") or 1) + 1
        rspec = {
            "id": job_id,
            "task": text,
            "base_task": st.get("task") or spec.get("task"),
            "cwd": st.get("cwd") or spec.get("cwd"),
            "model": st.get("model") or spec.get("model"),
            "effort": spec.get("effort") or "",
            "legacy": bool(spec.get("legacy")),
            "binary": claude_binary(),
            "launched_at": time.time(),
            "resume": sid,
            "run": run,
        }
        path = d / f"{job_id}.{run}.json"
        path.write_text(json.dumps(rspec))
        _spawn(path, f"jarvis-code-{job_id}-{run}-{uuid.uuid4().hex[:6]}")
        return {"ok": True, "id": job_id, "run": run}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
