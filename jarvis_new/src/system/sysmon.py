"""Focused, read-only system monitors: CPU, memory, GPU, temps, disk, network,
power and processes.

Layout (keep it that way, the tests rely on it):
- Pure parsers (``parse_*``) take fixture-able strings and never touch disk.
- Small sysfs readers (``read_*``) take a root path so tests can use tmp_path.
- Async ``collect_*`` functions gather one area into a plain dict. They are
  fail-soft: a missing file, tool or permission becomes a note, never a raise.
- ``*_findings`` apply deterministic thresholds -> ``Finding`` objects.
- ``render_*`` turn (data, findings) into a short spoken ``say`` and a
  longer ``detail`` string.

Rules: no sudo, no shell, no writes, subprocess timeouts, rate stats use one
short sample window (``SAMPLE_WINDOW``) and blocking file IO runs in a thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import pwd
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from system import run_cmd
from system.sysdetail import (
    cpu_breakdown,
    drm_client_rates,
    estimate_process_watts,
    fmt_age,
    fmt_proc_line,
    fmt_rate,
    heaters_for_sensor,
    net_proc_rates,
    parse_cpu_fields,
    parse_fdinfo,
    parse_pmon,
    parse_smaps_rollup,
    parse_snmp,
    parse_sockstat,
    parse_ss_conns,
    parse_stat_counters,
    parse_status_extra,
    render_process_section,
)

PROC = Path("/proc")
SYS_CPU = Path("/sys/devices/system/cpu")
DRM_ROOT = Path("/sys/class/drm")
THERMAL_ROOT = Path("/sys/class/thermal")
HWMON_ROOT = Path("/sys/class/hwmon")
POWER_ROOT = Path("/sys/class/power_supply")
NET_ROOT = Path("/sys/class/net")
SYS_BLOCK = Path("/sys/block")
MOUNTS_FILE = Path("/proc/mounts")
RAPL_ROOT = Path("/sys/class/powercap/intel-rapl:0")
BACKLIGHT_ROOT = Path("/sys/class/backlight")

SAMPLE_WINDOW = 0.5  # seconds; every rate stat shares one window
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PAGE_KB = (os.sysconf("SC_PAGE_SIZE") // 1024) if hasattr(os, "sysconf") else 4
SAY_MAX = 400


# ---------------------------------------------------------------- findings


@dataclass
class Finding:
    """One abnormal (or notable) observation with its evidence."""

    area: str
    severity: int  # 0 info, 1 warning, 2 critical
    title: str
    evidence: str
    advice: str = ""
    score: float = 0.0

    def line(self) -> str:
        tag = {0: "INFO", 1: "WARNING", 2: "CRITICAL"}.get(self.severity, "INFO")
        return f"{tag}: {self.title} ({self.evidence})"


def _score(sev: int, value: float = 0.0, threshold: float = 0.0) -> float:
    base = {0: 15.0, 1: 40.0, 2: 70.0}.get(sev, 15.0)
    bonus = 0.0
    if threshold > 0 and value > threshold:
        bonus = min(25.0, 25.0 * (value / threshold - 1.0))
    return base + bonus


def _f(
    area: str,
    sev: int,
    title: str,
    evidence: str,
    advice: str = "",
    value: float = 0.0,
    threshold: float = 0.0,
) -> Finding:
    return Finding(area, sev, title, evidence, advice, _score(sev, value, threshold))


# ---------------------------------------------------------------- low level


def _read(path: Path | str) -> str:
    try:
        return Path(path).read_text()
    except (OSError, ValueError):
        return ""


def _num(text: str) -> float | None:
    try:
        return float(text.strip())
    except (ValueError, AttributeError):
        return None


def _read_num(path: Path | str) -> float | None:
    return _num(_read(path))


def _read_int(path: Path | str) -> int | None:
    n = _read_num(path)
    return int(n) if n is not None else None


def _unit(text: str | None) -> float | None:
    """'58 C' / '4096 MiB' / '6.06 W' / 'N/A' -> number or None."""
    if not text:
        return None
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def _clip(text: str, limit: int = SAY_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _gb(kb: float) -> float:
    return kb / 1048576


def _sentence(parts: list[str]) -> str:
    parts = [p.rstrip(".") for p in parts if p]
    parts = [p[0].upper() + p[1:] for p in parts]
    return ". ".join(parts) + "." if parts else ""


# ---------------------------------------------------------------- pure parsers


def parse_proc_stat(text: str) -> dict:
    """/proc/stat -> {'cpu': (total, idle, iowait, steal), 'cpu0': ..., 'ctxt': n}."""
    out: dict = {}
    for ln in text.splitlines():
        parts = ln.split()
        if not parts:
            continue
        key = parts[0]
        if re.fullmatch(r"cpu\d*", key):
            try:
                v = [int(x) for x in parts[1:9]]
            except ValueError:
                continue
            v += [0] * (8 - len(v))
            out[key] = (sum(v), v[3] + v[4], v[4], v[7])
        elif key in ("ctxt", "procs_running", "procs_blocked") and len(parts) > 1:
            with contextlib.suppress(ValueError):
                out[key] = int(parts[1])
    return out


def cpu_utilisation(a: dict, b: dict) -> dict:
    """Percent busy / iowait / steal overall and per core between two samples."""

    def pct(key: str) -> tuple[float, float, float]:
        if key not in a or key not in b:
            return 0.0, 0.0, 0.0
        dt = b[key][0] - a[key][0]
        if dt <= 0:
            return 0.0, 0.0, 0.0
        busy = 100.0 * (1 - (b[key][1] - a[key][1]) / dt)
        return (
            max(0.0, min(100.0, busy)),
            100.0 * (b[key][2] - a[key][2]) / dt,
            100.0 * (b[key][3] - a[key][3]) / dt,
        )

    total, iowait, steal = pct("cpu")
    keys = sorted(
        (k for k in b if re.fullmatch(r"cpu\d+", k)), key=lambda k: int(k[3:])
    )
    return {
        "total": total,
        "iowait": iowait,
        "steal": steal,
        "cores": [pct(k)[0] for k in keys],
    }


def parse_loadavg(text: str) -> tuple[float, float, float] | None:
    p = text.split()
    try:
        return float(p[0]), float(p[1]), float(p[2])
    except (IndexError, ValueError):
        return None


def parse_meminfo(text: str) -> dict[str, int]:
    mem: dict[str, int] = {}
    for ln in text.splitlines():
        k, _, v = ln.partition(":")
        if v.split():
            with contextlib.suppress(ValueError):
                mem[k] = int(v.split()[0])
    return mem


def memory_summary(mem: dict[str, int]) -> dict:
    total = mem.get("MemTotal", 0)
    avail = mem.get("MemAvailable", mem.get("MemFree", 0))
    swap_t = mem.get("SwapTotal", 0)
    swap_u = swap_t - mem.get("SwapFree", 0)
    return {
        "total_kb": total,
        "total_gb": _gb(total),
        "used_gb": _gb(total - avail),
        "avail_gb": _gb(avail),
        "avail_pct": 100.0 * avail / total if total else 0.0,
        "free_gb": _gb(mem.get("MemFree", 0)),
        "cached_gb": _gb(mem.get("Cached", 0)),
        "buffers_gb": _gb(mem.get("Buffers", 0)),
        "slab_gb": _gb(mem.get("Slab", 0)),
        "slab_pct": 100.0 * mem.get("Slab", 0) / total if total else 0.0,
        "shmem_gb": _gb(mem.get("Shmem", 0)),
        "dirty_mb": mem.get("Dirty", 0) / 1024,
        "swap_total_gb": _gb(swap_t),
        "swap_used_gb": _gb(swap_u),
        "swap_used_pct": 100.0 * swap_u / swap_t if swap_t else 0.0,
    }


def parse_psi(text: str) -> dict:
    """/proc/pressure/* -> {'some': {'avg10': .., 'avg60': .., 'avg300': ..}, ...}."""
    out: dict = {}
    for ln in text.splitlines():
        parts = ln.split()
        if not parts or parts[0] not in ("some", "full"):
            continue
        vals = {}
        for kv in parts[1:]:
            k, _, v = kv.partition("=")
            if k.startswith("avg"):
                n = _num(v)
                if n is not None:
                    vals[k] = n
        out[parts[0]] = vals
    return out


def parse_swaps(text: str) -> list[dict]:
    rows = []
    for ln in text.splitlines()[1:]:
        p = ln.split()
        if len(p) >= 4 and p[2].isdigit() and p[3].isdigit():
            rows.append({"name": p[0], "size_kb": int(p[2]), "used_kb": int(p[3])})
    return rows


def parse_zram_mm_stat(text: str, disksize: int) -> dict:
    p = text.split()
    if len(p) < 3 or not all(x.isdigit() for x in p[:3]):
        return {}
    orig, compr, used = int(p[0]), int(p[1]), int(p[2])
    return {
        "orig_mb": orig / 1048576,
        "compressed_mb": compr / 1048576,
        "used_mb": used / 1048576,
        "ratio": orig / compr if compr else 0.0,
        "disk_gb": disksize / 1073741824,
    }


def parse_vmstat(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for ln in text.splitlines():
        p = ln.split()
        if len(p) == 2 and p[1].isdigit():
            out[p[0]] = int(p[1])
    return out


_OOM_KERNEL = re.compile(r"Killed process (\d+) \(([^)]+)\)")
_OOM_SYSTEMD = re.compile(r"systemd-oomd.*?Killed (\S+)")


def parse_oom_journal(text: str) -> list[str]:
    """Real OOM kills only (not drivers that merely print 'Out of memory')."""
    kills = []
    for ln in text.splitlines():
        stamp = " ".join(ln.split()[:3])
        m = _OOM_KERNEL.search(ln)
        if m:
            kills.append(f"{stamp} {m.group(2)} (pid {m.group(1)}) by kernel OOM")
            continue
        m = _OOM_SYSTEMD.search(ln)
        if m:
            kills.append(f"{stamp} {m.group(1)} by systemd-oomd")
    return kills


def parse_pid_stat(line: str) -> dict | None:
    """One /proc/<pid>/stat line (comm may contain spaces/parens)."""
    i, j = line.find("("), line.rfind(")")
    if i < 0 or j < i:
        return None
    try:
        rest = line[j + 2 :].split()
        return {
            "pid": int(line[:i]),
            "comm": line[i + 1 : j],
            "state": rest[0],
            "ticks": int(rest[11]) + int(rest[12]),
            "threads": int(rest[17]),
            "start_ticks": int(rest[19]),
            "rss_kb": int(rest[21]) * PAGE_KB,
        }
    except (ValueError, IndexError):
        return None


def short_cmdline(raw: str) -> str:
    """'/usr/bin/python3\\0/opt/x/agent.py\\0--dev' -> 'python3 agent.py'."""
    args = [a for a in raw.split("\0") if a]
    if not args:
        return ""
    names = [Path(args[0]).name]
    for a in args[1:4]:
        if not a.startswith("-"):
            names.append(Path(a).name)
            break
    return " ".join(names)


_USERS: dict[int, str] = {}


def _user(uid: int | None) -> str:
    if uid is None:
        return "?"
    if uid not in _USERS:
        try:
            _USERS[uid] = pwd.getpwuid(uid).pw_name
        except KeyError:
            _USERS[uid] = str(uid)
    return _USERS[uid]


def proc_rates(
    before: dict[int, dict],
    after: dict[int, dict],
    dt: float,
    clk_tck: int | None = None,
) -> list[dict]:
    """Per-process CPU% (of one core) and IO rates between two scans."""
    clk = clk_tck or CLK_TCK
    dt = max(dt, 1e-3)
    rows = []
    for pid, a in after.items():
        b = before.get(pid)
        cpu = 0.0
        if b is not None and b["comm"] == a["comm"]:
            cpu = max(0.0, 100.0 * (a["ticks"] - b["ticks"]) / clk / dt)
        rd = wr = None
        if (
            b is not None
            and a.get("read_bytes") is not None
            and b.get("read_bytes") is not None
        ):
            rd = max(0.0, (a["read_bytes"] - b["read_bytes"]) / dt)
            wr = max(0.0, (a["write_bytes"] - b["write_bytes"]) / dt)
        ctx = None
        if b is not None and a.get("ctx") is not None and b.get("ctx") is not None:
            ctx = max(0.0, (a["ctx"] - b["ctx"]) / dt)
        rows.append(
            {
                "pid": pid,
                "name": a["comm"],
                "state": a["state"],
                "ctx_ps": ctx,
                "uid": a.get("uid"),
                "user": _user(a.get("uid")),
                "cpu_pct": cpu,
                "rss_kb": a["rss_kb"],
                "threads": a["threads"],
                "start_ticks": a["start_ticks"],
                "read_bps": rd,
                "write_bps": wr,
                "io_bps": (rd or 0.0) + (wr or 0.0),
            }
        )
    return rows


def group_procs(rows: list[dict], key: str = "cpu_pct") -> list[dict]:
    """Merge processes with the same name (browsers have dozens)."""
    merged: dict[str, dict] = {}
    for r in rows:
        g = merged.setdefault(
            r["name"],
            {
                "name": r["name"],
                "cpu_pct": 0.0,
                "rss_kb": 0,
                "count": 0,
                "io_bps": 0.0,
                "threads": 0,
                "est_w": None,
                "ctx_ps": None,
                "pids": [],
                "members": [],
            },
        )
        g["cpu_pct"] += r["cpu_pct"]
        g["rss_kb"] += r["rss_kb"]
        g["count"] += 1
        g["io_bps"] += (r.get("read_bps") or 0.0) + (r.get("write_bps") or 0.0)
        g["threads"] += r.get("threads") or 0
        if r.get("est_w") is not None:
            g["est_w"] = (g["est_w"] or 0.0) + r["est_w"]
        if r.get("ctx_ps") is not None:
            g["ctx_ps"] = (g["ctx_ps"] or 0.0) + r["ctx_ps"]
        g["pids"].append(r["pid"])
        g["members"].append(r)
    for g in merged.values():
        g["members"] = sorted(g["members"], key=lambda m: -(m.get(key) or 0))[:6]
    return sorted(merged.values(), key=lambda g: -g.get(key, 0))


# ---- GPU parsers


def _xml_text(node: ET.Element | None, path: str) -> str | None:
    if node is None:
        return None
    el = node.find(path)
    return el.text.strip() if el is not None and el.text else None


def parse_nvidia_xml(text: str) -> list[dict]:
    """`nvidia-smi -q -x` -> list of GPU dicts (fail-soft: [] on bad XML)."""
    try:
        root = ET.fromstring(re.sub(r"<!DOCTYPE[^>]*>", "", text))
    except ET.ParseError:
        return []
    driver = _xml_text(root, "driver_version")
    gpus = []
    for g in root.findall("gpu"):
        reasons = g.find("clocks_event_reasons")
        if reasons is None:
            reasons = g.find("clocks_throttle_reasons")
        throttle = []
        if reasons is not None:
            for el in reasons:
                tag = re.sub(r"^clocks_(?:event|throttle)_reason_", "", el.tag)
                if (el.text or "").strip() == "Active" and tag not in (
                    "gpu_idle",
                    "applications_clocks_setting",
                    "display_clocks_setting",
                ):
                    throttle.append(tag)
        fan = _unit(_xml_text(g, "fan_speed"))
        procs = []
        for p in g.findall("processes/process_info"):
            try:
                pid = int(_xml_text(p, "pid") or "")
            except ValueError:
                continue
            procs.append(
                {
                    "pid": pid,
                    "name": Path(_xml_text(p, "process_name") or "?").name,
                    "type": _xml_text(p, "type") or "?",
                    "mem_mb": int(_unit(_xml_text(p, "used_memory")) or 0),
                }
            )
        power = _unit(_xml_text(g, "gpu_power_readings/instant_power_draw"))
        if power is None:
            power = _unit(_xml_text(g, "gpu_power_readings/average_power_draw"))
        gpus.append(
            {
                "vendor": "NVIDIA",
                "name": _xml_text(g, "product_name") or "NVIDIA GPU",
                "driver": driver,
                "util_pct": _int(_unit(_xml_text(g, "utilization/gpu_util"))),
                "mem_util_pct": _int(_unit(_xml_text(g, "utilization/memory_util"))),
                "vram_used_mb": _int(_unit(_xml_text(g, "fb_memory_usage/used"))),
                "vram_total_mb": _int(_unit(_xml_text(g, "fb_memory_usage/total"))),
                "temp_c": _int(_unit(_xml_text(g, "temperature/gpu_temp"))),
                "temp_slow_c": _int(
                    _unit(_xml_text(g, "temperature/gpu_temp_slow_threshold"))
                ),
                "temp_max_c": _int(
                    _unit(_xml_text(g, "temperature/gpu_temp_max_threshold"))
                ),
                "power_w": power,
                "power_limit_w": _unit(
                    _xml_text(g, "gpu_power_readings/current_power_limit")
                ),
                "clock_mhz": _int(_unit(_xml_text(g, "clocks/graphics_clock"))),
                "clock_max_mhz": _int(_unit(_xml_text(g, "max_clocks/graphics_clock"))),
                "mem_clock_mhz": _int(_unit(_xml_text(g, "clocks/mem_clock"))),
                "fan_pct": _int(fan),
                "fan_rpm": None,
                "pstate": _xml_text(g, "performance_state"),
                "throttle": throttle,
                "procs": procs,
                "extra": _nvidia_extra(g),
            }
        )
    return gpus


_NVIDIA_EXTRA = (
    ("architecture", "product_architecture"),
    ("display active", "display_active"),
    ("persistence mode", "persistence_mode"),
    ("PCIe gen now/max", None),
    ("PCIe width now/max", None),
    ("PCIe tx KB/s", "pci/tx_util"),
    ("PCIe rx KB/s", "pci/rx_util"),
    ("BAR1 used/total", None),
    ("encoder util", "utilization/encoder_util"),
    ("decoder util", "utilization/decoder_util"),
    ("video clock", "clocks/video_clock"),
    ("max memory clock", "max_clocks/mem_clock"),
    ("max video clock", "max_clocks/video_clock"),
    ("power limit default", "gpu_power_readings/default_power_limit"),
    ("power limit min", "gpu_power_readings/min_power_limit"),
    ("power limit max", "gpu_power_readings/max_power_limit"),
    ("average power", "gpu_power_readings/average_power_draw"),
    ("temp max threshold", "temperature/gpu_temp_max_threshold"),
    ("temp target", "temperature/gpu_target_temperature"),
    ("throttle time sw power cap", "clocks_event_reasons_counters/clocks_event_reasons_counters_sw_power_cap"),
    ("throttle time sw thermal", "clocks_event_reasons_counters/clocks_event_reasons_counters_sw_therm_slowdown"),
    ("throttle time hw thermal", "clocks_event_reasons_counters/clocks_event_reasons_counters_hw_therm_slowdown"),
    ("throttle time hw power brake", "clocks_event_reasons_counters/clocks_event_reasons_counters_hw_power_brake"),
    ("vbios", "vbios_version"),
)  # fmt: skip


def _nvidia_extra(g: ET.Element) -> list[tuple[str, str]]:
    """Every extra NVIDIA stat worth printing, as (label, value) pairs."""
    out: list[tuple[str, str]] = []
    for label, path in _NVIDIA_EXTRA:
        if path:
            val = _xml_text(g, path)
        elif label.startswith("PCIe gen"):
            now = _xml_text(g, "pci/pci_gpu_link_info/pcie_gen/current_link_gen")
            mx = _xml_text(g, "pci/pci_gpu_link_info/pcie_gen/max_link_gen")
            val = f"{now}/{mx}" if now else None
        elif label.startswith("PCIe width"):
            now = _xml_text(g, "pci/pci_gpu_link_info/link_widths/current_link_width")
            mx = _xml_text(g, "pci/pci_gpu_link_info/link_widths/max_link_width")
            val = f"{now}/{mx}" if now else None
        else:
            used = _xml_text(g, "bar1_memory_usage/used")
            tot = _xml_text(g, "bar1_memory_usage/total")
            val = f"{used}/{tot}" if used else None
        if val and val != "N/A":
            out.append((label, val))
    return out


def _int(v: float | None) -> int | None:
    return int(v) if v is not None else None


_DRM_VENDORS = {"0x1002": "AMD", "0x8086": "Intel", "0x10de": "NVIDIA"}


def read_drm_gpus(root: Path, skip_vendors: tuple[str, ...] = ()) -> list[dict]:
    """AMD (amdgpu sysfs) and Intel (i915/xe sysfs) GPUs; no root needed."""
    gpus = []
    try:
        cards = sorted(p for p in root.iterdir() if re.fullmatch(r"card\d+", p.name))
    except OSError:
        return []
    for card in cards:
        dev = card / "device"
        vendor = _DRM_VENDORS.get(_read(dev / "vendor").strip().lower(), "")
        if not vendor or vendor in skip_vendors:
            continue
        g: dict = {
            "vendor": vendor,
            "name": f"{vendor} graphics ({card.name})",
            "util_pct": _read_int(dev / "gpu_busy_percent"),
            "vram_used_mb": None,
            "vram_total_mb": None,
            "temp_c": None,
            "temp_slow_c": None,
            "power_w": None,
            "power_limit_w": None,
            "clock_mhz": None,
            "clock_max_mhz": None,
            "fan_pct": None,
            "fan_rpm": None,
            "throttle": [],
            "procs": [],
            "card": card.name,
        }
        used = _read_num(dev / "mem_info_vram_used")
        total = _read_num(dev / "mem_info_vram_total")
        if used is not None and total:
            g["vram_used_mb"], g["vram_total_mb"] = (
                int(used / 1048576),
                int(total / 1048576),
            )
        for hw in sorted((dev / "hwmon").glob("hwmon*")):
            t = _read_num(hw / "temp1_input")
            if t is not None and g["temp_c"] is None:
                g["temp_c"] = int(t / 1000)
            p = _read_num(hw / "power1_average")
            if p is None:
                p = _read_num(hw / "power1_input")
            if p is not None and g["power_w"] is None:
                g["power_w"] = p / 1e6
            cap = _read_num(hw / "power1_cap")
            if cap is not None and g["power_limit_w"] is None:
                g["power_limit_w"] = cap / 1e6
            rpm = _read_int(hw / "fan1_input")
            if rpm is not None and g["fan_rpm"] is None:
                g["fan_rpm"] = rpm
            fr = _read_num(hw / "freq1_input")
            if fr is not None and g["clock_mhz"] is None:
                g["clock_mhz"] = int(fr / 1e6)
        if vendor == "Intel":
            g["name"] = f"Intel integrated graphics ({card.name})"
            for rel in ("gt_cur_freq_mhz", "gt/gt0/rps_cur_freq_mhz"):
                v = _read_int(card / rel)
                if v is not None:
                    g["clock_mhz"] = v
                    break
            for rel in ("gt_max_freq_mhz", "gt/gt0/rps_max_freq_mhz"):
                v = _read_int(card / rel)
                if v is not None:
                    g["clock_max_mhz"] = v
                    break
        elif vendor == "AMD":
            # pp_dpm_sclk marks the current clock with '*'.
            for ln in _read(dev / "pp_dpm_sclk").splitlines():
                m = re.search(r":\s*(\d+)Mhz\s*\*", ln, re.I)
                if m and g["clock_mhz"] is None:
                    g["clock_mhz"] = int(m.group(1))
        gpus.append(g)
    return gpus


# ---- thermal


def read_thermal(thermal_root: Path, hwmon_root: Path) -> dict:
    """Every thermal zone + hwmon temp sensor, fans and active cooling."""
    sensors: list[dict] = []
    trip_list: list[dict] = []
    seen: dict[str, int] = {}

    def add(label: str, temp: float, crit: float | None, high: float | None) -> None:
        n = seen.get(label, 0)
        seen[label] = n + 1
        sensors.append(
            {
                "label": label if n == 0 else f"{label}#{n + 1}",
                "temp_c": round(temp, 1),
                "crit_c": crit,
                "high_c": high,
            }
        )

    def sane(v: float | None) -> float | None:
        if v is None:
            return None
        c = v / 1000
        return c if 0 < c <= 200 else None

    for zone in sorted(thermal_root.glob("thermal_zone*")):
        t = _read_num(zone / "temp")
        if t is None or not -50 < t / 1000 < 200:
            continue
        crit = high = None
        for tp in zone.glob("trip_point_*_type"):
            kind = _read(tp).strip()
            val = sane(_read_num(str(tp).replace("_type", "_temp")))
            if val is None:
                continue
            if kind == "critical":
                crit = val if crit is None else min(crit, val)
            elif kind in ("hot", "passive"):
                high = val if high is None else min(high, val)
        ztype = _read(zone / "type").strip() or zone.name
        trips = []
        for tp in sorted(zone.glob("trip_point_*_type")):
            tv = sane(_read_num(str(tp).replace("_type", "_temp")))
            if tv is not None:
                trips.append((_read(tp).strip(), tv))
        if trips:
            trip_list.append({"zone": ztype, "trips": trips})
        add(f"thermal:{ztype}", t / 1000, crit, high)

    fans: list[dict] = []
    for hw in sorted(hwmon_root.glob("hwmon*")):
        name = _read(hw / "name").strip() or hw.name
        if name == "nvme":  # two drives -> tell them apart (nvme0, nvme1)
            with contextlib.suppress(OSError):
                dev = os.path.basename(os.path.realpath(hw / "device"))
                if re.fullmatch(r"nvme\d+", dev):
                    name = dev
        for inp in sorted(hw.glob("temp*_input")):
            n = inp.name[4:].split("_")[0]
            t = _read_num(inp)
            if t is None or not -50 < t / 1000 < 200:
                continue
            label = _read(hw / f"temp{n}_label").strip() or f"temp{n}"
            crit = sane(_read_num(hw / f"temp{n}_crit"))
            high = sane(_read_num(hw / f"temp{n}_max")) or sane(
                _read_num(hw / f"temp{n}_high")
            )
            add(f"{name}:{label}", t / 1000, crit, high)
        for fan in sorted(hw.glob("fan*_input")):
            rpm = _read_int(fan)
            if rpm is not None:
                fans.append({"label": f"{name}:{fan.name.split('_')[0]}", "rpm": rpm})

    cooling = []
    cooling_all = []
    for cd in sorted(thermal_root.glob("cooling_device*")):
        kind = _read(cd / "type").strip()
        state, mx = _read_int(cd / "cur_state"), _read_int(cd / "max_state")
        cooling_all.append({"type": kind, "state": state or 0, "max": mx})
        if state and state > 0 and kind != "TCC Offset":
            cooling.append({"type": kind, "state": state, "max": mx})
    hottest = max(sensors, key=lambda s: s["temp_c"]) if sensors else None
    return {
        "sensors": sensors,
        "fans": fans,
        "cooling_active": cooling,
        "cooling_all": cooling_all,
        "trips": trip_list,
        "max": hottest,
    }


# ---- disk


def parse_diskstats(text: str, whole_devices: set[str] | None = None) -> dict:
    out = {}
    for ln in text.splitlines():
        p = ln.split()
        if len(p) < 14:
            continue
        name = p[2]
        if whole_devices is not None:
            if name not in whole_devices:
                continue
        elif not re.fullmatch(
            r"nvme\d+n\d+|sd[a-z]+|vd[a-z]+|hd[a-z]+|mmcblk\d+|xvd[a-z]+", name
        ):
            continue
        try:
            out[name] = {
                "reads": int(p[3]),
                "rsect": int(p[5]),
                "rms": int(p[6]),
                "writes": int(p[7]),
                "wsect": int(p[9]),
                "wms": int(p[10]),
                "io_ms": int(p[12]),
                "rmerge": int(p[4]),
                "wmerge": int(p[8]),
                "inflight": int(p[11]),
                "wio_ms": int(p[13]),
                "discards": int(p[14]) if len(p) > 14 else 0,
                "flushes": int(p[18]) if len(p) > 18 else 0,
            }
        except ValueError:
            continue
    return out


def disk_rates(a: dict, b: dict, dt: float) -> dict:
    dt = max(dt, 1e-3)
    rates = {}
    for name, y in b.items():
        x = a.get(name)
        if not x:
            continue
        ios = (y["reads"] - x["reads"]) + (y["writes"] - x["writes"])
        wait = (y["rms"] - x["rms"]) + (y["wms"] - x["wms"])
        rates[name] = {
            "read_mbps": (y["rsect"] - x["rsect"]) * 512 / 1e6 / dt,
            "write_mbps": (y["wsect"] - x["wsect"]) * 512 / 1e6 / dt,
            "iops": ios / dt,
            "read_iops": (y["reads"] - x["reads"]) / dt,
            "write_iops": (y["writes"] - x["writes"]) / dt,
            "merged_ps": (
                (y.get("rmerge", 0) - x.get("rmerge", 0))
                + (y.get("wmerge", 0) - x.get("wmerge", 0))
            )
            / dt,
            "avg_queue": (y.get("wio_ms", 0) - x.get("wio_ms", 0)) / (dt * 1000),
            "inflight": y.get("inflight", 0),
            "avg_req_kb": (
                ((y["rsect"] - x["rsect"]) + (y["wsect"] - x["wsect"])) * 0.5 / ios
                if ios > 0
                else 0.0
            ),
            "util_pct": min(100.0, 100.0 * (y["io_ms"] - x["io_ms"]) / (dt * 1000)),
            "await_ms": wait / ios if ios > 0 else 0.0,
        }
    return rates


REAL_FS = {
    "ext2",
    "ext3",
    "ext4",
    "btrfs",
    "xfs",
    "f2fs",
    "vfat",
    "exfat",
    "ntfs",
    "ntfs3",
    "fuseblk",
    "zfs",
    "jfs",
    "reiserfs",
    "nfs",
    "nfs4",
    "cifs",
}


def parse_mounts(text: str) -> list[dict]:
    mounts = []
    for ln in text.splitlines():
        p = ln.split()
        if len(p) < 4 or p[2] not in REAL_FS:
            continue
        mount = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), p[1])
        mounts.append(
            {
                "device": p[0],
                "mount": mount,
                "fs": p[2],
                "opts": p[3],
                "ro": "ro" in p[3].split(","),
            }
        )
    return mounts


def mount_usage(path: str) -> dict | None:
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    if not st.f_blocks:
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bfree * st.f_frsize
    avail = st.f_bavail * st.f_frsize
    used = total - free
    denom = used + avail
    return {
        "total_gb": total / 1073741824,
        "used_gb": used / 1073741824,
        "avail_gb": avail / 1073741824,
        "used_pct": 100.0 * used / denom if denom else 0.0,
        "inode_pct": (
            100.0 * (st.f_files - st.f_ffree) / st.f_files if st.f_files else None
        ),
        "_id": (st.f_blocks, st.f_bfree, st.f_files, st.f_ffree),
    }


def parse_smart_health(text: str) -> str:
    low = text.lower()
    if "permission denied" in low or "operation not permitted" in low:
        return "needs root"
    if "failed" in low and "smart" in low:
        return "FAILED"
    if "passed" in low or re.search(r"health status:\s*ok", low):
        return "PASSED"
    return "unknown"


# ---- network


def parse_net_dev(text: str) -> dict[str, dict]:
    out = {}
    for ln in text.splitlines()[2:]:
        name, _, rest = ln.partition(":")
        f = rest.split()
        if len(f) < 16:
            continue
        try:
            v = [int(x) for x in f]
        except ValueError:
            continue
        out[name.strip()] = {
            "rx_bytes": v[0],
            "rx_pkts": v[1],
            "rx_err": v[2],
            "rx_drop": v[3],
            "tx_bytes": v[8],
            "tx_pkts": v[9],
            "tx_err": v[10],
            "tx_drop": v[11],
            "rx_fifo": v[4],
            "rx_frame": v[5],
            "rx_compressed": v[6],
            "rx_multicast": v[7],
            "tx_fifo": v[12],
            "tx_colls": v[13],
            "tx_carrier": v[14],
            "tx_compressed": v[15],
        }
    return out


def net_rates(a: dict, b: dict, dt: float) -> dict:
    dt = max(dt, 1e-3)
    rates = {}
    for name, y in b.items():
        x = a.get(name)
        if not x:
            continue
        rx = max(0, y["rx_bytes"] - x["rx_bytes"]) * 8 / dt
        tx = max(0, y["tx_bytes"] - x["tx_bytes"]) * 8 / dt
        rates[name] = {
            "rx_mbps": rx / 1e6,
            "tx_mbps": tx / 1e6,
            "rx_kbps": rx / 1e3,
            "tx_kbps": tx / 1e3,
            "rx_pps": max(0, y["rx_pkts"] - x["rx_pkts"]) / dt,
            "tx_pps": max(0, y["tx_pkts"] - x["tx_pkts"]) / dt,
        }
    return rates


def parse_proc_wireless(text: str) -> dict[str, dict]:
    out = {}
    for ln in text.splitlines()[2:]:
        name, _, rest = ln.partition(":")
        f = rest.split()
        if len(f) >= 3:
            q, lvl = _num(f[1].rstrip(".")), _num(f[2].rstrip("."))
            if q is not None and lvl is not None:
                out[name.strip()] = {"quality": q, "level_dbm": lvl}
    return out


def parse_iw_link(text: str) -> dict:
    if "Not connected" in text or "signal:" not in text:
        return {}
    out: dict = {}
    for key, pat, conv in (
        ("ssid", r"SSID:\s*(.+)", str),
        ("freq_mhz", r"freq:\s*(\d+)", int),
        ("signal_dbm", r"signal:\s*(-?\d+)", int),
        ("rx_mbit", r"rx bitrate:\s*([\d.]+)", float),
        ("tx_mbit", r"tx bitrate:\s*([\d.]+)", float),
    ):
        m = re.search(pat, text)
        if m:
            out[key] = conv(m.group(1).strip())
    return out


def parse_iw_station(text: str) -> dict:
    out = {}
    for key, pat in (
        ("tx_retries", r"tx retries:\s*(\d+)"),
        ("tx_failed", r"tx failed:\s*(\d+)"),
        ("tx_packets", r"tx packets:\s*(\d+)"),
    ):
        m = re.search(pat, text)
        if m:
            out[key] = int(m.group(1))
    return out


def parse_default_gateway(route_text: str) -> str:
    for ln in route_text.splitlines()[1:]:
        p = ln.split()
        if len(p) >= 3 and p[1] == "00000000":
            try:
                raw = bytes.fromhex(p[2])[::-1]
            except ValueError:
                continue
            if len(raw) == 4:
                return ".".join(str(b) for b in raw)
    return ""


def parse_ping(text: str) -> dict:
    out: dict = {}
    m = re.search(r"([\d.]+)% packet loss", text)
    if m:
        out["loss_pct"] = float(m.group(1))
    m = re.search(r"=\s*[\d.]+/([\d.]+)/([\d.]+)/", text)
    if m:
        out["avg_ms"], out["max_ms"] = float(m.group(1)), float(m.group(2))
    return out


_USERS_RE = re.compile(r'users:\(\("([^"]+)",pid=(\d+)')


def parse_ss_listeners(text: str) -> list[dict]:
    rows = []
    for ln in text.splitlines():
        p = ln.split()
        if len(p) < 6 or p[1] not in ("LISTEN", "UNCONN"):
            continue
        local = p[4]
        host, _, port = local.rpartition(":")
        m = _USERS_RE.search(ln)
        rows.append(
            {
                "proto": p[0],
                "addr": host,
                "port": int(port) if port.isdigit() else 0,
                "proc": m.group(1) if m else "",
                "exposed": host in ("0.0.0.0", "*", "[::]", "::"),
            }
        )
    return rows


def parse_ss_talkers(text: str) -> list[dict]:
    """`ss -tinp` -> processes ranked by cumulative bytes on their connections."""
    agg: dict[str, dict] = {}
    cur: str | None = None
    for ln in text.splitlines():
        if not ln.strip():
            continue
        if not ln[0].isspace():
            m = _USERS_RE.search(ln)
            cur = m.group(1) if m else "unknown"
            agg.setdefault(cur, {"proc": cur, "bytes": 0, "conns": 0})
            agg[cur]["conns"] += 1
        elif cur is not None:
            sent = re.search(r"bytes_sent:(\d+)", ln)
            recv = re.search(r"bytes_received:(\d+)", ln)
            agg[cur]["bytes"] += int(sent.group(1)) if sent else 0
            agg[cur]["bytes"] += int(recv.group(1)) if recv else 0
    return sorted(agg.values(), key=lambda r: -r["bytes"])


# ---- power


def read_power(root: Path) -> dict:
    """Battery + AC from power_supply sysfs. Pure given a root."""
    battery = None
    ac: bool | None = None
    try:
        supplies = sorted(root.iterdir())
    except OSError:
        supplies = []
    for sup in supplies:
        kind = _read(sup / "type").strip()
        if sup.name.startswith("BAT") and battery is None:
            battery = _read_battery(sup)
        elif (
            kind == "Mains" or sup.name.startswith(("AC", "ADP"))
        ) and "source" not in sup.name:
            online = _read_int(sup / "online")
            if online is not None:
                ac = bool(online) or bool(ac)
    return {"battery": battery, "ac_online": ac}


def _read_battery(sup: Path) -> dict:
    status = _read(sup / "status").strip() or "Unknown"
    volt = _read_num(sup / "voltage_now")
    p_now = _read_num(sup / "power_now")
    cur = _read_num(sup / "current_now")
    watts = None
    if p_now is not None:
        watts = abs(p_now) / 1e6
    elif cur is not None and volt is not None:
        watts = abs(cur) * volt / 1e12

    def energy(prefix: str) -> float | None:  # Wh
        e = _read_num(sup / f"energy_{prefix}")
        if e is not None:
            return e / 1e6
        c = _read_num(sup / f"charge_{prefix}")
        if c is not None and volt is not None:
            return c * volt / 1e12
        return None

    e_now, e_full, e_design = energy("now"), energy("full"), energy("full_design")
    health = None
    if e_full is not None and e_design:
        health = 100.0 * e_full / e_design
    hours = None
    if watts and watts > 0.5 and e_now is not None:
        if status == "Discharging":
            hours = e_now / watts
        elif status == "Charging" and e_full is not None:
            hours = max(0.0, e_full - e_now) / watts
    pct = _read_int(sup / "capacity")
    return {
        "percent": pct,
        "status": status,
        "watts": watts,
        "energy_wh": e_now,
        "full_wh": e_full,
        "design_wh": e_design,
        "health_pct": health,
        "cycles": _read_int(sup / "cycle_count"),
        "hours_left": hours,
        "tech": _read(sup / "technology").strip(),
        "charge_limit": _read_int(sup / "charge_control_end_threshold"),
    }


# ---------------------------------------------------------------- findings


def cpu_findings(d: dict) -> list[Finding]:
    out: list[Finding] = []
    cores = d.get("cores_count") or 1
    total = d.get("total_pct", 0.0)
    if total >= 90:
        out.append(
            _f(
                "cpu",
                2,
                "CPU saturated",
                f"{total:.0f}% busy across {cores} cores (threshold 90%)",
                "See process_stats for the busiest programs.",
                total,
                90,
            )
        )
    elif total >= 70:
        out.append(
            _f(
                "cpu",
                1,
                "CPU heavily loaded",
                f"{total:.0f}% busy across {cores} cores (threshold 70%)",
                "See process_stats for the busiest programs.",
                total,
                70,
            )
        )
    iow = d.get("iowait", 0.0)
    if iow >= 20:
        out.append(
            _f(
                "cpu",
                1,
                "CPU waiting on disk I/O",
                f"{iow:.0f}% iowait (threshold 20%)",
                "Something is hammering the disk; check disk_stats.",
                iow,
                20,
            )
        )
    steal = d.get("steal", 0.0)
    if steal >= 10:
        out.append(
            _f(
                "cpu",
                1,
                "CPU time stolen by hypervisor",
                f"{steal:.0f}% steal (threshold 10%)",
                "",
                steal,
                10,
            )
        )
    load = d.get("load")
    if load:
        ratio = load[0] / cores
        if ratio >= 2:
            out.append(
                _f(
                    "cpu",
                    2,
                    "Run queue badly backed up",
                    f"load {load[0]:.1f} on {cores} cores = {ratio:.1f}x (threshold 2.0x)",
                    "",
                    ratio,
                    2,
                )
            )
        elif ratio >= 1.2:
            out.append(
                _f(
                    "cpu",
                    1,
                    "Run queue backed up",
                    f"load {load[0]:.1f} on {cores} cores = {ratio:.1f}x (threshold 1.2x)",
                    "",
                    ratio,
                    1.2,
                )
            )
    p10 = ((d.get("psi") or {}).get("some") or {}).get("avg10", 0.0)
    if p10 >= 20:
        sev = 2 if p10 >= 40 else 1
        out.append(
            _f(
                "cpu",
                sev,
                "CPU pressure stalls",
                f"tasks stalled waiting for CPU {p10:.0f}% of the last 10s (threshold 20%)",
                "",
                p10,
                20,
            )
        )
    freq = d.get("freq") or {}
    if (
        freq.get("avg_mhz")
        and freq.get("max_mhz")
        and total >= 50
        and freq["avg_mhz"] / freq["max_mhz"] < 0.5
    ):
        out.append(
            _f(
                "cpu",
                1,
                "CPU clock below half of max under load: possible throttling",
                f"avg {freq['avg_mhz']:.0f} MHz vs max {freq['max_mhz']:.0f} MHz at {total:.0f}% busy",
                "Check temps_stats and power_stats (power profile, thermals).",
                1.2,
                1.0,
            )
        )
    groups = d.get("groups") or []
    if groups:
        g = groups[0]
        if g["cpu_pct"] >= 80:
            share = 100.0 * g["cpu_pct"] / (100.0 * cores)
            sev = 2 if share >= 50 else 1
            extra = f", {g['count']} processes" if g.get("count", 1) > 1 else ""
            out.append(
                _f(
                    "cpu",
                    sev,
                    f"{g['name']} is using {g['cpu_pct']:.0f}% CPU",
                    f"{g['name']} {g['cpu_pct']:.0f}% of one core{extra} (threshold 80%), {share:.0f}% of total capacity",
                    f"Suggestion: check whether {g['name']} is expected to be busy; I will not kill it.",
                    g["cpu_pct"],
                    80,
                )
            )
    ctx = d.get("ctx_per_s", 0.0)
    if ctx >= 250000:
        out.append(
            _f(
                "cpu",
                1,
                "Very high context-switch rate",
                f"{ctx:,.0f}/s (threshold 250,000/s)",
                "",
                ctx,
                250000,
            )
        )
    return out


def memory_findings(d: dict) -> list[Finding]:
    out: list[Finding] = []
    avail = d.get("avail_pct", 100.0)
    if avail < 5:
        out.append(
            _f(
                "memory",
                2,
                "Memory nearly exhausted",
                f"only {avail:.1f}% available (threshold 5%)",
                "Close the biggest apps listed.",
                5 / max(avail, 0.5),
                1,
            )
        )
    elif avail < 10:
        out.append(
            _f(
                "memory",
                1,
                "Memory running low",
                f"only {avail:.1f}% available (threshold 10%)",
                "Close the biggest apps listed.",
                10 / max(avail, 1),
                1,
            )
        )
    swap = d.get("swap_used_pct", 0.0)
    act = d.get("swap_activity") or {}
    rate = (act.get("pswpin_s") or 0) + (act.get("pswpout_s") or 0)
    if (swap >= 60 and avail < 25) or (rate >= 500 and avail < 30):
        sev = 2 if swap >= 85 or rate >= 2000 else 1
        out.append(
            _f(
                "memory",
                sev,
                "Heavy swap use",
                f"swap {swap:.0f}% used, {rate:.0f} pages/s swapped, {avail:.0f}% RAM available (threshold: swap 60% or 500 pages/s while under 25-30% free)",
                "Memory pressure is pushing data to swap; close big apps.",
                max(swap / 60, rate / 500),
                1,
            )
        )
    p10 = ((d.get("psi") or {}).get("some") or {}).get("avg10", 0.0)
    if p10 >= 10:
        sev = 2 if p10 >= 25 else 1
        out.append(
            _f(
                "memory",
                sev,
                "Memory pressure stalls",
                f"tasks stalled on memory {p10:.0f}% of the last 10s (threshold 10%)",
                "",
                p10,
                10,
            )
        )
    kills = d.get("oom_kills") or []
    if kills:
        out.append(
            _f(
                "memory",
                1,
                f"OOM killer fired {len(kills)}x in 7 days",
                f"latest: {kills[-1]}",
                "Programs are being killed for lack of memory.",
                len(kills),
                1,
            )
        )
    total = d.get("total_kb") or 0
    groups = d.get("groups") or []
    if groups and total:
        g = groups[0]
        share = 100.0 * g["rss_kb"] / total
        if share >= 30:
            out.append(
                _f(
                    "memory",
                    1,
                    f"{g['name']} holds {_gb(g['rss_kb']):.1f} GB of RAM",
                    f"{g['name']} {share:.0f}% of RAM across {g['count']} processes (threshold 30%)",
                    f"Suggestion: restart or close {g['name']}.",
                    share,
                    30,
                )
            )
    slab = d.get("slab_pct", 0.0)
    if slab >= 15:
        out.append(
            _f(
                "memory",
                1,
                "Kernel slab cache unusually large",
                f"slab {slab:.0f}% of RAM (threshold 15%)",
                "",
                slab,
                15,
            )
        )
    return out


def gpu_findings(d: dict) -> list[Finding]:
    out: list[Finding] = []
    for g in d.get("gpus") or []:
        who = f"{g.get('vendor', '')} {g.get('name', 'GPU')}".strip()
        t, slow = g.get("temp_c"), g.get("temp_slow_c")
        if t is not None:
            limit = (slow - 3) if slow else 90
            if t >= limit:
                out.append(
                    _f(
                        "gpu",
                        2,
                        "GPU very hot",
                        f"{who} {t} C vs slowdown threshold {slow or 90} C",
                        "Check airflow; lower GPU load.",
                        t,
                        limit,
                    )
                )
            elif t >= 85:
                out.append(
                    _f(
                        "gpu",
                        1,
                        "GPU running hot",
                        f"{who} {t} C (threshold 85 C)",
                        "",
                        t,
                        85,
                    )
                )
        used, total = g.get("vram_used_mb"), g.get("vram_total_mb")
        if used is not None and total:
            pct = 100.0 * used / total
            if pct >= 85:
                sev = 2 if pct >= 95 else 1
                out.append(
                    _f(
                        "gpu",
                        sev,
                        "VRAM almost full",
                        f"{used} of {total} MiB used ({pct:.0f}%, threshold 85%)",
                        "Close GPU-heavy apps listed.",
                        pct,
                        85,
                    )
                )
        thermal = [
            r
            for r in g.get("throttle") or []
            if "thermal" in r or r == "hw_slowdown" or "brake" in r
        ]
        if thermal:
            out.append(
                _f(
                    "gpu",
                    2,
                    "GPU clocks throttled by heat/power",
                    f"{who}: {', '.join(thermal)}",
                    "Improve cooling or reduce load.",
                    1.3,
                    1,
                )
            )
        elif g.get("throttle"):
            out.append(
                _f("gpu", 0, "GPU clocks capped", f"{who}: {', '.join(g['throttle'])}")
            )
        if (g.get("util_pct") or 0) >= 95:
            out.append(
                _f("gpu", 0, "GPU fully busy", f"{who} {g['util_pct']}% utilisation")
            )
    return out


def _sensor_limits(label: str) -> tuple[float, float]:
    low = label.lower()
    if "nvme" in low:
        return 75.0, 85.0
    if any(k in low for k in ("wifi", "mt79", "iwl", "wlan")):
        return 80.0, 95.0
    return 90.0, 97.0


def temps_findings(d: dict) -> list[Finding]:
    out: list[Finding] = []
    for s in d.get("sensors") or []:
        t, crit, high = s["temp_c"], s.get("crit_c"), s.get("high_c")
        warn, hot = _sensor_limits(s["label"])
        sev = 0
        limit = warn
        if crit is not None:
            if t >= crit - 3:
                sev, limit = 2, crit - 3
            elif t >= crit - 8:
                sev, limit = 1, crit - 8
        if sev == 0 and high is not None and t >= high:
            sev, limit = 1, high
        if sev == 0 and t >= hot:
            sev, limit = 2, hot
        elif sev == 0 and t >= warn:
            sev, limit = 1, warn
        if sev:
            lim = f", critical at {crit:.0f} C" if crit else ""
            out.append(
                _f(
                    "temps",
                    sev,
                    f"{s['label']} is hot",
                    f"{s['label']} {t:.0f} C (threshold {limit:.0f} C{lim})",
                    "Check vents/dust and what is loading the machine.",
                    t,
                    limit,
                )
            )
    out = sorted(out, key=lambda f: -f.score)[:3]  # one hot die reads on many sensors
    cool = d.get("cooling_active") or []
    for c in cool:
        if c["type"] in ("Processor", "intel_powerclamp"):
            out.append(
                _f(
                    "temps",
                    1,
                    "Kernel is throttling the CPU for heat",
                    f"cooling device {c['type']} at state {c['state']}/{c.get('max')}",
                    "",
                    1.2,
                    1,
                )
            )
            break
    return out


def disk_findings(d: dict) -> list[Finding]:
    out: list[Finding] = []
    for m in d.get("mounts") or []:
        pct = m.get("used_pct", 0.0)
        if pct >= 90:
            out.append(
                _f(
                    "disk",
                    2 if pct >= 95 else 1,
                    f"{m['mount']} is nearly full",
                    f"{pct:.0f}% used (threshold 90%)",
                    "Free space or move large files.",
                    pct,
                    90,
                )
            )
        ino = m.get("inode_pct")
        if ino is not None and ino >= 90:
            out.append(
                _f(
                    "disk",
                    2 if ino >= 95 else 1,
                    f"{m['mount']} is out of inodes",
                    f"{ino:.0f}% inodes used (threshold 90%)",
                    "",
                    ino,
                    90,
                )
            )
        if m.get("ro"):
            out.append(
                _f(
                    "disk",
                    1,
                    f"{m['mount']} mounted read-only",
                    "mount options contain ro",
                    "A read-only data mount can mean filesystem errors.",
                )
            )
    for name, r in (d.get("io") or {}).items():
        if r.get("util_pct", 0) >= 90:
            out.append(
                _f(
                    "disk",
                    1,
                    f"{name} is saturated",
                    f"{r['util_pct']:.0f}% busy, {r.get('iops', 0):.0f} IOPS (threshold 90%)",
                    "Find the writer in process_stats sort_by io.",
                    r["util_pct"],
                    90,
                )
            )
        if r.get("await_ms", 0) >= 50 and r.get("iops", 0) > 1:
            out.append(
                _f(
                    "disk",
                    2 if r["await_ms"] >= 200 else 1,
                    f"{name} is slow to respond",
                    f"{r['await_ms']:.0f} ms per request (threshold 50 ms)",
                    "",
                    r["await_ms"],
                    50,
                )
            )
    for name, verdict in (d.get("smart") or {}).items():
        if verdict == "FAILED":
            out.append(
                _f(
                    "disk",
                    2,
                    f"{name} SMART health FAILED",
                    f"smartctl reports failure on {name}",
                    "Back up immediately.",
                )
            )
    return out


def network_findings(d: dict) -> list[Finding]:
    out: list[Finding] = []
    ifaces = d.get("ifaces") or []
    for i in ifaces:
        wifi = i.get("wifi") or {}
        sig = wifi.get("signal_dbm")
        if sig is not None:
            if sig <= -85:
                out.append(
                    _f(
                        "network",
                        2,
                        "Wifi signal very weak",
                        f"{i['name']} {sig} dBm (threshold -85 dBm)",
                        "Move closer to the router.",
                        abs(sig),
                        85,
                    )
                )
            elif sig <= -75:
                out.append(
                    _f(
                        "network",
                        1,
                        "Wifi signal weak",
                        f"{i['name']} {sig} dBm (threshold -75 dBm)",
                        "Move closer to the router or use 5 GHz.",
                        abs(sig),
                        75,
                    )
                )
        rx = i.get("rx_pkts") or 0
        for kind in ("drop", "err"):
            n = i.get(f"rx_{kind}") or 0
            if rx >= 1000 and n / rx >= 0.01:
                out.append(
                    _f(
                        "network",
                        1,
                        f"{i['name']} receive {kind}s",
                        f"{n} of {rx} packets ({100 * n / rx:.1f}%, threshold 1%) since boot",
                        "",
                        100 * n / rx,
                        1,
                    )
                )
        tx = wifi.get("tx_packets") or 0
        if tx >= 1000 and (wifi.get("tx_retries") or 0) / tx >= 0.25:
            r = 100 * wifi["tx_retries"] / tx
            out.append(
                _f(
                    "network",
                    1,
                    "Many wifi retransmissions",
                    f"{i['name']} {r:.0f}% of transmitted packets were retries (threshold 25%)",
                    "Interference or weak signal.",
                    r,
                    25,
                )
            )
    gw = d.get("gateway") or {}
    if gw.get("loss_pct", 0) > 0:
        sev = 2 if gw["loss_pct"] >= 10 else 1
        out.append(
            _f(
                "network",
                sev,
                "Packet loss to the router",
                f"{gw['loss_pct']:.0f}% packet loss to {gw.get('ip', 'gateway')} (threshold 0%)",
                "The problem is local (wifi/router), not the internet.",
                gw["loss_pct"] + 1,
                1,
            )
        )
    if gw.get("avg_ms", 0) >= 100:
        out.append(
            _f(
                "network",
                1,
                "High latency to the router",
                f"{gw['avg_ms']:.0f} ms average (threshold 100 ms)",
                "",
                gw["avg_ms"],
                100,
            )
        )
    if ifaces and all(i.get("state") not in (None, "up", "unknown") for i in ifaces):
        out.append(
            _f(
                "network",
                1,
                "No network interface is up",
                ", ".join(f"{i['name']}={i.get('state')}" for i in ifaces),
            )
        )
    return out


def power_findings(d: dict) -> list[Finding]:
    out: list[Finding] = []
    b = d.get("battery")
    if not b:
        return out
    on_batt = b.get("status") == "Discharging"
    pct = b.get("percent")
    if on_batt and pct is not None and pct <= 20:
        sev = 2 if pct <= 10 else 1
        left = (
            f", about {b['hours_left'] * 60:.0f} minutes left"
            if b.get("hours_left")
            else ""
        )
        out.append(
            _f(
                "power",
                sev,
                "Battery low",
                f"{pct}% left{left} (threshold 20%)",
                "Plug in.",
                20 / max(pct, 1),
                1,
            )
        )
    w = b.get("watts")
    if on_batt and w is not None and w >= 20:
        out.append(
            _f(
                "power",
                2 if w >= 30 else 1,
                "High power draw on battery",
                f"{w:.0f} W (threshold 20 W)",
                "Heavy CPU/GPU load or bright screen; see process_stats.",
                w,
                20,
            )
        )
    h = b.get("health_pct")
    if h is not None and h <= 70:
        out.append(
            _f(
                "power",
                1,
                "Battery health degraded",
                f"holds {h:.0f}% of design capacity (threshold 70%), {b.get('cycles')} cycles",
                "Short runtime is expected; consider replacing the battery.",
            )
        )
    if d.get("profile") == "performance" and on_batt:
        out.append(
            _f(
                "power",
                1,
                "Performance power profile on battery",
                "profile performance while discharging",
                "Suggestion: switch to balanced or power-saver (I will not change it).",
            )
        )
    return out


def process_findings(d: dict) -> list[Finding]:
    out: list[Finding] = []
    z = d.get("zombies") or 0
    if z >= 5:
        out.append(
            _f(
                "process",
                1,
                f"{z} zombie processes",
                f"{z} zombies (threshold 5)",
                "A parent is not reaping its children.",
                z,
                5,
            )
        )
    ds = d.get("dstate") or 0
    if ds >= 3:
        names = ", ".join(d.get("dstate_names") or [])
        out.append(
            _f(
                "process",
                1,
                f"{ds} processes stuck in uninterruptible I/O wait",
                f"{ds} in D state (threshold 3){': ' + names if names else ''}",
                "Usually a stalled disk or network mount.",
                ds,
                3,
            )
        )
    return out


# ---------------------------------------------------------------- sampling


def scan_procs(proc: Path | None = None, with_ctx: bool = False) -> dict[int, dict]:
    """Snapshot every readable process (stat + io where permitted)."""
    root = proc or PROC
    procs: dict[int, dict] = {}
    try:
        names = [n for n in os.listdir(root) if n.isdigit()]
    except OSError:
        return procs
    for n in names:
        base = root / n
        try:
            rec = parse_pid_stat((base / "stat").read_text())
        except (OSError, ValueError):
            continue
        if rec is None:
            continue
        try:
            rec["uid"] = os.stat(base).st_uid
        except OSError:
            rec["uid"] = None
        rec["read_bytes"] = rec["write_bytes"] = None
        try:
            for ln in (base / "io").read_text().splitlines():
                k, _, v = ln.partition(":")
                if k == "read_bytes":
                    rec["read_bytes"] = int(v)
                elif k == "write_bytes":
                    rec["write_bytes"] = int(v)
        except (OSError, ValueError):
            pass
        if with_ctx:
            rec["ctx"] = parse_status_extra(_read(base / "status")).get("ctx")
        procs[rec["pid"]] = rec
    return procs


def _read_rapl_domains() -> dict[str, tuple[int, int]]:
    """Package + sub-domain (core/uncore/dram) energy counters, if readable."""
    out: dict[str, tuple[int, int]] = {}
    base = RAPL_ROOT.parent
    dirs = [RAPL_ROOT, *sorted(base.glob(f"{RAPL_ROOT.name}:*"))]
    for d in dirs:
        e = _read_int(d / "energy_uj")
        if e is None:
            continue
        name = _read(d / "name").strip() or d.name
        out[name] = (e, _read_int(d / "max_energy_range_uj") or 0)
    return out


def rapl_watts(b: dict | None, a: dict | None, dt: float) -> dict[str, float]:
    """Watts per RAPL domain from two counter reads (handles wrap-around)."""
    watts: dict[str, float] = {}
    for name, (e1, mx) in (a or {}).items():
        if not b or name not in b:
            continue
        delta = e1 - b[name][0]
        if delta < 0 and mx:
            delta += mx
        if delta >= 0:
            watts[name] = delta / 1e6 / dt
    return watts


def _read_rapl() -> tuple[int, int] | None:
    e = _read_int(RAPL_ROOT / "energy_uj")
    if e is None:
        return None
    return e, _read_int(RAPL_ROOT / "max_energy_range_uj") or 0


def _read_sample(want: set[str]) -> dict:
    snap: dict = {"t": time.monotonic()}
    if "stat" in want:
        snap["stat"] = _read(PROC / "stat")
    if "diskstats" in want:
        snap["diskstats"] = _read(PROC / "diskstats")
    if "netdev" in want:
        snap["netdev"] = _read(PROC / "net" / "dev")
    if "vmstat" in want:
        snap["vmstat"] = _read(PROC / "vmstat")
    if "rapl" in want:
        snap["rapl"] = _read_rapl()
    if "rapl" in want:
        snap["rapl_domains"] = _read_rapl_domains()
    if "procs" in want or "procs_ctx" in want:
        snap["procs"] = scan_procs(with_ctx="procs_ctx" in want)
    return snap


async def take_sample(want: set[str], window: float | None = None) -> dict:
    """Two reads `window` seconds apart (default SAMPLE_WINDOW)."""
    w = SAMPLE_WINDOW if window is None else window
    before = await asyncio.to_thread(_read_sample, want)
    await asyncio.sleep(max(0.0, min(w, 1.0)))
    after = await asyncio.to_thread(_read_sample, want)
    return {"before": before, "after": after, "dt": max(after["t"] - before["t"], 1e-3)}


async def safe_collect(
    area: str, fn: Callable[..., Awaitable[dict]], /, *args, **kw
) -> dict:
    """Run a collector; any failure becomes {'error': ...} (never raises)."""
    try:
        return await fn(*args, **kw)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:200], "area": area}


def _label_top(rows: list[dict], n: int) -> None:
    """Name interpreter processes by script (python agent.py), top n rows only."""
    for r in rows[:n]:
        if r["name"] in ("python", "python3", "node", "java", "bash", "sh", "electron"):
            label = short_cmdline(_read(PROC / str(r["pid"]) / "cmdline"))
            if label:
                r["name"] = label


def enrich_rows(rows: list[dict], *, status: bool = False, smaps: bool = False) -> None:
    """Add cmd, runtime and (optionally) status/smaps memory detail, in place."""
    up = _num(_read(PROC / "uptime").split()[0]) if _read(PROC / "uptime") else None
    for r in rows:
        base = PROC / str(r["pid"])
        if "cmd" not in r:
            raw = _read(base / "cmdline")
            args = [a for a in raw.split("\0") if a]
            r["cmd"] = " ".join(args)[:160] if args else f"[{r['name']}]"
        if up is not None and r.get("start_ticks") is not None:
            r["age_s"] = max(0.0, up - r["start_ticks"] / CLK_TCK)
        if status:
            for k, v in parse_status_extra(_read(base / "status")).items():
                if k != "ctx":
                    r.setdefault(k, v)
        if smaps:
            sm = parse_smaps_rollup(_read(base / "smaps_rollup"))
            if sm:
                r.update(sm)
            elif "shared_kb" not in r:
                parts = _read(base / "statm").split()
                if len(parts) > 2 and parts[2].isdigit():
                    r["shared_kb"] = int(parts[2]) * PAGE_KB


def process_tables(
    rows: list[dict],
    key: str,
    *,
    n: int = 15,
    groups_n: int = 8,
    status: bool = False,
    smaps: bool = False,
    member_cap: int = 80,
) -> tuple[list[dict], list[dict]]:
    """Top-n individual rows + program groups (with enriched members)."""
    ranked = sorted(rows, key=lambda r: -(r.get(key) or 0))
    _label_top(ranked, 8)
    groups = group_procs(ranked, key)[:groups_n]
    by_pid = {r["pid"]: r for r in ranked}
    targets: dict[int, dict] = {r["pid"]: r for r in ranked[:n]}
    for g in groups:
        for m in g["members"]:
            targets[m["pid"]] = m
    if smaps:  # group totals need PSS for every member of the biggest groups
        budget = member_cap
        for g in groups[:5]:
            for pid in g["pids"][:budget]:
                targets[pid] = by_pid[pid]
            budget = max(0, budget - len(g["pids"]))
    enrich_rows(list(targets.values()), status=status, smaps=smaps)
    if smaps:
        for g in groups:
            members = [by_pid[p] for p in g["pids"] if "pss_kb" in by_pid[p]]
            g["pss_kb"] = sum(m["pss_kb"] for m in members) if members else None
            g["swap_kb"] = sum(by_pid[p].get("swap_kb", 0) for p in g["pids"])
    return ranked[:n], groups


# ---------------------------------------------------------------- collectors


async def collect_cpu(sample: dict | None = None) -> dict:
    s = sample or await take_sample({"stat", "procs", "rapl"})
    b, a, dt = s["before"], s["after"], s["dt"]
    notes: list[str] = []
    sa, sb = parse_proc_stat(b.get("stat", "")), parse_proc_stat(a.get("stat", ""))
    if "cpu" not in sb:
        notes.append("/proc/stat unreadable")
    util = cpu_utilisation(sa, sb)
    fa, fb = parse_cpu_fields(b.get("stat", "")), parse_cpu_fields(a.get("stat", ""))
    breakdown = (
        cpu_breakdown(fa["cpu"], fb["cpu"]) if "cpu" in fa and "cpu" in fb else {}
    )
    core_keys = sorted(
        (k for k in fb if k != "cpu" and k in fa), key=lambda k: int(k[3:])
    )
    cores_breakdown = [cpu_breakdown(fa[k], fb[k]) for k in core_keys]
    ca, cb = (
        parse_stat_counters(b.get("stat", "")),
        parse_stat_counters(a.get("stat", "")),
    )
    counter_rates = {k: (cb[k] - ca[k]) / dt for k in cb if k in ca}
    loadtxt = _read(PROC / "loadavg").split()
    load = parse_loadavg(" ".join(loadtxt))
    freq, extra = await asyncio.to_thread(_read_cpu_freq)
    rows = proc_rates(b.get("procs", {}), a.get("procs", {}), dt)
    top, groups = await asyncio.to_thread(process_tables, rows, "cpu_pct", n=15)
    rapl = rapl_watts(b.get("rapl_domains"), a.get("rapl_domains"), dt)
    rapl_w = next((w for k, w in rapl.items() if k.startswith("package")), None)
    if rapl_w is None:
        notes.append("package power (RAPL) not readable")
    if not freq.get("cur_mhz"):
        notes.append("cpufreq not exposed")
    info = await asyncio.to_thread(_read_cpu_info)
    return {
        "total_pct": util["total"],
        "iowait": util["iowait"],
        "steal": util["steal"],
        "cores_pct": util["cores"],
        "cores_count": len(util["cores"]) or os.cpu_count() or 1,
        "breakdown": breakdown,
        "cores_breakdown": cores_breakdown,
        "load": load,
        "load_tasks": loadtxt[3] if len(loadtxt) > 3 else "",
        "ctx_per_s": counter_rates.get("ctxt", 0.0),
        "rates": counter_rates,
        "running": sb.get("procs_running"),
        "blocked": sb.get("procs_blocked"),
        "freq": freq,
        "governor": extra.get("governor"),
        "epp": extra.get("epp"),
        "driver": extra.get("driver"),
        "throttle": extra.get("throttle", {"core": 0, "package": 0}),
        "cpu_extra": extra,
        "rapl": rapl,
        "rapl_w": rapl_w,
        "psi": parse_psi(_read(PROC / "pressure" / "cpu")),
        "groups": groups,
        "procs": top,
        "model": info.get("model", ""),
        "cpuinfo": info,
        "uptime_s": _num(_read(PROC / "uptime").split()[0])
        if _read(PROC / "uptime")
        else None,
        "notes": notes,
    }


def _read_cpu_info() -> dict:
    """Model, topology, cache and pstate/SMT/turbo switches."""
    info: dict = {}
    sockets: set[str] = set()
    threads = 0
    for ln in _read(PROC / "cpuinfo").splitlines():
        k, _, v = ln.partition(":")
        k, v = k.strip(), v.strip()
        if k == "model name" and "model" not in info:
            info["model"] = v
        elif k == "processor":
            threads += 1
        elif k == "physical id":
            sockets.add(v)
        elif k == "cpu cores" and "cores_per_socket" not in info:
            info["cores_per_socket"] = v
        elif k == "cache size" and "cache" not in info:
            info["cache"] = v
    info["threads"] = threads
    info["sockets"] = len(sockets) or 1
    pst = SYS_CPU / "intel_pstate"
    for key in ("status", "no_turbo", "min_perf_pct", "max_perf_pct", "turbo_pct"):
        v = _read(pst / key).strip()
        if v:
            info[f"pstate_{key}"] = v
    smt = _read(SYS_CPU / "smt" / "active").strip()
    if smt:
        info["smt_active"] = smt
    return info


def _read_cpu_freq() -> tuple[dict, dict]:
    cur: list[float] = []
    core_thr = 0
    pkg_thr = 0
    try:
        cpus = sorted(
            (p for p in SYS_CPU.iterdir() if re.fullmatch(r"cpu\d+", p.name)),
            key=lambda p: int(p.name[3:]),
        )
    except OSError:
        cpus = []
    for c in cpus:
        v = _read_num(c / "cpufreq" / "scaling_cur_freq")
        if v is not None:
            cur.append(v / 1000)
        core_thr += _read_int(c / "thermal_throttle" / "core_throttle_count") or 0
        pkg_thr = max(
            pkg_thr, _read_int(c / "thermal_throttle" / "package_throttle_count") or 0
        )
    first = cpus[0] / "cpufreq" if cpus else SYS_CPU / "cpu0" / "cpufreq"
    mx = _read_num(first / "cpuinfo_max_freq")
    mn = _read_num(first / "cpuinfo_min_freq")
    freq = {
        "cur_mhz": cur,
        "avg_mhz": sum(cur) / len(cur) if cur else None,
        "max_mhz": mx / 1000 if mx else None,
        "min_mhz": mn / 1000 if mn else None,
    }
    extra = {
        "governor": _read(first / "scaling_governor").strip() or None,
        "epp": _read(first / "energy_performance_preference").strip() or None,
        "driver": _read(first / "scaling_driver").strip() or None,
        "scaling_min_mhz": (_read_num(first / "scaling_min_freq") or 0) / 1000,
        "scaling_max_mhz": (_read_num(first / "scaling_max_freq") or 0) / 1000,
        "base_mhz": (_read_num(first / "base_frequency") or 0) / 1000,
        "available_governors": _read(first / "scaling_available_governors").strip(),
        "throttle": {"core": core_thr, "package": pkg_thr},
    }
    return freq, extra


async def _journal_oom() -> tuple[list[str], str]:
    rc, out, _err = await run_cmd(
        "journalctl",
        "-k",
        "--no-pager",
        "--since",
        "7 days ago",
        "-g",
        "Killed process|oom-kill:",
        timeout=8.0,
    )
    kills: list[str] = []
    note = ""
    if rc in (0, 1):  # journalctl -g exits 1 when nothing matched
        kills += parse_oom_journal(out or "")
    else:
        note = "kernel journal not readable (no OOM history)"
    rc2, out2, _ = await run_cmd(
        "journalctl",
        "--no-pager",
        "--since",
        "7 days ago",
        "-u",
        "systemd-oomd",
        "-g",
        "Killed",
        timeout=8.0,
    )
    if rc2 in (0, 1):
        kills += parse_oom_journal(out2 or "")
    return kills, note


async def collect_memory(sample: dict | None = None) -> dict:
    s = sample or await take_sample({"vmstat", "procs"})
    b, a, dt = s["before"], s["after"], s["dt"]
    notes: list[str] = []
    mem = parse_meminfo(_read(PROC / "meminfo"))
    if not mem:
        notes.append("/proc/meminfo unreadable")
    summ = memory_summary(mem)
    swaps = parse_swaps(_read(PROC / "swaps"))
    zram = []
    try:
        for z in sorted(SYS_BLOCK.glob("zram*")):
            st = parse_zram_mm_stat(
                _read(z / "mm_stat"), _read_int(z / "disksize") or 0
            )
            if st:
                st["name"] = z.name
                zram.append(st)
    except OSError:
        pass
    v0, v1 = parse_vmstat(b.get("vmstat", "")), parse_vmstat(a.get("vmstat", ""))
    activity = {
        "pswpin_s": max(0, v1.get("pswpin", 0) - v0.get("pswpin", 0)) / dt,
        "pswpout_s": max(0, v1.get("pswpout", 0) - v0.get("pswpout", 0)) / dt,
    }
    vm_keys = [
        k for k in v1
        if k.startswith(("pgfault", "pgmajfault", "pgpg", "pswp", "pgscan", "pgsteal", "allocstall", "workingset_refault", "oom_kill", "compact_stall", "thp_fault_alloc", "pgalloc_normal", "pgfree"))
    ]  # fmt: skip
    vmstat = {
        k: {"total": v1[k], "per_s": max(0, v1[k] - v0.get(k, v1[k])) / dt}
        for k in sorted(vm_keys)
    }
    rows = proc_rates(b.get("procs", {}), a.get("procs", {}), dt)
    top, groups = await asyncio.to_thread(
        process_tables, rows, "rss_kb", n=15, status=True, smaps=True
    )
    kills, jnote = await _journal_oom()
    if jnote:
        notes.append(jnote)
    return {
        **summ,
        "meminfo": mem,
        "vmstat": vmstat,
        "swaps": swaps,
        "zram": zram,
        "swap_activity": activity,
        "oom_total": v1.get("oom_kill"),
        "oom_kills": kills,
        "psi": parse_psi(_read(PROC / "pressure" / "memory")),
        "groups": groups,
        "procs": top,
        "notes": notes,
    }


def _scan_drm_clients() -> dict:
    """Own processes holding a DRM render/card fd -> fdinfo client stats."""
    out: dict = {}
    try:
        pids = [n for n in os.listdir(PROC) if n.isdigit()]
    except OSError:
        return out
    for n in pids:
        fd_dir = PROC / n / "fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(fd_dir / fd)
            except OSError:
                continue
            if not target.startswith("/dev/dri/"):
                continue
            info = parse_fdinfo(_read(PROC / n / "fdinfo" / fd))
            if info:
                info["pid"] = int(n)
                out[(int(n), info["client"])] = info
    return out


def _proc_identity(pid: int) -> dict:
    base = PROC / str(pid)
    try:
        uid = os.stat(base).st_uid
    except OSError:
        uid = None
    raw = _read(base / "cmdline")
    args = [x for x in raw.split("\0") if x]
    return {
        "user": _user(uid),
        "cmd": " ".join(args)[:160] if args else "",
        "comm": _read(base / "comm").strip(),
    }


def read_displays(root: Path) -> list[str]:
    out = []
    try:
        for c in sorted(root.glob("card*-*")):
            st = _read(c / "status").strip()
            modes = _read(c / "modes").splitlines()
            out.append(
                f"{c.name}: {st or '?'}"
                + (f" {modes[0]}" if st == "connected" and modes else "")
            )
    except OSError:
        pass
    return out


async def collect_gpu(sample: dict | None = None) -> dict:
    notes: list[str] = []
    gpus: list[dict] = []
    rc, out, err = await run_cmd("nvidia-smi", "-q", "-x", timeout=8.0)
    nvidia_ok = False
    if rc == 0 and out:
        gpus = parse_nvidia_xml(out)
        nvidia_ok = bool(gpus)
        if not gpus:
            notes.append("nvidia-smi returned no GPUs")
    elif rc == 127:
        notes.append("nvidia-smi not installed")
    else:
        notes.append(f"nvidia-smi failed: {(err or '').strip()[:80]}")
    if nvidia_ok:
        rc, pm, _ = await run_cmd(
            "nvidia-smi", "pmon", "-c", "1", "-s", "um", timeout=8.0
        )
        pmon = parse_pmon(pm or "") if rc == 0 else []
        if rc != 0:
            notes.append(
                "nvidia-smi pmon unavailable; per-process sm/enc/dec use missing"
            )
        by_pid = {p["pid"]: p for p in pmon}
        for p in gpus[0]["procs"]:
            m = by_pid.pop(p["pid"], None)
            if m:
                p.update(sm=m["sm"], mem=m["mem"], enc=m["enc"], dec=m["dec"])
        for m in by_pid.values():
            gpus[0]["procs"].append(
                {"pid": m["pid"], "name": m["name"], "type": m["type"], "mem_mb": None,
                 "sm": m["sm"], "mem": m["mem"], "enc": m["enc"], "dec": m["dec"]}
            )  # fmt: skip
    drm = await asyncio.to_thread(
        read_drm_gpus, DRM_ROOT, ("NVIDIA",) if nvidia_ok else ()
    )
    for g in drm:
        if g["vendor"] == "NVIDIA":
            notes.append("NVIDIA GPU present but nvidia-smi unavailable")
            continue
        if g["vendor"] == "Intel":
            notes.append(
                "Intel GPU total load needs intel_gpu_top (root); per-process engine use comes from fdinfo"
            )
        gpus.append(g)
    if any(g["vendor"] in ("Intel", "AMD") for g in gpus):
        t0 = time.monotonic()
        before = await asyncio.to_thread(_scan_drm_clients)
        await asyncio.sleep(max(0.0, min(SAMPLE_WINDOW, 0.5)))
        after = await asyncio.to_thread(_scan_drm_clients)
        rows = drm_client_rates(before, after, max(time.monotonic() - t0, 1e-3))
        drivers = {"i915": "Intel", "xe": "Intel", "amdgpu": "AMD"}
        merged: dict[tuple, dict] = {}
        for r in (
            rows
        ):  # one process may hold several DRM clients: sum memory, keep busiest engine
            m = merged.setdefault(
                (r["pid"], r["driver"]),
                {**r, "mem_kb": 0, "util_pct": 0.0, "engine": ""},
            )
            m["mem_kb"] += r["mem_kb"]
            if r["util_pct"] >= m["util_pct"] and (r["engine"] or not m["engine"]):
                m["util_pct"], m["engine"] = r["util_pct"], r["engine"]
        rows = sorted(merged.values(), key=lambda r: (-r["util_pct"], -r["mem_kb"]))
        for r in rows:
            vendor = drivers.get(r["driver"])
            gpu = next((g for g in gpus if g["vendor"] == vendor), None)
            if gpu is None:
                continue
            gpu["procs"].append(
                {"pid": r["pid"], "name": _read(PROC / str(r["pid"]) / "comm").strip() or "?",
                 "type": "drm", "mem_mb": r["mem_kb"] // 1024, "util_pct": r["util_pct"],
                 "engine": r["engine"]}
            )  # fmt: skip
        if not rows:
            notes.append(
                "no readable DRM clients (fdinfo only exposes your own processes)"
            )
    for g in gpus:
        for pr in g["procs"]:
            pr.update(await asyncio.to_thread(_proc_identity, pr["pid"]))
        g["procs"].sort(
            key=lambda x: (
                -(x.get("sm") or x.get("util_pct") or 0),
                -(x.get("mem_mb") or 0),
            )
        )
        if g["vendor"] == "Intel":
            card = DRM_ROOT / g.get("card", "card0")
            g["extra"] = [
                (lbl, v)
                for lbl, v in (
                    ("actual freq MHz", _read(card / "gt_act_freq_mhz").strip()),
                    ("min freq MHz", _read(card / "gt_min_freq_mhz").strip()),
                    ("boost freq MHz", _read(card / "gt_boost_freq_mhz").strip()),
                    (
                        "RC6 residency ms",
                        _read(card / "gt" / "gt0" / "rc6_residency_ms").strip(),
                    ),
                )
                if v
            ]
    return {
        "gpus": gpus,
        "displays": await asyncio.to_thread(read_displays, DRM_ROOT),
        "notes": list(dict.fromkeys(notes)),
    }


async def _nvidia_temp_sensor() -> tuple[list[dict], list[dict]]:
    rc, out, _ = await run_cmd(
        "nvidia-smi",
        "--query-gpu=name,temperature.gpu,fan.speed",
        "--format=csv,noheader,nounits",
        timeout=5.0,
    )
    sensors: list[dict] = []
    fans: list[dict] = []
    if rc != 0:
        return sensors, fans
    for ln in (out or "").splitlines():
        p = [x.strip() for x in ln.split(",")]
        if len(p) >= 2 and _num(p[1]) is not None:
            sensors.append(
                {
                    "label": f"nvidia:{p[0]}",
                    "temp_c": _num(p[1]),
                    "crit_c": None,
                    "high_c": None,
                }
            )
        if len(p) >= 3 and _num(p[2]) is not None:
            fans.append({"label": f"nvidia:{p[0]} fan", "rpm": None, "pct": _num(p[2])})
    return sensors, fans


async def collect_temps(sample: dict | None = None) -> dict:
    s = sample or await take_sample({"procs"})
    b, a, dt = s["before"], s["after"], s["dt"]
    data = await asyncio.to_thread(read_thermal, THERMAL_ROOT, HWMON_ROOT)
    notes: list[str] = []
    gs, gf = await _nvidia_temp_sensor()
    data["sensors"] += gs
    data["fans"] += gf
    if gs:
        data["max"] = max(data["sensors"], key=lambda x: x["temp_c"])
    if not data["sensors"]:
        notes.append("no thermal sensors readable")
    if not data["fans"]:
        notes.append("no fan sensor exposed by the kernel (EC-controlled fans)")
    data["throttle"] = (await asyncio.to_thread(_read_cpu_freq))[1].get("throttle", {})
    rows = proc_rates(b.get("procs", {}), a.get("procs", {}), dt)
    cpu_top, cpu_groups = await asyncio.to_thread(process_tables, rows, "cpu_pct", n=10)
    io_pool = [r for r in rows if r.get("read_bps") is not None]
    io_top, io_groups = await asyncio.to_thread(process_tables, io_pool, "io_bps", n=10)
    gpu_procs: list[dict] = []
    if gs:
        rc, out, _ = await run_cmd("nvidia-smi", "-q", "-x", timeout=8.0)
        if rc == 0:
            for g in parse_nvidia_xml(out or ""):
                gpu_procs += g["procs"]
    data["heaters"] = {
        "cpu_procs": cpu_top,
        "cpu_groups": cpu_groups,
        "io_groups": io_groups,
        "io_procs": io_top,
        "gpu_procs": gpu_procs,
    }
    data["notes"] = notes
    return data


async def _smart_one(dev: str) -> tuple[str, str]:
    rc, out, err = await run_cmd("smartctl", "-H", f"/dev/{dev}", timeout=6.0)
    if rc == 127:
        return dev, "smartctl not installed"
    return dev, parse_smart_health(f"{out or ''}\n{err or ''}")


def _block_info(name: str) -> dict:
    base = SYS_BLOCK / name
    size = _read_num(base / "size")
    sched = _read(base / "queue" / "scheduler").strip()
    m = re.search(r"\[([^\]]+)\]", sched)
    return {
        "model": _read(base / "device" / "model").strip(),
        "size_gb": size * 512 / 1e9 if size else None,
        "rotational": _read(base / "queue" / "rotational").strip(),
        "scheduler": m.group(1) if m else sched,
        "queue_depth": _read(base / "queue" / "nr_requests").strip(),
        "block_size": _read(base / "queue" / "logical_block_size").strip(),
    }


async def collect_disk(sample: dict | None = None) -> dict:
    s = sample or await take_sample({"diskstats", "procs"})
    b, a, dt = s["before"], s["after"], s["dt"]
    notes: list[str] = []
    try:
        whole: set[str] | None = {
            n for n in os.listdir(SYS_BLOCK) if not re.match(r"loop|ram|zram|sr\d", n)
        }
    except OSError:
        whole = None
    io = disk_rates(
        parse_diskstats(b.get("diskstats", ""), whole),
        parse_diskstats(a.get("diskstats", ""), whole),
        dt,
    )
    totals = parse_diskstats(a.get("diskstats", ""), whole)
    if not io:
        notes.append("no disk IO stats readable")
    mounts: list[dict] = []
    seen: set = set()
    for m in parse_mounts(_read(MOUNTS_FILE)):
        use = await asyncio.to_thread(mount_usage, m["mount"])
        if not use:
            continue
        key = use.pop("_id")
        if key in seen:  # btrfs subvolumes share one pool
            continue
        seen.add(key)
        mounts.append({**m, **use})
    if not mounts:
        notes.append("no mounts readable")
    phys = sorted(n for n in io if re.match(r"nvme\d+n\d+$|sd[a-z]+$", n))[:4]
    smart = dict(await asyncio.gather(*[_smart_one(d) for d in phys]))
    if smart and all(
        v in ("needs root", "unknown", "smartctl not installed") for v in smart.values()
    ):
        notes.append("SMART needs root or is unavailable; showing NVMe temps instead")
    th = await asyncio.to_thread(read_thermal, THERMAL_ROOT, HWMON_ROOT)
    drive_temps = [x for x in th["sensors"] if x["label"].startswith("nvme")]
    info = {n: await asyncio.to_thread(_block_info, n) for n in io}
    rows = proc_rates(b.get("procs", {}), a.get("procs", {}), dt)
    readable = [r for r in rows if r.get("read_bps") is not None]
    top, groups = await asyncio.to_thread(process_tables, readable, "io_bps", n=15)
    visible = sum(1 for r in rows if r.get("read_bps") is not None)
    if visible < len(rows):
        notes.append(
            f"per-process IO is only readable for your own processes ({visible} of {len(rows)})"
        )
    return {
        "mounts": mounts,
        "io": io,
        "io_totals": totals,
        "block_info": info,
        "smart": smart,
        "drive_temps": drive_temps,
        "psi": parse_psi(_read(PROC / "pressure" / "io")),
        "swaps": parse_swaps(_read(PROC / "swaps")),
        "groups": groups,
        "procs": top,
        "notes": notes,
    }


async def _ss_conns() -> tuple[list[dict], bool]:
    rc, out, _ = await run_cmd("ss", "-tinpH", "state", "established", timeout=5.0)
    return (parse_ss_conns(out or ""), True) if rc == 0 else ([], False)


def _read_iface_sys(name: str) -> dict:
    base = NET_ROOT / name
    return {
        "mtu": _read(base / "mtu").strip(),
        "duplex": _read(base / "duplex").strip(),
        "carrier": _read(base / "carrier").strip(),
        "type": _read(base / "type").strip(),
        "tx_queue_len": _read(base / "tx_queue_len").strip(),
    }


async def collect_network(sample: dict | None = None) -> dict:
    s = sample or await take_sample({"netdev"})
    b, a, dt = s["before"], s["after"], s["dt"]
    notes: list[str] = []
    # per-process bandwidth: two ss reads one short window apart
    t0 = time.monotonic()
    conns0, ss_ok = await _ss_conns()
    await asyncio.sleep(max(0.0, min(SAMPLE_WINDOW, 0.5)))
    conns1, _ = await _ss_conns()
    ss_dt = max(time.monotonic() - t0, 1e-3)
    per_proc = net_proc_rates(conns0, conns1, ss_dt)
    prev = {(c["local"], c["peer"]): c for c in conns0}
    for c in conns1:
        p = prev.get((c["local"], c["peer"]))
        c["tx_bps"] = max(0, c["sent"] - p["sent"]) / ss_dt if p else 0.0
        c["rx_bps"] = max(0, c["recv"] - p["recv"]) / ss_dt if p else 0.0
    conns1.sort(
        key=lambda c: -(c["tx_bps"] + c["rx_bps"] + (c["sent"] + c["recv"]) / 1e9)
    )
    n0, n1 = parse_net_dev(b.get("netdev", "")), parse_net_dev(a.get("netdev", ""))
    rates = net_rates(n0, n1, dt)
    wl = parse_proc_wireless(_read(PROC / "net" / "wireless"))
    rc, ipout, _ = await run_cmd("ip", "-br", "addr", timeout=4.0)
    addrs = {}
    for ln in (ipout or "").splitlines():
        parts = ln.split()
        if len(parts) >= 3:
            addrs[parts[0]] = parts[2:]
    ifaces = []
    for name, cnt in n1.items():
        state = _read(NET_ROOT / name / "operstate").strip() or None
        if name == "lo":
            continue
        if state == "down" and cnt["rx_bytes"] + cnt["tx_bytes"] == 0:
            continue
        speed = _read_int(NET_ROOT / name / "speed")
        row = {
            "name": name,
            "state": state,
            "speed_mbps": speed if speed and speed > 0 else None,
            "addrs": addrs.get(name, []),
            **await asyncio.to_thread(_read_iface_sys, name),
            **cnt,
            **rates.get(name, {}),
        }
        if name in wl or (NET_ROOT / name / "wireless").exists():
            wifi = dict(wl.get(name, {}))
            rc, out, _ = await run_cmd("iw", "dev", name, "link", timeout=4.0)
            if rc == 0:
                wifi.update(parse_iw_link(out or ""))
            elif rc == 127:
                notes.append("iw not installed; wifi signal from /proc only")
            if "signal_dbm" not in wifi and "level_dbm" in wifi:
                wifi["signal_dbm"] = int(wifi["level_dbm"])
            rc, out, _ = await run_cmd(
                "iw", "dev", name, "station", "dump", timeout=4.0
            )
            if rc == 0:
                wifi.update(parse_iw_station(out or ""))
            row["wifi"] = wifi
        ifaces.append(row)
    gw: dict = {}
    ip = parse_default_gateway(_read(PROC / "net" / "route"))
    if ip:
        rc, out, _ = await run_cmd(
            "ping", "-c", "3", "-i", "0.2", "-W", "1", ip, timeout=6.0
        )
        gw = {"ip": ip, **parse_ping(out or "")}
        if rc == 127:
            notes.append("ping not installed")
    else:
        notes.append("no default route")
    rc, out, _ = await run_cmd("ss", "-tulnpH", timeout=5.0)
    listeners = parse_ss_listeners(out or "") if rc == 0 else []
    if rc != 0:
        notes.append("ss unavailable; listening ports unknown")
    if not ss_ok:
        notes.append("ss unavailable; per-process bandwidth unknown")
    return {
        "ifaces": ifaces,
        "gateway": gw,
        "listeners": listeners,
        "talkers": per_proc[:5],
        "per_process": per_proc[:12],
        "conns": conns1[:40],
        "conn_count": len(conns1),
        "snmp": parse_snmp(_read(PROC / "net" / "snmp")),
        "sockstat": parse_sockstat(_read(PROC / "net" / "sockstat")),
        "notes": notes,
    }


async def collect_power(sample: dict | None = None) -> dict:
    s = sample or await take_sample({"rapl", "procs_ctx"})
    b, a, dt = s["before"], s["after"], s["dt"]
    info = await asyncio.to_thread(read_power, POWER_ROOT)
    notes: list[str] = []
    if info["battery"] is None:
        notes.append("no battery found")
    rc, out, _ = await run_cmd("powerprofilesctl", "get", timeout=4.0)
    info["profile"] = (out or "").strip() if rc == 0 and out else None
    if info["profile"] is None:
        notes.append("power profile unavailable (power-profiles-daemon missing)")
    rc, out, _ = await run_cmd("powerprofilesctl", "list", timeout=4.0)
    info["profiles_raw"] = (out or "").strip() if rc == 0 else ""
    rapl = rapl_watts(b.get("rapl_domains"), a.get("rapl_domains"), dt)
    pkg = next((w for k, w in rapl.items() if k.startswith("package")), None)
    if not rapl:
        notes.append("RAPL energy counters not readable")
    info["rapl"] = rapl
    info["cpu_package_w"] = pkg
    info["supplies"] = await asyncio.to_thread(_read_supplies)
    info["gpu_power"] = []
    rc, out, _ = await run_cmd(
        "nvidia-smi",
        "--query-gpu=name,power.draw,power.limit",
        "--format=csv,noheader,nounits",
        timeout=5.0,
    )
    if rc == 0:
        for ln in (out or "").splitlines():
            p = [x.strip() for x in ln.split(",")]
            if len(p) >= 3 and _num(p[1]) is not None:
                info["gpu_power"].append(
                    {"name": p[0], "watts": _num(p[1]), "limit": _num(p[2])}
                )
    bl = None
    try:
        for d in sorted(BACKLIGHT_ROOT.iterdir()):
            cur, mx = _read_num(d / "brightness"), _read_num(d / "max_brightness")
            if cur is not None and mx:
                bl = 100.0 * cur / mx
                break
    except OSError:
        pass
    info["brightness_pct"] = bl
    rows = proc_rates(b.get("procs", {}), a.get("procs", {}), dt)
    estimate_process_watts(rows, pkg)
    key = "est_w" if pkg is not None else "cpu_pct"
    top, groups = await asyncio.to_thread(process_tables, rows, key, n=15)
    wake = sorted((r for r in rows if r.get("ctx_ps")), key=lambda r: -r["ctx_ps"])[:10]
    await asyncio.to_thread(enrich_rows, wake)
    info["procs"], info["groups"], info["wakeups"] = top, groups, wake
    info["power_key"] = key
    info["notes"] = notes
    return info


def _read_supplies() -> list[dict]:
    out = []
    try:
        for sup in sorted(POWER_ROOT.iterdir()):
            vals = {}
            for f in sorted(sup.iterdir()):
                if f.is_file() and f.name not in (
                    "uevent",
                    "serial_number",
                    "model_name",
                    "manufacturer",
                ):
                    v = _read(f).strip()
                    if v and len(v) < 40:
                        vals[f.name] = v
            out.append({"name": sup.name, "values": vals})
    except OSError:
        pass
    return out


def _match_cmdline(rows: list[dict], needle: str) -> list[dict]:
    return [
        r for r in rows if needle in _read(PROC / str(r["pid"]) / "cmdline").lower()
    ][:20]


async def collect_process(
    sample: dict | None = None,
    sort_by: str = "cpu",
    top: int = 5,
    name: str = "",
    user: str = "",
) -> dict:
    s = sample or await take_sample({"procs"})
    b, a, dt = s["before"], s["after"], s["dt"]
    rows = proc_rates(b.get("procs", {}), a.get("procs", {}), dt)
    uptime = _num(_read(PROC / "uptime").split()[0]) if _read(PROC / "uptime") else None
    for r in rows:
        r["age_s"] = (
            max(0.0, uptime - r["start_ticks"] / CLK_TCK)
            if uptime is not None
            else None
        )
    key = {"cpu": "cpu_pct", "mem": "rss_kb", "io": "io_bps"}.get(sort_by, "cpu_pct")
    n = max(1, min(top, 15))
    pool = [r for r in rows if r["user"] == user] if user else rows
    if sort_by == "io":
        pool = [r for r in pool if r.get("read_bps") is not None]
    ranked_top, groups = await asyncio.to_thread(
        process_tables, pool, key, n=n, status=sort_by == "mem", smaps=sort_by == "mem"
    )
    matches: list[dict] = []
    if name:
        needle = name.lower()
        matches = [
            r
            for r in sorted(rows, key=lambda r: -r.get(key, 0))
            if needle in r["name"].lower()
        ]
        if not matches:  # also match interpreter scripts by cmdline
            matches = await asyncio.to_thread(_match_cmdline, rows, needle)
        await asyncio.to_thread(enrich_rows, matches[:20], status=True)
    by_user: dict[str, dict] = {}
    for r in rows:
        u = by_user.setdefault(r["user"], {"count": 0, "cpu_pct": 0.0, "rss_kb": 0})
        u["count"] += 1
        u["cpu_pct"] += r["cpu_pct"]
        u["rss_kb"] += r["rss_kb"]
    zombies = [r for r in rows if r["state"] == "Z"]
    dstate = [r for r in rows if r["state"] == "D"]
    return {
        "total": len(rows),
        "threads": sum(r["threads"] for r in rows),
        "running": sum(1 for r in rows if r["state"] == "R"),
        "zombies": len(zombies),
        "zombie_names": sorted({r["name"] for r in zombies})[:5],
        "dstate": len(dstate),
        "dstate_names": sorted({r["name"] for r in dstate})[:5],
        "by_user": by_user,
        "sort_by": sort_by,
        "query": name,
        "matches": matches[:20],
        "groups": groups,
        "procs": ranked_top,
        "io_visible": sum(1 for r in rows if r.get("read_bps") is not None),
        "total_kb": parse_meminfo(_read(PROC / "meminfo")).get("MemTotal", 0),
        "notes": [],
    }


COLLECTORS: dict[str, Callable[..., Awaitable[dict]]] = {
    "cpu": collect_cpu,
    "memory": collect_memory,
    "gpu": collect_gpu,
    "temps": collect_temps,
    "disk": collect_disk,
    "network": collect_network,
    "power": collect_power,
    "process": collect_process,
}
SAMPLE_WANT: dict[str, set[str]] = {
    "cpu": {"stat", "procs", "rapl"},
    "memory": {"vmstat", "procs"},
    "gpu": set(),
    "temps": {"procs"},
    "disk": {"diskstats", "procs"},
    "network": {"netdev"},
    "power": {"rapl", "procs_ctx"},
    "process": {"procs"},
}


# ---------------------------------------------------------------- renderers


def _fmt_bps(bps: float | None) -> str:
    if bps is None:
        return "n/a"
    if bps >= 1e6:
        return f"{bps / 1e6:.1f} MB/s"
    return f"{bps / 1e3:.0f} kB/s"


def _top_phrase(groups: list[dict], key: str, n: int = 3) -> str:
    if key == "cpu_pct":
        return ", ".join(f"{g['name']} {g['cpu_pct']:.0f} percent" for g in groups[:n])
    return ", ".join(
        f"{g['name']} {_gb(g['rss_kb']):.1f} gigabytes" for g in groups[:n]
    )


def _concern(findings: list[Finding]) -> str:
    worst = [f for f in sorted(findings, key=lambda f: -f.score) if f.severity >= 1]
    return f"Concern: {worst[0].title}" if worst else ""


def render_cpu(d: dict, findings: list[Finding]) -> tuple[str, str]:
    n = d.get("cores_count", 1)
    pct = d.get("total_pct", 0.0)
    cores = d.get("cores_pct") or []
    freq = d.get("freq") or {}
    load = d.get("load")
    parts = [f"CPU {pct:.0f} percent busy across {n} cores"]
    if load:
        parts[0] += f", load {load[0]:.1f}"
    if freq.get("avg_mhz"):
        gov = f" on the {d['governor']} governor" if d.get("governor") else ""
        parts.append(f"running at {freq['avg_mhz'] / 1000:.1f} gigahertz{gov}")
    if cores:
        parts.append(f"busiest core {max(cores):.0f} percent")
    if d.get("groups"):
        parts.append("top " + _top_phrase(d["groups"], "cpu_pct"))
    parts.append(_concern(findings))
    say = _clip(_sentence(parts))
    out_lines = [
        f"CPU: {d.get('model') or 'unknown model'}",
        f"Overall: {pct:.1f}% busy, iowait {d.get('iowait', 0):.1f}%, steal {d.get('steal', 0):.1f}%",
    ]
    if load:
        out_lines.append(
            f"Load average: {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f} on {n} cores"
        )
    out_lines.append(
        f"Tasks running {d.get('running')}, blocked {d.get('blocked')}; context switches {d.get('ctx_per_s', 0):,.0f}/s"
    )
    if cores:
        out_lines.append(
            "Per core %: " + " ".join(f"{i}:{c:.0f}" for i, c in enumerate(cores))
        )
    if freq.get("cur_mhz"):
        out_lines.append(
            f"Frequency MHz: avg {freq['avg_mhz']:.0f}, min core {min(freq['cur_mhz']):.0f}, max core {max(freq['cur_mhz']):.0f}; limits {freq.get('min_mhz') or 0:.0f}-{freq.get('max_mhz') or 0:.0f}"
        )
    out_lines.append(
        f"Governor {d.get('governor') or 'n/a'}, driver {d.get('driver') or 'n/a'}, energy preference {d.get('epp') or 'n/a'}"
    )
    thr = d.get("throttle") or {}
    out_lines.append(
        f"Thermal throttle events since boot: core {thr.get('core', 0)}, package {thr.get('package', 0)}"
    )
    if d.get("rapl_w") is not None:
        out_lines.append(f"CPU package power: {d['rapl_w']:.1f} W")
    psi = (d.get("psi") or {}).get("some")
    if psi:
        out_lines.append(
            f"CPU pressure (PSI some): avg10 {psi.get('avg10', 0):.1f}%, avg60 {psi.get('avg60', 0):.1f}%, avg300 {psi.get('avg300', 0):.1f}%"
        )
    out_lines += [f"Note: {x}" for x in d.get("notes") or []]
    return say, "\n".join(out_lines) + _ext_cpu(d)


def render_memory(d: dict, findings: list[Finding]) -> tuple[str, str]:
    parts = [
        f"Memory {d.get('used_gb', 0):.1f} of {d.get('total_gb', 0):.1f} gigabytes used, {d.get('avail_pct', 0):.0f} percent available"
    ]
    if d.get("swap_total_gb"):
        parts.append(
            f"swap {d['swap_used_gb']:.1f} of {d['swap_total_gb']:.1f} gigabytes"
        )
    p = ((d.get("psi") or {}).get("some") or {}).get("avg10")
    if p is not None:
        parts.append(f"memory pressure {p:.0f} percent")
    if d.get("groups"):
        parts.append("biggest " + _top_phrase(d["groups"], "rss_kb"))
    if d.get("oom_kills"):
        parts.append(f"{len(d['oom_kills'])} OOM kills this week")
    parts.append(_concern(findings))
    say = _clip(_sentence(parts))
    out_lines = [
        f"RAM total {d.get('total_gb', 0):.2f} GB: used {d.get('used_gb', 0):.2f}, available {d.get('avail_gb', 0):.2f} ({d.get('avail_pct', 0):.1f}%), free {d.get('free_gb', 0):.2f}",
        f"Cache {d.get('cached_gb', 0):.2f} GB, buffers {d.get('buffers_gb', 0):.2f} GB, slab {d.get('slab_gb', 0):.2f} GB ({d.get('slab_pct', 0):.1f}%), shmem {d.get('shmem_gb', 0):.2f} GB, dirty {d.get('dirty_mb', 0):.1f} MB",
        f"Swap {d.get('swap_used_gb', 0):.2f} of {d.get('swap_total_gb', 0):.2f} GB ({d.get('swap_used_pct', 0):.0f}%)",
    ]
    for s in d.get("swaps") or []:
        out_lines.append(
            f"  swap {s['name']}: {s['used_kb'] / 1024:.0f} of {s['size_kb'] / 1024:.0f} MB"
        )
    for z in d.get("zram") or []:
        out_lines.append(
            f"  {z['name']}: {z['orig_mb']:.0f} MB stored as {z['compressed_mb']:.0f} MB (ratio {z['ratio']:.1f}x, size {z['disk_gb']:.1f} GB)"
        )
    act = d.get("swap_activity") or {}
    out_lines.append(
        f"Swap activity: in {act.get('pswpin_s', 0):.0f} pages/s, out {act.get('pswpout_s', 0):.0f} pages/s"
    )
    psi = d.get("psi") or {}
    for kind in ("some", "full"):
        if psi.get(kind):
            out_lines.append(
                f"Memory pressure (PSI {kind}): avg10 {psi[kind].get('avg10', 0):.1f}%, avg60 {psi[kind].get('avg60', 0):.1f}%, avg300 {psi[kind].get('avg300', 0):.1f}%"
            )
    out_lines.append(
        f"OOM kills since boot (counter): {d.get('oom_total')}; journal (7 days): {len(d.get('oom_kills') or [])}"
    )
    out_lines += [f"  {k}" for k in (d.get("oom_kills") or [])[-5:]]
    out_lines += [f"Note: {x}" for x in d.get("notes") or []]
    return say, "\n".join(out_lines) + _ext_memory(d)


def render_gpu(d: dict, findings: list[Finding]) -> tuple[str, str]:
    gpus = d.get("gpus") or []
    notes = d.get("notes") or []
    if not gpus:
        say = "No GPU monitoring tool or readable GPU found on this machine."
        return say, "\n".join(
            ["No GPU data available."] + [f"Note: {n}" for n in notes]
        )
    parts = []
    for g in gpus[:2]:
        bits = [g["name"]]
        if g.get("util_pct") is not None:
            bits.append(f"{g['util_pct']} percent busy")
        if g.get("vram_used_mb") is not None and g.get("vram_total_mb"):
            bits.append(
                f"{g['vram_used_mb']} of {g['vram_total_mb']} megabytes video memory"
            )
        if g.get("temp_c") is not None:
            bits.append(f"{g['temp_c']} degrees")
        if g.get("power_w") is not None:
            bits.append(f"{g['power_w']:.0f} watts")
        if g.get("clock_mhz") is not None:
            bits.append(f"{g['clock_mhz']} megahertz")
        parts.append(", ".join(bits))
    top = next((g for g in gpus if g.get("procs")), None)
    if top:
        parts.append("using it: " + ", ".join(p["name"] for p in top["procs"][:3]))
    parts.append(_concern(findings))
    say = _clip(_sentence(parts))
    out_lines = []

    def kv(label: str, val, unit: str = "") -> str:
        return f"{label} {val}{unit}" if val is not None else ""

    for g in gpus:
        title = (
            g["name"]
            if g["name"].startswith(g["vendor"])
            else f"{g['vendor']} {g['name']}"
        )
        out_lines.append(
            title + (f" (driver {g['driver']})" if g.get("driver") else "")
        )
        rows = [
            ", ".join(
                x
                for x in (
                    kv("utilisation", g.get("util_pct"), "%"),
                    kv("memory controller", g.get("mem_util_pct"), "%"),
                    kv("pstate", g.get("pstate")),
                )
                if x
            ),
            (
                f"VRAM {g['vram_used_mb']} / {g['vram_total_mb']} MiB"
                if g.get("vram_used_mb") is not None
                else ""
            ),
            ", ".join(
                x
                for x in (
                    kv("clock", g.get("clock_mhz"), " MHz"),
                    kv("max", g.get("clock_max_mhz"), " MHz"),
                    kv("memory clock", g.get("mem_clock_mhz"), " MHz"),
                )
                if x
            ),
            ", ".join(
                x
                for x in (
                    kv("power", g.get("power_w"), " W"),
                    kv("limit", g.get("power_limit_w"), " W"),
                )
                if x
            ),
            ", ".join(
                x
                for x in (
                    kv("temperature", g.get("temp_c"), " C"),
                    kv("slowdown at", g.get("temp_slow_c"), " C"),
                )
                if x
            ),
            (
                f"fan {g['fan_pct']}%"
                if g.get("fan_pct") is not None
                else f"fan {g['fan_rpm']} rpm"
                if g.get("fan_rpm") is not None
                else "fan not exposed"
            ),
            "throttle reasons: " + (", ".join(g.get("throttle") or []) or "none"),
        ]
        out_lines += [f"  {r}" for r in rows if r]
    out_lines += [f"Note: {n}" for n in notes]
    return say, "\n".join(out_lines) + _ext_gpu(d)


def render_temps(d: dict, findings: list[Finding]) -> tuple[str, str]:
    sensors = d.get("sensors") or []
    mx = d.get("max")
    if not sensors:
        say = "No temperature sensors are readable on this machine."
        return say, "\n".join(f"Note: {n}" for n in d.get("notes") or [])
    parts = [f"Hottest sensor {mx['label']} at {mx['temp_c']:.0f} degrees"]
    cpu = next(
        (s for s in sensors if "pkg" in s["label"] or "Package" in s["label"]), None
    )
    if cpu and cpu is not mx:
        parts.append(f"CPU package {cpu['temp_c']:.0f}")
    nv = next((s for s in sensors if "Composite" in s["label"]), None)
    if nv:
        parts.append(f"NVMe {nv['temp_c']:.0f}")
    fans = d.get("fans") or []
    if fans:
        parts.append(
            "fans "
            + ", ".join(
                f"{f['rpm']} rpm"
                if f.get("rpm") is not None
                else f"{f['pct']:.0f} percent"
                for f in fans[:2]
            )
        )
    else:
        parts.append("no fan speed exposed")
    if d.get("cooling_active"):
        parts.append("kernel cooling is active")
    parts.append(_concern(findings))
    say = _clip(_sentence(parts))
    out_lines = [f"{len(sensors)} temperature sensors:"]
    for s in sorted(sensors, key=lambda s: -s["temp_c"]):
        lim = []
        if s.get("high_c"):
            lim.append(f"high {s['high_c']:.0f}")
        if s.get("crit_c"):
            lim.append(f"crit {s['crit_c']:.0f}")
        out_lines.append(
            f"  {s['label']}: {s['temp_c']:.1f} C"
            + (f" ({', '.join(lim)})" if lim else "")
        )
    out_lines.append(
        "Fans: "
        + (
            ", ".join(
                f"{f['label']} {f.get('rpm') if f.get('rpm') is not None else str(f.get('pct')) + '%'}"
                for f in fans
            )
            or "none exposed"
        )
    )
    cool = d.get("cooling_active") or []
    out_lines.append(
        "Active cooling/throttle devices: "
        + (", ".join(f"{c['type']} {c['state']}/{c['max']}" for c in cool) or "none")
    )
    thr = d.get("throttle") or {}
    out_lines.append(
        f"CPU thermal throttle events since boot: core {thr.get('core', 0)}, package {thr.get('package', 0)}"
    )
    out_lines += [f"Note: {n}" for n in d.get("notes") or []]
    return say, "\n".join(out_lines) + _ext_temps(d)


def render_disk(d: dict, findings: list[Finding]) -> tuple[str, str]:
    mounts, io = d.get("mounts") or [], d.get("io") or {}
    parts = []
    if mounts:
        root = next((m for m in mounts if m["mount"] == "/"), mounts[0])
        parts.append(
            f"{'Root disk' if root['mount'] == '/' else 'Disk ' + root['mount']} is {root['used_pct']:.0f} percent full, {root['avail_gb']:.0f} gigabytes free"
        )
        fullest = max(mounts, key=lambda m: m["used_pct"])
        if fullest is not root and fullest["used_pct"] >= 80:
            parts.append(
                f"{fullest['mount']} is {fullest['used_pct']:.0f} percent full"
            )
    if io:
        name, r = max(
            io.items(), key=lambda kv: kv[1]["read_mbps"] + kv[1]["write_mbps"]
        )
        parts.append(
            f"Drive {name} reading {r['read_mbps']:.1f} and writing {r['write_mbps']:.1f} megabytes per second, {r['util_pct']:.0f} percent busy"
        )
    dt = [t for t in d.get("drive_temps") or [] if "Composite" in t["label"]]
    if dt:
        parts.append(
            "NVMe " + " and ".join(f"{t['temp_c']:.0f}" for t in dt) + " degrees"
        )
    if not parts:
        parts.append("No disk data readable")
    parts.append(_concern(findings))
    say = _clip(_sentence(parts))
    out_lines = ["Mounts:"]
    for m in mounts:
        ino = (
            f", inodes {m['inode_pct']:.0f}%" if m.get("inode_pct") is not None else ""
        )
        out_lines.append(
            f"  {m['mount']} ({m['device']}, {m['fs']}{', READ-ONLY' if m['ro'] else ''}): {m['used_gb']:.1f} of {m['total_gb']:.1f} GB used ({m['used_pct']:.0f}%), {m['avail_gb']:.1f} GB free{ino}"
        )
    out_lines.append("IO rates:")
    for name, r in io.items():
        out_lines.append(
            f"  {name}: read {r['read_mbps']:.2f} MB/s, write {r['write_mbps']:.2f} MB/s, {r['iops']:.0f} IOPS, {r['util_pct']:.0f}% busy, avg wait {r['await_ms']:.1f} ms"
        )
    if d.get("smart"):
        out_lines.append(
            "SMART: " + ", ".join(f"{k} {v}" for k, v in d["smart"].items())
        )
    for t in d.get("drive_temps") or []:
        out_lines.append(f"Drive temp {t['label']}: {t['temp_c']:.0f} C")
    out_lines += [f"Note: {n}" for n in d.get("notes") or []]
    return say, "\n".join(out_lines) + _ext_disk(d)


def render_network(d: dict, findings: list[Finding]) -> tuple[str, str]:
    ifaces = d.get("ifaces") or []
    parts = []
    for i in ifaces[:2]:
        bit = f"interface {i['name']} {i.get('state') or 'unknown'}, down {i.get('rx_mbps', 0):.2f} up {i.get('tx_mbps', 0):.2f} megabits"
        w = i.get("wifi") or {}
        if w.get("signal_dbm") is not None:
            bit += f", wifi {w['signal_dbm']} dBm"
            if w.get("ssid"):
                bit += f" on {w['ssid']}"
        parts.append(bit)
    if not ifaces:
        parts.append("No active network interface")
    gw = d.get("gateway") or {}
    if gw.get("avg_ms") is not None:
        parts.append(
            f"router {gw['avg_ms']:.0f} milliseconds, {gw.get('loss_pct', 0):.0f} percent loss"
        )
    elif gw.get("loss_pct") == 100.0:
        parts.append("router unreachable")
    if d.get("listeners"):
        parts.append(f"{len(d['listeners'])} listening ports")
    tk = d.get("talkers") or []
    if tk and tk[0]["proc"] != "unknown":
        parts.append(f"top talker {tk[0]['proc']}")
    parts.append(_concern(findings))
    say = _clip(_sentence(parts))
    out_lines = []
    for i in ifaces:
        out_lines.append(
            f"{i['name']} [{i.get('state')}]"
            + (f" link {i['speed_mbps']} Mbit/s" if i.get("speed_mbps") else "")
        )
        out_lines.append(
            f"  rate: down {i.get('rx_mbps', 0):.3f} Mbit/s, up {i.get('tx_mbps', 0):.3f} Mbit/s ({i.get('rx_pps', 0):.0f}/{i.get('tx_pps', 0):.0f} pkt/s)"
        )
        out_lines.append(
            f"  totals since boot: rx {i['rx_bytes'] / 1e9:.2f} GB, tx {i['tx_bytes'] / 1e9:.2f} GB; errors rx {i['rx_err']} tx {i['tx_err']}; drops rx {i['rx_drop']} tx {i['tx_drop']}"
        )
        w = i.get("wifi")
        if w:
            out_lines.append(
                f"  wifi: {w.get('ssid', '?')} {w.get('freq_mhz', '?')} MHz, signal {w.get('signal_dbm', '?')} dBm, tx {w.get('tx_mbit', '?')} / rx {w.get('rx_mbit', '?')} Mbit/s, retries {w.get('tx_retries', 'n/a')} of {w.get('tx_packets', 'n/a')} pkts, failed {w.get('tx_failed', 'n/a')}"
            )
    if d.get("gateway"):
        g = d["gateway"]
        out_lines.append(
            f"Gateway {g.get('ip')}: avg {g.get('avg_ms')} ms, max {g.get('max_ms')} ms, loss {g.get('loss_pct')}%"
        )
    ls = d.get("listeners") or []
    exposed = list({(p["proto"], p["port"]): p for p in ls if p["exposed"]}.values())
    out_lines.append(
        f"Listening sockets: {len(ls)} total, {len(exposed)} on all interfaces"
    )
    for p in exposed[:8]:
        out_lines.append(f"  {p['proto']} :{p['port']} {p['proc'] or '(other user)'}")
    out_lines += [f"Note: {n}" for n in d.get("notes") or []]
    return say, "\n".join(out_lines) + _ext_network(d)


def render_power(d: dict, findings: list[Finding]) -> tuple[str, str]:
    b = d.get("battery")
    parts = []
    if b:
        status = b["status"].lower()
        parts.append(f"Battery {b['percent']} percent, {status}")
        if b.get("watts") and b["status"] in ("Discharging", "Charging"):
            parts.append(f"{b['watts']:.0f} watts")
        if b.get("hours_left"):
            h = b["hours_left"]
            word = "to empty" if b["status"] == "Discharging" else "to full"
            parts.append(f"about {int(h)} hours {int((h % 1) * 60)} minutes {word}")
        if b.get("health_pct") is not None:
            parts.append(
                f"health {b['health_pct']:.0f} percent with {b.get('cycles')} cycles"
            )
    else:
        parts.append("No battery found")
    if d.get("ac_online") is not None:
        parts.append("on AC power" if d["ac_online"] else "on battery power")
    if d.get("profile"):
        parts.append(f"{d['profile']} power profile")
    parts.append(_concern(findings))
    say = _clip(_sentence(parts))
    out_lines = []
    if b:
        out_lines.append(
            f"Battery: {b['percent']}% {b['status']} ({b.get('tech') or 'unknown chemistry'})"
        )
        out_lines.append(
            f"  energy {b.get('energy_wh') or 0:.1f} Wh now / {b.get('full_wh') or 0:.1f} Wh full / {b.get('design_wh') or 0:.1f} Wh design"
        )
        out_lines.append(
            f"  health {b['health_pct']:.1f}% of design, {b.get('cycles')} charge cycles"
            if b.get("health_pct") is not None
            else "  health unknown"
        )
        out_lines.append(
            f"  power flow {b['watts']:.1f} W"
            if b.get("watts") is not None
            else "  power flow unknown"
        )
        out_lines.append(
            f"  time remaining {b['hours_left']:.2f} h"
            if b.get("hours_left")
            else "  time remaining n/a"
        )
        if b.get("charge_limit"):
            out_lines.append(f"  charge limit {b['charge_limit']}%")
    out_lines.append(
        f"AC online: {d.get('ac_online')}; power profile: {d.get('profile') or 'n/a'}"
    )
    if d.get("cpu_package_w") is not None:
        out_lines.append(f"CPU package power: {d['cpu_package_w']:.1f} W")
    if d.get("brightness_pct") is not None:
        out_lines.append(f"Screen brightness: {d['brightness_pct']:.0f}%")
    out_lines += [f"Note: {n}" for n in d.get("notes") or []]
    return say, "\n".join(out_lines) + _ext_power(d)


def render_process(d: dict, findings: list[Finding]) -> tuple[str, str]:
    key = d.get("sort_by", "cpu")
    label = {"cpu": "CPU", "mem": "memory", "io": "disk IO"}.get(key, "CPU")
    kind = key if key in ("mem", "io") else "cpu"
    total_kb = d.get("total_kb", 0)
    q = d.get("query")
    if q:
        m = d.get("matches") or []
        if not m:
            say = f"No process matching {q} is running."
            return say, f"No process name or command line contains '{q}'."
        total_cpu = sum(r["cpu_pct"] for r in m)
        total_rss = sum(r["rss_kb"] for r in m)
        say = _clip(
            f"{len(m)} process{'es' if len(m) != 1 else ''} match {q}, {total_cpu:.0f} percent CPU and {_gb(total_rss):.1f} gigabytes in total. {_concern(findings)}".rstrip(
                ". "
            )
            + "."
        )
        out_lines = [
            f"Processes matching '{q}': {len(m)} total, {total_cpu:.1f}% cpu, rss {total_rss / 1024:.0f} MB, {sum(r['threads'] for r in m)} threads"
        ]
        for r in m[:20]:
            out_lines.append(fmt_proc_line(r, "cpu", total_kb))
            out_lines.append(
                f"      rss {r['rss_kb'] / 1024:.0f} MB, swap {r.get('swap_kb', 0) / 1024:.0f} MB, anon {r.get('rss_anon_kb', 0) / 1024:.0f} MB, file {r.get('rss_file_kb', 0) / 1024:.0f} MB, shmem {r.get('rss_shmem_kb', 0) / 1024:.0f} MB"
            )
        return say, "\n".join(out_lines)
    groups = d.get("groups") or []
    if key == "cpu":
        top = _top_phrase(groups, "cpu_pct", 3)
    elif key == "mem":
        top = _top_phrase(groups, "rss_kb", 3)
    else:
        top = ", ".join(f"{g['name']} {_fmt_bps(g['io_bps'])}" for g in groups[:3])
    parts = [
        f"{d.get('total', 0)} processes, {d.get('threads', 0)} threads, {d.get('zombies', 0)} zombies",
        f"top by {label}: {top}" if top else "",
        _concern(findings),
    ]
    say = _clip(_sentence(parts))
    out_lines = [
        f"Processes: {d.get('total')} ({d.get('running')} running, {d.get('zombies')} zombie, {d.get('dstate')} in uninterruptible wait), {d.get('threads')} threads",
        "By user: "
        + ", ".join(
            f"{u} {v['count']} procs {v['cpu_pct']:.0f}% cpu {_gb(v['rss_kb']):.1f} GB"
            for u, v in sorted(
                d.get("by_user", {}).items(), key=lambda kv: -kv[1]["count"]
            )[:8]
        ),
        render_process_section(label, d.get("procs") or [], groups, kind, total_kb),
    ]
    if key == "io":
        out_lines.append(
            f"(IO is only visible for your own processes: {d.get('io_visible')} readable)"
        )
    out_lines += [f"  zombie: {n}" for n in d.get("zombie_names") or []]
    out_lines += [f"  uninterruptible: {n}" for n in d.get("dstate_names") or []]
    return say, "\n".join(out_lines)


def _kvs(pairs: list[tuple[str, object]], sep: str = ", ") -> str:
    return sep.join(f"{k} {v}" for k, v in pairs if v not in (None, "", "n/a"))


def _ext_cpu(d: dict) -> str:
    out = ["", "--- Full CPU reading ---"]
    ci = d.get("cpuinfo") or {}
    out.append(
        "Topology: "
        + _kvs(
            [
                ("threads", ci.get("threads")),
                ("sockets", ci.get("sockets")),
                ("cores/socket", ci.get("cores_per_socket")),
                ("cache", ci.get("cache")),
                ("SMT active", ci.get("smt_active")),
                ("pstate", ci.get("pstate_status")),
                ("no_turbo", ci.get("pstate_no_turbo")),
                (
                    "perf pct min/max",
                    f"{ci.get('pstate_min_perf_pct', '?')}/{ci.get('pstate_max_perf_pct', '?')}"
                    if ci.get("pstate_min_perf_pct")
                    else None,
                ),
            ]
        )
    )
    br = d.get("breakdown") or {}
    if br:
        out.append("Time split %: " + ", ".join(f"{k} {v:.1f}" for k, v in br.items()))
    r = d.get("rates") or {}
    if r:
        out.append(
            f"Rates/s: interrupts {r.get('intr', 0):,.0f}, softirqs {r.get('softirq', 0):,.0f}, forks {r.get('forks', 0):.1f}, context switches {r.get('ctxt', 0):,.0f}"
        )
    if d.get("load_tasks"):
        out.append(f"Scheduler entities running/total: {d['load_tasks']}")
    ce = d.get("cpu_extra") or {}
    if ce.get("base_mhz") or ce.get("scaling_max_mhz"):
        out.append(
            f"Clock policy MHz: base {ce.get('base_mhz', 0):.0f}, scaling {ce.get('scaling_min_mhz', 0):.0f}-{ce.get('scaling_max_mhz', 0):.0f}; governors {ce.get('available_governors') or 'n/a'}"
        )
    rap = d.get("rapl") or {}
    if rap:
        out.append("RAPL power W: " + ", ".join(f"{k} {v:.1f}" for k, v in rap.items()))
    psi = d.get("psi") or {}
    for kind, vals in psi.items():
        out.append(f"PSI {kind}: " + ", ".join(f"{k} {v:.2f}" for k, v in vals.items()))
    if d.get("uptime_s"):
        out.append(f"Uptime {fmt_age(d['uptime_s'])}")
    cores, cb = d.get("cores_pct") or [], d.get("cores_breakdown") or []
    cur = (d.get("freq") or {}).get("cur_mhz") or []
    if cores:
        out.append("Per core:")
        for i, c in enumerate(cores):
            bd = cb[i] if i < len(cb) else {}
            split = (
                f" (user {bd.get('user', 0):.0f} sys {bd.get('system', 0):.0f} iowait {bd.get('iowait', 0):.0f} irq {bd.get('irq', 0) + bd.get('softirq', 0):.0f})"
                if bd
                else ""
            )
            mhz = f" @ {cur[i]:.0f} MHz" if i < len(cur) else ""
            out.append(f"  cpu{i}: {c:.0f}%{split}{mhz}")
    out.append(
        render_process_section(
            "CPU", d.get("procs") or [], d.get("groups") or [], "cpu"
        )
    )
    return "\n".join(out)


def _ext_memory(d: dict) -> str:
    out = ["", "--- Full memory reading ---"]
    mem = d.get("meminfo") or {}
    out.append("Full /proc/meminfo:")
    out += [f"  {k}: {v:,} kB ({v / 1024:,.1f} MB)" for k, v in mem.items()]
    vm = d.get("vmstat") or {}
    if vm:
        out.append("Kernel memory counters (total since boot, per second now):")
        out += [f"  {k}: {v['total']:,} ({v['per_s']:.1f}/s)" for k, v in vm.items()]
    out.append(
        render_process_section(
            "memory",
            d.get("procs") or [],
            d.get("groups") or [],
            "mem",
            d.get("total_kb", 0),
        )
    )
    return "\n".join(out)


def _gpu_proc_line(p: dict) -> str:
    bits = [
        f"VRAM {p['mem_mb']} MiB" if p.get("mem_mb") is not None else "",
        f"sm {p['sm']}%" if p.get("sm") is not None else "",
        f"mem {p['mem']}%" if p.get("mem") is not None else "",
        f"enc {p['enc']}%" if p.get("enc") is not None else "",
        f"dec {p['dec']}%" if p.get("dec") is not None else "",
        f"{p['engine']} {p['util_pct']:.0f}%" if p.get("engine") else "",
    ]
    cmd = (p.get("cmd") or "")[:100]
    return (
        f"  pid {p['pid']} {p.get('user', '?')} {p['name']} ({p.get('type', '?')}): "
        + (", ".join(b for b in bits if b) or "no usage reported")
        + (f" cmd: {cmd}" if cmd else "")
    )


def _ext_gpu(d: dict) -> str:
    out = ["", "--- Full GPU reading ---"]
    for g in d.get("gpus") or []:
        extra = g.get("extra") or []
        if extra:
            out.append(f"{g['name']} extra stats:")
            out += [f"  {k}: {v}" for k, v in extra]
        procs = g.get("procs") or []
        out.append(f"GPU processes on {g['name']} ({len(procs)}):")
        out += [_gpu_proc_line(p) for p in procs] or ["  (none visible)"]
        grouped: dict[str, list[dict]] = {}
        for p in procs:
            grouped.setdefault(p["name"], []).append(p)
        multi = {k: v for k, v in grouped.items() if len(v) > 1}
        if multi:
            out.append("Program groups:")
            for k, v in multi.items():
                out.append(
                    f"  {k}: {len(v)} processes, VRAM {sum(x.get('mem_mb') or 0 for x in v)} MiB"
                )
    if d.get("displays"):
        out.append("Display outputs: " + "; ".join(d["displays"]))
    return "\n".join(out)


def _ext_temps(d: dict) -> str:
    out = ["", "--- Full thermal reading ---"]
    h = d.get("heaters") or {}
    out.append("Likely heat sources (by sensor):")
    for s in sorted(d.get("sensors") or [], key=lambda x: -x["temp_c"])[:8]:
        out.append(
            f"  {s['label']} {s['temp_c']:.0f} C -> "
            + heaters_for_sensor(
                s["label"],
                h.get("cpu_groups") or [],
                h.get("gpu_procs") or [],
                h.get("io_groups") or [],
            )
        )
    for t in d.get("trips") or []:
        out.append(
            f"Trip points {t['zone']}: "
            + ", ".join(f"{k} {v:.0f} C" for k, v in t["trips"])
        )
    if d.get("cooling_all"):
        out.append(
            "Cooling devices: "
            + ", ".join(
                f"{c['type']} {c['state']}/{c['max']}" for c in d["cooling_all"]
            )
        )
    out.append(
        render_process_section(
            "CPU (heat sources)",
            h.get("cpu_procs") or [],
            h.get("cpu_groups") or [],
            "cpu",
        )
    )
    if h.get("gpu_procs"):
        out.append("GPU processes (heat sources):")
        out += [_gpu_proc_line(p) for p in h["gpu_procs"]]
    return "\n".join(out)


def _ext_disk(d: dict) -> str:
    out = ["", "--- Full disk reading ---"]
    for m in d.get("mounts") or []:
        out.append(f"Mount options {m['mount']}: {m.get('opts', '')}")
    totals = d.get("io_totals") or {}
    io = d.get("io") or {}
    for name, r in io.items():
        t = totals.get(name, {})
        out.append(
            f"{name} detail: read {r['read_iops']:.0f} IOPS, write {r['write_iops']:.0f} IOPS, merged {r['merged_ps']:.0f}/s, avg queue {r['avg_queue']:.2f}, in flight {r['inflight']}, avg request {r['avg_req_kb']:.1f} KB; since boot {t.get('reads', 0):,} reads / {t.get('writes', 0):,} writes, {t.get('rsect', 0) * 512 / 1e9:.1f} GB read, {t.get('wsect', 0) * 512 / 1e9:.1f} GB written, discards {t.get('discards', 0):,}"
        )
    for name, bi in (d.get("block_info") or {}).items():
        size = f"{bi['size_gb']:.0f}" if bi.get("size_gb") else None
        out.append(
            f"{name} device: "
            + _kvs(
                [
                    ("model", bi.get("model")),
                    ("size GB", size),
                    ("rotational", bi.get("rotational")),
                    ("scheduler", bi.get("scheduler")),
                    ("queue depth", bi.get("queue_depth")),
                    ("block size", bi.get("block_size")),
                ]
            )
        )
    psi = d.get("psi") or {}
    for kind, vals in psi.items():
        out.append(
            f"IO pressure (PSI {kind}): "
            + ", ".join(f"{k} {v:.2f}" for k, v in vals.items())
        )
    for sw in d.get("swaps") or []:
        out.append(
            f"Swap device {sw['name']}: {sw['used_kb'] / 1024:.0f} of {sw['size_kb'] / 1024:.0f} MB"
        )
    out.append(
        render_process_section(
            "disk IO", d.get("procs") or [], d.get("groups") or [], "io"
        )
    )
    return "\n".join(out)


def _ext_network(d: dict) -> str:
    out = ["", "--- Full network reading ---", "Per-interface stats:"]
    for i in d.get("ifaces") or []:
        out.append(
            f"  {i['name']}: {_kvs([('state', i.get('state')), ('mtu', i.get('mtu')), ('duplex', i.get('duplex')), ('carrier', i.get('carrier')), ('speed Mbit/s', i.get('speed_mbps')), ('tx queue', i.get('tx_queue_len'))])}; addrs {', '.join(i.get('addrs') or []) or 'n/a'}"
        )
        out.append(
            f"    rx: {i['rx_bytes'] / 1e9:.3f} GB, {i['rx_pkts']:,} pkts, err {i['rx_err']}, drop {i['rx_drop']}, fifo {i['rx_fifo']}, frame {i['rx_frame']}, multicast {i['rx_multicast']}"
        )
        out.append(
            f"    tx: {i['tx_bytes'] / 1e9:.3f} GB, {i['tx_pkts']:,} pkts, err {i['tx_err']}, drop {i['tx_drop']}, fifo {i['tx_fifo']}, colls {i['tx_colls']}, carrier {i['tx_carrier']}"
        )
    sn = d.get("snmp") or {}
    tcp = sn.get("Tcp") or {}
    if tcp:
        outseg = tcp.get("OutSegs") or 0
        ratio = (
            f" ({100 * tcp.get('RetransSegs', 0) / outseg:.2f}% of sent)"
            if outseg
            else ""
        )
        out.append(
            f"TCP: active opens {tcp.get('ActiveOpens', 0):,}, passive opens {tcp.get('PassiveOpens', 0):,}, in segs {tcp.get('InSegs', 0):,}, out segs {outseg:,}, retransmits {tcp.get('RetransSegs', 0):,}{ratio}, in errors {tcp.get('InErrs', 0)}, resets sent {tcp.get('OutRsts', 0):,}"
        )
    udp = sn.get("Udp") or {}
    if udp:
        out.append(
            f"UDP: in {udp.get('InDatagrams', 0):,}, out {udp.get('OutDatagrams', 0):,}, in errors {udp.get('InErrors', 0)}, receive buffer errors {udp.get('RcvbufErrors', 0)}"
        )
    ss = d.get("sockstat") or {}
    if ss:
        out.append(
            "Sockets: "
            + "; ".join(
                f"{k} " + ", ".join(f"{a} {b}" for a, b in v.items())
                for k, v in ss.items()
            )
        )
    ls = d.get("listeners") or []
    out.append(f"All listening sockets ({len(ls)}):")
    out += [
        f"  {p['proto']} {p['addr']}:{p['port']} {p['proc'] or '(other user)'}"
        for p in ls[:40]
    ]
    pp = d.get("per_process") or []
    out.append(
        f"Per-process bandwidth and connections (your processes, {d.get('conn_count', 0)} established TCP connections):"
    )
    conns = d.get("conns") or []
    for r in pp:
        pids = ",".join(str(x) for x in r["pids"][:6]) + (
            "..." if len(r["pids"]) > 6 else ""
        )
        out.append(
            f"  {r['proc']} (pid {pids or 'n/a'}): {r['conns']} connection{'s' if r['conns'] != 1 else ''}, tx {fmt_rate(r['tx_bps'])}, rx {fmt_rate(r['rx_bps'])}, {r['total_bytes'] / 1e6:.1f} MB lifetime"
        )
        for c in [c for c in conns if c["proc"] == r["proc"]][:3]:
            out.append(
                f"      {c['local']} -> {c['peer']}: tx {fmt_rate(c.get('tx_bps'))}, rx {fmt_rate(c.get('rx_bps'))}, {(c['sent'] + c['recv']) / 1e6:.2f} MB"
            )
    if not pp:
        out.append("  (none readable)")
    for i in d.get("ifaces") or []:
        w = i.get("wifi")
        if w:
            out.append(
                f"Wifi {i['name']} all fields: "
                + ", ".join(f"{k}={v}" for k, v in w.items())
            )
    return "\n".join(out)


def _ext_power(d: dict) -> str:
    out = ["", "--- Full power reading ---"]
    for sup in d.get("supplies") or []:
        out.append(
            f"Supply {sup['name']}: "
            + ", ".join(f"{k}={v}" for k, v in sup["values"].items())
        )
    if d.get("profiles_raw"):
        out.append(
            "Power profiles:\n"
            + "\n".join("  " + x for x in d["profiles_raw"].splitlines())
        )
    rap = d.get("rapl") or {}
    if rap:
        out.append(
            "RAPL domains W: " + ", ".join(f"{k} {v:.2f}" for k, v in rap.items())
        )
    for g in d.get("gpu_power") or []:
        out.append(
            f"GPU power {g['name']}: {g['watts']:.1f} W"
            + (f" of {g['limit']:.0f} W limit" if g.get("limit") is not None else "")
        )
    out.append(
        render_process_section(
            "estimated power", d.get("procs") or [], d.get("groups") or [], "power"
        )
    )
    out.append(
        "(estimate = process CPU-time share x CPU package power; wakeups = context switches per second)"
    )
    out.append("Top wakeup sources:")
    out += [fmt_proc_line(r, "power") for r in d.get("wakeups") or []] or [
        "  (none readable)"
    ]
    return "\n".join(out)


_RENDER_RAW: dict[str, Callable[[dict, list[Finding]], tuple[str, str]]] = {
    "cpu": render_cpu,
    "memory": render_memory,
    "gpu": render_gpu,
    "temps": render_temps,
    "disk": render_disk,
    "network": render_network,
    "power": render_power,
    "process": render_process,
}
FINDERS_RAW: dict[str, Callable[[dict], list[Finding]]] = {
    "cpu": cpu_findings,
    "memory": memory_findings,
    "gpu": gpu_findings,
    "temps": temps_findings,
    "disk": disk_findings,
    "network": network_findings,
    "power": power_findings,
    "process": process_findings,
}
AREA_LABEL = {
    "cpu": "CPU",
    "memory": "Memory",
    "gpu": "GPU",
    "temps": "Temperature",
    "disk": "Disk",
    "network": "Network",
    "power": "Power",
    "process": "Process",
}


def _make_render(name: str) -> Callable[[dict, list[Finding]], tuple[str, str]]:
    def render(d: dict, findings: list[Finding]) -> tuple[str, str]:
        if d.get("error"):
            return (
                f"{AREA_LABEL[name]} stats are unavailable: {d['error'][:120]}.",
                f"{AREA_LABEL[name]} collector failed: {d['error']}",
            )
        try:
            say, detail = _RENDER_RAW[name](d, findings)
        except Exception as exc:  # a render bug must never break the tool
            msg = f"{type(exc).__name__}: {exc}"[:120]
            return (
                f"{AREA_LABEL[name]} stats are unavailable: {msg}.",
                f"{AREA_LABEL[name]} render failed: {msg}",
            )
        return _clip(say), detail

    return render


def _make_finder(name: str) -> Callable[[dict], list[Finding]]:
    def find(d: dict) -> list[Finding]:
        if d.get("error"):
            return []
        try:
            return FINDERS_RAW[name](d)
        except Exception:
            return []

    return find


RENDERERS = {n: _make_render(n) for n in _RENDER_RAW}
FINDERS = {n: _make_finder(n) for n in FINDERS_RAW}
