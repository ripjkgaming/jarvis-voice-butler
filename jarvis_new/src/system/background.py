"""Background + health tools: usage stats, crash digest, webcam, idle.

Ported from proven laptop Jarvis logic:
- activity.py / sysinfo.py: uptime, load, memory, top processes
- crash_digest.py: journalctl err clustering (here offline/terse, no
  cloud model call — deterministic summary from clusters)
- camera.py: rearguard /tmp/jarvis-desk.jpg snapshot, cv2 direct fallback
- presence/idle: loginctl + xprintidle when available

Every tool refuses unless JARVIS_LOCAL=1.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from pathlib import Path

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local, run_cmd

DESK_SNAP = Path("/tmp/jarvis-desk.jpg")


def cluster_journal(lines: list[str], top: int = 5) -> list[tuple[str, int, str]]:
    """Group journal lines by unit/process. Pure, testable."""
    groups: dict[str, list[str]] = {}
    for ln in lines:
        m = re.search(r"(\w[\w\-\.@]+?)(?:\[\d+\])?:\s", ln)
        key = m.group(1) if m else ln[:40]
        groups.setdefault(key, []).append(ln)
    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:top]
    return [(k, len(v), v[0][:300]) for k, v in ranked]


class BackgroundTools:
    """Health + background tools. Register via .tools on the SystemAgent."""

    @property
    def tools(self) -> list:
        return [
            self.usage_stats,
            self.crash_report,
            self.webcam_snapshot,
            self.idle_status,
        ]

    @function_tool()
    async def usage_stats(self, context: RunContext) -> dict[str, str]:
        """Laptop load: uptime, CPU load, memory, top processes."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        _rc, up, _ = await run_cmd("uptime", timeout=5.0)
        _rc, mem, _ = await run_cmd("free", "-h", timeout=5.0)
        mem_line = ""
        for ln in (mem or "").splitlines():
            if ln.startswith("Mem:"):
                mem_line = " ".join(ln.split())
                break
        _rc, ps, _ = await run_cmd(
            "ps", "-eo", "comm,pcpu", "--sort=-pcpu", timeout=10.0
        )
        procs = [ln.strip().split()[0] for ln in (ps or "").splitlines()[1:4]]
        say = f"{(up or '').strip()[:200]} Memory: {mem_line or '?'}."
        if procs:
            say += f" Busiest: {', '.join(procs)}."
        log_action("usage", say[:200])
        return {"say": say[:500]}

    @function_tool()
    async def crash_report(
        self, context: RunContext, since: str = "1 hour ago"
    ) -> dict[str, str]:
        """Recent system errors from the journal, clustered by unit.

        Args:
            since: journalctl --since value ("1 hour ago", "today").
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        if not re.fullmatch(r"[\w\s:\-\.]+", since or ""):
            raise ToolError("Odd time range. Try '1 hour ago' or 'today'.")
        _rc, out, _ = await run_cmd(
            "journalctl",
            "--since",
            since,
            "-p",
            "err..alert",
            "-o",
            "short",
            "--no-pager",
            timeout=20.0,
        )
        lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()][-400:]
        if not lines:
            return {"say": f"No errors in the journal ({since})."}
        clusters = cluster_journal(lines)
        blob = "; ".join(f"{name} times {count}" for name, count, _ in clusters)
        counts = Counter(name for name, _, _ in clusters)
        worst = counts.most_common(1)[0][0] if counts else "unknown"
        say = f"{len(lines)} errors since {since}. Worst: {worst}. Breakdown: {blob}."
        log_action("crash", f"{len(lines)} errors, worst {worst}")
        return {"say": say[:800], "lines": str(len(lines))}

    @function_tool()
    async def webcam_snapshot(self, context: RunContext) -> dict[str, str]:
        """Desk camera snapshot status (rearguard feed or direct open)."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        try:
            if DESK_SNAP.exists():
                age = time.time() - DESK_SNAP.stat().st_mtime
                if age <= 5.0:
                    log_action("camera", f"fresh {age:.1f}s")
                    return {
                        "say": f"Camera feed is live, snapshot {age:.0f} seconds old.",
                        "path": str(DESK_SNAP),
                    }
                return {
                    "say": f"Camera feed is stale ({age:.0f} seconds). Rearguard may be down."
                }
        except Exception:
            pass
        try:
            import contextlib

            import cv2  # type: ignore[import]

            cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
            try:
                if cap.isOpened():
                    ok, _frame = cap.read()
                    if ok:
                        log_action("camera", "direct open ok")
                        return {"say": "Camera works with a direct open."}
            finally:
                with contextlib.suppress(Exception):
                    cap.release()
        except ImportError:
            pass
        except Exception:
            pass
        return {"say": "No camera available right now."}

    @function_tool()
    async def idle_status(self, context: RunContext) -> dict[str, str]:
        """Is the laptop idle? Session + idle-time report."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        bits = []
        _rc, out, _ = await run_cmd(
            "loginctl",
            "show-session",
            "self",
            "-p",
            "IdleHint",
            "-p",
            "IdleSinceHint",
            timeout=5.0,
        )
        if "IdleHint=yes" in (out or ""):
            bits.append("session idle")
        elif "IdleHint=no" in (out or ""):
            bits.append("session active")
        _rc, out2, _ = await run_cmd("xprintidle", timeout=5.0)
        if _rc == 0 and (out2 or "").strip().isdigit():
            ms = int(out2.strip())
            bits.append(
                f"no input for {ms // 60000}m"
                if ms >= 60000
                else f"no input for {ms // 1000}s"
            )
        log_action("idle", "; ".join(bits) or "unknown")
        return {
            "say": ("Laptop idle: " + ", ".join(bits) + ".")
            if bits
            else "Idle state unknown."
        }
