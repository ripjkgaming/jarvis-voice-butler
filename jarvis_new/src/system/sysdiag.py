"""Symptom diagnosis, health verdicts and saved full reports.

Builds on the collectors in ``system.sysmon``. Everything here is read-only:
it ranks likely causes and *suggests* next steps; it never kills, renices or
changes a setting.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import re
import time
from pathlib import Path

from system import run_cmd
from system.sysmon import (
    COLLECTORS,
    FINDERS,
    RENDERERS,
    SAMPLE_WANT,
    Finding,
    safe_collect,
    take_sample,
)

REPORT_DIR = Path.home() / ".jarvis" / "reports"
REPORT_KEEP = 20
ALL_AREAS = ("cpu", "memory", "gpu", "temps", "disk", "network", "power", "process")
SECTION_TITLES = {
    "cpu": "CPU",
    "memory": "MEMORY",
    "gpu": "GPU",
    "temps": "TEMPERATURES",
    "disk": "DISK",
    "network": "NETWORK",
    "power": "POWER",
    "process": "PROCESSES",
}

# (regex, {area: relevance 0..1}); matching rules are merged by max weight.
SYMPTOM_RULES: list[tuple[str, dict[str, float]]] = [
    (
        r"slow|lag|sluggish|freez|froze|hang|stutter|unresponsive|choppy",
        {
            "cpu": 1.0,
            "memory": 1.0,
            "disk": 0.9,
            "process": 0.8,
            "temps": 0.7,
            "gpu": 0.5,
            "power": 0.4,
        },
    ),
    (
        r"fan|loud|noise|noisy|hot|heat|warm|burn|thermal|temperature|throttl|overheat",
        {"temps": 1.0, "cpu": 0.9, "gpu": 0.8, "process": 0.7, "power": 0.3},
    ),
    (
        r"batter|drain|charg|power|unplug|juice|runtime|dying|dies",
        {
            "power": 1.0,
            "cpu": 0.8,
            "process": 0.8,
            "gpu": 0.7,
            "temps": 0.3,
            "network": 0.2,
        },
    ),
    (r"memory|\bram\b|swap|\boom\b|out of mem", {"memory": 1.0, "process": 0.8}),
    (
        r"wi-?fi|internet|network|connect|ping|latency|download|upload|online|dns|signal|router",
        {"network": 1.0, "process": 0.3},
    ),
    (
        r"\bcpu\b|processor|eating|busy|usage|using|hogg|\bload\b",
        {"cpu": 1.0, "process": 0.9, "temps": 0.4},
    ),
    (
        r"gpu|graphics|vram|nvidia|video card",
        {"gpu": 1.0, "temps": 0.7, "process": 0.5},
    ),
    (
        r"disk|storage|\bspace\b|\bssd\b|nvme|\bdrive\b|\bi/?o\b",
        {"disk": 1.0, "process": 0.6},
    ),
]
OFF_TOPIC = 0.25  # relevance of areas the symptom did not point at
RELEVANT_MIN = 0.5  # below this a finding is listed as 'also noticed'

JOURNAL_KEYWORDS = {
    "cpu": r"thermal|throttl|mce|machine check|cpu\d|rcu_|soft lockup",
    "memory": r"\boom\b|out of memory|killed process|oom-kill|page allocation",
    "gpu": r"nvrm|nvidia|amdgpu|i915|\bxe\b|drm|xid|gpu",
    "temps": r"thermal|throttl|temperature|overheat|thermald|fan",
    "disk": r"nvme|i/o error|ext4|btrfs|xfs|blk_update_request|smart|ata\d|scsi|sd[a-z]\b",
    "network": r"wlp|wlan|iwlwifi|mt79|wpa_supplicant|networkmanager|deauth|link is not ready|dhcp|carrier|disassoc",
    "power": r"acpi|battery|upower|power-profiles|thermald|ucsi|tlp",
    "process": r"segfault|core dumped|crash|coredump|killed",
}


def symptom_areas(symptom: str) -> dict[str, float]:
    s = (symptom or "").lower()
    areas: dict[str, float] = {}
    for pat, weights in SYMPTOM_RULES:
        if re.search(pat, s):
            for a, w in weights.items():
                areas[a] = max(areas.get(a, 0.0), w)
    if not areas:
        areas = dict.fromkeys(ALL_AREAS, 0.6)
    return areas


def rank_causes(findings: list[Finding], relevance: dict[str, float]) -> list[Finding]:
    """Order by finding score x relevance of its area to the symptom."""

    def key(f: Finding) -> float:
        return f.score * relevance.get(f.area, OFF_TOPIC)

    return sorted((f for f in findings if f.severity >= 1), key=key, reverse=True)


def journal_for_areas(lines: list[str], areas: list[str]) -> list[str]:
    pats = [
        re.compile(JOURNAL_KEYWORDS[a], re.I) for a in areas if a in JOURNAL_KEYWORDS
    ]
    return [ln for ln in lines if any(p.search(ln) for p in pats)]


async def journal_clusters(
    areas: list[str],
) -> tuple[list[str], list[tuple[str, int, str]]]:
    """Recent journal errors relevant to the symptom, clustered by unit."""
    from system.background import cluster_journal  # lazy: avoids an import cycle

    rc, out, _ = await run_cmd(
        "journalctl",
        "--since",
        "1 hour ago",
        "-p",
        "err..alert",
        "-o",
        "short",
        "--no-pager",
        timeout=15.0,
    )
    if rc != 0:
        return [], []
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()][-400:]
    matched = journal_for_areas(lines, areas)
    return matched, cluster_journal(matched) if matched else []


async def collect_areas(areas, sample: dict | None = None):
    """Collect several areas with ONE shared sampling window.

    Returns (data_by_area, findings, rendered_detail_by_area).
    """
    areas = [a for a in areas if a in COLLECTORS]
    want: set[str] = set()
    for a in areas:
        want |= SAMPLE_WANT.get(a, set())
    shared = (
        sample if sample is not None else (await take_sample(want) if want else None)
    )
    results = await asyncio.gather(
        *[safe_collect(a, COLLECTORS[a], sample=shared) for a in areas]
    )
    data = dict(zip(areas, results, strict=True))
    findings: list[Finding] = []
    details: dict[str, str] = {}
    for a, d in data.items():
        f = FINDERS[a](d)
        findings += f
        details[a] = RENDERERS[a](d, f)[1]
    return data, findings, details


def correlate(
    data: dict, findings: list[Finding], relevance: dict[str, float]
) -> list[Finding]:
    """Cross-area deterministic rules that turn symptoms into a named cause."""
    extra: list[Finding] = []
    cpu = data.get("cpu") or {}
    groups = cpu.get("groups") or []
    hog = groups[0] if groups and groups[0]["cpu_pct"] >= 80 else None
    hot = [f for f in findings if f.area in ("temps", "gpu") and f.severity >= 1]
    if hot and hog:
        extra.append(
            Finding(
                "cpu",
                2,
                f"Heat is likely caused by {hog['name']}",
                f"{hog['name']} {hog['cpu_pct']:.0f}% CPU while {hot[0].evidence}",
                f"Suggestion: pause or close {hog['name']} (I will not do it myself).",
                85,
            )
        )
    power = data.get("power") or {}
    batt = power.get("battery") or {}
    if batt.get("status") == "Discharging" and (batt.get("watts") or 0) >= 12 and hog:
        extra.append(
            Finding(
                "power",
                2,
                f"Battery drain is driven by {hog['name']}",
                f"{batt['watts']:.0f} W draw with {hog['name']} at {hog['cpu_pct']:.0f}% CPU",
                f"Suggestion: close {hog['name']} or switch to the power-saver profile.",
                82,
            )
        )
    disk_io = [f for f in findings if f.area == "disk" and "saturated" in f.title]
    if disk_io and cpu.get("iowait", 0) >= 20:
        extra.append(
            Finding(
                "disk",
                2,
                "Disk is the bottleneck",
                f"{disk_io[0].evidence}; CPU iowait {cpu['iowait']:.0f}%",
                "Suggestion: process_stats sort_by io shows who is writing.",
                80,
            )
        )
    mem = data.get("memory") or {}
    if (
        mem.get("avail_pct", 100) < 10
        and (mem.get("swap_activity") or {}).get("pswpout_s", 0) > 100
    ):
        top = (mem.get("groups") or [{"name": "an app"}])[0]["name"]
        extra.append(
            Finding(
                "memory",
                2,
                "System is thrashing swap",
                f"{mem['avail_pct']:.1f}% RAM free and heavy swapping; largest: {top}",
                f"Suggestion: close {top}.",
                84,
            )
        )
    return extra


NEXT_STEP = {
    "cpu": "Look at process_stats to see what is busy.",
    "memory": "Close the largest apps listed; restart browsers if they hold gigabytes.",
    "gpu": "Close GPU-heavy apps; check temps_stats.",
    "temps": "Check vents and dust, and what is loading the CPU/GPU.",
    "disk": "Free space or find the writer with process_stats sort_by io.",
    "network": "Move closer to the router, or restart the router if the gateway shows loss.",
    "power": "Dim the screen, close busy apps, or use the power-saver profile.",
    "process": "Check the listed processes.",
    "journal": "Read the clustered errors with crash_report.",
}


async def diagnose(symptom: str) -> dict[str, str]:
    relevance = symptom_areas(symptom)
    areas = [a for a in ALL_AREAS if a in relevance]
    (data, findings, details), (jlines, clusters) = await _both(areas)
    if clusters:
        top = clusters[0]
        findings.append(
            Finding(
                "journal",
                1,
                f"{len(jlines)} related journal errors in the last hour",
                f"{top[0]} x{top[1]}: {top[2][:120]}",
                NEXT_STEP["journal"],
                45,
            )
        )
        relevance = {**relevance, "journal": 0.8}
    findings += correlate(data, findings, relevance)
    ranked = rank_causes(findings, relevance)
    likely = [f for f in ranked if relevance.get(f.area, OFF_TOPIC) >= RELEVANT_MIN]
    other = [f for f in ranked if f not in likely]
    context = context_line(data, areas)
    say = _diagnose_say(symptom, likely, other, areas, context)
    out_lines = [
        f"Symptom: {symptom}",
        f"Checked: {', '.join(areas)}",
        "MODE: READ-ONLY. These are suggestions; nothing was killed, changed or run with sudo.",
        f"Current readings: {context or 'n/a'}",
        "",
        "Likely causes, ranked:",
    ]
    if likely:
        for n, f in enumerate(likely[:8], 1):
            out_lines += _cause_lines(n, f)
    else:
        out_lines.append(
            "None: every metric related to this symptom is within normal thresholds."
        )
    if other:
        out_lines += ["", "Also noticed (probably unrelated):"]
        for n, f in enumerate(other[:5], 1):
            out_lines += _cause_lines(n, f)
    if clusters:
        out_lines += ["", "Journal errors (last hour, symptom-related):"]
        out_lines += [
            f"  {name} x{count}: {line[:160]}" for name, count, line in clusters
        ]
    out_lines += ["", "Metrics:"]
    for a in areas:
        if a in details:
            out_lines.append(f"-- {SECTION_TITLES[a]} --")
            out_lines.append(details[a])
    return {"say": say, "detail": "\n".join(out_lines)[:4500]}


async def _both(areas):
    return await asyncio.gather(collect_areas(areas), journal_clusters(areas))


def _cause_lines(n: int, f: Finding) -> list[str]:
    tag = "CRITICAL" if f.severity == 2 else "WARNING"
    return [
        f"{n}. [{tag}] {f.title}",
        f"   evidence: {f.evidence}",
        f"   next step: {f.advice or NEXT_STEP.get(f.area, '')}",
    ]


def context_line(data: dict, areas: list[str]) -> str:
    """Plain current readings for the areas checked (used when nothing is wrong)."""
    bits: list[str] = []
    cpu = data.get("cpu") or {}
    if "cpu" in areas and "total_pct" in cpu:
        bits.append(f"CPU {cpu['total_pct']:.0f} percent busy")
        groups = cpu.get("groups") or []
        if groups:
            bits.append(
                "top "
                + ", ".join(
                    f"{g['name']} {g['cpu_pct']:.0f} percent" for g in groups[:2]
                )
            )
    mem = data.get("memory") or {}
    if "memory" in areas and "avail_pct" in mem:
        bits.append(f"{mem['avail_pct']:.0f} percent memory available")
    mx = (data.get("temps") or {}).get("max")
    if "temps" in areas and mx:
        bits.append(f"hottest sensor {mx['label']} {mx['temp_c']:.0f} degrees")
    gpus = (data.get("gpu") or {}).get("gpus") or []
    if "gpu" in areas and gpus and gpus[0].get("temp_c") is not None:
        bits.append(f"GPU {gpus[0]['temp_c']} degrees")
    pw = data.get("power") or {}
    b = pw.get("battery")
    if "power" in areas and b:
        state = b["status"].lower()
        draw = f", {b['watts']:.0f} watts" if b.get("watts") else ""
        bits.append(f"battery {b['percent']} percent {state}{draw}")
    for i in (data.get("network") or {}).get("ifaces") or []:
        sig = (i.get("wifi") or {}).get("signal_dbm")
        if sig is not None:
            bits.append(f"wifi {sig} dBm")
            break
    gw = (data.get("network") or {}).get("gateway") or {}
    if gw.get("avg_ms") is not None:
        bits.append(f"router {gw['avg_ms']:.0f} ms")
    return ", ".join(bits)


def _diagnose_say(
    symptom: str,
    ranked: list[Finding],
    other: list[Finding],
    areas: list[str],
    context: str = "",
) -> str:
    if not ranked:
        say = (
            f"I checked {', '.join(areas[:4])} for '{symptom}' and nothing explains it: "
            "everything related is within normal thresholds."
        )
        if context:
            say += f" Right now: {context}."
        if other:
            say += f" Unrelated: {other[0].title}."
        return say[:500]
    first = ranked[0]
    say = f"Most likely: {first.title}, {first.evidence}."
    if len(ranked) > 1:
        say += f" Next: {ranked[1].title}, {ranked[1].evidence}."
    nxt = first.advice or NEXT_STEP.get(first.area, "")
    if nxt:
        say += f" {nxt}"
    if context and len(say) + len(context) < 440:
        say += f" Right now: {context}."
    return say[:500]


def verdict_lines(findings: list[Finding]) -> list[str]:
    bad = sorted(
        (f for f in findings if f.severity >= 1), key=lambda f: (-f.severity, -f.score)
    )
    if not bad:
        return ["Nothing abnormal detected: every area is within normal thresholds."]
    out = []
    for f in bad:
        tag = "CRITICAL" if f.severity == 2 else "WARNING"
        nxt = f" Next: {f.advice}" if f.advice else ""
        out.append(f"{tag}: {f.title} ({f.evidence}).{nxt}")
    return out


def build_report_text(
    sections: dict[str, str], findings: list[Finding], header: str
) -> str:
    out = [header, ""]
    for key in ALL_AREAS:
        if key in sections:
            out += [f"=== {SECTION_TITLES[key]} ===", sections[key], ""]
    out += ["=== HEALTH VERDICT ==="]
    out += verdict_lines(findings)
    infos = [f for f in findings if f.severity == 0]
    if infos:
        out += ["", "Notes:"] + [f"  {f.line()}" for f in infos]
    return "\n".join(out) + "\n"


def save_report(
    text: str,
    root: Path | None = None,
    stamp: str | None = None,
    prefix: str = "usage",
) -> Path:
    """Write <prefix>-<stamp>.txt (0600); keep only the newest REPORT_KEEP per prefix."""
    base = root or REPORT_DIR
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = f"{prefix}-{stamp or time.strftime('%Y%m%d-%H%M%S')}.txt"
    path = base / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    old = sorted(base.glob(f"{prefix}-*.txt"))
    for p in old[:-REPORT_KEEP]:
        with contextlib.suppress(OSError):
            p.unlink()
    return path


def top_lines(detail: str, n: int = 12) -> str:
    """Headline slice of a detailed reading: the first per-process table."""
    lines = detail.splitlines()
    start = next(
        (i for i, ln in enumerate(lines) if re.match(r"Top \d+ processes by", ln)), 0
    )
    return "\n".join(lines[start : start + n])


async def full_report_text() -> tuple[str, list[Finding], dict]:
    data, findings, details = await collect_areas(list(ALL_AREAS))
    up = ""
    try:
        secs = float(Path("/proc/uptime").read_text().split()[0])
        up = f"{int(secs // 86400)}d {int(secs % 86400 // 3600)}h {int(secs % 3600 // 60)}m"
    except (OSError, ValueError, IndexError):
        pass
    header = (
        f"JARVIS FULL SYSTEM REPORT  {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"host {platform.node()}  kernel {platform.release()}  uptime {up or 'n/a'}"
    )
    return build_report_text(details, findings, header), findings, data
