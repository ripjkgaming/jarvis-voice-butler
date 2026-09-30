"""Voice tools for code work (IRONMAN_SPEC §5.3, src/code_assist.py).

open_pr is outward-facing, so it needs Sir's explicit yes first:
confirm_open_pr arms exactly one PR (project + title), open_pr uses it once.
"""

from __future__ import annotations

import asyncio
import re
import subprocess

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local


def _guard() -> None:
    try:
        require_local()
    except LocalSystemError as exc:
        raise ToolError(str(exc)) from exc


def _pr_key(project: str, title: str) -> str:
    title_key = re.sub(r"\s+", " ", title.strip().casefold())
    return f"{project.strip().lower()}|{title_key}"


class CodeTools:
    def __init__(self, run=subprocess.run) -> None:
        self._run = run
        self._armed_pr: str | None = None

    @property
    def tools(self) -> list:
        return [
            self.run_tests,
            self.explain_failure,
            self.confirm_open_pr,
            self.open_pr,
        ]

    @function_tool()
    async def run_tests(self, context: RunContext, project: str = "") -> dict[str, str]:
        """Run a project's tests in the background ("run the tests", "test the
        jarvis repo"). Jarvis announces the result when it finishes.

        Args:
            project: Project folder name or path; empty = the Jarvis repo.
        """
        _guard()
        import code_assist

        job, message = await asyncio.to_thread(
            code_assist.run_tests, project, self._run
        )
        if job is None:
            raise ToolError(message)
        log_action("code", f"tests started {job['id']}")
        return {"say": message}

    @function_tool()
    async def explain_failure(self, context: RunContext) -> dict[str, str]:
        """Explain why the last test run failed ("why did the tests fail")."""
        _guard()
        import code_assist

        job = code_assist.last_failed()
        if job is None:
            return {"say": "No failed test run to explain, Sir."}
        text, warning = await asyncio.to_thread(code_assist.explain, job)
        if not text:
            raise ToolError(f"I couldn't read the failure: {warning}.")
        return {"say": text}

    @function_tool()
    async def confirm_open_pr(
        self, context: RunContext, project: str, title: str
    ) -> dict[str, str]:
        """Authorise ONE pull request, only after Sir said yes to its project
        and title read back to him.

        Args:
            project: Project folder name or path ("" = Jarvis repo).
            title: The PR title Sir approved.
        """
        _guard()
        if not (title or "").strip():
            raise ToolError("A pull request needs a title.")
        self._armed_pr = _pr_key(project, title)
        log_action("code", "pr armed")
        return {"say": f"Authorised: pull request '{title.strip()[:80]}'."}

    @function_tool()
    async def open_pr(
        self, context: RunContext, project: str, title: str
    ) -> dict[str, str]:
        """Open a GitHub pull request for the project's current branch with
        gh. Needs confirm_open_pr with the same project and title first.

        Args:
            project: Project folder name or path ("" = Jarvis repo).
            title: PR title (must match the confirmed one).
        """
        _guard()
        import code_assist

        if self._armed_pr != _pr_key(project, title) or not title.strip():
            raise ToolError(
                "Not authorised. Read the project and title back to Sir and ask "
                "yes or no; if yes call confirm_open_pr, then open_pr again."
            )
        self._armed_pr = None
        root = code_assist.resolve_project(project)
        if root is None:
            raise ToolError(f"I can't find a project called {project}.")
        try:
            proc = await asyncio.to_thread(
                self._run,
                ["gh", "pr", "create", "--fill", "--title", title.strip()[:200]],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ToolError(f"gh failed: {exc}") from exc
        if proc.returncode != 0:
            raise ToolError(
                f"GitHub said no: {(proc.stderr or proc.stdout).strip()[-200:]}"
            )
        url = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        log_action("code", f"pr opened {url[0][:120]}")
        return {
            "say": f"Pull request opened, Sir: {title.strip()[:80]}.",
            "url": url[0],
        }
