"""Voice tools for background projects: in-depth research (Claude Sonnet via
Sir's Pro plan) and coding (headless opencode), plus the Project Archive.

The jobs run in the bridge (src/projects.py), which outlives this voice
call; these tools only start them over the bridge's HTTP API, return at
once, and — while the call is still live — speak the result when it lands.
Research is never a web search: quick facts stay on the search tools and
"open <site>" stays on open_app.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import urllib.request

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

POLL_S = 15.0
MAX_WAIT_S = 20 * 60.0


def _bridge_call(method: str, path: str, body: dict | None = None) -> dict | None:
    """One JSON call to the local bridge. None on any failure."""
    from wizard.gate import BRIDGE_DEFAULT_PORT, bridge_host, bridge_token

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://{bridge_host()}:{BRIDGE_DEFAULT_PORT}{path}", data=data, method=method
    )
    req.add_header("Content-Type", "application/json")
    token = bridge_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def spoken_result(meta: dict) -> str:
    """What Jarvis says when a project finishes. Pure."""
    title = meta.get("title", "your project")
    if meta.get("status") == "done":
        if meta.get("kind") == "research":
            summary = meta.get("summary") or "The report is in your archive."
            return f"Research on {title} is complete, Sir. {summary}"
        return f"The coding job is done, Sir: {title}. Details are in the archive."
    if meta.get("status") == "cancelled":
        return f"The {title} project was cancelled."
    return f"I'm afraid the {title} project failed, Sir: {meta.get('error') or 'no detail'}."


class ProjectTools:
    """Research/coding projects + archive window. Main-agent tools."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    @property
    def tools(self) -> list:
        return [self.research_project, self.code_project, self.open_projects]

    def _report_when_done(self, context: RunContext, pid: str) -> None:
        session = getattr(context, "session", None)

        async def watch() -> None:
            waited = 0.0
            while waited < MAX_WAIT_S:
                await asyncio.sleep(POLL_S)
                waited += POLL_S
                got = await asyncio.to_thread(_bridge_call, "GET", f"/projects/{pid}")
                meta = (got or {}).get("project")
                if not meta or meta.get("status") == "running":
                    continue
                if session is not None:
                    with contextlib.suppress(Exception):
                        await session.say(spoken_result(meta))
                return

        task = asyncio.create_task(watch())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @function_tool()
    async def research_project(self, context: RunContext, topic: str) -> dict[str, str]:
        """Start an IN-DEPTH research project that runs in the background.

        Only for explicit research requests: "research X", "look into X in
        depth", "do a deep dive / write me a report on X", "compare X and Y
        thoroughly". NOT for quick facts or "search for / google X" (use the
        search tools) and NOT for opening a website (open_app). The report
        lands in Sir's Project Archive; you'll be told when it's done.

        Args:
            topic: The full research question, in Sir's words.
        """
        topic = " ".join((topic or "").split())
        if len(topic) < 3:
            raise ToolError("What should I research, Sir?")
        got = await asyncio.to_thread(
            _bridge_call, "POST", "/projects", {"kind": "research", "topic": topic}
        )
        meta = (got or {}).get("project")
        if not meta:
            raise ToolError("The research engine isn't reachable right now.")
        self._report_when_done(context, meta["id"])
        return {
            "say": "On it, Sir. The research is underway; I'll report back when it's done.",
            "project_id": meta["id"],
        }

    @function_tool()
    async def code_project(
        self,
        context: RunContext,
        task: str,
        directory: str = "",
        effort: str = "",
    ) -> dict[str, str]:
        """Hand a CODING task to the background coding engine (opencode).

        For "write / build / fix / refactor code", "make me a script that…".
        Runs unattended in a project folder; results land in the Project
        Archive.

        Args:
            task: The complete coding task, with every detail Sir gave.
            directory: Existing project folder if Sir named one (e.g.
                "~/code/myapp"); blank starts a fresh folder.
            effort: "high" for ordinary tasks, "max" for big or hard ones
                (refactors, debugging, multi-file work); blank = auto.
        """
        task = (task or "").strip()
        if len(task) < 3:
            raise ToolError("What should I build, Sir?")
        body = {"kind": "code", "task": task, "directory": directory.strip()}
        if effort.strip().lower() in ("high", "max"):
            body["variant"] = effort.strip().lower()
        got = await asyncio.to_thread(_bridge_call, "POST", "/projects", body)
        meta = (got or {}).get("project")
        if not meta:
            raise ToolError("The coding engine isn't reachable right now.")
        if meta.get("status") == "failed":
            raise ToolError(meta.get("error") or "The coding job could not start.")
        self._report_when_done(context, meta["id"])
        return {
            "say": "Handing that to the coding engine, Sir. I'll let you know when it's done.",
            "project_id": meta["id"],
        }

    @function_tool()
    async def open_projects(self, context: RunContext, command: str = "") -> dict[str, str]:
        """Show Sir's Project Archive window and steer it by voice.

        For "open / show my (research) projects", and for anything that
        navigates inside it ("open project two", "open the first document",
        "start scrolling", "go back"): pass Sir's words as `command`.

        Args:
            command: Sir's navigation words, verbatim (e.g. "open project
                two and open the first document"). Empty = just show it.
        """
        import projects as _projects

        cmds = [{"action": "show"}]
        if command.strip():
            parsed = await asyncio.to_thread(_projects.parse_voice, command)
            if parsed is None:
                raise ToolError(
                    "I didn't catch which project or document, Sir. "
                    "Try 'open project two' or 'open the first document'."
                )
            cmds = parsed
        got = await asyncio.to_thread(
            _bridge_call,
            "POST",
            "/tool",
            {"tool": "projects_ui", "args": {"commands": cmds, "heard": command[:200]}},
        )
        if not (got or {}).get("ok"):
            raise ToolError((got or {}).get("error") or "The project archive didn't respond.")
        return {"say": _projects.reply_for(cmds) if command.strip() else "Your projects, Sir."}
