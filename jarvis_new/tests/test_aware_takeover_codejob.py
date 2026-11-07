"""Always Aware code executor (PLAN.md §3.4, §5): git snapshot without touching
the user's repo state, stream parsing, scope attribution, independent verify.

Real git in tmp repos only; the claude runner is a fake script.
"""

import asyncio
import json
import os
import subprocess
import sys
import textwrap

import pytest

from aware import codejob as cj
from aware import undo_journal as uj


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jhome"))


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "T")
    (r / "app.py").write_text("def add(a, b):\n    return a - b\n")
    (r / "other.py").write_text("x = 1\n")
    (r / ".gitignore").write_text("*.log\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def _repo_state(r):
    gitdir = r / ".git"
    return {
        "status": git(r, "status", "--porcelain=v1", "-uall"),
        "index": (gitdir / "index").read_bytes(),
        "head": git(r, "rev-parse", "HEAD"),
        "refs": git(
            r,
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            "refs/heads",
            "refs/tags",
        ),
        "stash": git(r, "stash", "list"),
        "branch": git(r, "branch", "--show-current"),
    }


def test_parse_stream_line():
    def ev(content):
        return json.dumps({"type": "assistant", "message": {"content": content}})

    out = cj.parse_stream_line(
        ev(
            [
                {
                    "type": "text",
                    "text": "PROGRESS: reading tests\nBLOCKED: needs a login",
                }
            ]
        )
    )
    assert ("progress", "reading tests") in [(k, t) for k, t, _ in out]
    assert ("blocked", "needs a login") in [(k, t) for k, t, _ in out]
    out = cj.parse_stream_line(
        ev(
            [
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {"file_path": "/r/app.py", "old_string": "a"},
                }
            ]
        )
    )
    kind, text, extra = out[0]
    assert kind == "action" and "Edit" in text and extra["path"] == "/r/app.py"
    out = cj.parse_stream_line(ev([{"type": "text", "text": "DONE: fixed it"}]))
    assert out[0][:2] == ("done", "fixed it")
    assert cj.parse_stream_line("not json") == []
    assert (
        cj.parse_stream_line(json.dumps({"type": "system", "session_id": "s"}))[0][0]
        == "heartbeat"
    )


def test_snapshot_leaves_user_repo_state_alone(repo):
    (repo / "app.py").write_text("staged change\n")
    git(repo, "add", "app.py")
    (repo / "other.py").write_text("unstaged change\n")
    (repo / "new.txt").write_text("untracked\n")
    (repo / "x.log").write_text("ignored\n")
    before = _repo_state(repo)
    j = uj.Journal.create("snap-1", kind="code", title="t")
    sha = cj.snapshot(repo, j, "pre")
    assert _repo_state(repo) == before
    assert git(repo, "rev-parse", "refs/jarvis/aware/snap-1/pre").strip() == sha
    assert git(repo, "show", f"{sha}:new.txt") == "untracked\n"
    assert git(repo, "show", f"{sha}:other.py") == "unstaged change\n"
    assert "x.log" not in git(repo, "ls-tree", "-r", "--name-only", sha)
    assert "refs/jarvis/aware/snap-1/pre" in j.meta()["refs"]
    assert not [p for p in j.dir.iterdir() if p.name.startswith("index")]


def test_snapshot_works_without_commits(tmp_path):
    r = tmp_path / "empty"
    r.mkdir()
    git(r, "init", "-q")
    (r / "a.py").write_text("a\n")
    j = uj.Journal.create("snap-2", kind="code", title="t")
    sha = cj.snapshot(r, j, "pre")
    assert git(r, "show", f"{sha}:a.py") == "a\n"


def test_collect_attribution_and_journal(repo):
    (repo / "other.py").write_text("dirty before run\n")  # another agent's work
    j = uj.Journal.create("col-1", kind="code", title="t")
    ctx = cj.prepare(repo, j, scope=["app.py"])
    assert ctx.dirty_before == {"other.py"}
    # the "run": in-scope edit, a new in-scope test, a stray edit by claude,
    # and an unrelated change by someone else
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "stray.py").write_text("oops\n")
    (repo / "other.py").write_text("other agent kept typing\n")
    col = cj.collect(ctx, j, touched={str(repo / "app.py"), str(repo / "stray.py")})
    assert col.ours == ["app.py"]
    assert col.out_of_scope == ["stray.py"]
    assert col.unattributed == ["other.py"]
    # stray claude edit is journaled too, so the revert removes it
    rep = uj.undo(j)
    assert (repo / "app.py").read_text().endswith("a - b\n")
    assert not (repo / "stray.py").exists()
    assert (repo / "other.py").read_text() == "other agent kept typing\n"
    assert set(rep.restored) == {str(repo / "app.py"), str(repo / "stray.py")}
    assert "+    return a + b" in j.diff_text()


def test_collect_empty_scope_means_clean_files_claude_touched(repo):
    j = uj.Journal.create("col-2", kind="code", title="t")
    ctx = cj.prepare(repo, j, scope=[])
    (repo / "app.py").write_text("changed\n")
    (repo / "other.py").write_text("someone else\n")
    col = cj.collect(ctx, j, touched={str(repo / "app.py")})
    assert col.ours == ["app.py"] and col.unattributed == ["other.py"]


def test_collect_bash_edit_in_scope_counts_as_ours(repo):
    j = uj.Journal.create("col-3", kind="code", title="t")
    ctx = cj.prepare(repo, j, scope=[str(repo / "app.py")])
    (repo / "app.py").unlink()  # deleted via a Bash rm: no tool event
    col = cj.collect(ctx, j, touched=set())
    assert col.ours == ["app.py"]
    uj.undo(j)
    assert (repo / "app.py").exists()


def test_collect_notes_head_moved(repo):
    j = uj.Journal.create("col-4", kind="code", title="t")
    ctx = cj.prepare(repo, j, scope=["app.py"])
    (repo / "app.py").write_text("x\n")
    git(repo, "commit", "-qam", "claude committed")
    col = cj.collect(ctx, j, touched={str(repo / "app.py")})
    assert col.head_moved


def test_verify_tests_uses_its_own_run(repo):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw["cwd"]))
        return subprocess.CompletedProcess(cmd, 0, "14 passed in 0.5s\n", "")

    ok, ev = cj.verify_tests(repo, "pytest -q tests/test_auth.py", run=fake_run)
    assert ok and "14 passed" in ev
    assert calls == [(["pytest", "-q", "tests/test_auth.py"], str(repo))]

    def failing(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "2 failed, 12 passed\n", "")

    ok, ev = cj.verify_tests(repo, ["pytest"], run=failing)
    assert not ok and "2 failed" in ev

    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)

    ok, ev = cj.verify_tests(repo, ["pytest"], run=timeout)
    assert not ok and "timed out" in ev
    ok, ev = cj.verify_tests(repo, None)
    assert not ok


def test_task_text_carries_rules():
    text = cj.task_text(
        title="Fix the add bug",
        steps=["fix add", "rerun tests"],
        criteria=["tests/test_app.py passes"],
        files=["app.py"],
        test_command=["pytest", "-q"],
    )
    for needle in (
        "BLOCKED:",
        "no push",
        "app.py",
        "pytest -q",
        "tests/test_app.py passes",
        "commit",
    ):
        assert needle in text


FAKE_CLAUDE = textwrap.dedent(
    """
    import json, sys, time, pathlib, os
    prompt = sys.stdin.read()
    args = sys.argv[1:]
    def say(content):
        print(json.dumps({"type": "assistant", "message": {"content": content}}), flush=True)
    print(json.dumps({"type": "system", "session_id": "s1", "args": args, "big": "x" * 200000}), flush=True)
    say([{"type": "text", "text": "PROGRESS: on it"}])
    if "HANG" in prompt:
        time.sleep(60)
    target = pathlib.Path(os.getcwd()) / "app.py"
    say([{"type": "tool_use", "name": "Edit", "input": {"file_path": str(target)}}])
    target.write_text("fixed\\n")
    say([{"type": "text", "text": "DONE: fixed"}])
    print(json.dumps({"type": "result", "result": "fixed"}), flush=True)
    """
)


@pytest.fixture
def fake_claude(tmp_path):
    p = tmp_path / "fake_claude"
    p.write_text("#!" + sys.executable + "\n" + FAKE_CLAUDE)
    p.chmod(0o755)
    return p


async def test_claude_runner_streams_events(repo, tmp_path, fake_claude):
    events = []
    runner = cj.ClaudeRunner(binary=str(fake_claude), job_dir=tmp_path / "jobs")
    res = await runner(
        {"id": "aw-t1", "task": "fix it", "cwd": str(repo)},
        on_event=lambda k, t, e: events.append((k, t, e)),
    )
    assert res["rc"] == 0 and res["summary"] == "fixed"
    kinds = [k for k, _, _ in events]
    assert "progress" in kinds and "action" in kinds and "done" in kinds
    assert str(repo / "app.py") in res["touched"]
    assert (repo / "app.py").read_text() == "fixed\n"
    log = (tmp_path / "jobs" / "aw-t1.log").read_text()
    assert "--permission-mode" in log  # argv from claude_code_runner.build_argv


async def test_claude_runner_respects_repo_lock(repo, tmp_path, fake_claude):
    import fcntl

    import claude_code_runner as ccr

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    held = open(ccr.lock_path(repo, jobs), "a+")  # noqa: SIM115
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        runner = cj.ClaudeRunner(binary=str(fake_claude), job_dir=jobs)
        res = await runner(
            {"id": "aw-t3", "task": "x", "cwd": str(repo)}, on_event=lambda *a: None
        )
        assert res["rc"] == 1 and "another coding job" in res["error"]
        assert (repo / "app.py").read_text().startswith("def add")
    finally:
        held.close()


async def test_claude_runner_cancel_kills_process_group(repo, tmp_path, fake_claude):
    runner = cj.ClaudeRunner(binary=str(fake_claude), job_dir=tmp_path / "jobs")
    started = asyncio.Event()

    def on_event(k, t, e):
        if k == "progress":
            started.set()

    task = asyncio.create_task(
        runner({"id": "aw-t2", "task": "HANG", "cwd": str(repo)}, on_event=on_event)
    )
    await asyncio.wait_for(started.wait(), 10)
    pid = runner.last_pid
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
