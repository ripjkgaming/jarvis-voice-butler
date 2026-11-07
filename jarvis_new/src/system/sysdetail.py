"""Pure parsers and formatters for the *detailed readings* (no IO, no sudo).

Used by system.sysmon: per-process tables, program groups, drm/fdinfo GPU
clients, nvidia pmon, per-process network rates and the extra /proc parsers.
"""

from __future__ import annotations

import re

_USERS_RE = re.compile(r'users:\(\("([^"]+)",pid=(\d+)')
_ADDR_RE = re.compile(r"(\[[0-9a-fA-F:.%\w]+\]:[\w*]+|[\w.\-%*]+:[\w*]+)")
_CPU_KEYS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")


# ---------------------------------------------------------------- /proc


def parse_cpu_fields(text: str) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for ln in text.splitlines():
        p = ln.split()
        if p and re.fullmatch(r"cpu\d*", p[0]):
            try:
                v = [int(x) for x in p[1:9]]
            except ValueError:
                continue
            out[p[0]] = v + [0] * (8 - len(v))
    return out


def cpu_breakdown(a: list[int], b: list[int]) -> dict[str, float]:
    """Percent of time in each CPU state between two cpu lines."""
    delta = [max(0, y - x) for x, y in zip(a, b, strict=False)]
    total = sum(delta)
    if total <= 0:
        return dict.fromkeys(_CPU_KEYS, 0.0)
    return {k: 100.0 * v / total for k, v in zip(_CPU_KEYS, delta, strict=False)}


def parse_stat_counters(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    names = {"intr": "intr", "processes": "forks", "ctxt": "ctxt", "softirq": "softirq"}
    for ln in text.splitlines():
        p = ln.split()
        if len(p) >= 2 and p[0] in names and p[1].isdigit():
            out[names[p[0]]] = int(p[1])
    return out


def _kb(text: str, key: str) -> int | None:
    m = re.search(rf"^{key}:\s*(\d+)", text, re.M)
    return int(m.group(1)) if m else None


def parse_status_extra(text: str) -> dict:
    if not text.strip():
        return {}
    out: dict = {}
    for key, name in (
        ("VmSwap", "swap_kb"),
        ("RssAnon", "rss_anon_kb"),
        ("RssFile", "rss_file_kb"),
        ("RssShmem", "rss_shmem_kb"),
    ):
        v = _kb(text, key)
        if v is not None:
            out[name] = v
    vol, inv = (
        _kb(text, "voluntary_ctxt_switches"),
        _kb(text, "nonvoluntary_ctxt_switches"),
    )
    if vol is not None or inv is not None:
        out["ctx"] = (vol or 0) + (inv or 0)
    return out


def parse_smaps_rollup(text: str) -> dict:
    if not text.strip():
        return {}
    pss = _kb(text, "Pss")
    if pss is None:
        return {}
    return {
        "pss_kb": pss,
        "shared_kb": (_kb(text, "Shared_Clean") or 0)
        + (_kb(text, "Shared_Dirty") or 0),
        "swap_kb": _kb(text, "Swap") or 0,
        "pss_anon_kb": _kb(text, "Pss_Anon") or 0,
    }


def parse_snmp(text: str) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    lines = text.splitlines()
    for head, vals in zip(lines[::2], lines[1::2], strict=False):
        h, v = head.split(), vals.split()
        if h and v and h[0] == v[0]:
            out[h[0].rstrip(":")] = {
                k: int(x)
                for k, x in zip(h[1:], v[1:], strict=False)
                if x.lstrip("-").isdigit()
            }
    return out


def parse_sockstat(text: str) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for ln in text.splitlines():
        name, _, rest = ln.partition(":")
        toks = rest.split()
        out[name.strip()] = {
            k: int(v)
            for k, v in zip(toks[::2], toks[1::2], strict=False)
            if v.isdigit()
        }
    return out


# ---------------------------------------------------------------- GPU


def parse_fdinfo(text: str) -> dict:
    """One /proc/<pid>/fdinfo/<fd> of a DRM file -> client info (or {})."""
    drv = re.search(r"^drm-driver:\s*(\S+)", text, re.M)
    cid = re.search(r"^drm-client-id:\s*(\d+)", text, re.M)
    if not drv or not cid:
        return {}
    engines = {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"^drm-engine-([\w\-]+):\s*(\d+)\s*ns", text, re.M)
        if not m.group(1).startswith(("capacity", "cycles"))
    }
    total = [
        int(m.group(1))
        for m in re.finditer(r"^drm-total-[\w\-]+:\s*(\d+)\s*KiB", text, re.M)
    ]
    mem = [
        int(m.group(1))
        for m in re.finditer(r"^drm-memory-[\w\-]+:\s*(\d+)\s*KiB", text, re.M)
    ]
    return {
        "driver": drv.group(1),
        "client": int(cid.group(1)),
        "engines": engines,
        "mem_kb": sum(total) if total else sum(mem),
    }


def drm_client_rates(before: dict, after: dict, dt: float) -> list[dict]:
    """Per-client GPU engine utilisation (busiest engine) from two fdinfo scans."""
    dt = max(dt, 1e-3)
    rows = []
    for key, a in after.items():
        b = before.get(key, {})
        best, name = 0.0, ""
        for eng, ns in a.get("engines", {}).items():
            prev = b.get("engines", {}).get(eng)
            if prev is None:
                continue
            pct = 100.0 * max(0, ns - prev) / (dt * 1e9)
            if pct >= best:
                best, name = pct, eng
        rows.append(
            {
                "pid": a.get("pid", key[0]),
                "driver": a.get("driver", ""),
                "client": a.get("client"),
                "util_pct": best,
                "engine": name,
                "mem_kb": a.get("mem_kb", 0),
            }
        )
    return sorted(rows, key=lambda r: (-r["util_pct"], -r["mem_kb"]))


def parse_pmon(text: str) -> list[dict]:
    """`nvidia-smi pmon -c 1 -s um` -> per-process sm/mem/enc/dec use."""
    rows = []
    for ln in text.splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        p = ln.split()
        if len(p) < 10 or not p[1].isdigit():
            continue

        def v(x: str) -> int | None:
            return int(x) if x.isdigit() else None

        rows.append(
            {
                "pid": int(p[1]),
                "type": p[2],
                "sm": v(p[3]),
                "mem": v(p[4]),
                "enc": v(p[5]),
                "dec": v(p[6]),
                "name": " ".join(p[9:]),
            }
        )
    return rows


# ---------------------------------------------------------------- network


def parse_ss_conns(text: str) -> list[dict]:
    """`ss -tinp` (any state column style) -> connections with byte counters."""
    conns: list[dict] = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        if not ln[0].isspace():
            head = ln.split("users:")[0]
            addrs = _ADDR_RE.findall(head)
            m = _USERS_RE.search(ln)
            conns.append(
                {
                    "local": addrs[0] if addrs else "?",
                    "peer": addrs[1] if len(addrs) > 1 else "?",
                    "proc": m.group(1) if m else "unknown",
                    "pid": int(m.group(2)) if m else None,
                    "sent": 0,
                    "recv": 0,
                }
            )
        elif conns:
            s = re.search(r"bytes_sent:(\d+)", ln)
            r = re.search(r"bytes_received:(\d+)", ln)
            conns[-1]["sent"] += int(s.group(1)) if s else 0
            conns[-1]["recv"] += int(r.group(1)) if r else 0
            rtt = re.search(r"\brtt:([\d.]+)", ln)
            if rtt:
                conns[-1]["rtt_ms"] = float(rtt.group(1))
    return conns


def net_proc_rates(before: list[dict], after: list[dict], dt: float) -> list[dict]:
    """Per-process tx/rx rates from two ss samples (matched by local+peer)."""
    dt = max(dt, 1e-3)
    prev = {(c["local"], c["peer"]): c for c in before}
    agg: dict[str, dict] = {}
    for c in after:
        row = agg.setdefault(
            c["proc"],
            {
                "proc": c["proc"],
                "pid": c["pid"],
                "pids": set(),
                "conns": 0,
                "tx_bps": 0.0,
                "rx_bps": 0.0,
                "total_bytes": 0,
                "peers": [],
            },
        )
        row["conns"] += 1
        if c["pid"] is not None:
            row["pids"].add(c["pid"])
        row["total_bytes"] += c["sent"] + c["recv"]
        row["peers"].append(c["peer"])
        p = prev.get((c["local"], c["peer"]))
        if p:
            row["tx_bps"] += max(0, c["sent"] - p["sent"]) / dt
            row["rx_bps"] += max(0, c["recv"] - p["recv"]) / dt
    for row in agg.values():
        row["pids"] = sorted(row["pids"])
    return sorted(
        agg.values(), key=lambda r: (-(r["tx_bps"] + r["rx_bps"]), -r["total_bytes"])
    )


# ---------------------------------------------------------------- power / heat


def estimate_process_watts(rows: list[dict], package_w: float | None) -> None:
    """Attach est_w = CPU-time share x package power (an estimate, in place)."""
    total = sum(r.get("cpu_pct", 0.0) for r in rows)
    for r in rows:
        if package_w is None or total <= 0:
            r["est_w"] = None
        else:
            r["est_w"] = package_w * r.get("cpu_pct", 0.0) / total


def heaters_for_sensor(
    label: str, cpu_groups: list[dict], gpu_procs: list[dict], io_groups: list[dict]
) -> str:
    low = label.lower()
    if "nvidia" in low or "gpu" in low:
        names = [f"{p['name']} {p.get('mem_mb', 0)} MiB" for p in gpu_procs[:4]]
        return "GPU processes: " + (", ".join(names) or "none using the GPU")
    if "nvme" in low or "ssd" in low:
        names = [
            f"{g['name']} {fmt_rate(g.get('io_bps'))}"
            for g in io_groups[:4]
            if g.get("io_bps")
        ]
        return "disk IO: " + (", ".join(names) or "no process IO visible")
    if any(k in low for k in ("mt79", "iwl", "wifi", "wlan")):
        return "network traffic (see network_stats for per-process bandwidth)"
    names = [f"{g['name']} {g['cpu_pct']:.0f}% cpu" for g in cpu_groups[:4]]
    return "CPU load: " + (", ".join(names) or "idle")


# ---------------------------------------------------------------- formatting


def fmt_rate(bps: float | None) -> str:
    if bps is None:
        return "n/a"
    return f"{bps / 1e6:.1f} MB/s" if bps >= 1e6 else f"{bps / 1e3:.0f} kB/s"


def fmt_age(secs: float | None) -> str:
    if secs is None:
        return "n/a"
    if secs >= 86400:
        return f"{secs / 86400:.1f}d"
    if secs >= 3600:
        return f"{secs / 3600:.1f}h"
    return f"{secs / 60:.0f}m"


def _mb(kb: float | None) -> str:
    return "n/a" if kb is None else f"{kb / 1024:.0f} MB"


def fmt_proc_line(row: dict, kind: str, total_kb: int = 0) -> str:
    head = f"  pid {row['pid']} {row.get('user', '?')} {row.get('name', '?')}:"
    cmd = (row.get("cmd") or "")[:110]
    tail = f" cmd: {cmd}" if cmd else ""
    if kind == "mem":
        pct = f" {100.0 * row['rss_kb'] / total_kb:.1f}%" if total_kb else ""
        return (
            f"{head} rss {_mb(row.get('rss_kb'))}, pss {_mb(row.get('pss_kb'))}, "
            f"shared {_mb(row.get('shared_kb'))}, swap {_mb(row.get('swap_kb'))},{pct} of RAM{tail}"
        )
    if kind == "io":
        return (
            f"{head} read {fmt_rate(row.get('read_bps'))}, "
            f"write {fmt_rate(row.get('write_bps'))}, rss {_mb(row.get('rss_kb'))}{tail}"
        )
    if kind == "power":
        w = row.get("est_w")
        est = f"~{w:.2f} W" if w is not None else "~n/a W"
        ctx = row.get("ctx_ps")
        wk = f"{ctx:.0f} wakeups/s" if ctx is not None else "n/a wakeups/s"
        return f"{head} {est}, {wk}, cpu {row.get('cpu_pct', 0):.1f}%{tail}"
    return (
        f"{head} cpu {row.get('cpu_pct', 0):.1f}% threads {row.get('threads', '?')} "
        f"state {row.get('state', '?')} run {fmt_age(row.get('age_s'))}{tail}"
    )


def _group_total(g: dict, kind: str, total_kb: int) -> str:
    if kind == "mem":
        pss = f", pss {_mb(g['pss_kb'])}" if g.get("pss_kb") else ""
        swap = f", swap {_mb(g['swap_kb'])}" if g.get("swap_kb") else ""
        return f"total rss {_mb(g['rss_kb'])}{pss}{swap}"
    if kind == "io":
        return f"total io {fmt_rate(g.get('io_bps'))}"
    if kind == "power":
        w = g.get("est_w")
        return f"total ~{w:.2f} W" if w is not None else "total ~n/a W"
    return (
        f"total {g['cpu_pct']:.1f}% cpu, {g.get('threads', 0)} threads, "
        f"rss {_mb(g['rss_kb'])}"
    )


_TITLES = {"cpu": "CPU", "mem": "memory", "io": "disk IO", "power": "estimated power"}


def render_process_section(
    title: str,
    rows: list[dict],
    groups: list[dict],
    kind: str,
    total_kb: int = 0,
    max_groups: int = 8,
) -> str:
    """Individual table, then every program group with its total and members."""
    out = [f"Top {len(rows)} processes by {title}:"]
    out += [fmt_proc_line(r, kind, total_kb) for r in rows] or ["  (none)"]
    shown = [g for g in groups if g.get("count", 1) > 1][:max_groups]
    if shown:
        out.append("Program groups (total, then members):")
        for g in shown:
            n = g["count"]
            out.append(
                f"  {g['name']}: {n} process{'es' if n != 1 else ''}, "
                f"{_group_total(g, kind, total_kb)}"
            )
            out += [
                "  " + fmt_proc_line(m, kind, total_kb) for m in g.get("members", [])
            ]
    return "\n".join(out)
