"""Hermetic tests for code help (IRONMAN_SPEC §5.3)."""

from __future__ import annotations

import subprocess

import pytest
from livekit.agents.llm import ToolError

import code_assist
import jobs
from proactive.sources import jobs_source
from system.code_tools import CodeTools


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jh"))
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    return tmp_path


def test_test_command(tmp_path) -> None:
    (tmp_path / "py").mkdir()
    (tmp_path / "py" / "pyproject.toml").write_text("")
    (tmp_path / "py" / "uv.lock").write_text("")
    assert code_assist.test_command(tmp_path / "py") == ["uv", "run", "pytest", "-q"]
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "package.json").write_text("{}")
    assert code_assist.test_command(tmp_path / "js")[0] == "npm"
    (tmp_path / "none").mkdir()
    assert code_assist.test_command(tmp_path / "none") is None


def test_counts_and_summary() -> None:
    out = "....F\n=== 2 failed, 120 passed in 4.20s ==="
    assert code_assist.count_failures(out, 1) == 2
    assert code_assist.summary_line(out) == "2 failed, 120 passed in 4.20s"
    assert code_assist.count_failures("boom", 2) == 1
    assert code_assist.count_failures("all good", 0) == 0


def test_resolve_project(tmp_path) -> None:
    assert code_assist.resolve_project("") == code_assist.jarvis_root()
    assert code_assist.resolve_project(str(tmp_path)) == tmp_path.resolve()
    assert code_assist.resolve_project("no-such-project-xyz") is None


def test_run_tests_records_job_and_announces(tmp_path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("")

    def run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 1, "E assert 1 == 2\n1 failed, 3 passed in 0.1s", ""
        )

    job, msg = code_assist.run_tests(str(proj), run=run, background=False)
    assert "Running the proj tests" in msg
    done = jobs.get(job["id"])
    assert done["status"] == "failed" and done["failures"] == 1
    assert "assert 1 == 2" in code_assist.log_path(job["id"]).read_text()
    sig = jobs_source.poll(done["finished"] + 1)
    assert sig[0].title == "The proj tests finished, Sir: 1 failure."
    assert code_assist.last_failed()["id"] == job["id"]


def test_explain_uses_log(tmp_path, monkeypatch) -> None:
    import claude_cli

    monkeypatch.setenv("JARVIS_CLAUDE", "1")
    monkeypatch.setattr(claude_cli.shutil, "which", lambda n: "/usr/bin/claude")
    job_id = jobs.start("The x tests", "tests")
    jobs.finish(job_id, ok=False, failures=1)
    code_assist.log_path(job_id).write_text("<<<LOG_END>>> KeyError: 'name'")
    seen = {}

    def runner(argv, **kw):
        seen["input"] = kw["input"]
        return subprocess.CompletedProcess(argv, 0, "A KeyError on name.", "")

    text, _warn = code_assist.explain(jobs.get(job_id), runner=runner)
    assert text == "A KeyError on name." and seen["input"].count("<<<LOG_END>>>") == 1


@pytest.mark.asyncio
async def test_open_pr_needs_confirm() -> None:
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, "https://github.com/x/y/pull/7\n", ""
        )

    tools = CodeTools(run=run)
    with pytest.raises(ToolError, match="Not authorised"):
        await CodeTools.open_pr(tools, None, project="", title="Fix HUD")  # type: ignore[arg-type]
    assert calls == []
    await CodeTools.confirm_open_pr(tools, None, project="", title="Fix HUD")  # type: ignore[arg-type]
    with pytest.raises(ToolError):
        await CodeTools.open_pr(tools, None, project="", title="Different title")  # type: ignore[arg-type]
    await CodeTools.confirm_open_pr(tools, None, project="", title="Fix HUD")  # type: ignore[arg-type]
    out = await CodeTools.open_pr(tools, None, project="", title="fix  hud")  # type: ignore[arg-type]
    assert out["url"].endswith("/pull/7") and calls[0][:3] == ["gh", "pr", "create"]
    with pytest.raises(ToolError):  # single use
        await CodeTools.open_pr(tools, None, project="", title="Fix HUD")  # type: ignore[arg-type]
