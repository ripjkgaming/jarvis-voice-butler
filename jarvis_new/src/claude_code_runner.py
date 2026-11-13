"""Detached Claude Code (Sonnet 5.5) coding job with progress updates for Jarvis.

Launched by the code_project voice tool. Runs outside the agent process so a
job that restarts Jarvis keeps going. Usage: claude_code_runner.py <spec.json>
Spec: {"id","task","cwd","model"}. Progress lines the model prints as
"PROGRESS: ..." are spoken/notified; a heartbeat covers silent stretches.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent
HEARTBEAT_S = 120.0
NOTIFY_GAP_S = 20.0

BRIEF = """You are a coding agent working for Jarvis, Sir's voice assistant, \
on Sir's Linux laptop. Sir spoke the request below aloud, so it may be terse \
or loosely worded. Infer the most sensible concrete interpretation, make \
reasonable decisions yourself, and do the work end to end. Do not ask Sir \
questions: pick a sensible default and say what you chose.

Working directory: {cwd}
Jarvis source (if the task concerns Jarvis itself): {repo}

Rules:
- Actually make the changes, run them, and verify they work (tests, run the \
script, load the page). Never claim success you did not observe.
- Choose file names and layout yourself. Keep the change minimal and in \
the existing style.
- Jarvis keeps Sir updated by voice. Your VERY FIRST output, before reading \
or exploring anything, is one line starting with exactly "PROGRESS: " saying \
what you are about to do. After that print another only at a major step or \
before something slow or risky, and never more than one per ~20 seconds of \
work. Max 20 words, plain speech, no code or paths.
- Speed matters: keep any plan to a few lines. Do independent pieces in \
parallel: when work splits into separate files, modules, tests or research, \
launch several Task subagents in ONE message (one tool-call block) instead \
of one after another, and use model "haiku" for mechanical subagent work. \
Batch independent tool calls in one message too. Tiny tasks: just do them \
yourself, no subagents.
- If the task needs restarting Jarvis or his services, print a PROGRESS line \
first (the voice call will drop), then do it.
- Never delete data you did not create, touch credentials, or send messages.
- Finish with one line starting "DONE: " summarising what you did and what \
Sir should know, max 40 words, plain speech.

Sir's request:
{task}
"""

RULES = BRIEF.split("Rules:", 1)[1].split("Sir's request:", 1)[0]

RESUME_BRIEF = (
    """Sir has a follow-up on the work you did earlier in this conversation. \
Same rules as before; do not ask questions, pick sensible defaults.

Rules:"""
    + RULES
    + """Sir's follow-up:
{task}
"""
)


def _notify(text: str, urgency: str = "info", gap_key: str = "") -> None:
    try:
        sys.path.insert(0, str(SRC))
        import notify

        notify.send(
            text,
            title="Jarvis coding",
            kind="code",
            source="claude-code",
            urgency=urgency,
            always_announce=urgency == "urgent",
            min_gap_s=NOTIFY_GAP_S,
        )
    except Exception:
        pass


TOOLS = "Bash,Read,Edit,Write,Glob,Grep,WebFetch,WebSearch,Task"
LEGACY_TOOLS = "Bash,Read,Edit,Write,Glob,Grep,WebFetch,WebSearch"
EMPTY_MCP = '{"mcpServers":{}}'


def build_argv(spec: dict, binary: str) -> list[str]:
    """claude argv for a spec. Only flags that exist in the installed CLI."""
    argv = [
        binary,
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--model",
        spec.get("model") or "claude-sonnet-5-5",
        "--permission-mode",
        "acceptEdits",
    ]
    if spec.get("legacy"):
        argv += ["--allowedTools", LEGACY_TOOLS]
    else:
        argv += ["--allowedTools", TOOLS]
        if spec.get("effort"):
            argv += ["--effort", spec["effort"]]
        # headless coding needs none of the user's MCP servers, skills,
        # plugins or SessionStart hooks; project CLAUDE.md still loads.
        argv += [
            "--strict-mcp-config",
            "--mcp-config",
            EMPTY_MCP,
            "--disable-slash-commands",
            "--setting-sources",
            "project,local",
        ]
    if (spec.get("resume") or "").strip():
        argv += ["--resume", spec["resume"].strip()]
    return argv


def lock_path(cwd: Path, job_dir: Path) -> Path:
    h = hashlib.sha1(str(cwd.resolve()).encode()).hexdigest()[:16]
    return job_dir / f"{h}.lock"


def main(spec_path: str) -> int:
    t_main = time.time()
    spec = json.loads(Path(spec_path).read_text())
    job_dir = Path(spec_path).parent
    run_n = int(spec.get("run") or 1)
    resume = (spec.get("resume") or "").strip()
    log_name = f"{spec['id']}.log" if run_n <= 1 else f"{spec['id']}.{run_n}.log"
    log = (job_dir / log_name).open("a", encoding="utf-8")
    status_path = job_dir / f"{spec['id']}.status.json"
    try:
        data = json.loads(status_path.read_text())
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    followups = [str(x) for x in (data.get("followups") or [])]
    if resume:
        followups = (followups + [spec["task"][:200]])[-10:]
    else:
        data = {}
    base_task = data.get("task") or spec.get("base_task") or spec["task"]
    started_at = data.get("started_at") or time.time()
    data.update(
        id=spec["id"],
        task=base_task,
        cwd=spec["cwd"],
        model=spec.get("model") or "",
        started_at=started_at,
        followups=followups,
        run=run_n,
    )
    data.pop("error", None)

    def status(state: str, **extra) -> None:
        data.update(extra)
        data["state"] = state
        data["ts"] = time.time()
        if state not in ("running", "queued"):
            data["finished_at"] = data["ts"]
        else:
            data.pop("finished_at", None)
            data.pop("summary", None)
        status_path.write_text(json.dumps(data))

    t_launch = float(spec.get("launched_at") or t_main)
    timings = {
        "spawn_ms": max(0, int((t_main - t_launch) * 1000)),
        "model": spec.get("model") or "",
        "tier": spec.get("tier") or "",
        "effort": spec.get("effort") or "",
    }
    data.update(timings)
    for k in (
        "first_token_ms",
        "first_progress_ms",
        "total_ms",
        "lock_wait_ms",
        "steps",
    ):
        data.pop(k, None)
    cwd = Path(spec["cwd"])
    cwd.mkdir(parents=True, exist_ok=True)
    # Same cwd => serialise (edits must not collide); other cwds run in parallel.
    lock_f = open(lock_path(cwd, job_dir), "a+")
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        status("queued")
        fcntl.flock(lock_f, fcntl.LOCK_EX)
    data["lock_wait_ms"] = int((time.time() - t_main) * 1000)
    binary = (
        spec.get("binary")
        or shutil.which("claude")
        or str(Path.home() / ".local/bin/claude")
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("ANTHROPIC_", "CLAUDE_")) and k != "CLAUDECODE"
    }
    argv = build_argv(spec, binary)
    if resume:
        prompt = RESUME_BRIEF.format(task=spec["task"], cwd=cwd, repo=SRC.parent)
    else:
        prompt = BRIEF.format(cwd=cwd, repo=SRC.parent, task=spec["task"])
    status("running")
    _notify("Picking that up, Sir." if resume else "Starting the coding job, Sir.")
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=log,
        text=True,
        cwd=str(cwd),
        env=env,
    )
    proc.stdin.write(prompt)
    proc.stdin.close()
    last_notice = time.time()
    steps = 0
    first_token = first_progress = None
    final = ""
    result_err = ""
    for line in proc.stdout:
        log.write(line)
        log.flush()
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("session_id"):
            if ev.get("type") in ("system", "result") or not data.get("session_id"):
                if data.get("session_id") != ev["session_id"]:
                    data["session_id"] = ev["session_id"]
                    status("running")
        if ev.get("type") == "assistant":
            if first_token is None:
                first_token = int((time.time() - t_launch) * 1000)
                data["first_token_ms"] = first_token
                status("running")
            for item in ev.get("message", {}).get("content", []):
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use":
                    steps += 1
                elif item.get("type") == "text":
                    for ln in (item.get("text") or "").splitlines():
                        ln = ln.strip()
                        if ln.startswith("PROGRESS:"):
                            _notify(ln[9:].strip())
                            if first_progress is None:
                                first_progress = int((time.time() - t_launch) * 1000)
                                data["first_progress_ms"] = first_progress
                                status("running")
                            last_notice = time.time()
                        elif ln.startswith("DONE:"):
                            final = ln[5:].strip()
        elif ev.get("type") == "result":
            if ev.get("is_error"):
                result_err = str(ev.get("result") or "unknown error")[:200]
            elif not final:
                final = str(ev.get("result") or "")[-300:]
        if time.time() - last_notice > HEARTBEAT_S:
            _notify(f"Still working on the coding job, Sir. {steps} steps so far.")
            last_notice = time.time()
    code = proc.wait()
    data["total_ms"] = int((time.time() - t_launch) * 1000)
    data["steps"] = steps
    if code == 0 and not result_err:
        status("done", summary=final)
        _notify(final or "The coding job is finished, Sir.", "urgent")
        return 0
    status("failed", error=result_err or f"exit {code}")
    _notify(
        f"The coding job failed, Sir. {result_err or 'The coding process exited with an error.'}",
        "urgent",
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
