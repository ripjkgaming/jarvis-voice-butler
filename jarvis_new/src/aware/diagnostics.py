"""Lightweight host diagnostics for Always Aware.

The collector is deliberately fail-soft: unsupported hardware and integrations
are reported as ``unavailable`` rather than being treated as healthy. Slow
probes are cached on disk, as are active incidents, so repeated polls do not
repeat the same speech candidate.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import stat
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Callable

SLOW_CHECKS = frozenset(
    {"network", "jarvis", "whatsapp", "calendar", "camera", "input"}
)
AVAILABILITY_CHECKS = ("jarvis", "whatsapp", "calendar", "camera", "input")
SERVICE_FAILURE_THRESHOLD = 2
CRITICAL_HARDWARE = frozenset({"disk", "memory", "temperature", "battery"})
SEVERITY = {"healthy": 0, "unavailable": 1, "degraded": 2, "critical": 3}
UNAVAILABLE = {
    "status": "unavailable",
    "detail": "No verified probe is available",
    "actionable": False,
}


def _default_state_path() -> Path:
    home = os.environ.get("JARVIS_HOME", "").strip()
    base = Path(home) if home else Path.home() / ".jarvis"
    return base / "aware" / "diagnostics.json"


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)


def _result(status: str, detail: str, **fields) -> dict:
    return {"status": status, "detail": detail, **fields}


def _cpu_load() -> dict:
    try:
        one, five, fifteen = os.getloadavg()
        cores = max(os.cpu_count() or 1, 1)
        ratio = one / cores
        status = "critical" if ratio >= 2 else "degraded" if ratio >= 1 else "healthy"
        speech = (
            "The computer is under sustained heavy CPU load."
            if status != "healthy"
            else None
        )
        return _result(
            status,
            f"1-minute load {one:.2f} across {cores} cores",
            load_1m=one,
            load_5m=five,
            load_15m=fifteen,
            cores=cores,
            load_per_core=ratio,
            speech=speech,
        )
    except (AttributeError, OSError):
        return _result(
            "unavailable", "CPU load average is unsupported", actionable=False
        )


def _temperature(sys_root: Path) -> dict:
    readings = []
    try:
        for path in (sys_root / "class/thermal").glob("thermal_zone*/temp"):
            try:
                raw = float(path.read_text().strip())
            except (OSError, ValueError):
                continue
            celsius = raw / 1000 if raw > 200 else raw
            if -40 <= celsius <= 150:
                readings.append(
                    {"sensor": path.parent.name, "celsius": round(celsius, 1)}
                )
    except OSError:
        pass
    if not readings:
        return _result("unavailable", "No readable thermal sensor", actionable=False)
    hottest = max(item["celsius"] for item in readings)
    status = "critical" if hottest >= 90 else "degraded" if hottest >= 80 else "healthy"
    return _result(
        status,
        f"Highest reported temperature {hottest:.1f} °C",
        celsius=hottest,
        sensors=readings,
        speech="The computer is overheating." if status != "healthy" else None,
    )


def _memory(proc_root: Path) -> dict:
    try:
        values = {}
        for line in (proc_root / "meminfo").read_text().splitlines():
            key, sep, rest = line.partition(":")
            if sep:
                parts = rest.split()
                if parts:
                    values[key] = int(parts[0]) * 1024
        total = values["MemTotal"]
        available = values.get("MemAvailable", values.get("MemFree", 0))
        if total <= 0:
            raise ValueError("empty memory total")
        used = max(0, total - available)
        percent = used * 100 / total
        status = (
            "critical" if percent >= 95 else "degraded" if percent >= 90 else "healthy"
        )
        return _result(
            status,
            f"{percent:.1f}% memory used",
            total_bytes=total,
            available_bytes=available,
            used_percent=round(percent, 1),
            speech="Available memory is critically low."
            if status == "critical"
            else None,
        )
    except (OSError, KeyError, ValueError):
        return _result(
            "unavailable", "Memory statistics could not be read", actionable=False
        )


def _disk(root_path: Path, disk_usage: Callable = shutil.disk_usage) -> dict:
    try:
        usage = disk_usage(root_path)
        percent = usage.used * 100 / max(usage.total, 1)
        status = (
            "critical" if percent >= 97 else "degraded" if percent >= 90 else "healthy"
        )
        return _result(
            status,
            f"{percent:.1f}% disk space used",
            total_bytes=usage.total,
            free_bytes=usage.free,
            used_percent=round(percent, 1),
            speech="Disk space is critically low." if status == "critical" else None,
        )
    except OSError as exc:
        return _result(
            "unavailable",
            f"Disk space could not be read: {type(exc).__name__}",
            actionable=False,
        )


def _battery(sys_root: Path) -> dict:
    batteries = []
    try:
        for base in (sys_root / "class/power_supply").glob("*"):
            try:
                if (base / "type").read_text().strip().lower() != "battery":
                    continue
                capacity = int((base / "capacity").read_text().strip())
                state = (base / "status").read_text().strip().lower()
                batteries.append(
                    {
                        "name": base.name,
                        "percent": capacity,
                        "charging": state in {"charging", "full"},
                        "state": state,
                    }
                )
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    if not batteries:
        return _result("unavailable", "No readable battery", actionable=False)
    lowest = min(batteries, key=lambda b: b["percent"])
    status = (
        "critical"
        if lowest["percent"] <= 5 and not lowest["charging"]
        else "degraded"
        if lowest["percent"] <= 15 and not lowest["charging"]
        else "healthy"
    )
    return _result(
        status,
        f"Battery {lowest['percent']}% ({lowest['state']})",
        batteries=batteries,
        speech="Battery is critically low; connect power."
        if status == "critical"
        else None,
    )


def _network(connect: Callable = socket.create_connection) -> dict:
    try:
        with connect(("1.1.1.1", 53), timeout=1.5):
            pass
        return _result("healthy", "Internet connectivity verified")
    except OSError as exc:
        return _result(
            "degraded",
            f"Internet connectivity check failed: {type(exc).__name__}",
            speech="Internet connectivity appears to be down.",
        )


def _jarvis_probe() -> dict:
    port = os.environ.get("JARVIS_BRIDGE_PORT", "4317")
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{int(port)}/status", timeout=1.0
        ) as response:
            if response.status == 200:
                return _result("healthy", "Jarvis bridge status endpoint responded")
        return _result(
            "degraded",
            "Jarvis bridge returned a non-success status",
            speech="The Jarvis service is not responding normally.",
        )
    except Exception as exc:
        return _result(
            "unavailable",
            f"Jarvis status endpoint unavailable: {type(exc).__name__}",
            actionable=True,
            speech="The Jarvis service health check is unavailable.",
        )


def _whatsapp_probe() -> dict:
    try:
        from system import whatsapp

        if not whatsapp.alive():
            return _result(
                "unavailable",
                "WhatSie WhatsApp debug endpoint is not responding",
                actionable=True,
                speech="WhatsApp is not reachable.",
            )
        if whatsapp.page_present():
            return _result("healthy", "WhatsApp Web page is available in WhatSie")
        return _result(
            "degraded",
            "WhatSie is responding but WhatsApp Web is not open",
            speech="WhatsApp is open without a WhatsApp Web page.",
        )
    except Exception as exc:
        return _result(
            "unavailable",
            f"WhatsApp status could not be checked: {type(exc).__name__}",
            actionable=True,
        )


def _calendar_probe() -> dict:
    try:
        from auto_calendar_bridge import status

        data = status()
        calendar = data.get("calendar", {})
        if calendar.get("connected"):
            return _result(
                "healthy", calendar.get("detail", "Calendar connection verified")
            )
        if calendar.get("configured"):
            return _result(
                "degraded",
                calendar.get("detail", "Calendar connection not verified"),
                speech="Google Calendar needs a connection check.",
            )
        return _result(
            "unavailable",
            calendar.get("detail", "Calendar is not configured"),
            actionable=False,
        )
    except Exception as exc:
        return _result(
            "unavailable",
            f"Calendar status could not be checked: {type(exc).__name__}",
            actionable=True,
        )


def _camera_probe(now: float) -> dict:
    try:
        import camera_hub

        metadata = camera_hub.latest_meta()
        frame_at = float(metadata.get("ts", 0))
        if not frame_at:
            return _result(
                "unavailable", "No camera frame has been published", actionable=False
            )
        age = max(0.0, now - frame_at)
        if age > 10:
            return _result(
                "degraded",
                f"Latest camera frame is {age:.0f}s old",
                age_s=age,
                speech="The camera feed appears stale.",
            )
        return _result("healthy", f"Camera frame is {age:.1f}s old", age_s=age)
    except Exception as exc:
        return _result(
            "unavailable",
            f"Camera status could not be checked: {type(exc).__name__}",
            actionable=True,
        )


def _input_probe() -> dict:
    """Report input-device access only; this does not verify an exercise lock."""
    device = Path("/dev/uinput")
    try:
        accessible = stat.S_ISCHR(device.stat().st_mode) and os.access(device, os.W_OK)
        if accessible:
            return _result(
                "healthy",
                "Input device is writable; exercise blocking is not verified",
                exercise_blocking_verified=False,
            )
        return _result(
            "unavailable",
            "Input device is present but not writable",
            actionable=False,
            exercise_blocking_verified=False,
        )
    except OSError:
        return _result(
            "unavailable",
            "No accessible input-control device",
            actionable=False,
            exercise_blocking_verified=False,
        )


def _normalize_probe(name: str, fn: Callable) -> dict:
    try:
        value = fn()
        if isinstance(value, str):
            value = {"status": value}
        if not isinstance(value, dict):
            raise ValueError("probe returned a non-dictionary")
        status = str(value.get("status", "unavailable")).lower()
        if status not in {"healthy", "degraded", "critical", "unavailable"}:
            status = "unavailable"
        return {
            "status": status,
            "detail": str(value.get("detail", "")),
            **{k: v for k, v in value.items() if k not in {"status", "detail"}},
        }
    except Exception as exc:
        return _result(
            "unavailable", f"{name} probe failed: {type(exc).__name__}", actionable=True
        )


def _cached_reading(cache: dict, name: str, now: float, interval: float) -> dict | None:
    record = cache.get(name)
    if not isinstance(record, dict):
        return None
    try:
        fresh = now - float(record.get("checked_at", 0)) < interval
    except (TypeError, ValueError):
        return None
    reading = record.get("reading")
    if not fresh or not isinstance(reading, dict):
        return None
    return {**reading, "cached": True}


def _incident_events(
    readings: dict, previous: dict, now: float
) -> tuple[dict, dict, list[dict]]:
    raw_active = previous.get("active_incidents", {})
    active = dict(raw_active) if isinstance(raw_active, dict) else {}
    raw_counts = previous.get("failure_counts", {})
    failure_counts = dict(raw_counts) if isinstance(raw_counts, dict) else {}
    events = []
    unhealthy = {"degraded", "critical", "unavailable"}
    for name, reading in readings.items():
        key = f"diagnostics:{name}"
        status = reading.get("status")
        if name in AVAILABILITY_CHECKS and reading.get("cached") is True:
            # A cached service result is the same observation, not a fresh
            # consecutive failure (or a new recovery/escalation signal).
            continue
        if status == "healthy":
            failure_counts.pop(name, None)
            if key in active:
                incident = active.pop(key)
                events.append(
                    {
                        "kind": "recovered",
                        "key": key,
                        "name": name,
                        "status": "healthy",
                        "started_at": incident.get("started_at"),
                        "recovered_at": now,
                        "speech": f"{name.replace('_', ' ').title()} has recovered.",
                    }
                )
            continue

        # Unavailable without an actionable failure means the probe cannot
        # establish service state. It is neither a failure nor recovery.
        if status not in unhealthy or not reading.get("actionable", True):
            continue

        if name in AVAILABILITY_CHECKS:
            failure_counts[name] = int(failure_counts.get(name, 0)) + 1
            if failure_counts[name] < SERVICE_FAILURE_THRESHOLD and name not in active:
                continue

        if name in CRITICAL_HARDWARE and status == "critical":
            failure_counts.pop(name, None)

        if key not in active:
            incident = {
                "key": key,
                "name": name,
                "status": reading["status"],
                "detail": reading.get("detail", ""),
                "started_at": now,
                "announced_status": status,
            }
            active[key] = incident
            events.append(
                {
                    "kind": "started",
                    **incident,
                    "speech": reading.get("speech")
                    or f"{name.replace('_', ' ').title()} needs attention.",
                }
            )
        else:
            incident = active[key]
            announced = incident.get(
                "announced_status", incident.get("status", "degraded")
            )
            incident["status"] = status
            incident["detail"] = reading.get("detail", "")
            if SEVERITY.get(status, 0) > SEVERITY.get(announced, 0):
                incident["announced_status"] = status
                events.append(
                    {
                        "kind": "escalated",
                        "key": key,
                        "name": name,
                        "status": status,
                        "detail": reading.get("detail", ""),
                        "started_at": incident.get("started_at"),
                        "speech": reading.get("speech")
                        or f"{name.replace('_', ' ').title()} has become critical.",
                    }
                )
    return active, failure_counts, events


def sample(
    now: float | None = None,
    *,
    state_path: str | Path | None = None,
    proc_root: str | Path = "/proc",
    sys_root: str | Path = "/sys",
    root_path: str | Path = "/",
    probes: dict[str, Callable] | None = None,
    slow_probes: set[str] | frozenset[str] = SLOW_CHECKS,
    slow_interval_s: float = 300.0,
    disk_usage: Callable = shutil.disk_usage,
    network_connect: Callable = socket.create_connection,
    jarvis_probe: Callable = _jarvis_probe,
) -> dict:
    """Collect host readings, verified integration availability and incidents.

    ``probes`` injects named callables for ``jarvis``, ``whatsapp``,
    ``calendar``, ``camera`` and ``input`` availability, and can override any
    built-in reading. ``slow_probes`` identifies probe names that must be
    throttled.
    """
    timestamp = time.time() if now is None else float(now)
    path = Path(state_path) if state_path is not None else _default_state_path()
    proc, sysfs, root = Path(proc_root), Path(sys_root), Path(root_path)
    state = _read_json(path)
    cached = state.get("slow_cache", {})
    if not isinstance(cached, dict):
        cached = {}
    providers = dict(probes or {})
    builtins = {
        "cpu": _cpu_load,
        "temperature": lambda: _temperature(sysfs),
        "memory": lambda: _memory(proc),
        "disk": lambda: _disk(root, disk_usage),
        "battery": lambda: _battery(sysfs),
        "network": lambda: _network(network_connect),
        "jarvis": jarvis_probe,
        "whatsapp": _whatsapp_probe,
        "calendar": _calendar_probe,
        "camera": lambda: _camera_probe(timestamp),
        "input": _input_probe,
    }
    builtins.update(providers)
    readings = {}
    for name, fn in builtins.items():
        cached_value = (
            _cached_reading(cached, name, timestamp, slow_interval_s)
            if name in slow_probes
            else None
        )
        if cached_value is not None:
            reading = cached_value
        else:
            reading = _normalize_probe(name, fn)
            if name in slow_probes:
                cached[name] = {"checked_at": timestamp, "reading": reading}
        readings[name] = reading

    for name in ("whatsapp", "calendar", "camera", "input"):
        readings.setdefault(name, dict(UNAVAILABLE))

    previous = state
    active, failure_counts, events = _incident_events(readings, previous, timestamp)
    state.update(
        {
            "slow_cache": cached,
            "active_incidents": active,
            "failure_counts": failure_counts,
            "updated_at": timestamp,
        }
    )
    with contextlib.suppress(OSError):
        _write_json(path, state)
    availability = {
        name: readings.get(name, dict(UNAVAILABLE)) for name in AVAILABILITY_CHECKS
    }
    return {
        "timestamp": timestamp,
        "readings": readings,
        "availability": availability,
        "incidents": events,
        "active_incidents": list(active.values()),
    }
