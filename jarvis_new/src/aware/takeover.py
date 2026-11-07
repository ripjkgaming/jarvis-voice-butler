"""Always Aware takeover run (PLAN.md §3.4).

``await run(plan, progress=cb)`` -> :class:`aware.types.TakeoverResult`.

* Dispatch by ``plan.kind``: "code" -> :mod:`aware.codejob` (claude_code_runner
  under a private-ref git snapshot), "file" -> :mod:`aware.fileedit` (journaled
  ops), "web" -> ``aware.brave_live.run`` (P4, imported lazily).
* Independent verification: code reruns the plan's test command itself, file
  re-checks the end state on disk, web calls ``brave_live.verify``. The
  worker's own "DONE" is never evidence. Only a passing check is "verified".
* Watchdog / stuck policy: BLOCKED from the worker, no progress for 120 s, wall
  time past the budget, the same action 3 times in a row, Sir's cancel
  (``request_cancel``, a flag file so the voice process can send it), or the
  kill switch -> stop the worker, revert our own changes (files Sir changed
  since are left alone and named), and say honestly what blocked it.
* Progress: ``progress(fraction | None, text)`` for the caller, mirrored into
  the jobs registry and the HUD activity ring (activity uses 0..100).
* One takeover at a time (flock). Every step is audited to
  ``aware/audit.jsonl`` and the run journal.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import importlib
import inspect
import json
import os
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path

from aware import codejob, fileedit, state
from aware import undo_journal as uj
from aware.types import TakeoverPlan, TakeoverResult, TakeoverStatus

NO_PROGRESS_S = 120.0
BUDGET_FLOOR_S = 300.0
DEFAULT_EST_MIN = 20.0
REPEAT_LIMIT = 3

VERIFIED = TakeoverStatus.VERIFIED.value
FAILED = TakeoverStatus.FAILED.value
STUCK = TakeoverStatus.STUCK.value
CANCELLED = TakeoverStatus.CANCELLED.value

Progress = Callable[[float | None, str], None]


# --------------------------------------------------------------------------
# small helpers


def _audit(run_id: str, step: str, tool: str, target: str, result: str) -> None:
    row = {
        "ts": time.time(),
        "run": run_id,
        "step": step,
        "tool": tool,
        "target": str(target)[:300],
        "result": str(result)[:300],
    }
    state.append_private(
        state.aware_dir() / "audit.jsonl", json.dumps(row, sort_keys=True)
    )


def _names(paths: list[str], limit: int = 3) -> str:
    short = [Path(p).name or p for p in paths]
    if len(short) <= limit:
        return ", ".join(short)
    return ", ".join(short[:limit]) + f" and {len(short) - limit} more"


def _current_path() -> Path:
    return state.aware_dir() / "takeover.current"


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, ValueError, TypeError, OverflowError):
        return False
    except PermissionError:
        return True


def current() -> str | None:
    """Run id of the takeover in progress (any process), else None."""
    try:
        data = json.loads(_current_path().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not _pid_alive(data.get("pid")):
        return None
    return str(data.get("run_id") or "") or None


running = current


def request_cancel(run_id: str | None = None) -> bool:
    """'stop / cancel that / I've got it'. Cross-process: drops a flag file."""
    rid = run_id or current()
    if not rid:
        return False
    try:
        j = uj.Journal.open(rid)
    except (FileNotFoundError, ValueError):
        return False
    with contextlib.suppress(OSError):
        (j.dir / "CANCEL").touch()
        return True
    return False


def show_last(limit: int = 20000) -> str:
    """'show me': the last run's diff (trimmed)."""
    j = uj.last()
    if j is None:
        return ""
    text = j.diff_text()
    return text if len(text) <= limit else text[:limit] + "\n... (diff trimmed)\n"


def what_did_you_try() -> str:
    """'what did you try': title, outcome and the last few steps, plain speech."""
    j = uj.last()
    if j is None:
        return "I haven't taken anything over yet, Sir."
    meta = j.meta()
    steps = [
        e["text"]
        for e in j.events()
        if e.get("kind") in ("progress", "action", "blocked") and e.get("text")
    ]
    status_ = meta.get("status") or "unknown"
    out = f"{meta.get('title') or 'The last takeover'}: {status_}"
    if meta.get("reason"):
        out += f", because {meta['reason']}"
    out += "."
    if steps:
        out += " Steps: " + "; ".join(s[:80] for s in steps[-6:]) + "."
    return out


# --------------------------------------------------------------------------
# ring / jobs mirror


class _Ring:
    """Fans progress out to the caller, the jobs registry and the HUD ring."""

    def __init__(self, title: str, progress: Progress | None):
        self.cb = progress
        self.job_id = ""
        self.aid = ""
        self.last: float | None = 0.0
        with contextlib.suppress(Exception):
            import jobs

            self.job_id = jobs.start(f"AWARE: {title}"[:120], "aware")
            self.aid = jobs._activity_id(self.job_id)

    def __call__(self, frac: float | None, text: str) -> None:
        if frac is not None:
            frac = max(0.0, min(1.0, float(frac)))
            if self.last is not None and frac < self.last:
                frac = self.last  # the ring never runs backwards
            self.last = frac
        if self.cb:
            with contextlib.suppress(Exception):
                self.cb(frac, text)
        if self.aid:
            with contextlib.suppress(Exception):
                import activity

                activity.update(
                    self.aid,
                    progress=None if frac is None else frac * 100.0,
                    detail=text[:200],
                )

    def finish(self, ok: bool, summary: str) -> None:
        if self.job_id:
            with contextlib.suppress(Exception):
                import jobs

                jobs.finish(self.job_id, ok=ok, summary=summary)


# --------------------------------------------------------------------------
# watchdog


class Watch:
    """Tracks worker events and decides when the run must stop."""

    def __init__(
        self,
        run_id: str,
        journal: uj.Journal,
        ring: _Ring,
        *,
        budget_s: float,
        no_progress_s: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.run_id = run_id
        self.journal = journal
        self.ring = ring
        self.budget_s = budget_s
        self.no_progress_s = no_progress_s
        self.clock = clock
        self.t0 = clock()
        self.last_ts = self.t0
        self.blocked = ""
        self.last_sig = None
        self.repeats = 0
        self.steps = 0
        self.touched: set[str] = set()
        self.stopped: tuple[str, str] | None = None
        self._lock = threading.Lock()

    def event(self, kind: str, text: str = "", extra: dict | None = None) -> None:
        extra = extra or {}
        with self._lock:
            self.last_ts = self.clock()
            if kind == "blocked":
                self.blocked = text or "the worker needs Sir"
            elif kind == "action":
                self.steps += 1
                sig = extra.get("sig") or text
                self.repeats = self.repeats + 1 if sig == self.last_sig else 1
                self.last_sig = sig
                if extra.get("path"):
                    self.touched.add(str(extra["path"]))
        if kind == "heartbeat":
            return
        with contextlib.suppress(Exception):
            self.journal.event(kind, text)
        if kind == "action":
            with contextlib.suppress(Exception):
                _audit(self.run_id, str(self.steps), "executor", text, "ok")
        if kind == "progress" and text:
            self.ring(extra.get("p"), text)

    def check(self) -> tuple[str, str] | None:
        """(status, reason) when the run must stop now, else None."""
        if self.stopped:
            return self.stopped
        stop = None
        if (self.journal.dir / "CANCEL").exists():
            stop = (CANCELLED, "Sir stopped it")
        elif state.killed() or not state.env_allows():
            stop = (CANCELLED, "the kill switch is on")
        elif self.blocked:
            stop = (STUCK, self.blocked)
        elif self.repeats >= REPEAT_LIMIT:
            stop = (STUCK, "it kept repeating the same step")
        else:
            now = self.clock()
            if now - self.t0 > self.budget_s:
                stop = (STUCK, "it ran past its time budget")
            elif now - self.last_ts > self.no_progress_s:
                mins = self.no_progress_s / 60
                span = (
                    f"{mins:g} minutes"
                    if mins >= 1
                    else f"{self.no_progress_s:g} seconds"
                )
                stop = (STUCK, f"there was no progress for {span}")
        if stop:
            self.stopped = stop
        return stop

    def reason_only(self) -> str | None:
        stop = self.check()
        return stop[1] if stop else None

    def remaining(self) -> float:
        return max(0.0, self.budget_s - (self.clock() - self.t0))


async def _supervise(task: asyncio.Task, watch: Watch, poll_s: float):
    """Await the worker; cancel it the moment the watchdog says stop."""
    while True:
        done, _ = await asyncio.wait({task}, timeout=poll_s)
        if done:
            return task.result(), None
        stop = watch.check()
        if stop:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            return None, stop


# --------------------------------------------------------------------------
# outcome


class _Outcome:
    def __init__(self):
        self.status = FAILED
        self.reason = ""
        self.evidence = ""
        self.changed: list[str] = []
        self.notes: list[str] = []
        self.spoken_override = ""
        self.revert = True


def _spoken(o: _Outcome, rep: uj.UndoReport | None) -> str:
    if o.status == VERIFIED:
        if o.spoken_override:
            return o.spoken_override
        line = f"Done, Sir. {o.evidence.rstrip('.')}."
        if o.changed:
            line += f" I changed {_names(o.changed)}. Say 'show me' for the diff."
        return line
    head = (
        "Stopped, Sir."
        if o.status == CANCELLED
        else f"I couldn't finish: {o.reason.rstrip('.')}."
    )
    tail = ""
    if rep is not None and rep.restored:
        tail = " I've reverted my changes."
    elif rep is not None and not rep.left_alone and not rep.errors:
        tail = " Nothing was changed."
    if rep is not None and rep.left_alone:
        tail += f" I left {_names(rep.left_alone)} alone because it changed since."
    if rep is not None and rep.errors:
        tail += f" I could not restore {len(rep.errors)} item(s); the journal has the originals."
    for note in o.notes:
        tail += f" {note}"
    return head + tail


# --------------------------------------------------------------------------
# executors


async def _run_code(
    plan, journal, watch, ring, o, *, runner, verify_run, poll_s, policy
):
    t = plan.targets or {}
    repo = t.get("repo")
    if not repo:
        o.reason = "the plan has no repository"
        return
    try:
        repo = fileedit.check_path(repo, policy, must_write=True)
    except fileedit.Denied as exc:
        o.reason = f"I may not work in that folder ({exc})"
        return
    scope = [str(f) for f in t.get("files") or []]
    try:
        ctx = await asyncio.to_thread(codejob.prepare, repo, journal, scope=scope)
    except (codejob.GitError, OSError) as exc:
        o.reason = f"{repo} is not a usable git repository ({str(exc)[:120]})"
        return
    ring(0.05, "snapshot taken")
    _audit(journal.run_id, "snapshot", "git", str(repo), ctx.pre)
    spec = {
        "id": journal.run_id,
        "task": codejob.task_text(
            title=plan.title,
            steps=[s.step for s in plan.steps],
            criteria=list(plan.success_criteria or []),
            files=scope,
            test_command=t.get("test_command"),
        ),
        "cwd": str(repo),
    }
    ring(None, "the coding agent is working")
    runner = runner or codejob.ClaudeRunner()
    task = asyncio.create_task(runner(spec, on_event=watch.event))
    res, stop = None, None
    try:
        res, stop = await _supervise(task, watch, poll_s)
    except Exception as exc:  # the runner itself crashed
        o.reason = f"the coding run failed to run ({str(exc)[:120]})"
    if stop is None and watch.blocked:
        stop = (STUCK, watch.blocked)
    touched = set(watch.touched) | set((res or {}).get("touched") or ())
    try:
        col = await asyncio.to_thread(codejob.collect, ctx, journal, touched=touched)
    except (codejob.GitError, OSError) as exc:
        o.reason = f"I couldn't check what changed ({str(exc)[:120]})"
        o.notes.append("Please look at the repository yourself.")
        return
    o.changed = col.ours + col.out_of_scope
    if col.unattributed:
        journal.event(
            "note",
            "changed by someone else, left alone: " + ", ".join(col.unattributed),
        )
    if col.head_moved:
        o.notes.append("A git commit was made during the run; I left it in place.")
    if stop:
        o.status, o.reason = stop
        return
    if o.reason:
        return
    if col.out_of_scope:
        o.reason = f"it edited files outside the plan ({_names(col.out_of_scope)})"
        return
    if col.head_moved:
        o.reason = "it made a git commit, which it must not do"
        return
    ring(0.85, "checking the result myself")
    cmd = t.get("test_command")
    timeout = min(codejob.VERIFY_TIMEOUT_S, max(60.0, watch.remaining()))
    kw = {"timeout": timeout}
    if verify_run is not None:
        kw["run"] = verify_run
    ok, ev = await asyncio.to_thread(codejob.verify_tests, repo, cmd, **kw)
    _audit(journal.run_id, "verify", "tests", str(cmd), ev)
    if ok:
        o.status, o.evidence = VERIFIED, ev
        o.revert = False
    else:
        err = str((res or {}).get("error") or "").strip()
        o.reason = f"the check still fails ({ev})"
        if err:
            o.reason = f"{err.rstrip('.')}, and the check still fails ({ev})"


def _load_ops(plan) -> list[dict]:
    ops = fileedit.derive_ops(plan.targets or {})
    if ops:
        return ops
    pj = (plan.dry_run or {}).get("plan_json")
    if pj:
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(Path(pj).read_text())
            raw = data.get("ops") if isinstance(data, dict) else data
            if isinstance(raw, list):
                return [dict(x) for x in raw if isinstance(x, dict)]
    return []


async def _run_file(plan, journal, watch, ring, o, *, policy):
    ops = _load_ops(plan)
    if not ops:
        o.reason = "there was no file plan to carry out"
        return
    before = {}
    for op in ops:
        if op.get("op") == "move" and op.get("src"):
            before[str(op["src"])] = uj.sha256_file(Path(op["src"]))
    editor = fileedit.Editor(journal, policy)
    total = len(ops)

    def on_event(kind, text):
        watch.event(kind, text, {"sig": text})

    def on_progress(done, n):
        ring(0.05 + 0.85 * done / max(1, n), f"{done} of {n} file steps")

    ring(0.05, f"{total} file steps planned")
    try:
        await asyncio.to_thread(
            fileedit.apply_ops,
            editor,
            ops,
            on_event=on_event,
            should_stop=watch.reason_only,
            on_progress=on_progress,
        )
    except fileedit.Stopped:
        o.status, o.reason = watch.stopped or (STUCK, "it was stopped")
        return
    except (fileedit.Denied, fileedit.OpError, OSError) as exc:
        o.reason = str(exc)[:200]
        return
    ring(0.92, "checking the files myself")
    ok, ev = await asyncio.to_thread(
        fileedit.verify_ops,
        ops,
        before=before,
        expect=(plan.targets or {}).get("expect"),
    )
    _audit(journal.run_id, "verify", "files", f"{total} ops", ev)
    if ok:
        o.status, o.evidence, o.revert = VERIFIED, ev, False
    else:
        o.reason = f"the files are not as planned ({ev})"


async def _maybe_await(value):
    return await value if inspect.isawaitable(value) else value


async def _run_web(plan, journal, watch, ring, o, *, brave, poll_s):
    if brave is None:
        try:
            brave = importlib.import_module("aware.brave_live")
        except ImportError:
            o.reason = "the live browser control isn't available yet"
            return

    def web_progress(frac, text=""):
        watch.event("progress", str(text or ""), {"p": frac})
        if not text:
            ring(frac, "")

    kwargs = {"progress": web_progress}
    with contextlib.suppress(TypeError, ValueError):
        if "run_id" in inspect.signature(brave.run).parameters:
            kwargs["run_id"] = journal.run_id
    task = asyncio.create_task(brave.run(plan, **kwargs))
    res, stop = None, None
    try:
        res, stop = await _supervise(task, watch, poll_s)
    except Exception as exc:
        o.reason = f"the browser run failed ({str(exc)[:120]})"
    if isinstance(res, dict):
        res = TakeoverResult.from_dict(res)
    o.revert = False  # nothing on disk; the tab goes back via handback

    async def handback():
        rid = res.run_id if res is not None and res.run_id else journal.run_id
        with contextlib.suppress(Exception):
            await _maybe_await(brave.handback(rid))

    if stop:
        o.status, o.reason = stop
        await handback()
        return
    if res is None:
        o.reason = o.reason or "the browser run gave no result"
        await handback()
        return
    if res.status != VERIFIED:
        o.status = res.status if res.status in (FAILED, STUCK, CANCELLED) else FAILED
        o.reason = res.reason or "the browser run did not finish"
        await handback()
        return
    ring(0.9, "checking the page myself")
    verify = getattr(brave, "verify", None)
    if callable(verify):
        try:
            ok, ev = await _maybe_await(verify(plan, res))
        except Exception as exc:
            ok, ev = False, f"the page check failed ({str(exc)[:100]})"
    else:
        ok, ev = bool((res.evidence or "").strip()), res.evidence or "no evidence"
    _audit(journal.run_id, "verify", "web", plan.targets.get("host", ""), str(ev))
    if ok:
        o.status, o.evidence = VERIFIED, str(ev)
        o.spoken_override = res.spoken if res.spoken else ""
    else:
        o.reason = f"the page does not show it finished ({ev})"
        await handback()


# --------------------------------------------------------------------------
# entry points


def _budget(plan, floor_s: float) -> float:
    if plan.time_budget_s and plan.time_budget_s > 0:
        return max(floor_s, float(plan.time_budget_s))
    est = (
        plan.est_minutes
        if plan.est_minutes and plan.est_minutes > 0
        else DEFAULT_EST_MIN
    )
    return max(floor_s, 1.5 * est * 60.0)


def _result(run_id, plan, status_, reason="", spoken="", **kw) -> TakeoverResult:
    if not spoken:
        spoken = (
            "Stopped, Sir."
            if status_ == CANCELLED
            else f"I couldn't start: {reason.rstrip('.')}."
        )
        if status_ == CANCELLED and reason:
            spoken = f"I didn't start, Sir: {reason}."
    return TakeoverResult(
        run_id=run_id,
        plan_id=plan.id,
        status=status_,
        reason=reason,
        spoken=spoken,
        **kw,
    )


async def run(
    plan: TakeoverPlan | dict,
    *,
    progress: Progress | None,
    runner=None,
    brave=None,
    verify_run=None,
    policy: fileedit.Policy | None = None,
    poll_s: float = 1.0,
    no_progress_s: float = NO_PROGRESS_S,
    budget_floor_s: float = BUDGET_FLOOR_S,
    clock: Callable[[], float] = time.monotonic,
) -> TakeoverResult:
    """Run one accepted takeover to a verified result or an honest stop."""
    if isinstance(plan, dict):
        plan = TakeoverPlan.from_dict(plan)
    t_start = time.time()
    if not state.env_allows() or state.killed():
        return _result("", plan, CANCELLED, "the kill switch is on")
    if not state.load().get("enabled"):
        return _result("", plan, CANCELLED, "Always Aware is off")

    lock_fd = os.open(
        state.aware_dir() / "takeover.lock", os.O_RDWR | os.O_CREAT, 0o600
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock_fd)
        return _result("", plan, FAILED, "another takeover is running")

    run_id = f"aw-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    journal = None
    ring = None
    try:
        journal = uj.Journal.create(
            run_id,
            kind=plan.kind,
            title=plan.title,
            meta={
                "plan_id": plan.id,
                "task_kind": plan.task_kind,
                "pid": os.getpid(),
                "plan": plan.to_dict(),
            },
        )
        state.write_private(
            _current_path(),
            json.dumps({"run_id": run_id, "pid": os.getpid(), "plan_id": plan.id}),
        )
        ring = _Ring(plan.title, progress)
        ring(0.0, f"taking over: {plan.title}")
        _audit(run_id, "start", plan.kind, plan.title, "running")
        watch = Watch(
            run_id,
            journal,
            ring,
            budget_s=_budget(plan, budget_floor_s),
            no_progress_s=no_progress_s,
            clock=clock,
        )
        policy = policy or fileedit.Policy.default()
        o = _Outcome()
        try:
            if plan.kind == "code":
                await _run_code(
                    plan,
                    journal,
                    watch,
                    ring,
                    o,
                    runner=runner,
                    verify_run=verify_run,
                    poll_s=poll_s,
                    policy=policy,
                )
            elif plan.kind == "file":
                await _run_file(plan, journal, watch, ring, o, policy=policy)
            elif plan.kind == "web":
                await _run_web(
                    plan, journal, watch, ring, o, brave=brave, poll_s=poll_s
                )
            else:
                o.reason = "I can't take over that kind of task yet"
        except asyncio.CancelledError:
            o.status, o.reason = CANCELLED, "the run was cancelled"
            raise
        except Exception as exc:  # never leave a half-done run without reverting
            o.status, o.reason = (
                FAILED,
                f"something went wrong ({type(exc).__name__}: {str(exc)[:120]})",
            )
        finally:
            rep = None
            if o.status != VERIFIED and o.revert:
                ring(None, "reverting my changes")
                rep = await asyncio.to_thread(uj.undo, journal)
            elif o.status != VERIFIED:
                rep = uj.UndoReport(run_id)
            spoken = _spoken(o, rep)
            if o.status == VERIFIED:
                ring(1.0, "verified")
            journal.set_status(
                o.status, reason=o.reason, evidence=o.evidence, spoken=spoken
            )
            _audit(
                run_id,
                "result",
                plan.kind,
                plan.title,
                f"{o.status}: {o.reason or o.evidence}",
            )
            ring.finish(o.status == VERIFIED, spoken)
            result = TakeoverResult(
                run_id=run_id,
                plan_id=plan.id,
                status=o.status,
                evidence=o.evidence,
                spoken=spoken,
                reverted=bool(rep and rep.restored),
                left_alone=list(rep.left_alone) if rep else [],
                reason=o.reason,
                journal=str(journal.dir),
                duration_s=round(time.time() - t_start, 3),
            )
        return result
    finally:
        with contextlib.suppress(OSError):
            data = json.loads(_current_path().read_text())
            if data.get("run_id") == run_id:
                _current_path().unlink()
        with contextlib.suppress(OSError):
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        with contextlib.suppress(Exception):
            uj.prune()


def recover() -> list[TakeoverResult]:
    """Sidecar start: revert runs left open by a dead process (bridge restart).

    Returns one failed result per run so the caller can say one honest line."""
    out = []
    for j in uj.runs():
        meta = j.meta()
        if meta.get("status") != "open":
            continue
        pid = meta.get("pid")
        if pid == os.getpid() or _pid_alive(pid):
            continue
        rep = uj.UndoReport(j.run_id)
        if meta.get("kind") != "web":
            rep = uj.undo(j)
        o = _Outcome()
        o.reason = "it was interrupted by a restart"
        spoken = _spoken(o, rep)
        j.set_status(FAILED, reason=o.reason, spoken=spoken)
        _audit(
            j.run_id,
            "result",
            str(meta.get("kind")),
            str(meta.get("title")),
            f"failed: {o.reason}",
        )
        out.append(
            TakeoverResult(
                run_id=j.run_id,
                plan_id=str(meta.get("plan_id") or ""),
                status=FAILED,
                spoken=spoken,
                reverted=bool(rep.restored),
                left_alone=list(rep.left_alone),
                reason=o.reason,
                journal=str(j.dir),
            )
        )
    return out
