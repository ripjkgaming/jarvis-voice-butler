"""Voice tools for multi-step background tasks (src/agent_tasks.py)."""

from __future__ import annotations

import asyncio

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local


def _guard() -> None:
    try:
        require_local()
    except LocalSystemError as exc:
        raise ToolError(str(exc)) from exc


class TaskTools:
    @property
    def tools(self) -> list:
        return [self.start_task, self.task_status, self.approve_task, self.cancel_task]

    @function_tool()
    async def start_task(self, context: RunContext, goal: str) -> dict[str, str]:
        """Start a multi-step job in the background: "research laptops under
        1000 and draft an email to dad about it". Jarvis plans it, works
        through it, and asks before anything that sends, pays, posts or books.

        Args:
            goal: The whole job in Sir's words.
        """
        _guard()
        import agent_tasks

        if len((goal or "").strip()) < 8:
            raise ToolError("What should the task do, Sir?")
        task = await asyncio.to_thread(agent_tasks.start, goal)
        log_action("task", f"start {task['id']}")
        return {
            "say": "On it, Sir. I'll work through that in the background and report back."
        }

    @function_tool()
    async def task_status(self, context: RunContext) -> dict[str, str]:
        """How the background tasks are going, and their results when done."""
        _guard()
        import agent_tasks

        rows = agent_tasks.all_tasks()[-3:]
        if not rows:
            return {"say": "No background tasks, Sir."}
        return {
            "say": " | ".join(agent_tasks.describe(t) for t in reversed(rows))[:1500]
        }

    @function_tool()
    async def approve_task(
        self, context: RunContext, task_id: str = ""
    ) -> dict[str, str]:
        """Sir said yes to the step a task is waiting on (e.g. sending the
        drafted email). The task then hands you that step: do it with your
        normal tools and their own confirmations.

        Args:
            task_id: Empty = the task waiting most recently.
        """
        _guard()
        import agent_tasks

        task = await asyncio.to_thread(agent_tasks.approve, task_id, None, False)
        if task is None:
            return {"say": "No task is waiting for approval, Sir."}
        step = next((s for s in task["steps"] if s["kind"] == "consequential"), None)
        prepared = next(
            (
                s["output"]
                for s in reversed(task["steps"])
                if s["kind"] != "consequential" and s.get("output")
            ),
            "",
        )
        log_action("task", f"approved {task['id']}")
        return {
            "say": f"Approved. Now {step['title'].lower() if step else 'finish it'} using your tools.",
            "prepared": prepared[:3000],
            "action": (step or {}).get("detail", ""),
        }

    @function_tool()
    async def cancel_task(
        self, context: RunContext, task_id: str = ""
    ) -> dict[str, str]:
        """Stop a background task ("cancel that task").

        Args:
            task_id: Empty = the most recent unfinished task.
        """
        _guard()
        import agent_tasks

        task = agent_tasks.cancel(task_id)
        if task is None:
            return {"say": "There's no running task to cancel, Sir."}
        log_action("task", f"cancelled {task['id']}")
        return {"say": f"Cancelled '{task['title']}'."}


class WorkshopTools:
    @property
    def tools(self) -> list:
        return [self.start_workshop, self.workshop_status]

    @function_tool()
    async def start_workshop(
        self, context: RunContext, goal: str, roles: str = ""
    ) -> dict[str, str]:
        """Put specialists on a goal at once ("get the team on adding a
        calendar panel", "workshop the physics revision plan"): research
        (web), code (reads the Jarvis repo) and ops (plan and risks) work in
        parallel, read-only; you speak one merged status when they finish.

        Args:
            goal: The goal in Sir's words.
            roles: Optional subset, e.g. "research, ops"; empty = all three.
        """
        _guard()
        import workshop

        if len((goal or "").strip()) < 8:
            raise ToolError("What should the workshop work on, Sir?")
        chosen = workshop.pick_roles(roles)
        workshop.start(goal.strip(), chosen)
        log_action("workshop", f"start {','.join(chosen)}")
        return {"say": f"The {', '.join(chosen)} team is on it, Sir. I'll report back."}

    @function_tool()
    async def workshop_status(self, context: RunContext) -> dict[str, str]:
        """What the last workshop found (or that it is still working)."""
        _guard()
        import workshop

        ws = workshop.latest()
        if ws is None:
            return {"say": "No workshop has run yet, Sir."}
        if ws["status"] == "running":
            return {"say": f"Still working on '{ws['goal'][:60]}', Sir."}
        return {"say": ws.get("spoken") or f"The workshop failed: {ws.get('error')}"}
