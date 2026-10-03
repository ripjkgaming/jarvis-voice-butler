"""Voice tools for the HUD panels (src/hud_panels.py)."""

from __future__ import annotations

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local


def _guard() -> None:
    try:
        require_local()
    except LocalSystemError as exc:
        raise ToolError(str(exc)) from exc


class PanelTools:
    @property
    def tools(self) -> list:
        return [
            self.show_panel,
            self.hide_panel,
            self.open_insights,
            self.close_insights,
        ]

    @function_tool()
    async def show_panel(self, context: RunContext, panel: str) -> dict[str, str]:
        """Open a HUD panel: "show mail", "show my calendar", "show tasks",
        "show drafts", "show suggestions", "show exams", "show system stats",
        or "show everything".

        Args:
            panel: calendar, mail, tasks, drafts, suggestions, exams, system, or all.
        """
        _guard()
        import hud_panels

        name = (
            "all"
            if (panel or "").strip().lower() in ("all", "everything")
            else hud_panels.normalize(panel)
        )
        if name is None:
            raise ToolError(f"Panels: {', '.join(hud_panels.PANELS)}.")
        hud_panels.show(name)
        log_action("hud", f"show {name}")
        return {"say": "All panels up." if name == "all" else f"{name.title()} is up."}

    @function_tool()
    async def hide_panel(
        self, context: RunContext, panel: str = "all"
    ) -> dict[str, str]:
        """Close a HUD panel ("hide calendar") or all of them ("clear the HUD").

        Args:
            panel: A panel name, or all.
        """
        _guard()
        import hud_panels

        name = (
            "all"
            if (panel or "all").strip().lower() in ("all", "everything")
            else hud_panels.normalize(panel)
        )
        if name is None:
            raise ToolError(f"Panels: {', '.join(hud_panels.PANELS)}.")
        hud_panels.hide(name)
        log_action("hud", f"hide {name}")
        return {
            "say": "Panels cleared." if name == "all" else f"{name.title()} hidden."
        }

    @function_tool()
    async def open_insights(
        self, context: RunContext, view: str = "usage"
    ) -> dict[str, str]:
        """Open the full Insights screen on the HUD. Use for "check AI usage",
        "open my AI usage", "show token spend" (view=usage) and "open paper
        trading", "show the paper market", "how's my portfolio" (view=market).

        Args:
            view: usage or market.
        """
        _guard()
        import hud_panels

        v = (view or "usage").strip().lower()
        tab = (
            "market"
            if any(w in v for w in ("market", "trad", "portfolio"))
            else "usage"
        )
        hud_panels.set_insights(tab)
        log_action("hud", f"insights {tab}")
        return {"say": "Paper market is up." if tab == "market" else "AI usage is up."}

    @function_tool()
    async def close_insights(self, context: RunContext) -> dict[str, str]:
        """Close the Insights screen ("close AI usage", "close paper trading",
        "close insights")."""
        _guard()
        import hud_panels

        hud_panels.set_insights(None)
        log_action("hud", "insights closed")
        return {"say": "Closed."}
