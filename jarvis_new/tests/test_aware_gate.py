"""Always Aware capability gate (PLAN.md §3.1): fake probes + fake verifier."""

import json
from pathlib import Path

import pytest

from aware import feasibility, gate
from aware import taskprofiles as tp
from aware.types import Consequential, Opportunity, Stage, TakeoverPlan


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    return tmp_path


class FakeProbes(gate.Probes):
    """Everything green unless a test flips an attribute."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.ctx = {"guest": False, "private": False, "locked": False, "in_call": False}
        self.tools = {"claude"}
        self.busy = False
        self.repo = tmp / "repo"
        self.repo.mkdir(exist_ok=True)
        self.git = True
        self.cmd = ["python3", "-m", "pytest", "-q"]
        self.test_rc, self.test_out = 1, "1 failed, 13 passed in 0.5s"
        self.dirty: set | None = set()
        self.cdp, self.worker = True, True
        self.tab_list = [
            {
                "id": "T1",
                "type": "page",
                "url": "https://council.example/form?x=1",
                "webSocketDebuggerUrl": "ws://x",
            }
        ]
        self.page_info = {"title": "Bin form", "text": "Postcode field", "login": False}
        self.dom = {"selector_ok": True, "predicate": False}
        self.control, self.running = True, False
        self.saved = True
        self.cap = (None, "GBP")
        self.chatgpt = True
        self.out_dir = tmp / "out"
        self.out_dir.mkdir(exist_ok=True)
        self.head = {"ok": True, "size": 1000, "login": False}
        self.free = 10**12
        self.test_runs = 0
        from aware import fileedit

        self.file_policy = fileedit.Policy(
            home=tmp, jarvis_home=tmp / "home", roots=(tmp,)
        )

    def context(self):
        return dict(self.ctx)

    def which(self, name):
        return name in self.tools

    def runner_busy(self, repo):
        return self.busy

    def chatgpt_ok(self):
        return self.chatgpt

    def cdp_ok(self):
        return self.cdp

    def worker_ok(self):
        return self.worker

    def resolve_repo(self, hint):
        return self.repo if hint else None

    def is_git(self, repo):
        return self.git

    def test_command(self, repo):
        return self.cmd

    def run_tests(self, repo, cmd, timeout):
        self.test_runs += 1
        return self.test_rc, self.test_out

    def dirty_files(self, repo):
        return self.dirty

    def diff_stat(self, repo):
        return ""

    def tabs(self):
        return list(self.tab_list)

    def page(self, tab):
        return self.page_info

    def dom_check(self, tab, selector, predicate):
        return self.dom

    def control_free(self):
        return self.control

    def takeover_running(self):
        return self.running

    def saved_payment(self, method, host):
        return self.saved

    def spend_cap(self):
        return self.cap

    def url_head(self, url):
        return self.head

    def disk_free(self, path):
        return self.free

    def deliverable_dir(self):
        return self.out_dir

    def plans_dir(self):
        d = self.tmp / "plans"
        d.mkdir(exist_ok=True)
        return d


def verifier(feasible=True, plan=None, human=None, risks=None):
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return json.dumps(
            {
                "feasible": feasible,
                "plan": plan
                if plan is not None
                else [{"step": "do it", "tool": "TOOL"}],
                "human_steps": human or [],
                "risks": risks or [],
            }
        )

    ask.calls = calls
    return ask


def auto_verifier(**kw):
    """Verifier whose single step uses the first inventory tool."""

    def ask(prompt):
        inv = json.loads(
            prompt.split("Inventory (JSON): ", 1)[1].split("\n\nReply", 1)[0]
        )
        default = [{"step": "first step", "tool": inv["tools"][0]}]
        if inv.get("family") == "web":
            default = [
                {
                    "step": "Fill postcode",
                    "tool": "type",
                    "target": "css=#postcode",
                    "text": "AB1 2CD",
                }
            ]
            default += [
                {"step": "Submit", "tool": "click", "action": i}
                for i, _ in enumerate(inv.get("actions", []))
            ]
        plan = kw.get("plan") or default
        return json.dumps(
            {
                "feasible": kw.get("feasible", True),
                "plan": plan,
                "human_steps": kw.get("human", []),
                "risks": [],
            }
        )

    return ask


def opp(task_kind="test_fix", **kw):
    ev = kw.pop("evidence", {})
    base = {
        "id": "o1",
        "kind": tp.PROFILES[task_kind].family if task_kind in tp.PROFILES else "other",
        "summary": "fix the failing auth tests",
        "evidence": {"text": "pytest: 1 failed", **ev},
        "est_minutes": 5,
        "task_kind": task_kind,
        "title": "Fix failing auth test",
        "subject": "jarvis_new",
        "outcome": "all tests in tests/test_auth.py pass",
        "success_check": "pytest exits 0",
        "self_feasibility": 0.95,
        "needs": {"tools": [], "data": [], "credentials": [], "user_decisions": []},
        "blockers": [],
    }
    base.update(kw)
    return Opportunity(**base)


def web_opp(**kw):
    ev = {"tab_id": "T1", "first_selector": "#postcode", **kw.pop("evidence", {})}
    return opp(
        kw.pop("task_kind", "web_form"),
        summary="finish the council bin form",
        title="Finish council bin form",
        subject="council.example",
        outcome=kw.pop("outcome", "the form is submitted"),
        success_check=kw.pop("success_check", 'page shows "Thank you"'),
        evidence=ev,
        **kw,
    )


def pay_action(**kw):
    base = {
        "kind": "payment",
        "site": "scan.example",
        "merchant": "Scan",
        "amount": "94.99",
        "currency": "GBP",
        "items": ["2 TB SSD"],
        "payment_method": "saved Visa 4242",
    }
    base.update(kw)
    return Consequential(**base)


def run(o, probes, ask=None, threshold=0.75):
    return gate.evaluate_sync(
        o,
        probes=probes,
        ask_model=ask or auto_verifier(),
        threshold=threshold,
        now=1_000_000.0,
    )


# ------------------------------------------------------------- passing paths
def test_code_fix_passes_and_builds_plan(tmp_path):
    p = FakeProbes(tmp_path)
    res = run(opp(), p)
    assert res.passed, res.reasons
    plan = res.plan
    assert isinstance(plan, TakeoverPlan)
    assert plan.kind == "code" and plan.task_kind == "test_fix"
    assert plan.targets["repo"] == str(p.repo)
    assert plan.targets["test_command"] == p.cmd
    assert plan.time_budget_s == 450.0
    assert plan.steps and plan.steps[0].tool == "claude_code"
    assert any("exits 0" in c for c in plan.success_criteria)
    assert plan.dry_run["failures"] == 1
    assert p.test_runs == 1
    assert res.log_tag() == "pass"


def test_web_form_submit_passes_with_inferred_submit(tmp_path):
    res = run(web_opp(), FakeProbes(tmp_path))
    assert res.passed, res.reasons
    assert res.plan.kind == "web"
    assert res.plan.targets["tab_id"] == "T1"
    assert res.plan.targets["url"] == "https://council.example/form"  # query stripped
    assert [a.kind for a in res.plan.actions] == ["submit"]
    assert res.plan.actions[0].site == "council.example"


def test_payment_fully_determined_passes(tmp_path):
    res = run(
        web_opp(outcome="checkout completes", actions=[pay_action()]),
        FakeProbes(tmp_path),
    )
    assert res.passed, res.reasons
    act = res.plan.actions[0]
    assert (act.merchant, act.amount, act.currency) == ("Scan", "94.99", "GBP")


def test_file_organise_writes_plan_json(tmp_path):
    src = tmp_path / "dl"
    src.mkdir()
    a = src / "a.pdf"
    a.write_text("x")
    dest = tmp_path / "docs"
    dest.mkdir()
    o = opp(
        "file_organise",
        outcome="pdfs moved into docs",
        success_check="files exist in docs",
        evidence={"paths": [str(a)], "dest": str(dest)},
    )
    res = run(o, FakeProbes(tmp_path))
    assert res.passed, res.reasons
    plan_json = Path(res.plan.dry_run["plan_json"])
    ops = json.loads(plan_json.read_text())["ops"]
    assert ops[0] == {"op": "mkdir", "path": str(dest)}
    assert ops[1] == {"op": "move", "src": str(a), "dst": str(dest / "a.pdf")}
    assert res.plan.targets["ops"] == ops  # P3 executes targets["ops"]
    assert res.plan.targets["expect"][str(dest / "a.pdf")] == "exists"


def test_doc_draft_passes(tmp_path):
    o = opp(
        "doc_draft",
        outcome="a draft report saved as a file",
        success_check="file exists",
    )
    res = run(o, FakeProbes(tmp_path))
    assert res.passed, res.reasons
    assert res.plan.kind == "other"


def test_download_passes(tmp_path):
    dest = tmp_path / "Downloads"
    dest.mkdir()
    o = opp(
        "download",
        outcome="the iso is downloaded",
        success_check="file exists",
        evidence={"url": "https://x.example/f.iso", "dest": str(dest)},
    )
    res = run(o, FakeProbes(tmp_path))
    assert res.passed, res.reasons


# ---------------------------------------------------- adversarial: must FAIL
@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("self_feasibility", 0.5, "low_feasibility"),
        ("needs", {"credentials": ["github token"]}, "needs_credentials"),
        ("needs", {"user_decisions": ["which colour"]}, "needs_user_decision"),
        ("blockers", ["site is down"], "blockers"),
        ("est_minutes", 45, "too_long"),
        ("est_minutes", 0, "no_estimate"),
        ("task_kind", "gui_click", "unknown_kind"),
    ],
)
def test_stage1_rejects(tmp_path, field, value, reason):
    res = run(opp(**{field: value}), FakeProbes(tmp_path))
    assert not res.passed
    assert res.stage_failed == Stage.SELF_CHECK.value
    assert any(r.startswith(reason) for r in res.reasons), res.reasons


@pytest.mark.parametrize(
    "outcome,tag",
    [
        ("log in to the portal and submit the form", "login"),
        ("sign in, then submit", "login"),
        ("enter the 2FA code and submit", "2fa"),
        ("approve the 3-D Secure check and pay", "3ds"),
        ("type the one-time code sent by SMS", "otp"),
        ("confirm in your bank app", "bank_app"),
        ("solve the captcha then submit", "captcha"),
        ("Sir needs to choose the delivery slot then submit", "user_decision"),
        ("enter your card details and pay", "card_entry"),
    ],
)
def test_human_steps_fail_gate(tmp_path, outcome, tag):
    res = run(web_opp(outcome=outcome), FakeProbes(tmp_path))
    assert not res.passed
    assert f"needs_user:{tag}" in res.reasons


@pytest.mark.parametrize(
    "outcome,tag",
    [
        ("change your password on the account page", "account_security"),
        ("export the saved passwords to a file", "secret_export"),
        ("install the extension and enable it", "extension_install"),
        ("download the installer and run it", "download_execute"),
    ],
)
def test_forbidden_actions_fail_gate(tmp_path, outcome, tag):
    res = run(web_opp(outcome=outcome), FakeProbes(tmp_path))
    assert not res.passed
    assert any(r.startswith("forbidden") for r in res.reasons), res.reasons


def test_code_title_about_login_is_not_a_login_wall(tmp_path):
    o = opp(
        title="Fix test_login_redirect", evidence={"text": "FAILED test_login_redirect"}
    )
    assert run(o, FakeProbes(tmp_path)).passed


# --------------------------------------------- stage 2: one row false at a time
def _stage2(res, prefix):
    assert not res.passed
    assert res.stage_failed == Stage.CHECKLIST.value, res.reasons
    assert any(r.startswith(prefix) for r in res.reasons), res.reasons


@pytest.mark.parametrize("flag", ["guest", "private", "locked", "in_call"])
def test_context_row(tmp_path, flag):
    p = FakeProbes(tmp_path)
    p.ctx[flag] = True
    _stage2(run(opp(), p), f"context:{flag}")


def test_context_unknown_fails_closed(tmp_path):
    p = FakeProbes(tmp_path)
    p.ctx = {}
    _stage2(run(opp(), p), "context:guest")


def test_sidecar_context_flags_override(tmp_path):
    _stage2(
        run(opp(evidence={"context": {"in_call": True}}), FakeProbes(tmp_path)),
        "context:in_call",
    )


@pytest.mark.parametrize(
    "mutate,prefix",
    [
        (lambda p: p.tools.clear(), "executor:no_claude"),
        (lambda p: setattr(p, "git", False), "target:not_git"),
        (lambda p: setattr(p, "busy", True), "contention:runner_lock"),
        (lambda p: setattr(p, "running", True), "contention:takeover_running"),
        (
            lambda p: setattr(p, "dirty", {"tests/test_auth.py"}),
            "contention:uncommitted",
        ),
        (lambda p: setattr(p, "dirty", None), "contention:git_status_failed"),
        (lambda p: setattr(p, "cmd", None), "success:no_test_command"),
        (
            lambda p: (setattr(p, "test_rc", 0), setattr(p, "test_out", "14 passed")),
            "success:test_not_failing",
        ),
        (lambda p: setattr(p, "test_rc", -1), "success:test_run_timeout"),
    ],
)
def test_code_rows(tmp_path, mutate, prefix):
    p = FakeProbes(tmp_path)
    mutate(p)
    o = opp(evidence={"files": ["tests/test_auth.py"]})
    _stage2(run(o, p), prefix)


def test_code_repo_unresolved(tmp_path):
    _stage2(run(opp(subject=""), FakeProbes(tmp_path)), "target:repo_unresolved")


def test_code_push_is_refused(tmp_path):
    _stage2(
        run(opp(outcome="fix it and git push to main"), FakeProbes(tmp_path)),
        "consequential:remote_push",
    )


def test_code_failing_test_must_fail_on_rerun(tmp_path):
    p = FakeProbes(tmp_path)
    p.test_rc, p.test_out = 0, "14 passed in 1s"
    res = run(opp(), p)
    _stage2(res, "success:test_not_failing")
    assert p.test_runs == 1


@pytest.mark.parametrize(
    "mutate,prefix",
    [
        (lambda p: setattr(p, "cdp", False), "executor:no_cdp"),
        (lambda p: setattr(p, "worker", False), "executor:no_tab_worker"),
        (lambda p: setattr(p, "tab_list", []), "target:tab_unresolved"),
        (lambda p: setattr(p, "page_info", None), "auth:page_unreadable"),
        (
            lambda p: setattr(
                p, "page_info", {"title": "x", "text": "y", "login": True}
            ),
            "auth:login_form",
        ),
        (
            lambda p: setattr(
                p, "page_info", {"title": "Sign in", "text": "Sign in to continue"}
            ),
            "auth:login",
        ),
        (
            lambda p: setattr(
                p, "page_info", {"title": "x", "text": "Please verify you are human"}
            ),
            "auth:bot-check",
        ),
        (
            lambda p: setattr(
                p, "page_info", {"title": "Pay", "text": "Enter the one-time code"}
            ),
            "needs_user:otp",
        ),
        (
            lambda p: setattr(
                p, "page_info", {"title": "Pay", "text": "3D Secure verification"}
            ),
            "needs_user:3ds",
        ),
        (lambda p: setattr(p, "control", False), "contention:control_lease"),
    ],
)
def test_web_rows(tmp_path, mutate, prefix):
    p = FakeProbes(tmp_path)
    mutate(p)
    _stage2(run(web_opp(), p), prefix)


def test_web_success_must_be_machine_verifiable(tmp_path):
    _stage2(
        run(web_opp(success_check="it looks done"), FakeProbes(tmp_path)),
        "success:not_machine_verifiable",
    )


def test_web_undeclared_payment_refused(tmp_path):
    _stage2(
        run(web_opp(outcome="buy the SSD"), FakeProbes(tmp_path)),
        "consequential:undeclared",
    )


def test_web_lookup_may_not_pay(tmp_path):
    o = web_opp(
        task_kind="web_lookup", outcome="find the price", actions=[pay_action()]
    )
    _stage2(run(o, FakeProbes(tmp_path)), "action_not_allowed:payment")


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"merchant": ""}, "payment_merchant_unknown"),
        ({"amount": ""}, "payment_amount_unknown"),
        ({"amount": "about 90"}, "payment_amount_unknown"),
        ({"amount": "0"}, "payment_amount_unknown"),
        ({"currency": ""}, "payment_currency_unknown"),
        ({"currency": "pounds"}, "payment_currency_unknown"),
        ({"items": []}, "payment_items_unknown"),
        ({"payment_method": ""}, "payment_method_unknown"),
    ],
)
def test_payment_must_be_fully_determined(tmp_path, change, reason):
    o = web_opp(outcome="checkout completes", actions=[pay_action(**change)])
    _stage2(run(o, FakeProbes(tmp_path)), reason)


def test_payment_method_must_be_saved(tmp_path):
    p = FakeProbes(tmp_path)
    p.saved = False
    _stage2(
        run(web_opp(outcome="checkout", actions=[pay_action()]), p),
        "payment_method_not_saved",
    )


def test_spend_cap(tmp_path):
    p = FakeProbes(tmp_path)
    p.cap = (50, "GBP")
    _stage2(
        run(web_opp(outcome="checkout", actions=[pay_action()]), p), "over_spend_cap"
    )
    p.cap = (100, "GBP")
    assert run(web_opp(outcome="checkout", actions=[pay_action()]), p).passed
    p.cap = (100, "USD")
    _stage2(
        run(web_opp(outcome="checkout", actions=[pay_action()]), p),
        "spend_cap_currency_mismatch",
    )


def test_send_needs_recipient_and_content(tmp_path):
    o = opp(
        "doc_draft",
        outcome="draft and send the summary",
        actions=[Consequential(kind="send", recipient="", content="x")],
    )
    _stage2(run(o, FakeProbes(tmp_path)), "send_undetermined")


def test_web_too_many_steps(tmp_path):
    _stage2(run(web_opp(evidence={"est_steps": 80}), FakeProbes(tmp_path)), "unbounded")


@pytest.mark.parametrize(
    "setup,prefix",
    [
        ("missing", "target:missing"),
        ("secret", "target:secret_path"),
        ("relative", "target:not_absolute"),
        ("none", "target:no_paths"),
        ("delete", "consequential:delete"),
    ],
)
def test_file_rows(tmp_path, setup, prefix):
    good = tmp_path / "a.txt"
    good.write_text("x")
    paths = {
        "missing": [str(tmp_path / "nope.txt")],
        "secret": [str(tmp_path / ".ssh" / "id_rsa")],
        "relative": ["a.txt"],
        "none": [],
        "delete": [str(good)],
    }[setup]
    if setup == "secret":
        (tmp_path / ".ssh").mkdir()
        Path(paths[0]).write_text("k")
    outcome = "delete the old files" if setup == "delete" else "files tidied"
    o = opp(
        "file_organise",
        outcome=outcome,
        success_check="files exist",
        evidence={"paths": paths, "dest": str(tmp_path)},
    )
    _stage2(run(o, FakeProbes(tmp_path)), prefix)


def test_download_rows(tmp_path):
    dest = tmp_path / "Downloads"
    dest.mkdir()
    o = opp(
        "download",
        outcome="downloaded",
        success_check="file exists",
        evidence={"url": "https://x.example/f.iso", "dest": str(dest)},
    )
    p = FakeProbes(tmp_path)
    p.head = {"ok": True, "size": 10, "login": True}
    _stage2(run(o, p), "auth:login_redirect")
    p.head = {"ok": True, "size": 3 * tp.GB, "login": False}
    _stage2(run(o, p), "unbounded:too_large")
    p.head = {"ok": True, "size": 1000, "login": False}
    p.free = 1500
    _stage2(run(o, p), "executor:disk_full")
    p.head = None
    _stage2(run(o, p), "executor:url_unresolvable")


def test_other_needs_chatgpt(tmp_path):
    p = FakeProbes(tmp_path)
    p.chatgpt = False
    _stage2(
        run(opp("web_research", outcome="a research note file"), p),
        "executor:no_chatgpt",
    )


def test_probe_exception_fails_closed(tmp_path):
    p = FakeProbes(tmp_path)

    def boom(*a):
        raise RuntimeError("x")

    p.is_git = boom
    _stage2(run(opp(), p), "probe_error")


# ----------------------------------------------------------- stage 3 verifier
def _stage3(res, prefix):
    assert not res.passed
    assert res.stage_failed == Stage.VERIFIER.value, res.reasons
    assert any(r.startswith(prefix) for r in res.reasons), res.reasons


@pytest.mark.parametrize(
    "human",
    [
        ["Sir must log in to the council site"],
        ["needs 2FA code"],
        ["Sir must choose a delivery date"],
    ],
)
def test_verifier_human_steps_fail(tmp_path, human):
    _stage3(
        run(web_opp(), FakeProbes(tmp_path), auto_verifier(human=human)), "human_step"
    )


def test_verifier_infeasible_fails(tmp_path):
    _stage3(
        run(opp(), FakeProbes(tmp_path), auto_verifier(feasible=False)),
        "verifier_infeasible",
    )


def test_verifier_unknown_tool_fails(tmp_path):
    ask = verifier(plan=[{"step": "click around", "tool": "computer_use"}])
    _stage3(run(opp(), FakeProbes(tmp_path), ask), "tool_not_in_inventory")


def test_verifier_plan_step_needing_login_fails(tmp_path):
    ask = auto_verifier(
        plan=[{"step": "sign in with Sir's account", "tool": "browser"}]
    )
    _stage3(run(web_opp(), FakeProbes(tmp_path), ask), "plan_needs_user:login")


def test_verifier_bad_json_and_error_fail_closed(tmp_path):
    _stage3(run(opp(), FakeProbes(tmp_path), lambda p: "not json"), "verifier_bad_json")

    def boom(p):
        raise RuntimeError("429 quota")

    _stage3(run(opp(), FakeProbes(tmp_path), boom), "verifier_error")


def test_verifier_empty_plan_fails(tmp_path):
    _stage3(run(opp(), FakeProbes(tmp_path), verifier(plan=[])), "verifier_no_plan")


def test_verifier_prompt_is_adversarial_and_carries_inventory(tmp_path):
    ask = verifier(plan=[{"step": "x", "tool": "claude_code"}])
    run(opp(), FakeProbes(tmp_path), ask)
    prompt = ask.calls[0]
    assert "needs Sir" in prompt and "3-D Secure" in prompt
    assert "claude_code" in prompt and "untrusted" in prompt


# --------------------------------------------------------------- stage 4 dry
def _stage4(res, prefix):
    assert not res.passed
    assert res.stage_failed == Stage.DRY_RUN.value, res.reasons
    assert any(r.startswith(prefix) for r in res.reasons), res.reasons


def test_web_dry_run(tmp_path):
    p = FakeProbes(tmp_path)
    p.dom = {"selector_ok": False, "predicate": False}
    _stage4(run(web_opp(), p), "dry_run:first_step_target_missing")
    p.dom = {"selector_ok": True, "predicate": True}
    _stage4(run(web_opp(), p), "dry_run:already_done")
    p.dom = None
    _stage4(run(web_opp(), p), "dry_run:inspect_failed")


def test_file_dry_run_collision(tmp_path):
    src = tmp_path / "dl"
    src.mkdir()
    a = src / "a.pdf"
    a.write_text("x")
    dest = tmp_path / "docs"
    dest.mkdir()
    (dest / "a.pdf").write_text("already")
    o = opp(
        "file_organise",
        outcome="moved",
        success_check="files exist",
        evidence={"paths": [str(a)], "dest": str(dest)},
    )
    _stage4(run(o, FakeProbes(tmp_path)), "dry_run:dest_exists")


# ------------------------------------------------------- learning + plumbing
def test_threshold_learning():
    assert feasibility.threshold_from([], "test_fix") == 0.85
    yes = [{"kind": "test_fix", "answer": "yes"}] * 8
    no = [{"kind": "test_fix", "answer": "no"}] * 8
    assert feasibility.threshold_from(yes, "test_fix") < 0.80
    assert feasibility.threshold_from(no, "test_fix") > 0.90
    assert feasibility.threshold_from([], "test_fix", stuck=2) == 0.95
    shadow = [{"kind": "test_fix", "answer": "no", "shadow": True}] * 8
    assert feasibility.threshold_from(shadow, "test_fix") == 0.85


def test_record_stuck_raises_threshold(tmp_path):
    before = feasibility.threshold("web_form", history=[])
    feasibility.record_stuck("web_form", {"opp": "x"})
    assert feasibility.threshold("web_form", history=[]) == pytest.approx(before + 0.05)
    assert feasibility.misses_path().read_text().count("\n") == 1


def test_learned_threshold_is_used_when_not_given(tmp_path):
    p = FakeProbes(tmp_path)
    o = opp(self_feasibility=0.8)  # default learned threshold is 0.85
    res = gate.evaluate_sync(o, probes=p, ask_model=auto_verifier())
    assert res.stage_failed == "self_check"


def test_reads_fields_from_p1_evidence_dict(tmp_path):
    """P1 passes the whole vision dict in evidence; contract fields only."""
    vision = {
        "vision_kind": "test_fix",
        "kind": "test_fix",
        "title": "Fix auth",
        "evidence": "pytest: 1 failed",
        "outcome": "tests pass",
        "needs": {"credentials": [], "user_decisions": []},
        "success_check": "pytest exits 0",
        "est_minutes": 5,
        "self_feasibility": 0.95,
        "blockers": [],
        "subject": "jarvis_new",
    }
    o = Opportunity(
        id="x", kind="code", summary="Fix auth", evidence=vision, est_minutes=5
    )
    res = run(o, FakeProbes(tmp_path))
    assert res.passed, res.reasons


def test_log_tag():
    res = gate._fail(Stage.CHECKLIST, ["auth:login"], opp())
    assert res.log_tag() == "fail:checklist:auth:login"


async def test_async_evaluate_contract(tmp_path):
    gate.configure(probes=FakeProbes(tmp_path), ask_model=auto_verifier())
    try:
        res = await gate.evaluate(opp(self_feasibility=0.99))
    finally:
        gate._DEFAULTS.update(probes=None, ask_model=None)
    assert res.passed, res.reasons


def test_predicate_compile():
    assert tp.compile_predicate('page shows "Thank you"') == {"text": "Thank you"}
    assert tp.compile_predicate("url contains /done")["url_contains"] == "/done"
    assert tp.compile_predicate("looks finished") is None
    assert tp.compile_predicate("x", {"selector": ".ok"}) == {"selector": ".ok"}


def test_gemini_ask_uses_model_chain_with_fake_post(monkeypatch):
    import focus

    monkeypatch.setattr(focus, "google_key", lambda: "k")
    monkeypatch.setattr(focus, "gemini_models", lambda: ["m1", "m2"])
    seen = []

    def post(url, body, key):
        seen.append(url)
        if "m1" in url:
            raise RuntimeError("500 outage")
        req = json.loads(body)
        assert req["generationConfig"]["responseMimeType"] == "application/json"
        return {
            "candidates": [{"content": {"parts": [{"text": '{"feasible": false}'}]}}]
        }

    assert json.loads(gate.gemini_ask("p", post=post)) == {"feasible": False}
    assert len(seen) == 2

    def quota(url, body, key):
        seen.append(url)
        raise RuntimeError("429 quota")

    seen.clear()
    with pytest.raises(RuntimeError):
        gate.gemini_ask("p", post=quota)
    assert len(seen) == 1  # quota stops the chain


def test_real_probes_path_problem_uses_p3_denylist(tmp_path):
    from aware import fileedit

    p = gate.Probes()
    p.file_policy = fileedit.Policy(
        home=tmp_path, jarvis_home=tmp_path / "j", roots=(tmp_path,)
    )
    ok = tmp_path / "a.txt"
    ok.write_text("x")
    assert p.path_problem(str(ok)) is None
    (tmp_path / ".ssh").mkdir()
    key = tmp_path / ".ssh" / "id_rsa"
    key.write_text("k")
    assert p.path_problem(str(key)) == "secret_path"
    assert p.path_problem("/etc/passwd") == "denied"
    assert p.path_problem(str(tmp_path / "missing")) == "missing"


def test_default_verifier_prefers_chatgpt_without_tools(monkeypatch):
    import chatgpt_cli

    calls = []
    verdict = '{"feasible": false, "plan": [], "human_steps": [], "risks": []}'

    def reply(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return verdict, None

    monkeypatch.setattr(chatgpt_cli, "chatgpt_reply", reply)
    monkeypatch.setattr(
        gate, "gemini_ask", lambda p: pytest.fail("unexpected fallback")
    )
    assert gate.verifier_ask("check this") == verdict
    assert calls[0][0] == "check this"
    assert calls[0][1]["model"] == chatgpt_cli.FAST_MODEL
    assert calls[0][1]["reasoning"] == "low"
    assert calls[0][1]["tools"] == ""


@pytest.mark.parametrize("text,warning", [("", "unavailable"), ("not JSON", None)])
def test_default_verifier_falls_back_to_gemini(monkeypatch, text, warning):
    import chatgpt_cli

    monkeypatch.setattr(chatgpt_cli, "chatgpt_reply", lambda *a, **k: (text, warning))
    monkeypatch.setattr(gate, "gemini_ask", lambda p: "fallback:" + p)
    assert gate.verifier_ask("check") == "fallback:check"


def test_web_verifier_builds_executable_steps_and_keeps_final_action(tmp_path):
    from aware.brave_live import parse_steps

    ask = verifier(
        plan=[
            {
                "step": "Fill postcode",
                "tool": "type",
                "target": "css=#postcode",
                "text": "AB1 2CD",
            },
            {
                "step": "Submit form",
                "tool": "click",
                "target": "Submit",
                "action": 0,
                "final": True,
            },
        ]
    )
    res = run(web_opp(), FakeProbes(tmp_path), ask)
    assert res.passed, res.reasons
    steps = parse_steps(TakeoverPlan.from_dict(res.plan.to_dict()))
    assert [(s.op, s.target, s.text, s.action, s.final) for s in steps] == [
        ("type", "css=#postcode", "AB1 2CD", None, True),
        ("click", "Submit", "", 0, True),
    ]


@pytest.mark.parametrize(
    "plan,reason",
    [
        ([{"step": "Finish the form", "tool": "browser"}], "tool_not_in_inventory"),
        ([{"step": "Read DOM", "tool": "cdp_read"}], "tool_not_in_inventory"),
        (
            [{"step": "document.body.click()", "tool": "evaluate"}],
            "tool_not_in_inventory",
        ),
        (
            [{"step": "Submit", "tool": "click", "action": True}],
            "web_plan:action_index",
        ),
        ([{"step": "Submit", "tool": "click", "action": "0"}], "web_plan:action_index"),
        ([{"step": "Submit", "tool": "click", "action": 1}], "web_plan:action_index"),
        (
            [{"step": "Submit", "tool": "click", "action": 0, "final": "false"}],
            "web_plan:final",
        ),
        ([{"step": "Submit", "tool": "click"}], "web_plan:missing_final_action"),
        (
            [{"step": "Submit", "tool": "click", "action": 0, "final": False}],
            "web_plan:missing_final_action",
        ),
        ([{"step": "Submit", "tool": "type", "action": 0}], "web_plan:action_op"),
        (
            [{"step": "https://example.com", "tool": "goto"}],
            "web_plan:navigate_user_tab",
        ),
        (
            [{"step": "Submit", "tool": "click", "action": 0}, "unexpected prose"],
            "web_plan:step",
        ),
        (
            [{"step": "Submit", "tool": "click", "action": 0}] * 13,
            "web_plan:too_many_steps",
        ),
    ],
)
def test_web_plan_rejects_non_executable_or_unguarded_steps(tmp_path, plan, reason):
    _stage3(run(web_opp(), FakeProbes(tmp_path), verifier(plan=plan)), reason)


def test_web_plan_never_truncates_typed_values(tmp_path):
    content = "x" * 350
    ask = verifier(
        plan=[
            {"step": "Fill note", "tool": "type", "target": "Note", "text": content},
            {"step": "Submit", "tool": "click", "action": 0},
        ]
    )
    res = run(web_opp(), FakeProbes(tmp_path), ask)
    assert res.passed, res.reasons
    assert res.plan.targets["web_steps"][0]["text"] == content


def test_web_dry_run_checks_actual_first_step(tmp_path):
    class RecordingProbes(FakeProbes):
        def dom_check(self, tab, selector, predicate):
            self.checked = selector
            return super().dom_check(tab, selector, predicate)

    p = RecordingProbes(tmp_path)
    ask = verifier(
        plan=[{"step": "Submit", "tool": "click", "target": "css=#actual", "action": 0}]
    )
    res = run(web_opp(evidence={"first_selector": "#unrelated"}), p, ask)
    assert res.passed, res.reasons
    assert p.checked == "#actual"


@pytest.mark.parametrize("tool", ["web.click", "browser.click", "brave.click"])
def test_web_plan_normalizes_prefixed_ops(tmp_path, tool):
    ask = verifier(plan=[{"step": "Submit", "tool": tool, "action": 0}])
    res = run(web_opp(), FakeProbes(tmp_path), ask)
    assert res.passed, res.reasons
    assert res.plan.targets["web_steps"][0]["op"] == "click"
    assert res.plan.steps[0].tool == "click"


def test_web_plan_preserves_intermediate_and_each_final_action(tmp_path):
    from aware.brave_live import parse_steps

    actions = [Consequential(kind="submit", site="council.example") for _ in range(2)]
    ask = verifier(
        plan=[
            {"step": "Review", "tool": "click", "action": 1, "final": False},
            {"step": "Submit second", "tool": "click", "action": 1},
            {
                "step": "Submit first",
                "tool": "press",
                "target": "css=#first",
                "text": "Enter",
                "action": 0,
            },
        ]
    )
    res = run(web_opp(actions=actions), FakeProbes(tmp_path), ask)
    assert res.passed, res.reasons
    assert [(s.action, s.final) for s in parse_steps(res.plan)] == [
        (1, False),
        (1, True),
        (0, True),
    ]


def test_web_plan_rejects_duplicate_final_action(tmp_path):
    ask = verifier(plan=[{"step": "Submit", "tool": "click", "action": 0}] * 2)
    _stage3(run(web_opp(), FakeProbes(tmp_path), ask), "web_plan:missing_final_action")


def test_web_dry_run_refuses_ambiguous_first_target(tmp_path):
    p = FakeProbes(tmp_path)
    p.dom = {"selector_ok": False, "predicate": False}
    ask = verifier(plan=[{"step": "Submit", "tool": "click", "action": 0}])
    _stage4(run(web_opp(), p, ask), "dry_run:first_step_target_missing")
