"""Always Aware takeover run (PLAN.md §3.4): dispatch, independent verify,
watchdog/stuck policy, revert, handback, honest report, cancel, lock.

Tmp dirs only; code jobs use a fake runner; web uses a fake brave_live.
"""

import asyncio
import json
import subprocess
import sys

import pytest

from aware import fileedit as fe
from aware import state
from aware import takeover as tk
from aware import undo_journal as uj
from aware.types import TakeoverPlan, TakeoverResult

SUCCESS = ("done", "pass", "fixed", "finished", "complete")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jhome"))
    # the default reach is $HOME + /mnt/data; in tests it is tmp_path only
    monkeypatch.setattr(
        fe.Policy,
        "default",
        classmethod(
            lambda cls: fe.Policy(
                home=tmp_path, jarvis_home=tmp_path / "jhome", roots=(tmp_path,)
            )
        ),
    )
    monkeypatch.delenv("JARVIS_AWARE", raising=False)
    state.update(enabled=True)
    return tmp_path


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
    (r / "notes.md").write_text("notes\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


CHECK = [
    sys.executable,
    "-c",
    "import app; assert app.add(1, 2) == 3; print('1 passed')",
]


def code_plan(repo, **kw):
    base = {
        "id": "plan-1",
        "opp_id": "opp-1",
        "kind": "code",
        "task_kind": "test_fix",
        "title": "Fix add",
        "steps": [],
        "success_criteria": ["add works"],
        "time_budget_s": 600,
        "est_minutes": 2,
        "targets": {"repo": str(repo), "test_command": CHECK, "files": ["app.py"]},
    }
    base.update(kw)
    return TakeoverPlan(**base)


def make_runner(script):
    """script: list of ("event", kind, text, extra) | ("write", path, text) | ("sleep", s) | ("result", dict)."""
    seen = {}

    async def runner(spec, *, on_event):
        seen["spec"] = spec
        touched = set()
        res = {"rc": 0, "summary": "DONE claimed", "error": ""}
        for step in script:
            if step[0] == "event":
                on_event(step[1], step[2], step[3] if len(step) > 3 else {})
            elif step[0] == "write":
                from pathlib import Path

                Path(step[1]).write_text(step[2])
                touched.add(str(step[1]))
                on_event(
                    "action", f"Edit {step[1]}", {"path": str(step[1]), "sig": step[2]}
                )
            elif step[0] == "sleep":
                await asyncio.sleep(step[1])
            elif step[0] == "result":
                res.update(step[1])
        res["touched"] = touched
        return res

    runner.seen = seen
    return runner


class Prog:
    def __init__(self):
        self.calls = []

    def __call__(self, p, text):
        self.calls.append((p, text))


def no_success_words(text):
    low = text.lower()
    return not any(f" {w}" in f" {low}" for w in ("done,", "fixed", "all tests pass"))


async def test_code_verified(repo):
    runner = make_runner(
        [
            ("event", "progress", "fixing add"),
            ("write", repo / "app.py", "def add(a, b):\n    return a + b\n"),
            ("event", "done", "fixed"),
        ]
    )
    prog = Prog()
    res = await tk.run(code_plan(repo), progress=prog, runner=runner, poll_s=0.01)
    assert isinstance(res, TakeoverResult)
    assert res.status == "verified", res
    assert "1 passed" in res.evidence
    assert res.spoken.startswith("Done, Sir.") and "app.py" in res.spoken
    assert not res.reverted
    assert (repo / "app.py").read_text().endswith("a + b\n")
    assert prog.calls[0][0] == 0.0 and prog.calls[-1][0] == 1.0
    assert any(p is None for p, _ in prog.calls)  # executor phase is indeterminate
    # brief came from the plan and carries the rules
    assert "BLOCKED:" in runner.seen["spec"]["task"] and runner.seen["spec"][
        "cwd"
    ] == str(repo)
    j = uj.Journal.open(res.run_id)
    assert j.meta()["status"] == "verified"
    assert "+    return a + b" in tk.show_last()
    # undo that works afterwards
    rep = uj.undo_last(1)[0]
    assert (repo / "app.py").read_text().endswith("a - b\n") and rep.restored
    # user's repo state untouched: no commits, no stash
    assert git(repo, "rev-list", "--count", "HEAD").strip() == "1"
    assert git(repo, "stash", "list") == ""


async def test_code_claim_without_fix_fails_and_reverts(repo):
    # the worker says DONE but its edit does not fix anything
    runner = make_runner(
        [
            ("write", repo / "app.py", "def add(a, b):\n    return a * b\n"),
            ("event", "done", "fixed it"),
        ]
    )
    res = await tk.run(code_plan(repo), progress=Prog(), runner=runner, poll_s=0.01)
    assert res.status == "failed"
    assert res.reverted
    assert (repo / "app.py").read_text().endswith("a - b\n")
    assert res.spoken.startswith("I couldn't finish")
    assert no_success_words(res.spoken)


async def test_blocked_is_stuck_and_reverts(repo):
    runner = make_runner(
        [
            ("write", repo / "app.py", "half\n"),
            ("event", "blocked", "it needs Sir's database password"),
            ("sleep", 5),
        ]
    )
    res = await tk.run(code_plan(repo), progress=Prog(), runner=runner, poll_s=0.01)
    assert res.status == "stuck"
    assert "password" in res.reason
    assert (repo / "app.py").read_text().endswith("a - b\n")
    assert "password" in res.spoken and "reverted" in res.spoken


async def test_no_progress_watchdog(repo):
    runner = make_runner([("event", "progress", "start"), ("sleep", 5)])
    res = await tk.run(
        code_plan(repo), progress=Prog(), runner=runner, poll_s=0.01, no_progress_s=0.2
    )
    assert res.status == "stuck" and "no progress" in res.reason


async def test_time_budget(repo):
    runner = make_runner(
        [("event", "progress", "a")]
        + [("sleep", 0.05), ("event", "progress", "tick")] * 40
    )
    res = await tk.run(
        code_plan(repo, time_budget_s=0.3),
        progress=Prog(),
        runner=runner,
        poll_s=0.01,
        budget_floor_s=0,
    )
    assert res.status == "stuck" and "time" in res.reason


async def test_repeated_action_is_stuck(repo):
    same = ("event", "action", "Bash pytest", {"sig": "same"})
    runner = make_runner([same, same, same, ("sleep", 5)])
    res = await tk.run(code_plan(repo), progress=Prog(), runner=runner, poll_s=0.01)
    assert res.status == "stuck" and "same step" in res.reason


async def test_out_of_scope_edit_fails_and_reverts_it(repo):
    runner = make_runner(
        [
            ("write", repo / "app.py", "def add(a, b):\n    return a + b\n"),
            ("write", repo / "notes.md", "claude scribbled\n"),
        ]
    )
    res = await tk.run(code_plan(repo), progress=Prog(), runner=runner, poll_s=0.01)
    assert res.status == "failed" and "notes.md" in res.reason
    assert (repo / "notes.md").read_text() == "notes\n"
    assert (repo / "app.py").read_text().endswith("a - b\n")


async def test_unattributed_change_is_left_alone(repo):
    async def runner(spec, *, on_event):
        (repo / "app.py").write_text("def add(a, b):\n    return a + b\n")
        on_event("action", "Edit app.py", {"path": str(repo / "app.py")})
        (repo / "notes.md").write_text("another agent typing\n")  # not claude
        return {"rc": 0, "summary": "", "error": "", "touched": {str(repo / "app.py")}}

    res = await tk.run(code_plan(repo), progress=Prog(), runner=runner, poll_s=0.01)
    assert res.status == "verified"
    assert (repo / "notes.md").read_text() == "another agent typing\n"


async def test_sir_edit_during_run_is_left_alone_on_revert(repo):
    async def runner(spec, *, on_event):
        (repo / "app.py").write_text("broken\n")
        on_event("action", "Edit app.py", {"path": str(repo / "app.py")})
        return {
            "rc": 1,
            "summary": "",
            "error": "exit 1",
            "touched": {str(repo / "app.py")},
        }

    real_collect = tk.codejob.collect

    def collect_then_sir_types(ctx, journal, *, touched):
        col = real_collect(ctx, journal, touched=touched)
        (repo / "app.py").write_text("Sir's own fix\n")
        return col

    tk.codejob.collect, saved = collect_then_sir_types, real_collect
    try:
        res = await tk.run(code_plan(repo), progress=Prog(), runner=runner, poll_s=0.01)
    finally:
        tk.codejob.collect = saved
    assert res.status == "failed"
    assert (repo / "app.py").read_text() == "Sir's own fix\n"
    assert res.left_alone == [str(repo / "app.py")]
    assert "app.py" in res.spoken and "left" in res.spoken


async def test_cancel_cross_process_flag(repo):
    async def runner(spec, *, on_event):
        (repo / "app.py").write_text("partial\n")
        on_event("action", "Edit app.py", {"path": str(repo / "app.py")})
        for _ in range(500):
            on_event("heartbeat", "", {})
            await asyncio.sleep(0.01)
        return {"rc": 0, "touched": set()}

    async def stop_soon():
        for _ in range(200):
            await asyncio.sleep(0.01)
            if tk.current():
                assert tk.request_cancel() is True
                return

    res, _ = await asyncio.gather(
        tk.run(code_plan(repo), progress=Prog(), runner=runner, poll_s=0.01),
        stop_soon(),
    )
    assert res.status == "cancelled"
    assert (repo / "app.py").read_text().endswith("a - b\n")
    assert res.spoken.startswith("Stopped")
    assert tk.current() is None


async def test_kill_file_stops_run(repo):
    async def runner(spec, *, on_event):
        state.kill_path().touch()
        for _ in range(500):
            on_event("heartbeat", "", {})
            await asyncio.sleep(0.01)
        return {"rc": 0, "touched": set()}

    res = await tk.run(code_plan(repo), progress=Prog(), runner=runner, poll_s=0.01)
    assert res.status == "cancelled" and "kill" in res.reason


async def test_refuses_when_off_or_killed(repo):
    state.update(enabled=False)
    res = await tk.run(code_plan(repo), progress=Prog(), runner=make_runner([]))
    assert res.status == "cancelled" and "off" in res.reason
    state.update(enabled=True)
    state.kill_path().touch()
    res = await tk.run(code_plan(repo), progress=Prog(), runner=make_runner([]))
    assert res.status == "cancelled"


async def test_one_takeover_at_a_time(repo):
    gate = asyncio.Event()

    async def slow(spec, *, on_event):
        while not gate.is_set():
            on_event("heartbeat", "", {})
            await asyncio.sleep(0.01)
        return {"rc": 0, "touched": set()}

    first = asyncio.create_task(
        tk.run(code_plan(repo), progress=Prog(), runner=slow, poll_s=0.01)
    )
    while tk.current() is None:
        await asyncio.sleep(0.01)
    second = await tk.run(
        code_plan(repo, id="plan-2"), progress=Prog(), runner=make_runner([])
    )
    assert second.status == "failed" and "another takeover" in second.reason
    gate.set()
    await first


async def test_not_a_git_repo(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    res = await tk.run(code_plan(d), progress=Prog(), runner=make_runner([]))
    assert res.status == "failed" and "git" in res.reason


async def test_progress_callback_errors_never_break_run(repo):
    def bad(p, t):
        raise RuntimeError("hud down")

    runner = make_runner(
        [("write", repo / "app.py", "def add(a, b):\n    return a + b\n")]
    )
    res = await tk.run(code_plan(repo), progress=bad, runner=runner, poll_s=0.01)
    assert res.status == "verified"


async def test_activity_ring_mirrors_progress(repo):
    import activity

    runner = make_runner(
        [("write", repo / "app.py", "def add(a, b):\n    return a + b\n")]
    )
    res = await tk.run(code_plan(repo), progress=Prog(), runner=runner, poll_s=0.01)
    items = [i for i in activity.list_items() if "AWARE" in (i.get("title") or "")]
    assert items and items[0]["status"] == "done"
    assert items[0]["progress"] == 100.0  # activity uses 0..100
    audit = (state.aware_dir() / "audit.jsonl").read_text().splitlines()
    rows = [json.loads(x) for x in audit]
    assert any(r.get("run") == res.run_id and r.get("step") == "result" for r in rows)


# ---------------------------------------------------------------- file kind


def file_plan(targets, **kw):
    base = {
        "id": "fplan",
        "opp_id": "o",
        "kind": "file",
        "task_kind": "file_organise",
        "title": "Tidy downloads",
        "steps": [],
        "success_criteria": [],
        "time_budget_s": 600,
        "targets": targets,
    }
    base.update(kw)
    return TakeoverPlan(**base)


@pytest.fixture
def fhome(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home, fe.Policy(home=home, jarvis_home=tmp_path / "jhome", roots=(home,))


async def test_file_organise_verified_and_undo(fhome):
    home, policy = fhome
    dl = home / "Downloads"
    dl.mkdir()
    for n in ("a.pdf", "b.pdf"):
        (dl / n).write_text(n)
    plan = file_plan(
        {"paths": [str(dl / "a.pdf"), str(dl / "b.pdf")], "dest": str(home / "PDFs")}
    )
    prog = Prog()
    res = await tk.run(plan, progress=prog, policy=policy)
    assert res.status == "verified", res
    assert "3 of 3 checks pass" in res.evidence
    assert (home / "PDFs" / "a.pdf").read_text() == "a.pdf"
    assert [p for p, _ in prog.calls if p is not None] == sorted(
        p for p, _ in prog.calls if p is not None
    )
    uj.undo_last(1)
    assert (dl / "a.pdf").exists() and not (home / "PDFs").exists()


async def test_file_denied_midway_reverts_earlier_ops(fhome):
    home, policy = fhome
    (home / "a.txt").write_text("a")
    (home / ".ssh").mkdir()
    ops = [
        {"op": "write", "path": str(home / "a.txt"), "content": "changed"},
        {"op": "write", "path": str(home / ".ssh" / "config"), "content": "Host *"},
    ]
    res = await tk.run(file_plan({"ops": ops}), progress=Prog(), policy=policy)
    assert res.status == "failed" and ".ssh" in res.reason
    assert (home / "a.txt").read_text() == "a"
    assert not (home / ".ssh" / "config").exists()
    assert res.reverted


async def test_file_plan_json_from_dry_run(fhome, tmp_path):
    home, policy = fhome
    pj = tmp_path / "plan.json"
    pj.write_text(
        json.dumps(
            {"ops": [{"op": "write", "path": str(home / "n.md"), "content": "hi"}]}
        )
    )
    res = await tk.run(
        file_plan({}, dry_run={"plan_json": str(pj)}), progress=Prog(), policy=policy
    )
    assert res.status == "verified" and (home / "n.md").read_text() == "hi"


async def test_file_no_plan(fhome):
    _, policy = fhome
    res = await tk.run(file_plan({}), progress=Prog(), policy=policy)
    assert res.status == "failed" and "plan" in res.reason


async def test_file_expect_mismatch_fails(fhome):
    home, policy = fhome
    ops = [{"op": "write", "path": str(home / "n.md"), "content": "hi"}]
    res = await tk.run(
        file_plan({"ops": ops, "expect": {str(home / "other.md"): "exists"}}),
        progress=Prog(),
        policy=policy,
    )
    assert res.status == "failed" and not (home / "n.md").exists()


async def test_plan_as_dict_accepted(fhome):
    home, policy = fhome
    ops = [{"op": "write", "path": str(home / "n.md"), "content": "hi"}]
    res = await tk.run(
        file_plan({"ops": ops}).to_dict(), progress=Prog(), policy=policy
    )
    assert res.status == "verified"


# ---------------------------------------------------------------- web kind


class FakeBrave:
    def __init__(self, status="verified", verify=True, hang=False, has_verify=True):
        self.status, self.verify_ok, self.hang = status, verify, hang
        self.handed_back = []
        if not has_verify:
            self.verify = None

    async def run(self, plan, *, progress):
        progress(0.5, "filling the form")
        if self.hang:
            await asyncio.sleep(10)
        return TakeoverResult(
            run_id="brave-1",
            plan_id=plan.id,
            status=self.status,
            evidence="confirmation page shown",
            spoken="Submitted, Sir. The tab is back in your window.",
        )

    async def verify(self, plan, result):
        return self.verify_ok, "predicate true" if self.verify_ok else "predicate false"

    async def handback(self, run_id):
        self.handed_back.append(run_id)


def web_plan(**kw):
    base = {
        "id": "wplan",
        "opp_id": "o",
        "kind": "web",
        "task_kind": "web_form",
        "title": "Council form",
        "steps": [],
        "success_criteria": ["thank-you page"],
        "time_budget_s": 600,
        "targets": {"tab_id": "T1"},
    }
    base.update(kw)
    return TakeoverPlan(**base)


async def test_web_verified_uses_independent_verify():
    b = FakeBrave()
    res = await tk.run(web_plan(), progress=Prog(), brave=b, poll_s=0.01)
    assert res.status == "verified" and "predicate true" in res.evidence
    assert b.handed_back == []


async def test_web_claim_but_verify_false():
    b = FakeBrave(verify=False)
    res = await tk.run(web_plan(), progress=Prog(), brave=b, poll_s=0.01)
    assert res.status == "failed" and b.handed_back
    assert no_success_words(res.spoken)


async def test_web_stuck_hands_back():
    b = FakeBrave(status="stuck")
    res = await tk.run(web_plan(), progress=Prog(), brave=b, poll_s=0.01)
    assert res.status == "stuck" and b.handed_back


async def test_web_timeout_cancels_and_hands_back():
    b = FakeBrave(hang=True)
    res = await tk.run(
        web_plan(time_budget_s=0.2),
        progress=Prog(),
        brave=b,
        poll_s=0.01,
        budget_floor_s=0,
    )
    assert res.status == "stuck" and b.handed_back


async def test_web_without_verify_needs_evidence():
    b = FakeBrave(has_verify=False)
    res = await tk.run(web_plan(), progress=Prog(), brave=b, poll_s=0.01)
    assert res.status == "verified"


async def test_web_module_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "aware.brave_live", None)  # import -> ImportError
    res = await tk.run(web_plan(), progress=Prog(), poll_s=0.01)
    assert res.status == "failed" and "browser" in res.reason


async def test_other_kind_not_supported():
    plan = web_plan(kind="other", task_kind="doc_draft")
    res = await tk.run(plan, progress=Prog())
    assert res.status == "failed"


def test_recover_reverts_interrupted_runs(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("orig")
    j = uj.Journal.create("aw-dead", kind="file", title="t", meta={"pid": 999999999})
    j.backup(f)
    uj.atomic_write(f, b"half")
    j.record_write(f, before=b"orig", after=b"half")
    out = tk.recover()
    assert [r.run_id for r in out] == ["aw-dead"]
    assert out[0].status == "failed" and "interrupted" in out[0].reason
    assert f.read_text() == "orig"
    assert tk.recover() == []


def test_what_did_you_try(repo):
    j = uj.Journal.create("aw-x", kind="code", title="Fix add")
    j.event("progress", "reading the failing test")
    j.event("blocked", "needs a password")
    j.set_status("stuck", reason="needs a password")
    text = tk.what_did_you_try()
    assert (
        "Fix add" in text and "reading the failing test" in text and "password" in text
    )
