"""Delegator-side spawner for hidden tmux worker sessions with tiered escalation.

Each task runs as a headless Claude Code worker (``claude -p``) in a detached
tmux session named ``<prefix><task_id>``. A worker that gets stuck writes a
``result.json`` with ``status: "stuck"`` plus a handoff brief; the watch loop
then re-spawns the same task one tier up: haiku -> sonnet -> opus. Opus is
only ever launched after a sonnet worker reported stuck, and sonnet only after
haiku did.

The delegator itself takes no work: ``plan_and_spawn`` only spawns.

CLI::

    python -m src.tiered_agents watch [--interval S] [--once]
    python -m src.tiered_agents list
    python -m src.tiered_agents status TASK_ID
    python -m src.tiered_agents kill TASK_ID

Environment:
    JARVIS_AGENTS_DIR     state root (default ~/.jarvis/agents)
    JARVIS_TMUX_PREFIX    session name prefix (default "jv-")
    JARVIS_TMUX_SOCKET    tmux -L socket name (default: the default tmux server)
    JARVIS_AGENT_CMD      worker command used instead of the `claude` CLI
    JARVIS_AGENT_NO_TMUX  1 forces the detached-subprocess fallback
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("jarvis.tiered_agents")

TIERS = ("haiku", "sonnet", "opus")
TIER_MODEL = {
    "haiku": "claude-haiku-5-5",
    "sonnet": "claude-sonnet-5-5",
    "opus": "claude-opus-5-5",
}
TIER_EFFORT = {"haiku": "high", "sonnet": "medium", "opus": "medium"}
ESCALATES_TO = {"haiku": "sonnet", "sonnet": "opus"}
# A tier may only be launched after the tier named here reported stuck.
REQUIRED_STUCK_TIER = {"sonnet": "haiku", "opus": "sonnet"}

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_PLACEHOLDER_RE = re.compile(r"<<([A-Z_]+)>>")

WORKER_PROMPT = """You are a headless worker spawned by a delegator. Nobody watches this session live. Your outputs are the work itself and one result file.

TASK ID: <<TASK_ID>>
Your working directory is the project to change.

TASK:
<<BRIEF>>

<<HANDOFF>>

RULES
1. Do the task. Make the smallest correct change and do not expand scope.
2. You get 2 failed attempts in total. If an approach fails, try a different one.
3. Stop and report "stuck" after the 2nd failed attempt, or as soon as you hit a blocker you cannot resolve (missing credentials, missing file, ambiguous requirement, permission you lack). Do not keep trying.
4. When the task is complete and verified, report "done".
5. Write the result file exactly once, as the last thing you do, as valid JSON. Then exit.

RESULT FILE: <<RESULT_PATH>>

To report stuck, write:
{"status":"stuck","tried":["<attempt 1>","<attempt 2>"],"blocker":"<the specific blocker>","handoff_brief":"<everything a more capable worker needs: goal, what is already done, what failed and why, the next step to try>"}

To report done, write:
{"status":"done","summary":"<what you changed and how you verified it>"}
"""


def agents_root() -> Path:
    override = os.environ.get("JARVIS_AGENTS_DIR", "").strip()
    return (
        Path(override).expanduser() if override else Path.home() / ".jarvis" / "agents"
    )


def _check_id(task_id) -> str:
    if not isinstance(task_id, str) or not _TASK_ID_RE.match(task_id):
        raise ValueError(
            f"invalid task_id {task_id!r}: use letters, digits, '-' or '_'"
        )
    return task_id


def task_dir(task_id: str) -> Path:
    return agents_root() / _check_id(task_id)


def session_name(task_id: str, tier: str) -> str:
    prefix = os.environ.get("JARVIS_TMUX_PREFIX", "jv-")
    suffix = "" if tier == "haiku" else f"-{tier}"
    return f"{prefix}{task_id}{suffix}"


def build_prompt(
    task_id: str, brief: str, result_path: Path, handoff: str | None = None
) -> str:
    handoff_block = ""
    if handoff and handoff.strip():
        handoff_block = (
            "A less capable worker already tried this task and got stuck. Its handoff "
            "brief follows; verify its claims before relying on them.\n\n"
            "HANDOFF BRIEF:\n" + handoff.strip()
        )
    values = {
        "TASK_ID": task_id,
        "BRIEF": brief.strip(),
        "HANDOFF": handoff_block,
        "RESULT_PATH": str(result_path),
    }
    # Single pass: inserted text is never scanned for placeholders.
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], WORKER_PROMPT)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event(event: str, **fields) -> None:
    log.info("tiered_agent %s %s", event, fields)
    try:
        from src.away_log import log_event  # optional; not present in every checkout
    except Exception:
        return
    try:
        log_event("tiered_agents", event=event, **fields)
    except Exception:
        log.debug("away_log.log_event failed", exc_info=True)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# --- index (HUD listing) -------------------------------------------------


def _index_path() -> Path:
    return agents_root() / "index.json"


@contextmanager
def _index_locked():
    root = agents_root()
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "index.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _read_index() -> dict:
    try:
        data = json.loads(_index_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_index(index: dict) -> None:
    _atomic_write(_index_path(), json.dumps(index, indent=2, sort_keys=True))


def get_record(task_id: str) -> dict | None:
    rec = _read_index().get(_check_id(task_id))
    return dict(rec) if isinstance(rec, dict) else None


def _store(task_id: str, record: dict) -> None:
    with _index_locked():
        index = _read_index()
        index[task_id] = record
        _write_index(index)


def _mutate(task_id: str, fn) -> dict | None:
    with _index_locked():
        index = _read_index()
        rec = index.get(task_id)
        if not isinstance(rec, dict):
            return None
        fn(rec)
        index[task_id] = rec
        _write_index(index)
        return dict(rec)


# --- tmux / subprocess plumbing -----------------------------------------

_PROCS: dict[int, subprocess.Popen] = {}


def _has_tmux() -> bool:
    return (
        not os.environ.get("JARVIS_AGENT_NO_TMUX") and shutil.which("tmux") is not None
    )


def _tmux(*args: str) -> subprocess.CompletedProcess:
    cmd = ["tmux"]
    sock = os.environ.get("JARVIS_TMUX_SOCKET", "").strip()
    if sock:
        cmd += ["-L", sock]
    return subprocess.run(cmd + list(args), capture_output=True, text=True, timeout=15)


def _tmux_has(name: str) -> bool:
    return (
        shutil.which("tmux") is not None
        and _tmux("has-session", "-t", f"={name}").returncode == 0
    )


def _pid_alive(pid: int) -> bool:
    proc = _PROCS.get(pid)
    if proc is not None and proc.poll() is not None:
        _PROCS.pop(pid, None)
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _session_alive(session: str) -> bool:
    if session.startswith("proc:"):
        return _pid_alive(int(session[5:]))
    return bool(session) and _tmux_has(session)


def _worker_alive(task_id: str, rec: dict) -> bool:
    # The per-tier exit marker is written by the runner as its last step.
    if (task_dir(task_id) / f"exit-{rec.get('tier', 'haiku')}.code").exists():
        return False
    return _session_alive(str(rec.get("session") or ""))


def _kill_session(rec: dict) -> None:
    session = str(rec.get("session") or "")
    if not session:
        return
    if session.startswith("proc:"):
        pid = int(session[5:])
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGTERM)
        proc = _PROCS.pop(pid, None)
        if proc is not None:
            proc.poll()
    elif shutil.which("tmux") is not None:
        _tmux("kill-session", "-t", f"={session}")


def read_result(task_id: str, name: str = "result.json") -> dict | None:
    try:
        data = json.loads((task_dir(task_id) / name).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


# --- launching ----------------------------------------------------------


def _worker_base_argv() -> list[str]:
    override = os.environ.get("JARVIS_AGENT_CMD", "").strip()
    if override:
        return shlex.split(override)
    claude = shutil.which("claude")
    if not claude:
        raise RuntimeError(
            "claude CLI not found on PATH (set JARVIS_AGENT_CMD to override)"
        )
    return [claude]


def _runner_script(task_id: str, tier: str, cwd: Path, d: Path, base: list[str]) -> str:
    q = shlex.quote
    log_file = d / "log.txt"
    exit_file = d / f"exit-{tier}.code"
    prompt_file = d / f"prompt-{tier}.md"
    model = TIER_MODEL[tier]
    effort = TIER_EFFORT[tier]
    argv = " ".join(q(part) for part in base)
    return f"""#!/usr/bin/env bash
# Generated by src/tiered_agents.py: one worker attempt. Safe to delete.
set -o pipefail
export JARVIS_AGENT_TASK_ID={q(task_id)}
export JARVIS_AGENT_DIR={q(str(d))}
export JARVIS_AGENT_TIER={q(tier)}
# Subscription auth only: drop API-key/billing overrides (same rule as claude_backend).
for v in $(env | cut -d= -f1 | grep -E '^(ANTHROPIC_|CLAUDE_)'); do unset "$v"; done
cd -- {q(str(cwd))} || {{ echo "tiered_agents: cwd missing" >> {q(str(log_file))}; echo 1 > {q(str(exit_file))}; exit 1; }}
echo "== $(date -u +%FT%TZ) tier={tier} model={model} effort={effort}" >> {q(str(log_file))}
{argv} -p "$(cat -- {q(str(prompt_file))})" --model {q(model)} --effort {q(effort)} \\
  --dangerously-skip-permissions --output-format text 2>&1 | tee -a {q(str(log_file))}
rc=$?
printf '%s\\n' "$rc" > {q(str(exit_file))}.tmp && mv -f {q(str(exit_file))}.tmp {q(str(exit_file))}
"""


def _launch(
    task_id: str,
    tier: str,
    brief: str,
    cwd: Path,
    handoff: str | None = None,
    history: list | None = None,
) -> dict:
    """Start one worker attempt for ``tier``. Returns the tier's record fields."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}")
    needed = REQUIRED_STUCK_TIER.get(tier)
    if needed and not any(
        h.get("tier") == needed and h.get("status") == "stuck" for h in (history or [])
    ):
        raise TierGateError(
            f"{tier} may only start after a {needed} worker reported stuck"
        )

    d = task_dir(task_id)
    d.mkdir(parents=True, exist_ok=True)
    result_path = d / "result.json"
    result_path.unlink(missing_ok=True)
    (d / f"exit-{tier}.code").unlink(missing_ok=True)
    _atomic_write(
        d / f"prompt-{tier}.md", build_prompt(task_id, brief, result_path, handoff)
    )
    run_sh = d / f"run-{tier}.sh"
    _atomic_write(run_sh, _runner_script(task_id, tier, cwd, d, _worker_base_argv()))

    session = session_name(task_id, tier)
    if _has_tmux():
        if _tmux_has(session):
            raise RuntimeError(f"tmux session {session} already exists")
        res = _tmux("new-session", "-d", "-s", session, "/bin/bash", str(run_sh))
        if res.returncode != 0:
            raise RuntimeError(f"tmux new-session failed: {res.stderr.strip()}")
    else:
        proc = subprocess.Popen(
            ["/bin/bash", str(run_sh)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _PROCS[proc.pid] = proc
        session = f"proc:{proc.pid}"
    return {"tier": tier, "status": "running", "session": session, "started": _now()}


class TierGateError(RuntimeError):
    """Raised when a tier is requested before the tier below it reported stuck."""


def _validate_spawn(task_id, brief, cwd) -> Path:
    _check_id(task_id)
    if not isinstance(brief, str) or not brief.strip():
        raise ValueError("brief is empty")
    path = Path(cwd).expanduser().resolve() if cwd else None
    if path is None or not path.is_dir():
        raise ValueError(f"cwd is not a directory: {cwd!r}")
    return path


def spawn_task(task_id: str, brief: str, cwd: str) -> dict:
    """Start a detached haiku worker for ``task_id`` in ``cwd``."""
    cwd_path = _validate_spawn(task_id, brief, cwd)
    existing = get_record(task_id)
    if (
        existing
        and existing.get("status") == "running"
        and _worker_alive(task_id, existing)
    ):
        raise RuntimeError(f"task {task_id} is already running")

    d = task_dir(task_id)
    d.mkdir(parents=True, exist_ok=True)
    _atomic_write(d / "brief.md", brief)
    launch = _launch(task_id, "haiku", brief, cwd_path)
    record = {
        "task_id": task_id,
        "cwd": str(cwd_path),
        "dir": str(d),
        "history": [],
        **launch,
    }
    _store(task_id, record)
    _event("spawned", task_id=task_id, tier="haiku", session=record["session"])
    return dict(record)


def plan_and_spawn(tasks: list[dict]) -> list[dict]:
    """Spawn one worker per ``{task_id, brief, cwd}`` entry. Does no work itself.

    Every entry is validated before anything is spawned, so a bad entry does
    not leave the rest of the batch half-started.
    """
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks must be a non-empty list")
    seen: set[str] = set()
    for entry in tasks:
        if (
            not isinstance(entry, dict)
            or not {"task_id", "brief", "cwd"} <= entry.keys()
        ):
            raise ValueError("each task needs task_id, brief and cwd")
        _validate_spawn(entry["task_id"], entry["brief"], entry["cwd"])
        if entry["task_id"] in seen:
            raise ValueError(f"duplicate task_id {entry['task_id']!r}")
        seen.add(entry["task_id"])
    return [spawn_task(e["task_id"], e["brief"], e["cwd"]) for e in tasks]


# --- status, escalation, kill ------------------------------------------


def _handoff_text(result: dict) -> str:
    hb = result.get("handoff_brief")
    if isinstance(hb, str) and hb.strip():
        return hb
    tried = result.get("tried") or []
    lines = [f"Blocker: {result.get('blocker', 'unknown')}", "Tried:"]
    lines += [f"- {t}" for t in tried] if isinstance(tried, list) else [f"- {tried}"]
    return "\n".join(lines)


def _refresh(task_id: str) -> dict | None:
    """Sync a running record with its result file and session liveness."""
    rec = get_record(task_id)
    if not rec or rec.get("status") != "running":
        return rec
    result = read_result(task_id)
    status = None
    if result and result.get("status") == "done":
        status = "done"
    elif result and result.get("status") == "stuck":
        status = "stuck"
    elif not _worker_alive(task_id, rec):
        status = "crashed"
    if status is None:
        return rec

    def apply(r: dict) -> None:
        if r.get("status") != "running":
            return
        r["status"] = status
        if status == "done":
            r["summary"] = str(result.get("summary", ""))
        elif status == "stuck":
            r["blocker"] = str(result.get("blocker", ""))

    updated = _mutate(task_id, apply) or rec
    _event(status, task_id=task_id, tier=rec.get("tier"))
    return updated


def escalate(task_id: str) -> dict | None:
    """Re-spawn a stuck task one tier up. Returns the new record, or None."""
    rec = get_record(task_id)
    if rec is None:
        raise KeyError(f"unknown task {task_id}")
    result = read_result(task_id)
    if not result or result.get("status") != "stuck":
        return None
    tier = rec["tier"]
    nxt = ESCALATES_TO.get(tier)
    if nxt is None:
        _event("exhausted", task_id=task_id, tier=tier)
        return None

    cwd = Path(rec["cwd"])
    brief = (task_dir(task_id) / "brief.md").read_text(encoding="utf-8")
    handoff = _handoff_text(result)
    archived = task_dir(task_id) / f"result-{tier}.json"
    os.replace(task_dir(task_id) / "result.json", archived)
    stuck_entry = {
        "tier": tier,
        "status": "stuck",
        "session": rec.get("session"),
        "blocker": str(result.get("blocker", "")),
        "result": str(archived),
    }

    def record_stuck(r: dict) -> None:
        r["history"] = [*r.get("history", []), stuck_entry]
        r["status"] = "escalating"

    updated = _mutate(task_id, record_stuck)
    _kill_session(rec)  # the stuck worker is already exiting; make sure nothing lingers
    try:
        launch = _launch(task_id, nxt, brief, cwd, handoff, updated["history"])
    except Exception as exc:
        err = str(exc)
        _mutate(task_id, lambda r: r.update(status="crashed", error=err))
        raise
    _mutate(task_id, lambda r: r.update(launch))
    _event(
        "escalated",
        task_id=task_id,
        from_tier=tier,
        tier=nxt,
        session=launch["session"],
    )
    return get_record(task_id)


def status(task_id: str) -> dict:
    rec = _refresh(_check_id(task_id))
    if rec is None:
        raise KeyError(f"unknown task {task_id}")
    return {**rec, "alive": _worker_alive(task_id, rec)}


def kill(task_id: str) -> bool:
    rec = get_record(task_id)
    if rec is None:
        return False
    _kill_session(rec)
    if rec.get("status") in ("running", "escalating"):
        _mutate(task_id, lambda r: r.update(status="killed"))
    _event("killed", task_id=task_id)
    return True


def list_agents() -> list[dict]:
    rows = [
        {"task_id": tid, **rec}
        for tid, rec in _read_index().items()
        if isinstance(rec, dict)
    ]
    return sorted(rows, key=lambda r: str(r.get("started", "")))


def poll_once() -> dict[str, str]:
    """One watch pass: refresh running tasks and escalate stuck ones.

    Returns ``{task_id: status}`` for every indexed task after the pass.
    """
    for task_id, rec in list(_read_index().items()):
        try:
            if rec.get("status") == "running":
                _refresh(task_id)
            current = get_record(task_id) or {}
            if current.get("status") == "stuck" and current.get("tier") in ESCALATES_TO:
                escalate(task_id)
        except Exception as exc:  # one bad task must not stop the loop
            log.exception("tiered_agents poll failed for %s", task_id)
            _event("poll_error", task_id=task_id, error=str(exc))
    return {tid: str((rec or {}).get("status")) for tid, rec in _read_index().items()}


def watch(interval: float = 5.0, once: bool = False) -> None:
    _event("watch_started", interval=interval)
    while True:
        poll_once()
        if once:
            return
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.tiered_agents")
    sub = parser.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch", help="poll workers and auto-escalate stuck tasks")
    w.add_argument("--interval", type=float, default=5.0)
    w.add_argument("--once", action="store_true")
    sub.add_parser("list", help="list indexed tasks")
    s = sub.add_parser("status", help="show one task")
    s.add_argument("task_id")
    k = sub.add_parser("kill", help="kill one task's session")
    k.add_argument("task_id")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    if args.cmd == "watch":
        watch(interval=args.interval, once=args.once)
    elif args.cmd == "list":
        print(json.dumps(list_agents(), indent=2))
    elif args.cmd == "status":
        print(json.dumps(status(args.task_id), indent=2))
    elif args.cmd == "kill":
        print("killed" if kill(args.task_id) else "unknown task")
    return 0


if __name__ == "__main__":
    sys.exit(main())
