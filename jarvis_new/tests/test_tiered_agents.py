"""Tests for the tiered worker spawner. Uses a fake worker, never a real model."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pytest

import tiered_agents as ta

HAS_TMUX = shutil.which("tmux") is not None
needs_tmux = pytest.mark.skipif(not HAS_TMUX, reason="tmux not installed")

HAIKU, SONNET, OPUS = (
    ta.TIER_MODEL["haiku"],
    ta.TIER_MODEL["sonnet"],
    ta.TIER_MODEL["opus"],
)

FAKE_WORKER = textwrap.dedent(
    """
    import json, os, sys, time
    PLAN = {plan!r}   # model -> done | stuck | hang | silent
    LOG = {log!r}
    args = sys.argv[1:]
    model = args[args.index("--model") + 1]
    effort = args[args.index("--effort") + 1]
    prompt = args[args.index("-p") + 1]
    with open(LOG, "a") as fh:
        fh.write(json.dumps({{"model": model, "effort": effort, "prompt": prompt}}) + "\\n")
    mode = PLAN.get(model, "done")
    if mode == "hang":
        while True:
            time.sleep(0.1)
    if mode == "silent":
        sys.exit(0)
    d = os.environ["JARVIS_AGENT_DIR"]
    if mode == "stuck":
        payload = {{"status": "stuck", "tried": ["try 1", "try 2"],
                    "blocker": "needs X", "handoff_brief": "HANDOFF FROM " + model}}
    else:
        payload = {{"status": "done", "summary": "fake work by " + model}}
    with open(os.path.join(d, "result.json"), "w") as fh:
        json.dump(payload, fh)
    print("fake worker", model, mode)
    """
)


@pytest.fixture
def agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated state dir, test tmux prefix and private tmux socket."""
    monkeypatch.setenv("JARVIS_AGENTS_DIR", str(tmp_path / "agents"))
    monkeypatch.setenv("JARVIS_TMUX_PREFIX", "jvtest-")
    monkeypatch.delenv("JARVIS_AGENT_NO_TMUX", raising=False)
    sock = f"jvtest-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    monkeypatch.setenv("JARVIS_TMUX_SOCKET", sock)
    cwd = tmp_path / "project"
    cwd.mkdir()
    yield {"tmp": tmp_path, "cwd": cwd, "sock": sock}
    if HAS_TMUX:
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True)
        (Path("/tmp") / f"tmux-{os.getuid()}" / sock).unlink(missing_ok=True)


def _use_fake(monkeypatch, tmp_path: Path, plan: dict[str, str]) -> Path:
    """Point the worker command at a fake that follows ``plan`` per model."""
    log = tmp_path / "fake_calls.jsonl"
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER.format(plan=plan, log=str(log)))
    monkeypatch.setenv(
        "JARVIS_AGENT_CMD", f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    )
    return log


def _calls(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def _until(predicate, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        ta.poll_once()
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError("condition not reached in time")


def _tmux_has(name: str) -> bool:
    return (
        subprocess.run(
            [
                "tmux",
                "-L",
                os.environ["JARVIS_TMUX_SOCKET"],
                "has-session",
                "-t",
                f"={name}",
            ],
            capture_output=True,
        ).returncode
        == 0
    )


def test_session_names_use_prefix_and_tier_suffix(monkeypatch):
    monkeypatch.setenv("JARVIS_TMUX_PREFIX", "jvtest-")
    assert ta.session_name("abc", "haiku") == "jvtest-abc"
    assert ta.session_name("abc", "sonnet") == "jvtest-abc-sonnet"
    assert ta.session_name("abc", "opus") == "jvtest-abc-opus"


def test_tier_models_and_efforts_are_fixed():
    assert ta.TIER_MODEL == {
        "haiku": "claude-haiku-5-5",
        "sonnet": "claude-sonnet-5-5",
        "opus": "claude-opus-5-5",
    }
    assert ta.TIER_EFFORT == {"haiku": "high", "sonnet": "medium", "opus": "medium"}
    assert ta.ESCALATES_TO == {"haiku": "sonnet", "sonnet": "opus"}


def test_prompt_carries_contract_and_is_not_reexpanded(tmp_path):
    brief = "fix <<TASK_ID>> and keep {braces}"
    prompt = ta.build_prompt(
        "t1", brief, tmp_path / "result.json", handoff="prev notes"
    )
    assert '"status":"stuck"' in prompt and '"status":"done"' in prompt
    assert "2 failed attempts" in prompt
    assert str(tmp_path / "result.json") in prompt
    assert "fix <<TASK_ID>> and keep {braces}" in prompt  # inserted text untouched
    assert "prev notes" in prompt
    assert "HANDOFF BRIEF" not in ta.build_prompt("t1", brief, tmp_path / "r.json")


def test_invalid_spawn_inputs_are_rejected(agents):
    with pytest.raises(ValueError):
        ta.spawn_task("bad id/../x", "do it", str(agents["cwd"]))
    with pytest.raises(ValueError):
        ta.spawn_task("ok", "   ", str(agents["cwd"]))
    with pytest.raises(ValueError):
        ta.spawn_task("ok", "do it", str(agents["tmp"] / "missing"))


def test_opus_and_sonnet_refused_without_lower_tier_stuck(agents, monkeypatch):
    _use_fake(monkeypatch, agents["tmp"], {})
    brief = "x"
    with pytest.raises(ta.TierGateError):
        ta._launch("gate1", "opus", brief, agents["cwd"], history=[])
    with pytest.raises(ta.TierGateError):
        ta._launch(
            "gate1",
            "opus",
            brief,
            agents["cwd"],
            history=[{"tier": "haiku", "status": "stuck"}],
        )
    with pytest.raises(ta.TierGateError):
        ta._launch("gate1", "sonnet", brief, agents["cwd"], history=[])
    assert not (agents["tmp"] / "agents" / "gate1" / "run-opus.sh").exists()


def test_escalate_is_noop_unless_result_is_stuck(agents, monkeypatch):
    log = _use_fake(monkeypatch, agents["tmp"], {HAIKU: "hang"})
    rec = ta.spawn_task("calm", "do it", str(agents["cwd"]))
    _until(lambda: len(_calls(log)) == 1)
    assert ta.escalate("calm") is None
    ta.kill("calm")
    assert _calls(log)[0]["model"] == HAIKU
    assert rec["tier"] == "haiku"


@needs_tmux
def test_spawn_runs_in_tmux_and_session_is_cleaned_up(agents, monkeypatch):
    log = _use_fake(monkeypatch, agents["tmp"], {HAIKU: "done"})
    rec = ta.spawn_task("easy", "write hello", str(agents["cwd"]))
    d = agents["tmp"] / "agents" / "easy"
    assert (d / "brief.md").read_text() == "write hello"
    assert rec["session"] == "jvtest-easy"

    _until(lambda: ta.get_record("easy")["status"] == "done")
    assert ta.get_record("easy")["summary"] == "fake work by " + HAIKU
    assert _calls(log)[0]["effort"] == "high"
    assert not _tmux_has("jvtest-easy")  # session ended with the worker
    assert "fake worker" in (d / "log.txt").read_text()
    assert (
        json.loads((agents["tmp"] / "agents" / "index.json").read_text())["easy"][
            "tier"
        ]
        == "haiku"
    )


@needs_tmux
def test_stuck_haiku_escalates_to_sonnet_and_stops_there(agents, monkeypatch):
    log = _use_fake(
        monkeypatch, agents["tmp"], {HAIKU: "stuck", SONNET: "done", OPUS: "done"}
    )
    ta.spawn_task("hard", "migrate db", str(agents["cwd"]))
    _until(lambda: ta.get_record("hard")["status"] == "done")

    calls = _calls(log)
    assert [c["model"] for c in calls] == [HAIKU, SONNET]
    assert calls[1]["effort"] == "medium"
    assert "HANDOFF FROM " + HAIKU in calls[1]["prompt"]
    rec = ta.get_record("hard")
    assert rec["tier"] == "sonnet" and rec["session"] == "jvtest-hard-sonnet"
    assert [h["tier"] for h in rec["history"]] == ["haiku"]
    assert (agents["tmp"] / "agents" / "hard" / "result-haiku.json").exists()
    assert not _tmux_has("jvtest-hard") and not _tmux_has("jvtest-hard-sonnet")


@needs_tmux
def test_full_chain_reaches_opus_only_after_sonnet_stuck(agents, monkeypatch):
    log = _use_fake(
        monkeypatch, agents["tmp"], {HAIKU: "stuck", SONNET: "stuck", OPUS: "done"}
    )
    ta.spawn_task("chain", "hard task", str(agents["cwd"]))
    _until(lambda: ta.get_record("chain")["status"] == "done")

    calls = _calls(log)
    assert [c["model"] for c in calls] == [HAIKU, SONNET, OPUS]
    assert calls[2]["effort"] == "medium"
    assert "HANDOFF FROM " + SONNET in calls[2]["prompt"]
    assert [h["tier"] for h in ta.get_record("chain")["history"]] == ["haiku", "sonnet"]


@needs_tmux
def test_opus_stuck_is_final_and_never_escalates_further(agents, monkeypatch):
    log = _use_fake(
        monkeypatch, agents["tmp"], {HAIKU: "stuck", SONNET: "stuck", OPUS: "stuck"}
    )
    ta.spawn_task("final", "impossible", str(agents["cwd"]))
    _until(
        lambda: (
            ta.get_record("final")["status"] == "stuck"
            and ta.get_record("final")["tier"] == "opus"
        )
    )
    for _ in range(3):
        ta.poll_once()
        time.sleep(0.2)
    assert [c["model"] for c in _calls(log)] == [HAIKU, SONNET, OPUS]
    assert ta.get_record("final")["status"] == "stuck"


@needs_tmux
def test_silent_exit_without_result_is_crashed_not_escalated(agents, monkeypatch):
    log = _use_fake(monkeypatch, agents["tmp"], {HAIKU: "silent"})
    ta.spawn_task("quiet", "anything", str(agents["cwd"]))
    _until(lambda: ta.get_record("quiet")["status"] == "crashed")
    ta.poll_once()
    assert [c["model"] for c in _calls(log)] == [HAIKU]


@needs_tmux
def test_kill_stops_the_session(agents, monkeypatch):
    _use_fake(monkeypatch, agents["tmp"], {HAIKU: "hang"})
    ta.spawn_task("stuck-forever", "wait", str(agents["cwd"]))
    assert _tmux_has("jvtest-stuck-forever")
    assert ta.status("stuck-forever")["alive"] is True
    assert ta.kill("stuck-forever") is True
    assert not _tmux_has("jvtest-stuck-forever")
    assert ta.get_record("stuck-forever")["status"] == "killed"
    assert ta.kill("no-such-task") is False


@needs_tmux
def test_duplicate_running_spawn_is_rejected(agents, monkeypatch):
    _use_fake(monkeypatch, agents["tmp"], {HAIKU: "hang"})
    ta.spawn_task("dup", "wait", str(agents["cwd"]))
    try:
        with pytest.raises(RuntimeError):
            ta.spawn_task("dup", "wait", str(agents["cwd"]))
    finally:
        ta.kill("dup")


@needs_tmux
def test_plan_and_spawn_only_spawns_and_validates_first(agents, monkeypatch):
    _use_fake(monkeypatch, agents["tmp"], {HAIKU: "hang"})
    tasks = [
        {"task_id": "p1", "brief": "one", "cwd": str(agents["cwd"])},
        {"task_id": "p2", "brief": "two", "cwd": str(agents["cwd"])},
    ]
    try:
        out = ta.plan_and_spawn(tasks)
        assert [r["task_id"] for r in out] == ["p1", "p2"]
        assert _tmux_has("jvtest-p1") and _tmux_has("jvtest-p2")
        assert {r["task_id"] for r in ta.list_agents()} == {"p1", "p2"}
    finally:
        for name in ("p1", "p2"):
            ta.kill(name)

    with pytest.raises(ValueError):
        ta.plan_and_spawn(
            [
                {"task_id": "ok3", "brief": "fine", "cwd": str(agents["cwd"])},
                {"task_id": "bad", "brief": "", "cwd": str(agents["cwd"])},
            ]
        )
    assert not _tmux_has("jvtest-ok3")
    with pytest.raises(ValueError):
        ta.plan_and_spawn([])


def test_fallback_without_tmux_runs_escalation_chain(agents, monkeypatch):
    monkeypatch.setenv("JARVIS_AGENT_NO_TMUX", "1")
    log = _use_fake(monkeypatch, agents["tmp"], {HAIKU: "stuck", SONNET: "done"})
    rec = ta.spawn_task("nt", "no tmux here", str(agents["cwd"]))
    assert rec["session"].startswith("proc:")
    _until(lambda: ta.get_record("nt")["status"] == "done")
    assert [c["model"] for c in _calls(log)] == [HAIKU, SONNET]


def test_list_and_status_for_unknown_task(agents):
    assert ta.list_agents() == []
    assert ta.get_record("nothing") is None
    with pytest.raises(KeyError):
        ta.status("nothing")
    with pytest.raises(KeyError):
        ta.escalate("nothing")
