"""Voice tools for the focused system monitors (mixed into BackgroundTools)."""

from __future__ import annotations

import asyncio

from jarvis_rt import RunContext, ToolError, function_tool
from system import LocalSystemError, log_action, require_local, sysdiag
from system.sysmon import COLLECTORS, FINDERS, RENDERERS, safe_collect


def _guard() -> None:
    try:
        require_local()
    except LocalSystemError as exc:
        raise ToolError(str(exc)) from exc


async def _focused(area: str, **kw) -> dict[str, str]:
    data = await safe_collect(area, COLLECTORS[area], **kw)
    say, detail = RENDERERS[area](data, FINDERS[area](data))
    log_action(f"sysmon.{area}", say[:200])
    path = ""
    try:
        saved = await asyncio.to_thread(sysdiag.save_report, detail, prefix=area)
        path = str(saved)
    except OSError:
        pass
    return {
        "say": say,
        "detail": detail,
        "top": sysdiag.top_lines(detail),
        "path": path,
    }


class SysmonTools:
    """Focused, read-only system stat tools. Never kills or changes anything."""

    @function_tool()
    async def cpu_stats(self, context: RunContext) -> dict[str, str]:
        """CPU only: per-core use, frequency, governor, load, context switches,
        throttling and top processes by CPU. For "cpu", "processor", "how busy is
        the cpu". Speak `say`; `detail` is the complete reading incl. per-process table
        (also saved at `path`); `top` is its headline slice."""
        _guard()
        return await _focused("cpu")

    @function_tool()
    async def memory_stats(self, context: RunContext) -> dict[str, str]:
        """Memory only: used/available/cache/slab, swap and zram, pressure,
        recent OOM kills and top processes by RAM. For "ram", "memory", "swap"."""
        _guard()
        return await _focused("memory")

    @function_tool()
    async def gpu_stats(self, context: RunContext) -> dict[str, str]:
        """GPU only (NVIDIA, AMD or Intel): load, video memory, clocks, power,
        fan, temperature, throttling and processes. For "gpu", "graphics card",
        "vram". Says plainly when no GPU tool is present."""
        _guard()
        return await _focused("gpu")

    @function_tool()
    async def temps_stats(self, context: RunContext) -> dict[str, str]:
        """Every temperature sensor, fans, thresholds and throttle state. For
        "temps", "temperatures", "how hot", "fans"."""
        _guard()
        return await _focused("temps")

    @function_tool()
    async def disk_stats(self, context: RunContext) -> dict[str, str]:
        """Disks only: space and inodes per mount, live read/write rates, busy
        percent, NVMe temperature, SMART when readable. For "disk", "storage",
        "space"."""
        _guard()
        return await _focused("disk")

    @function_tool()
    async def network_stats(self, context: RunContext) -> dict[str, str]:
        """Network only: per-interface speed, errors, wifi signal, router
        latency and loss, listening ports, top talkers. For "network", "wifi",
        "internet speed", "connection"."""
        _guard()
        return await _focused("network")

    @function_tool()
    async def power_stats(self, context: RunContext) -> dict[str, str]:
        """Battery and power: percent, state, watts, health, cycles, time
        remaining, AC and power profile. For "battery", "power", "how long
        will the battery last"."""
        _guard()
        return await _focused("power")

    @function_tool()
    async def process_stats(
        self,
        context: RunContext,
        sort_by: str = "cpu",
        top: int = 5,
        name: str = "",
        user: str = "",
    ) -> dict[str, str]:
        """Processes: top N by cpu, mem or io, counts, zombies, per user, or
        search one by name. For "what's running", "biggest apps", "is brave
        running".

        Args:
            sort_by: "cpu", "mem" or "io".
            top: how many to list (1 to 15).
            name: optional process name or command fragment to look up.
            user: optional username filter.
        """
        _guard()
        if sort_by not in ("cpu", "mem", "io"):
            raise ToolError("sort_by must be cpu, mem or io.")
        top = max(1, min(int(top or 5), 15))
        return await _focused(
            "process",
            sort_by=sort_by,
            top=top,
            name=(name or "").strip()[:64],
            user=(user or "").strip()[:32],
        )

    @function_tool()
    async def full_report(self, context: RunContext) -> dict[str, str]:
        """Everything in one very detailed system report with a health verdict,
        saved to ~/.jarvis/reports. For "full report", "everything about the
        system", "full diagnostics"."""
        _guard()
        text, findings, data = await sysdiag.full_report_text()
        path = sysdiag.save_report(text)
        bad = [f for f in findings if f.severity >= 1]
        cpu, mem = data.get("cpu") or {}, data.get("memory") or {}
        bits = []
        if "total_pct" in cpu:
            bits.append(f"CPU {cpu['total_pct']:.0f} percent")
        if "avail_pct" in mem:
            bits.append(f"{mem['avail_pct']:.0f} percent memory free")
        bat = (data.get("power") or {}).get("battery")
        if bat and bat.get("percent") is not None:
            bits.append(f"battery {bat['percent']} percent")
        if bad:
            worst = sorted(bad, key=lambda f: (-f.severity, -f.score))[0]
            verdict = f"{len(bad)} concern{'s' if len(bad) != 1 else ''}, worst: {worst.title}."
        else:
            verdict = "Nothing abnormal."
        say = f"Full report saved. {verdict} {', '.join(bits)}."
        log_action("sysmon.full_report", f"{path.name} {len(bad)} concerns")
        verdicts = "\n".join(sysdiag.verdict_lines(findings))
        return {"say": say[:480], "path": str(path), "detail": verdicts[:3000]}

    @function_tool()
    async def debug_system(self, context: RunContext, symptom: str) -> dict[str, str]:
        """Diagnose a symptom, read-only: ranks likely causes with evidence and
        suggests the next step. For "why is it slow", "fan loud", "battery
        draining", "why is memory full", "wifi bad", "gpu hot". Never changes
        anything.

        Args:
            symptom: the problem in the user's words, e.g. "laptop is slow".
        """
        _guard()
        symptom = (symptom or "").strip()
        if not symptom:
            raise ToolError("Tell me the symptom, for example 'laptop is slow'.")
        out = await sysdiag.diagnose(symptom[:200])
        log_action("sysmon.debug", f"{symptom[:60]}: {out['say'][:120]}")
        return out
