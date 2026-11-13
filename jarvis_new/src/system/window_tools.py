"""Voice tools for Sir's windows (IRONMAN_SPEC §3).

Read-only (list, which is active) and reversible (focus, arrange, undo)
actions are free and logged; nothing here closes, types or deletes.
Closing stays with window_action behind the system handoff.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from jarvis_rt import EndCallTool, RunContext, ToolError, function_tool
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
            self.maximize_window,
            self.restore_window,
            self.bring_to_attention,
            self.move_window_to_monitor,
            self.arrange_windows,
            self.undo_layout,
            self.hide_jarvis,
        ]

    @function_tool()
    async def hide_jarvis(self, context: RunContext) -> str:
        """Fade Jarvis into the desktop wallpaper: "Jarvis hide" or "enter hidden mode"."""
        _guard()
        repo = Path(__file__).resolve().parents[2]
        candidates = [
            [
                str(repo / "shell" / "src-tauri" / "target" / "debug" / "jarvis-shell"),
                "hide",
            ],
            ["jarvis-shell", "hide"],
            ["jarvis", "hide"],
        ]
        for argv in candidates:
            try:
                result = await asyncio.to_thread(
                    subprocess.run, argv, timeout=5, capture_output=True, check=False
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode == 0:
                log_action("hud", "requested animated hide")
                instruction = "Say a brief goodbye as Jarvis fades into the wallpaper, then end the call."
            else:
                continue
            break
        else:
            log_action("hud", "hide animation unavailable; ending call anyway")
            instruction = "Briefly tell Sir you couldn't reach the desktop display, then end the call."

        # Reuse the normal end-call lifecycle so the goodbye can finish before
        # the active voice session disconnects.
        return await EndCallTool(end_instructions=instruction)._end_call(context)

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
        """Switch to a window: pull it up in front of everything else.

        Use for "switch to spotify", "go to my browser", "bring up the
        chemistry notes", "pull up Dolphin". It reports honestly when no
        such window exists or it did not come forward.

        Args:
            title: Part of the window title or app name, e.g. "spotify".
        """
        _guard()
        from system import window_ctl

        ok, say = await asyncio.to_thread(window_ctl.focus, title)
        log_action("window", f"focus {title[:60]} ok={ok}")
        if not ok:
            raise ToolError(say)
        return {"say": say}

    @function_tool()
    async def maximize_window(
        self, context: RunContext, title: str, fullscreen: bool = False
    ) -> dict[str, str]:
        """Bring a window to the front and maximise it, or make it full screen.

        Use for "maximise the terminal", "make Spotify full screen", "make
        Brave fill the screen", "maximise Dolphin". It reports honestly when
        no such window exists or KWin does not confirm the new size.

        Args:
            title: Part of the window title or app name, e.g. "terminal".
            fullscreen: True for full screen (no title bar), False to maximise.
        """
        _guard()
        from system import window_ctl

        ok, say = await asyncio.to_thread(window_ctl.maximize, title, bool(fullscreen))
        log_action(
            "window",
            f"maximize {title[:60]} fullscreen={bool(fullscreen)} ok={ok}",
        )
        if not ok:
            raise ToolError(say)
        return {"say": say}

    @function_tool()
    async def restore_window(self, context: RunContext, title: str) -> dict[str, str]:
        """Take a maximised or full-screen window back to normal size.

        Use for "restore Spotify", "un-maximise the terminal", "make Brave
        normal size again", "leave full screen".

        Args:
            title: Part of the window title or app name, e.g. "spotify".
        """
        _guard()
        from system import window_ctl

        ok, say = await asyncio.to_thread(window_ctl.restore_window, title)
        log_action("window", f"restore {title[:60]} ok={ok}")
        if not ok:
            raise ToolError(say)
        return {"say": say}

    @function_tool()
    async def bring_to_attention(
        self, context: RunContext, title: str, maximize: bool = False
    ) -> dict[str, str]:
        """Bring a window that is behind others to Sir's attention, in front of
        everything. Use for "bring Brave to the front", "bring the window to my
        attention", "show me the browser", "bring up the chat", "put Spotify in
        front". With maximize true it also maximises the window ("bring Brave to
        the front and maximise it").

        Args:
            title: Part of the window title or app name, e.g. "browser", "brave".
            maximize: True to also maximise the window once it is in front.
        """
        _guard()
        from system import window_ctl

        if maximize:
            ok, say = await asyncio.to_thread(window_ctl.maximize, title, False)
        else:
            ok, say = await asyncio.to_thread(window_ctl.attention, title)
        log_action(
            "window", f"attention {title[:60]} maximize={bool(maximize)} ok={ok}"
        )
        if not ok:
            raise ToolError(say)
        return {"say": say}

    @function_tool()
    async def move_window_to_monitor(
        self, context: RunContext, title: str, monitor: str = "other"
    ) -> dict[str, str]:
        """Move an already-open window to another screen; do not relaunch it.

        Use for "move ChatGPT to my other monitor", "put Spotify on my
        laptop screen", "move Firefox to the right display".

        Args:
            title: Existing application/window name, such as ChatGPT.
            monitor: "monitor 1" (main/where Claude runs), "monitor 2" (laptop
                screen), "monitor 3" (final monitor), other, free (emptiest),
                this, left, right, or an output name like DP-1.
        """
        _guard()
        from system.monitor_window_move import move_window

        ok, say = await asyncio.to_thread(move_window, title, monitor)
        log_action("window", f"move {title[:60]} to {monitor[:35]} ok={ok}")
        if not ok:
            raise ToolError(say)
        return {"say": say}

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
