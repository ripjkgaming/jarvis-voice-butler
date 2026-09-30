"""Voice tools for Sir's windows (IRONMAN_SPEC §3).

Read-only (list, which is active) and reversible (focus, arrange, undo)
actions are free and logged; nothing here closes, types or deletes.
Closing stays with window_action behind the system handoff.
"""

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


def split_names(windows: str) -> list[str]:
    """'firefox and code, spotify' -> ['firefox', 'code', 'spotify']. Pure."""
    raw = (windows or "").replace(" and ", ",").replace("&", ",")
    return [w.strip() for w in raw.split(",") if w.strip()][:9]


class WindowTools:
    @property
    def tools(self) -> list:
        return [
            self.list_windows,
            self.focus_window,
            self.arrange_windows,
            self.undo_layout,
        ]

    @function_tool()
    async def list_windows(self, context: RunContext) -> dict[str, str]:
        """Which windows are open (and which is active): "what's open"."""
        _guard()
        import active_window

        wins = active_window.windows()
        if not wins:
            return {"say": "I can't see the window list right now, Sir."}
        names = [
            f"{w['title'][:40]}{' (active)' if w.get('active') else ''}"
            for w in wins[:12]
        ]
        return {"say": f"{len(wins)} open: " + "; ".join(names)}

    @function_tool()
    async def focus_window(self, context: RunContext, title: str) -> dict[str, str]:
        """Bring a window to the front by part of its title or app name.

        Args:
            title: e.g. "spotify", "chemistry notes".
        """
        _guard()
        from system import kwin_windows

        if not (title or "").strip():
            raise ToolError("Which window, Sir?")
        hit = await asyncio.to_thread(kwin_windows.act, "focus", title.strip())
        if hit is None:
            raise ToolError(f"No window matching {title.strip()[:60]}.")
        log_action("window", f"focus {hit[0][:80]}")
        return {"say": f"{hit[0][:70]} is in front."}

    @function_tool()
    async def arrange_windows(
        self, context: RunContext, layout: str, windows: str = ""
    ) -> dict[str, str]:
        """Arrange windows: "put spotify on the left", "firefox and code side
        by side", "tile everything", "make this fullscreen". Undo with
        undo_layout.

        Args:
            layout: left, right, split (side by side), grid, thirds, fullscreen.
            windows: Window names, comma separated; empty = active window (left/
                right/fullscreen/split) or every window (grid/thirds).
        """
        _guard()
        from system import window_layout

        layout = (layout or "").strip().lower().replace("side by side", "split")
        if layout not in window_layout.LAYOUTS:
            raise ToolError(f"Layouts: {', '.join(window_layout.LAYOUTS)}.")
        names = split_names(windows)
        ok = await asyncio.to_thread(window_layout.arrange, layout, names)
        if not ok:
            raise ToolError("KWin didn't take the layout, Sir.")
        log_action("window", f"arrange {layout} {','.join(names)[:80]}")
        which = " and ".join(names) if names else "the windows"
        return {"say": f"Done: {which}, {layout}. Say undo layout to put them back."}

    @function_tool()
    async def undo_layout(self, context: RunContext) -> dict[str, str]:
        """Put windows back where they were before the last arrange."""
        _guard()
        from system import window_layout

        snap = await asyncio.to_thread(window_layout.undo)
        if snap is None:
            return {"say": "There's no layout to undo, Sir."}
        log_action("window", "undo layout")
        return {"say": f"Restored {len(snap.get('windows') or [])} windows."}
