"""Always Aware code executor (PLAN.md §3.4 code_fix/test_fix, §5 git net).

Git safety net, chosen to be stash-free: a snapshot commit is built with a
PRIVATE temporary index (``GIT_INDEX_FILE`` inside the run journal, seeded
from a copy of the user's index for its stat cache), ``add -A`` /
``write-tree`` / ``commit-tree -p HEAD``, and pinned at
``refs/jarvis/aware/<run>/<label>``. The user's index, worktree, HEAD,
branches and stash list are never written. Undo does not use git at all:
:func:`collect` extracts each changed file's pre-run blob into the run
journal, so the hash-checked :func:`undo_journal.undo` restores it.

Attribution (other agents edit these repos with uncommitted work):
* in plan scope and clean before the run, or any path claude's Edit/Write
  tools named          -> ours: journaled, reverted on failure
* claude touched it but it is outside scope -> ``out_of_scope``: journaled and
  reverted, and the run fails
* changed, but nothing says it was us -> ``unattributed``: never touched,
  only named.

The executor is the claude CLI started with ``claude_code_runner.build_argv``
(same spec and flags as the code_project tool) and the runner's per-repo
lock, as a child in its own process group so the watchdog can kill it.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import shlex
import shutil
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from aware import undo_journal as uj

GIT_TIMEOUT_S = 120
VERIFY_TIMEOUT_S = 600
SRC = Path(__file__).resolve().parent.parent
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")

EventFn = Callable[[str, str, dict], None]


class GitError(RuntimeError):
    pass


def _git_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_OPTIONAL_LOCKS="0",
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="Jarvis Always Aware",
        GIT_AUTHOR_EMAIL="aware@jarvis.invalid",
        GIT_COMMITTER_NAME="Jarvis Always Aware",
        GIT_COMMITTER_EMAIL="aware@jarvis.invalid",
    )
    env.update(extra or {})
    return env


def git(
    repo: Path,
    *args: str,
    env: dict | None = None,
    text: bool = True,
    check: bool = True,
):
    proc = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", *args],
        capture_output=True,
        text=text,
        timeout=GIT_TIMEOUT_S,
        env=_git_env(env),
        check=False,
    )
    if check and proc.returncode != 0:
        err = proc.stderr if text else proc.stderr.decode(errors="replace")
        raise GitError(f"git {args[0]} failed: {err.strip()[:300]}")
    return proc


def head(repo: Path) -> str | None:
    proc = git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def snapshot(repo: Path, journal: uj.Journal, label: str) -> str:
    """Commit the whole worktree (tracked + untracked, minus ignored) to a
    private ref without touching the user's index/HEAD/stash. Returns the sha."""
    repo = Path(repo)
    idx = journal.dir / f"index-{label}"
    ref = f"refs/jarvis/aware/{journal.run_id}/{label}"
    try:
        real_index = git(repo, "rev-parse", "--git-path", "index").stdout.strip()
        real_index = (
            Path(real_index) if os.path.isabs(real_index) else repo / real_index
        )
        env = {"GIT_INDEX_FILE": str(idx)}
        parent = head(repo)
        if parent and real_index.is_file():
            shutil.copyfile(real_index, idx)
        elif parent:
            git(repo, "read-tree", parent, env=env)
        else:
            git(repo, "read-tree", "--empty", env=env)
        git(repo, "add", "-A", "--", ".", env=env)
        tree = git(repo, "write-tree", env=env).stdout.strip()
        argv = ["commit-tree", tree, "-m", f"jarvis aware {journal.run_id} {label}"]
        if parent:
            argv[2:2] = ["-p", parent]
        sha = git(repo, *argv).stdout.strip()
        git(repo, "update-ref", ref, sha)
    finally:
        for p in (idx, idx.with_name(idx.name + ".lock")):
            with contextlib.suppress(OSError):
                p.unlink()
    meta = journal.meta()
    refs = list(meta.get("refs") or [])
    if ref not in refs:
        refs.append(ref)
    journal.update_meta(repo=str(repo), refs=refs)
    return sha


def _names(repo: Path, *args: str) -> list[str]:
    out = git(repo, *args, "-z").stdout
    return [p for p in out.split("\0") if p]


def _rel(repo: Path, path: str) -> str | None:
    p = Path(path)
    if not p.is_absolute():
        return Path(os.path.normpath(p)).as_posix()
    for base in (repo, Path(os.path.realpath(repo))):
        with contextlib.suppress(ValueError):
            return Path(os.path.normpath(p)).relative_to(base).as_posix()
    return None


def _in_scope(path: str, scope: list[str]) -> bool:
    for s in scope:
        s = s.rstrip("/")
        if path == s or path.startswith(s + "/"):
            return True
    return False


@dataclass
class CodeCtx:
    repo: Path
    pre: str
    head: str | None
    scope: list[str]
    dirty_before: set[str]


@dataclass
class Collected:
    post: str = ""
    ours: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    unattributed: list[str] = field(default_factory=list)
    skipped: list[str] = field(
        default_factory=list
    )  # symlinks/submodules: not journaled
    head_moved: bool = False


def prepare(repo: Path, journal: uj.Journal, *, scope: list[str]) -> CodeCtx:
    repo = Path(repo)
    h = head(repo)
    pre = snapshot(repo, journal, "pre")
    rel_scope = [r for r in (_rel(repo, s) for s in scope or []) if r]
    dirty = (
        set(_names(repo, "diff", "--name-only", "--no-renames", h, pre)) if h else set()
    )
    journal.update_meta(head=h, pre=pre, scope=rel_scope, dirty_before=sorted(dirty))
    return CodeCtx(repo=repo, pre=pre, head=h, scope=rel_scope, dirty_before=dirty)


def _blob_info(
    repo: Path, rev: str, path: str
) -> tuple[bytes | None, int | None, bool]:
    """(bytes, mode, journalable) of path at rev; bytes None if absent there."""
    line = git(repo, "ls-tree", "-z", rev, "--", path).stdout.strip("\0")
    if not line:
        return None, None, True
    mode = line.split(" ", 1)[0]
    if mode not in ("100644", "100755"):
        return None, None, False
    data = git(repo, "cat-file", "blob", f"{rev}:{path}", text=False).stdout
    return data, 0o755 if mode == "100755" else 0o644, True


def collect(ctx: CodeCtx, journal: uj.Journal, *, touched: set[str]) -> Collected:
    """Snapshot after the run, attribute every changed path, journal ours."""
    col = Collected()
    col.post = snapshot(ctx.repo, journal, "post")
    col.head_moved = head(ctx.repo) != ctx.head
    changed = _names(ctx.repo, "diff", "--name-only", "--no-renames", ctx.pre, col.post)
    mine = {r for r in (_rel(ctx.repo, t) for t in touched) if r}
    for path in changed:
        if ctx.scope and _in_scope(path, ctx.scope):
            bucket = (
                "ours"
                if (path not in ctx.dirty_before or path in mine)
                else "unattributed"
            )
        elif path in mine:
            bucket = "out_of_scope" if ctx.scope else "ours"
        else:
            bucket = "unattributed"
        if bucket == "unattributed":
            col.unattributed.append(path)
            continue
        before, mode, ok = _blob_info(ctx.repo, ctx.pre, path)
        full = ctx.repo / path
        if not ok or os.path.islink(full):
            col.skipped.append(path)
            continue
        journal.record_external(
            full, before=before, after_sha=uj.sha256_file(full), mode=mode
        )
        getattr(col, bucket).append(path)
    recorded = col.ours + col.out_of_scope
    if recorded:
        diff = git(
            ctx.repo, "diff", "--no-renames", ctx.pre, col.post, "--", *recorded
        ).stdout
        journal.note_diff(diff)
    journal.update_meta(
        post=col.post,
        ours=col.ours,
        out_of_scope=col.out_of_scope,
        unattributed=col.unattributed,
        head_moved=col.head_moved,
    )
    return col


def verify_tests(
    repo: Path, cmd, *, run=subprocess.run, timeout: float = VERIFY_TIMEOUT_S
) -> tuple[bool, str]:
    """Rerun the success command ourselves. Only rc 0 with 0 failures passes."""
    import code_assist

    if not cmd:
        return False, "no test command to check with"
    argv = shlex.split(cmd) if isinstance(cmd, str) else [str(c) for c in cmd]
    try:
        proc = run(argv, cwd=str(repo), capture_output=True, text=True, timeout=timeout)
        out, rc = (proc.stdout or "") + (proc.stderr or ""), proc.returncode
    except subprocess.TimeoutExpired:
        return False, f"the check timed out after {int(timeout)} seconds"
    except OSError as exc:
        return False, f"could not start {argv[0]}: {exc}"
    fails = code_assist.count_failures(out, rc)
    summary = code_assist.summary_line(out) or f"exit {rc}"
    if rc == 0 and fails == 0:
        return True, summary
    return False, summary


def task_text(
    *, title: str, steps: list[str], criteria: list[str], files: list[str], test_command
) -> str:
    """The executor brief, built from the gated plan only (never raw screen text)."""
    cmd = (
        test_command if isinstance(test_command, str) else " ".join(test_command or [])
    )
    lines = [f"Task: {title}", ""]
    if steps:
        lines += ["Plan:"] + [f"- {s}" for s in steps] + [""]
    lines += ["Done means (Jarvis checks this himself afterwards):"]
    lines += [f"- {c}" for c in criteria or []]
    if cmd:
        lines.append(f"- `{cmd}` exits 0 with no failures")
    lines.append("")
    if files:
        lines.append("Only edit these files: " + ", ".join(files))
    lines += [
        "Hard rules: no push, no commit, no branch changes, no stash, no new "
        "dependencies, no unrelated edits, no network installs.",
        "If you need Sir for anything (a login, a password, a decision, missing "
        "data), print one line `BLOCKED: <reason>` and stop at once.",
        "Print `PROGRESS: <what>` at each major step.",
    ]
    return "\n".join(lines)


def parse_stream_line(line: str) -> list[tuple[str, str, dict]]:
    """claude stream-json line -> [(kind, text, extra)]. Pure.

    kinds: progress | blocked | done | action | error | heartbeat."""
    try:
        ev = json.loads(line)
    except ValueError:
        return []
    if not isinstance(ev, dict):
        return []
    out: list[tuple[str, str, dict]] = []
    if ev.get("type") == "assistant":
        for item in (ev.get("message") or {}).get("content") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                for ln in (item.get("text") or "").splitlines():
                    ln = ln.strip()
                    for tag, kind in (
                        ("PROGRESS:", "progress"),
                        ("BLOCKED:", "blocked"),
                        ("DONE:", "done"),
                    ):
                        if ln.startswith(tag):
                            out.append((kind, ln[len(tag) :].strip()[:300], {}))
            elif item.get("type") == "tool_use":
                name = str(item.get("name") or "tool")
                inp = item.get("input") if isinstance(item.get("input"), dict) else {}
                extra = {}
                target = inp.get("file_path") or inp.get("notebook_path") or ""
                if name in EDIT_TOOLS and target:
                    extra["path"] = str(target)
                sig = json.dumps(inp, sort_keys=True, default=str)[:300]
                out.append(
                    ("action", f"{name} {target or sig}"[:400], {**extra, "sig": sig})
                )
        if not out:
            out.append(("heartbeat", "", {}))
    elif ev.get("type") == "result" and ev.get("is_error"):
        out.append(("error", str(ev.get("result") or "error")[:300], {}))
    else:
        out.append(("heartbeat", "", {}))
    return out


TAKEOVER_BRIEF = """You are a coding agent working for Jarvis, Sir's voice assistant, \
on Sir's Linux machine. Jarvis offered to do this task on his own and Sir said \
yes, so do it end to end without asking questions. Never restart Jarvis or any \
service, never touch credentials, never send messages, never delete data you \
did not create.

Working directory: {cwd}

{task}
"""


class ClaudeRunner:
    """Runs the claude CLI for a takeover, as a killable child.

    Reuses claude_code_runner's argv builder (same flags, tools, empty MCP
    config) and its per-repo lock, so a takeover never edits a repo at the
    same time as a code_project job. It does NOT go through the runner's
    main(), because that speaks "the coding job is finished" before Jarvis
    has verified anything; the takeover speaks its own verified report."""

    def __init__(
        self,
        *,
        binary: str | None = None,
        job_dir: Path | None = None,
        line_limit: int = 16 * 1024 * 1024,
    ):
        self.binary = binary
        self.job_dir = Path(job_dir) if job_dir else None
        self.line_limit = line_limit
        self.last_pid: int | None = None

    def _job_dir(self) -> Path:
        if self.job_dir is None:
            import claude_code_launch

            self.job_dir = claude_code_launch.jobs_dir()
        self.job_dir.mkdir(parents=True, exist_ok=True)
        return self.job_dir

    def _full_spec(self, spec: dict) -> dict:
        out = dict(spec)
        out.setdefault("launched_at", time.time())
        with contextlib.suppress(Exception):
            import claude_code_launch

            model, effort, tier = claude_code_launch.route(out.get("task", ""))
            out.setdefault("model", model)
            out.setdefault("effort", effort)
            out.setdefault("tier", tier)
            if not self.binary:
                out.setdefault("binary", claude_code_launch.claude_binary())
        if self.binary:
            out["binary"] = self.binary
        out.setdefault("binary", shutil.which("claude") or "claude")
        return out

    async def __call__(self, spec: dict, *, on_event: EventFn) -> dict:
        import claude_code_runner as ccr

        d = self._job_dir()
        spec = self._full_spec(spec)
        cwd = Path(spec["cwd"])
        (d / f"{spec['id']}.json").write_text(json.dumps(spec))
        lock_f = open(ccr.lock_path(cwd, d), "a+")  # noqa: SIM115 (held for the run)
        try:
            try:
                fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return {
                    "rc": 1,
                    "summary": "",
                    "error": "another coding job is already working in that repository",
                    "touched": set(),
                }
            return await self._run(spec, cwd, d, ccr, on_event)
        finally:
            lock_f.close()

    async def _run(self, spec, cwd, d, ccr, on_event) -> dict:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("ANTHROPIC_", "CLAUDE_")) and k != "CLAUDECODE"
        }
        argv = ccr.build_argv(spec, spec["binary"])
        prompt = TAKEOVER_BRIEF.format(cwd=cwd, task=spec["task"])
        touched: set[str] = set()
        summary, error = "", ""
        with open(d / f"{spec['id']}.log", "ab") as log:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                cwd=str(cwd),
                env=env,
                start_new_session=True,
                limit=self.line_limit,
            )
            self.last_pid = proc.pid
            try:
                proc.stdin.write(prompt.encode())
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    await proc.stdin.drain()
                proc.stdin.close()
                while True:
                    raw = await proc.stdout.readline()
                    if not raw:
                        break
                    log.write(raw)
                    log.flush()
                    for kind, text, extra in parse_stream_line(
                        raw.decode(errors="replace")
                    ):
                        if extra.get("path"):
                            touched.add(extra["path"])
                        if kind == "done":
                            summary = text
                        elif kind == "error":
                            error = text
                        on_event(kind, text, extra)
                rc = await proc.wait()
            except BaseException:
                await _kill_group(proc)
                raise
        return {"rc": rc, "summary": summary, "error": error, "touched": touched}


async def _kill_group(proc) -> None:
    if proc.returncode is not None:
        return
    for sig, wait in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0)):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, sig)
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), wait)
            return
        except TimeoutError:
            continue
