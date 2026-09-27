"""quote_action: movie and meme lines Sir says to Jarvis (src/quotes.py).

Gemini spots the line and passes Sir's words; quotes.match_quote decides
for itself, so a near-miss sentence is refused rather than acted on.
Every action reuses an existing tool (play_media, weather_now, ...) or
a read-only bridge stat; nothing here shuts down, deletes, or sends.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

import quotes as Q
from system.projects_tools import _bridge_call

END_CALL_DELAY_S = 4.0


def _said(result: object) -> str:
    """The spoken line of a tool result dict, or ""."""
    if isinstance(result, dict):
        return str(result.get("say") or "").strip()
    return ""


def vitals_line(sys: dict | None) -> str:
    """CPU temp, load and memory as one spoken line. Pure."""
    if not sys:
        return "The system monitor isn't answering."
    bits = []
    temp = sys.get("cpu_temp_c")
    if isinstance(temp, (int, float)):
        bits.append(f"core at {round(temp)} degrees" + (", running hot" if temp >= 85 else ""))
    load = sys.get("load_1_5_15") or []
    cores = sys.get("cpu_count") or 1
    with contextlib.suppress(Exception):
        bits.append(f"load {round(float(load[0]) / cores * 100)} percent of {cores} cores")
    mem = sys.get("mem_bytes") or {}
    total, avail = mem.get("MemTotal"), mem.get("MemAvailable")
    if total and avail is not None:
        bits.append(f"memory {round((total - avail) / total * 100)} percent used")
    free = sys.get("home_free_bytes")
    if isinstance(free, (int, float)):
        bits.append(f"{round(free / 1e9)} gigabytes free on home")
    return ("Vitals: " + ", ".join(bits) + ".") if bits else "No readings yet."


def phone_line(sys: dict | None) -> str:
    """Phone link + battery as one spoken line. Pure."""
    sys = sys or {}
    phone, net = sys.get("phone"), sys.get("phone_tailnet") or {}
    if phone:
        charging = ", charging" if phone.get("charging") else ""
        return f"The phone is linked at {phone.get('battery')} percent{charging}."
    if net.get("online"):
        return "The phone is on the tailnet, but its app isn't open, so no battery reading."
    return "The phone is offline, Sir."


def coding_line(projects: list[dict]) -> str:
    """What the coding engine is doing. Pure."""
    code = [p for p in projects if p.get("kind") == "code"]
    running = [p for p in code if p.get("status") == "running"]
    if running:
        names = ", ".join(p.get("title", "untitled")[:40] for p in running[:2])
        return f"{len(running)} coding job{'s' if len(running) > 1 else ''} cooking: {names}."
    if code:
        last = code[0]
        return f"Nothing cooking. The last job, {last.get('title', 'untitled')[:40]}, is {last.get('status')}."
    return "Nothing cooking, Sir. No coding jobs yet."


class QuoteTools:
    """Movie/meme quote actions. Main-agent tool."""

    def __init__(self, system=None, inbox=None, browser=None) -> None:
        self.system = system
        self.inbox = inbox
        self.browser = browser
        self._last_fired: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()

    @property
    def tools(self) -> list:
        return [self.quote_action]

    @function_tool()
    async def quote_action(self, context: RunContext, quote: str) -> dict[str, str]:
        """Sir said a movie line or meme from the quote list: run its action.

        Only when Sir's WHOLE utterance is the quote (the list is in your
        instructions). A sentence that merely contains the words is a
        normal request: never call this for it.

        Args:
            quote: Sir's exact words.
        """
        q = Q.match_quote(quote)
        if q is None:
            raise ToolError("That isn't one of the quotes. Answer Sir normally.")
        now = time.monotonic()
        last = self._last_fired.get(q.id)
        if last is not None and now - last < Q.COOLDOWN_S:
            return {"say": "Once was enough for that one, Sir.", "quote": q.id}
        self._last_fired[q.id] = now
        try:
            detail = await self._run(context, q)
        except ToolError:
            raise
        except Exception as exc:
            detail = f"Though that part failed: {str(exc)[:80]}."
        say = f"{q.reply} {detail}".strip()
        return {"say": say, "quote": q.id, "action": q.action}

    async def _sys(self) -> dict | None:
        return await asyncio.to_thread(_bridge_call, "GET", "/sys")

    async def _run(self, context: RunContext, q: Q.Quote) -> str:
        a = q.action
        if a == "reply":
            return ""
        if a == "play":
            return _said(await self.system.play_media(context, q.args["query"]))
        if a == "party":
            with contextlib.suppress(Exception):
                await self.system.set_volume(context, "set", int(q.args.get("volume", 70)))
            return _said(await self.system.play_media(context, q.args["query"]))
        if a == "briefing":
            return _said(await self.inbox.morning_briefing(context))
        if a == "weather":
            return _said(await self.inbox.weather_now(context))
        if a == "disk":
            return _said(await self.system.disk_space(context))
        if a == "open_files":
            await self.system.open_app(context, "files")
            return ""
        if a in ("diagnostic", "vitals"):
            return vitals_line(await self._sys())
        if a == "phone_status":
            return phone_line(await self._sys())
        if a == "status":
            import projects

            parts = [time.strftime("It's %-I:%M %p.")]
            with contextlib.suppress(Exception):
                parts.append(_said(await self.system.battery_status(context)))
            parts.append(phone_line(await self._sys()))
            running = [
                p for p in await asyncio.to_thread(projects.list_projects)
                if p.get("status") == "running"
            ]
            parts.append(
                f"{len(running)} project{'s' if len(running) != 1 else ''} running."
                if running
                else "No projects running."
            )
            return " ".join(p for p in parts if p)
        if a == "budget":
            from system.budget import status

            b = status()
            return (
                f"{round(b.get('pct', 0) * 100)} percent of the monthly talking "
                f"allowance is spent."
            )
        if a == "clean_slate":
            if self.browser is not None:
                with contextlib.suppress(Exception):
                    await self.browser.close_helper(context)
            await asyncio.to_thread(
                _bridge_call,
                "POST",
                "/tool",
                {"tool": "projects_ui", "args": {"commands": [{"action": "hide"}]}},
            )
            return ""
        if a == "research_projects":
            got = await asyncio.to_thread(
                _bridge_call,
                "POST",
                "/tool",
                {
                    "tool": "projects_ui",
                    "args": {
                        "commands": [
                            {"action": "show"},
                            {"action": "filter", "filter": "research"},
                        ],
                        "heard": "big brain time",
                    },
                },
            )
            return "" if (got or {}).get("ok") else "The archive didn't open, though."
        if a == "coding_status":
            import projects

            return coding_line(await asyncio.to_thread(projects.list_projects))
        if a == "search":
            if self.browser is None:
                return ""
            await self.browser.open_helper_google(context, q.args["query"])
            return "Read the helper tab with read_helper, then close_helper."
        if a == "end_call":
            session = getattr(context, "session", None)

            async def _close() -> None:
                await asyncio.sleep(END_CALL_DELAY_S)
                if session is not None:
                    with contextlib.suppress(Exception):
                        await session.aclose()

            task = asyncio.create_task(_close())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return ""
        raise ToolError(f"No handler for {a}.")
