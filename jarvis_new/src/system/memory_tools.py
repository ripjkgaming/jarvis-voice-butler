"""Voice tools for long-term memory (IRONMAN_SPEC §2, src/memory.py)."""

from __future__ import annotations

import datetime as dt

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local


def _guard() -> None:
    try:
        require_local()
    except LocalSystemError as exc:
        raise ToolError(str(exc)) from exc


def parse_day(when: str, today: dt.date) -> dt.date | None:
    """'yesterday' / 'today' / 'monday' / '2026-09-29' -> date. Pure-ish."""
    w = " ".join((when or "yesterday").lower().split())
    if w in ("yesterday", "last night"):
        return today - dt.timedelta(days=1)
    if w in ("today", "this morning", "earlier", "earlier today"):
        return today
    names = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    w2 = w.removeprefix("last ").removeprefix("on ")
    if w2 in names:
        back = (today.weekday() - names.index(w2)) % 7 or 7
        return today - dt.timedelta(days=back)
    try:
        return dt.date.fromisoformat(w[:10])
    except ValueError:
        return None


class MemoryTools:
    @property
    def tools(self) -> list:
        return [
            self.recall_memory,
            self.what_was_i_doing,
            self.pick_up_where_left_off,
            self.remember_that,
            self.forget_that,
        ]

    @function_tool()
    async def recall_memory(self, context: RunContext, query: str) -> dict[str, str]:
        """Search Jarvis's long-term memory (past sessions, threads, things
        Sir asked to remember, and his files) for a topic: "what did we
        decide about the HUD", "when did I last work on physics".

        Args:
            query: Topic words.
        """
        _guard()
        import memory

        hits = memory.recall(query)
        if not hits:
            return {"say": f"Nothing in my memory about {query[:60]}, Sir."}
        return {"say": " | ".join(f"{h['source']}: {h['text']}" for h in hits)[:1500]}

    @function_tool()
    async def what_was_i_doing(
        self, context: RunContext, when: str = "yesterday"
    ) -> dict[str, str]:
        """What Sir did on a day: "what was I doing yesterday", "what did we
        do on Monday".

        Args:
            when: yesterday, today, a weekday, or YYYY-MM-DD.
        """
        _guard()
        import memory

        day = parse_day(when, dt.date.today())
        if day is None:
            raise ToolError("Which day, Sir? Yesterday, a weekday, or a date.")
        text = memory.day_summary(day)
        if not text:
            return {"say": f"I have no notes for {day.strftime('%A %d %B')}, Sir."}
        return {"say": text[:1500]}

    @function_tool()
    async def pick_up_where_left_off(self, context: RunContext) -> dict[str, str]:
        """ "Pick up where I left off" / "where was I": the last session and the
        open threads with their next steps."""
        _guard()
        import memory

        last = memory.last_time()
        threads = memory.open_threads(4)
        if not last and not threads:
            return {"say": "I have no record of where we left off yet, Sir."}
        extra = ""
        if threads:
            extra = " Next steps: " + "; ".join(
                f"{name}: {step}" if step else name for name, step in threads
            )
        return {"say": (last.replace("\n", " ") + extra)[:1500]}

    @function_tool()
    async def remember_that(self, context: RunContext, fact: str) -> dict[str, str]:
        """Save something Sir explicitly asks to remember ("remember that I
        prefer metric", "remember my locker is 214"). Only on his request.

        Args:
            fact: The thing to remember, in a short sentence.
        """
        _guard()
        import memory

        if not memory.remember(fact):
            raise ToolError("I could not save that, Sir.")
        log_action("memory", "remembered a preference")
        return {"say": "Noted, Sir. I'll remember that."}

    @function_tool()
    async def forget_that(self, context: RunContext, what: str) -> dict[str, str]:
        """Forget a remembered thing or a thread ("forget my locker number").
        It is archived out of memory, not destroyed.

        Args:
            what: Words identifying what to forget.
        """
        _guard()
        import memory

        moved = memory.forget(what)
        if not moved:
            return {"say": f"I had nothing about {what[:60]} to forget, Sir."}
        log_action("memory", f"archived {len(moved)} item(s)")
        return {
            "say": f"Done. Moved {len(moved)} item{'s' if len(moved) != 1 else ''} out of my memory into the archive."
        }
