"""Voice tools for multi-step background tasks (src/agent_tasks.py)."""

from __future__ import annotations

import asyncio

from jarvis_rt import RunContext, function_tool
from jarvis_rt import ToolError

from system import LocalSystemError, log_action, require_local


def _guard() -> None:
    try:
        require_local()
    except LocalSystemError as exc:
        raise ToolError(str(exc)) from exc
    import phone_session

    if phone_session.private():
        raise ToolError(
            "Detached task controls are unavailable during a private phone call."
        )
    if phone_session.guest_required():
        raise ToolError("Guest mode refuses owner task controls.")


class TaskTools:
    @property
    def tools(self) -> list:
        return [
            self.start_task,
            self.task_status,
            self.approve_task,
            self.cancel_task,
            self.redirect_task,
            self.cancel_tool,
        ]

    @function_tool()
    async def start_task(self, context: RunContext, goal: str) -> dict:
        """Start a multi-step job in the background: "research laptops under
        1000 and draft an email to dad about it". Jarvis plans it, works
        through it, and asks before anything that sends, pays, posts or books.

        Tasks can read Sir's own email (with attachments and linked docs),
        calendar and notes, search the web, and save a finished list or
        document to Downloads. Never say you cannot read his email for a task.

        Args:
            goal: The whole job in Sir's words, keeping every name, place,
                date and constraint he gave (e.g. "Chiang Mai climbing trip",
                not "my itinerary") and where to save the result if he said.
        """
        _guard()
        import agent_tasks

        if len((goal or "").strip()) < 8:
            raise ToolError("What should the task do, Sir?")
        from cancellation_adapters import available_adapter

        adapter = available_adapter()
        options = (
            {"on_created": adapter.register} if adapter.registry is not None else {}
        )
        task = await asyncio.to_thread(agent_tasks.start, goal, **options)
        log_action("task", f"start {task['id']}")
        result = {
            "say": "On it, Sir. I'll work through that in the background and report back.",
            "task_id": task["id"],
        }
        row = adapter.sync(task)
        if row:
            result["job_id"] = row["id"]
        return result

    @function_tool()
    async def task_status(self, context: RunContext) -> dict:
        """How the background tasks are going, and their results when done."""
        _guard()
        import agent_tasks
        from cancellation_adapters import available_adapter

        rows = agent_tasks.all_tasks()[-3:]
        if not rows:
            return {"say": "No background tasks, Sir."}
        adapter = available_adapter()
        receipts = [adapter.receipt(t) for t in reversed(rows)]
        return {
            "say": " | ".join(agent_tasks.describe(t) for t in reversed(rows))[:1500],
            "tasks": receipts,
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
    async def cancel_task(self, context: RunContext, task_id: str = "") -> dict:
        """Request stopping one background task ("cancel that task").

        Speech interruption only yields the conversation. It never means an
        external effect was undone. Use state/settled in this tool's receipt.

        Args:
            task_id: Native task ID or explicitly mapped agent-task job ID.
                Empty selects only one unambiguous unfinished task.
        """
        _guard()
        import agent_tasks
        from cancellation_adapters import available_adapter
        from cancellation_control import resolve_task

        adapter = available_adapter()
        if not task_id:
            task = resolve_task(agent_tasks.all_tasks(), "")
            if task is None:
                return {
                    "say": "Name one unfinished task ID to cancel; no task was changed.",
                    "accepted": False,
                    "settled": False,
                }
            task_id = task["id"]
        receipt = adapter.cancel(task_id)
        if receipt is None:
            return {
                "say": "No eligible task with that ID; no task was changed.",
                "accepted": False,
                "settled": False,
            }
        log_action(
            "task", f"cancel request {receipt['task_id']} state={receipt['state']}"
        )
        return receipt

    @function_tool()
    async def cancel_tool(self, context: RunContext, operation_id: str) -> dict:
        """Cancel one explicitly identified owned computer-use operation.

        Other tool invocations, external build PIDs and job-monitor IDs are
        unsupported here. Current input may finish; do not claim rollback.
        """
        _guard()
        from cancellation_adapters import TaskCancellationAdapter
        from system.computer_use import _SERVICE

        result = TaskCancellationAdapter().cancel_tool(operation_id, _SERVICE)
        return result or {
            "say": "No supported owned operation with that ID; no work was changed.",
            "accepted": False,
            "settled": False,
        }

    @function_tool()
    async def redirect_task(self, context: RunContext, task_id: str, goal: str) -> dict:
        """Explicitly replace one task with a new goal after old work stops.

        Requires task ID and replacement goal; earlier effects stay committed.
        If still stopping, wait for status and ask for the replacement again.
        Never forward the old task's approvals to the new task.
        """
        _guard()
        import action_guard
        import agent_tasks
        from cancellation_adapters import available_adapter

        input_id = action_guard.request_id(context)
        if not input_id:
            raise ToolError("A fresh user-turn identity is required to replace a task.")
        if len((goal or "").strip()) < 8:
            raise ToolError("Give the explicit replacement goal before stopping work.")
        adapter = available_adapter()
        native = adapter.native_id(task_id)
        if not native:
            raise ToolError("No eligible task with that ID; no task was changed.")
        receipt = adapter.cancel(task_id)
        if receipt is None or receipt["state"] not in {"cancelled", "cancelling"}:
            raise ToolError("Only an unfinished or cancelled task can be replaced.")
        if not receipt["settled"]:
            return {
                **receipt,
                "replacement_started": False,
                "say": "Stopping the old preparation first. Request the replacement again after it stops.",
            }
        try:
            replacement = await asyncio.to_thread(
                agent_tasks.redirect,
                native,
                goal,
                input_id,
                on_created=adapter.register if adapter.registry is not None else None,
            )
        except (ValueError, OSError) as exc:
            raise ToolError(str(exc)) from exc
        row = adapter.sync(replacement)
        return {
            "say": "Replacement task prepared with fresh approvals; earlier effects have not been undone.",
            "task_id": replacement["id"],
            "supersedes": native,
            "job_id": row["id"] if row else None,
            "replacement_started": True,
        }


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
