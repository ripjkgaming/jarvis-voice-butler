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

import asyncio
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

from jarvis_rt import RunContext, ToolError, function_tool
from system import LocalSystemError, log_action, require_local, run_cmd
from system.sysmon_tools import SysmonTools

DESK_SNAP = Path("/tmp/jarvis-desk.jpg")


def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def read_system_snapshot() -> dict[str, float | int | None]:
    """Load, memory, swap, battery and temperature from /proc and /sys."""
    snap: dict[str, float | int | None] = {}
    load = _read("/proc/loadavg").split()
    snap["load1"] = float(load[0]) if len(load) >= 3 else None
    snap["load5"] = float(load[1]) if len(load) >= 3 else None
    snap["load15"] = float(load[2]) if len(load) >= 3 else None
    snap["cores"] = os.cpu_count() or 1
    mem: dict[str, int] = {}
    for ln in _read("/proc/meminfo").splitlines():
        k, _, v = ln.partition(":")
        if v.split():
            mem[k] = int(v.split()[0])  # kB
    total = mem.get("MemTotal", 0)
    snap["mem_total_gb"] = total / 1048576
    snap["mem_used_gb"] = (total - mem.get("MemAvailable", 0)) / 1048576
    swap = mem.get("SwapTotal", 0)
    snap["swap_total_gb"] = swap / 1048576
    snap["swap_used_gb"] = (swap - mem.get("SwapFree", 0)) / 1048576
    bat = next(iter(sorted(Path("/sys/class/power_supply").glob("BAT*"))), None)
    cap = _read(f"{bat}/capacity") if bat else ""
    snap["battery"] = int(cap) if cap.isdigit() else None
    snap["charging"] = (
        1 if bat and _read(f"{bat}/status") in ("Charging", "Full") else 0
    )
    temps = [
        int(t) / 1000
        for f in Path("/sys/class/thermal").glob("thermal_zone*/temp")
        if (t := _read(str(f))).lstrip("-").isdigit()
    ]
    snap["temp_c"] = max(temps) if temps else None
    return snap


def parse_ps(out: str, top: int = 3) -> list[tuple[str, float, float]]:
    """(name, cpu%, mem%) for the busiest processes, merged by name."""
    merged: dict[str, list[float]] = {}
    for ln in out.splitlines()[1:]:
        parts = ln.split()
        if len(parts) < 3:
            continue
        try:
            cpu, mem = float(parts[-2]), float(parts[-1])
        except ValueError:
            continue
        name = " ".join(parts[:-2])
        agg = merged.setdefault(name, [0.0, 0.0])
        agg[0] += cpu
        agg[1] += mem
    ranked = sorted(merged.items(), key=lambda kv: -kv[1][0])[:top]
    return [(n, v[0], v[1]) for n, v in ranked]


def parse_df(out: str) -> str:
    lines = out.strip().splitlines()
    if len(lines) < 2:
        return ""
    f = lines[-1].split()
    return f"{f[2]} of {f[1]} used ({f[4]})" if len(f) >= 5 else ""


async def _ai_usage_today() -> str:
    """Today's AI token total and estimated spend from the local tracker."""
    try:
        import insights_proxy

        status, raw = await asyncio.to_thread(insights_proxy.usage, "range=today")
        if status != 200:
            return ""
        data = json.loads(raw)
        summ = data.get("summary") or {}
        info = data.get("source_info") or {}
        tokens = int(summ.get("total_tokens") or 0)
        cost = float(summ.get("estimated_cost_usd") or 0)
        stale = " (tracker data is stale)" if info.get("status") == "stale" else ""
        if tokens == 0:
            return f"No AI token usage recorded today{stale}."
        return f"AI today: {tokens:,} tokens, about ${cost:.2f}{stale}."
    except Exception:
        return ""


def format_usage(
    *,
    uptime: str,
    snap: dict,
    procs: list[tuple[str, float, float]],
    disk: str,
    ai: str,
) -> str:
    """One spoken-friendly paragraph. Pure, testable."""
    parts = []
    if snap.get("load1") is not None:
        cores = snap.get("cores") or 1
        pct = round(100 * snap["load1"] / cores)
        parts.append(
            f"CPU load {snap['load1']:.1f}, {snap['load5']:.1f}, {snap['load15']:.1f} "
            f"on {cores} cores, about {pct} percent busy"
        )
    if snap.get("mem_total_gb"):
        used, total = snap["mem_used_gb"], snap["mem_total_gb"]
        parts.append(
            f"memory {used:.1f} of {total:.1f} GB used, {round(100 * used / total)} percent"
        )
    if snap.get("swap_total_gb"):
        parts.append(
            f"swap {snap['swap_used_gb']:.1f} of {snap['swap_total_gb']:.1f} GB"
        )
    if disk:
        parts.append(f"disk {disk}")
    if snap.get("battery") is not None:
        state = "charging" if snap.get("charging") else "on battery"
        parts.append(f"battery {snap['battery']} percent, {state}")
    if snap.get("temp_c") is not None:
        parts.append(f"hottest sensor {snap['temp_c']:.0f} degrees")
    if uptime:
        parts.append(uptime)
    say = parts[0][0].upper() + parts[0][1:] if parts else "Stats unavailable"
    say += "".join(f"; {p}" for p in parts[1:]) + "."
    if procs:
        top = ", ".join(f"{n} {c:.0f}% CPU {m:.0f}% memory" for n, c, m in procs)
        say += f" Busiest: {top}."
    if ai:
        say += f" {ai}"
    return say


def cluster_journal(lines: list[str], top: int = 5) -> list[tuple[str, int, str]]:
    """Group journal lines by unit/process. Pure, testable."""
    groups: dict[str, list[str]] = {}
    for ln in lines:
        m = re.search(r"(\w[\w\-\.@]+?)(?:\[\d+\])?:\s", ln)
        key = m.group(1) if m else ln[:40]
        groups.setdefault(key, []).append(ln)
    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:top]
    return [(k, len(v), v[0][:300]) for k, v in ranked]


class BackgroundTools(SysmonTools):
    """Health + background tools. Register via .tools on the SystemAgent.

    Inherits the focused monitors (cpu_stats ... debug_system) from SysmonTools.
    """

    @property
    def tools(self) -> list:
        return [
            self.usage_stats,
            self.crash_report,
            self.webcam_snapshot,
            self.idle_status,
            self.cpu_stats,
            self.memory_stats,
            self.gpu_stats,
            self.temps_stats,
            self.disk_stats,
            self.network_stats,
            self.power_stats,
            self.process_stats,
            self.full_report,
            self.debug_system,
        ]

    @function_tool()
    async def usage_stats(self, context: RunContext) -> dict[str, str]:
        """Detailed laptop usage: CPU load per core, memory, swap, disk, battery,
        temperature, top processes by CPU and memory, plus today's AI token usage
        and spend. Use for "usage stats", "system stats", "how is the laptop doing"."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        _rc, up, _ = await run_cmd("uptime", "-p", timeout=5.0)
        _rc, ps, _ = await run_cmd(
            "ps", "-eo", "comm,pcpu,pmem", "--sort=-pcpu", timeout=10.0
        )
        _rc, df, _ = await run_cmd("df", "-h", "/", timeout=5.0)
        snap = read_system_snapshot()
        say = format_usage(
            uptime=(up or "").strip(),
            snap=snap,
            procs=parse_ps(ps or ""),
            disk=parse_df(df or ""),
            ai=await _ai_usage_today(),
        )
        log_action("usage", say[:200])
        return {"say": say[:1200]}

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
