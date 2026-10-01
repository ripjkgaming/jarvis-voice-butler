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
        return [self.show_panel, self.hide_panel]

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
