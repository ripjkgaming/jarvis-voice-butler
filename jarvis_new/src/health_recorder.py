"""Opt-in, volatile numeric health history. No I/O or threads on import."""

from __future__ import annotations

import contextlib
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

METRICS = {
    "vad_end_to_agent_speaking_s",
    "vad_end_to_verified_workflow_s",
    "provider_generation_to_first_audio_s",
    "tts_first_audio_s",
    "tool_duration_s",
}
ERRORS = {"quota", "timeout", "cancelled", "tool_failed", "session_error", "unknown"}
METRIC_SCOPES = {
    "vad_end_to_agent_speaking_s": "state_events_not_acoustic_e2e",
    "vad_end_to_verified_workflow_s": "validated_read_workflow_not_spoken_completion",
    "provider_generation_to_first_audio_s": "provider_generation_not_user_e2e",
    "tts_first_audio_s": "request_to_first_audio",
    "tool_duration_s": "tool_call",
}
SCOPES = {
    "state_events_not_acoustic_e2e",
    "validated_read_workflow_not_spoken_completion",
    "provider_generation_not_user_e2e",
    "request_to_first_audio",
    "tool_call",
    "session",
}
SYSTEM_FIELDS = {
    "cpu_busy_pct",
    "iowait_pct",
    "mem_total_bytes",
    "mem_available_bytes",
    "swap_total_bytes",
    "swap_free_bytes",
    "cpu_psi_some_pct",
    "memory_psi_some_pct",
    "io_psi_some_pct",
}


def number(value, maximum=1e15):
    if type(value) not in (int, float):
        raise ValueError("invalid numeric measurement")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError("invalid numeric measurement") from exc
    if not math.isfinite(result) or not 0 <= result <= maximum:
        raise ValueError("invalid numeric measurement")
    return result


@dataclass(frozen=True, slots=True)
class Event:
    at: float
    metric: str | None
    value: float | None
    scope: str
    error: str | None


def validate_event(raw, now):
    if type(raw) is not dict or set(raw) != {"at", "metric", "value", "scope", "error"}:
        raise ValueError("invalid event fields")
    at = number(raw["at"])
    if at > now + 1 or now - at > 1800:
        raise ValueError("invalid event time")
    metric, scope, error = raw["metric"], raw["scope"], raw["error"]
    if type(scope) is not str or scope not in SCOPES:
        raise ValueError("invalid scope")
    if metric is not None and (type(metric) is not str or metric not in METRICS):
        raise ValueError("invalid metric")
    if error is not None and (type(error) is not str or error not in ERRORS):
        raise ValueError("invalid error category")
    if metric is not None and METRIC_SCOPES[metric] != scope:
        raise ValueError("invalid measurement boundary")
    if metric is None and error is None:
        raise ValueError("empty event")
    value = number(raw["value"], 3600) if metric is not None else None
    if metric is None and raw["value"] is not None:
        raise ValueError("unexpected value")
    return Event(at, metric, value, scope, error)


class ProcReader:
    """Only five allowlisted kernel files; no process or personal content."""

    PATHS = ("stat", "meminfo", "pressure/cpu", "pressure/memory", "pressure/io")

    def __call__(self, name):
        if name not in self.PATHS:
            raise ValueError("unsupported kernel source")
        with (Path("/proc") / name).open("rb") as source:
            data = source.read(65537)
        if len(data) > 65536 or len(data.splitlines()) > 256:
            raise ValueError("kernel source exceeded bounds")
        return data.decode("ascii")


class KernelSampler:
    def __init__(self, reader=None):
        self.reader = reader or ProcReader()
        self.cpu = None
        self.psi = {}
        self.at = None

    def reset(self):
        self.cpu, self.psi, self.at = None, {}, None

    def sample(self, now):
        fields = dict.fromkeys(SYSTEM_FIELDS)
        interval = now - self.at if self.at is not None else None
        valid = interval is not None and 0 < interval <= 15
        try:
            line = self.reader("stat").splitlines()[0].split()
            if line[0] != "cpu" or len(line) < 6:
                raise ValueError("missing aggregate CPU")
            counters = [int(x) for x in line[1:9]]
            if any(x < 0 for x in counters):
                raise ValueError("negative counter")
            current = (sum(counters), counters[3], counters[4])
            if valid and self.cpu is not None:
                delta = tuple(a - b for a, b in zip(current, self.cpu))
                total, idle, wait = delta
                if total > 0 and min(delta) >= 0 and idle + wait <= total:
                    fields["cpu_busy_pct"] = 100 * (total - idle - wait) / total
                    fields["iowait_pct"] = 100 * wait / total
            self.cpu = current
        except (OSError, ValueError, IndexError, UnicodeError):
            self.cpu = None
        try:
            memory = {}
            for line in self.reader("meminfo").splitlines():
                parts = line.split()
                if parts and parts[0].rstrip(":") in {
                    "MemTotal",
                    "MemAvailable",
                    "SwapTotal",
                    "SwapFree",
                }:
                    if len(parts) != 3 or parts[2] != "kB":
                        raise ValueError("invalid memory unit")
                    memory[parts[0].rstrip(":")] = number(int(parts[1])) * 1024
            for source, dest in (
                ("MemTotal", "mem_total_bytes"),
                ("MemAvailable", "mem_available_bytes"),
                ("SwapTotal", "swap_total_bytes"),
                ("SwapFree", "swap_free_bytes"),
            ):
                fields[dest] = memory.get(source)
            for total, free in (
                ("mem_total_bytes", "mem_available_bytes"),
                ("swap_total_bytes", "swap_free_bytes"),
            ):
                if (
                    fields[total] is not None
                    and fields[free] is not None
                    and fields[free] > fields[total]
                ):
                    fields[free] = None
        except (OSError, ValueError, UnicodeError):
            pass
        for kind in ("cpu", "memory", "io"):
            try:
                row = next(
                    row
                    for row in self.reader("pressure/" + kind).splitlines()
                    if row.startswith("some ")
                )
                tokens = dict(token.split("=", 1) for token in row.split()[1:])
                total = number(int(tokens["total"]))
                previous = self.psi.get(kind)
                if (
                    valid
                    and previous is not None
                    and 0 <= total - previous <= interval * 1e6
                ):
                    fields[kind + "_psi_some_pct"] = (
                        (total - previous) / (interval * 1e6) * 100
                    )
                self.psi[kind] = total
            except (OSError, ValueError, KeyError, StopIteration, UnicodeError):
                self.psi.pop(kind, None)
        self.at = now
        return fields


def tools_enabled():
    return (
        os.environ.get("JARVIS_HEALTH_TOOLS", "1").lower() in {"1", "true", "on", "yes"}
        and os.environ.get("JARVIS_LOCAL") == "1"
    )


def current_snapshot(*, reader=None):
    """One transient aggregate-kernel sample; no threads, events or history."""
    fields = KernelSampler(reader=reader).sample(time.monotonic())
    available = fields.get("mem_available_bytes")
    total = fields.get("mem_total_bytes")
    memory = (
        f"Current memory available: {available / 2**30:.1f} GiB of {total / 2**30:.1f} GiB. "
        if available is not None and total is not None
        else "Current memory measurement is unavailable. "
    )
    say = (
        memory
        + "Background health recording is off; no recent voice timing or historical cause is available."
    )
    return {
        "mode": "current_snapshot",
        "recording_enabled": False,
        "retained_history": False,
        "say": say,
        "text": say
        + " CPU, I/O wait and pressure rates require multiple samples; no process or acoustic attribution is available.",
        "kernel_snapshot": fields,
        "metrics": {},
        "coverage": {
            "system_samples": int(any(value is not None for value in fields.values())),
            "event_samples": 0,
            "valid_cpu_samples": 0,
        },
    }


class HealthRecorder:
    def __init__(self, *, enabled=False, clock=time.monotonic, reader=None):
        self.enabled = enabled
        self.clock = clock
        self._system = deque(maxlen=360)
        self._events = deque(maxlen=512)
        self._lock = threading.Lock()
        self._sampler = KernelSampler(reader)
        self._last_sample = None
        self._last_ingest = None
        self.dropped = 0
        self._stop = threading.Event()
        self._worker = None

    def _expire(self, now):
        # Arrival order need not equal observation order.
        self._events = deque(
            (x for x in self._events if 0 <= now - x.at <= 1800), maxlen=512
        )
        while self._system and now - self._system[0][0] > 1800:
            self._system.popleft()

    def record_batch(self, raw):
        if not self.enabled:
            return 0
        if type(raw) is not list or len(raw) > 32:
            raise ValueError("invalid batch")
        now = self.clock()
        events = [validate_event(row, now) for row in raw]
        if not self._lock.acquire(blocking=False):
            return 0
        try:
            if not self.enabled:
                return 0
            self._expire(now)
            self.dropped = min(
                2**31 - 1, self.dropped + max(0, len(self._events) + len(events) - 512)
            )
            self._events.extend(events)
            return len(events)
        finally:
            self._lock.release()

    def sample(self):
        if not self.enabled:
            return False
        with self._lock:
            if not self.enabled:
                return False
            now = self.clock()
            if self._last_sample is not None and now - self._last_sample < 5:
                return False
            self._expire(now)
            fields = self._sampler.sample(now)
            self._system.append((now, fields))
            self._last_sample = now
            return True

    def clear(self):
        with self._lock:
            self._system.clear()
            self._events.clear()
            self._sampler.reset()
            self._last_sample = None
            self._last_ingest = None
            self.dropped = 0

    def start(self):
        """Explicit activation only, never imported/constructed as a side effect."""
        if not self.enabled or self._worker is not None:
            return
        self._stop.clear()

        def run():
            while not self._stop.wait(5):
                with contextlib.suppress(Exception):
                    self.sample()

        self._worker = threading.Thread(target=run, name="jarvis-health", daemon=True)
        self._worker.start()

    def close(self):
        self.enabled = False
        self._stop.set()
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.join(timeout=0.1)
        self.clear()

    def report(self, window_s=300):
        if type(window_s) is not int or not 30 <= window_s <= 1800:
            raise ValueError("window must be 30-1800 seconds")
        with self._lock:
            now = self.clock()
            self._expire(now)
            system = tuple(
                (at, dict(row)) for at, row in self._system if 0 <= now - at <= window_s
            )
            events = tuple(x for x in self._events if 0 <= now - x.at <= window_s)
            dropped = self.dropped
        evidence = []
        low_run, previous = 0, None
        for at, row in system:
            total, available = (
                row.get("mem_total_bytes"),
                row.get("mem_available_bytes"),
            )
            low = (
                total is not None
                and total > 0
                and available is not None
                and available / total <= 0.05
            )
            low_run = (
                low_run + 1 if low and (previous is None or at - previous <= 7.5) else 0
            )
            previous = at
            if low_run >= 3:
                evidence.append(
                    "Available host memory was persistently low; allocation pressure is possible."
                )
                break
        # Three consecutive valid samples, with no sampling gap.
        for key, threshold, label in (
            ("cpu_busy_pct", 90, "Sustained high host CPU"),
            ("memory_psi_some_pct", 10, "Sustained memory pressure"),
            ("io_psi_some_pct", 10, "Sustained I/O pressure"),
        ):
            run, previous = 0, None
            for at, row in system:
                value = row.get(key)
                run = (
                    run + 1
                    if value is not None
                    and value >= threshold
                    and (previous is None or at - previous <= 7.5)
                    else 0
                )
                previous = at
                if run >= 3:
                    evidence.append(
                        label
                        + " was observed; correlation does not identify its cause."
                    )
                    break
        errors = sorted({event.error for event in events if event.error})
        if errors:
            evidence.insert(
                0, "Observed voice error categories: " + ", ".join(errors) + "."
            )
        metrics = {}
        for event in events:
            if event.metric:
                current = metrics.setdefault(
                    event.metric, {"n": 0, "max_s": 0.0, "scope": event.scope}
                )
                current["n"] += 1
                current["max_s"] = max(current["max_s"], event.value)
        say = (
            "Health recording is disabled."
            if not self.enabled
            else " ".join(evidence[:2])
            if evidence
            else "No supported cause is established by the available health measurements."
        )
        if self.enabled and not system and not events:
            say = "No recent health measurements are available."
        limitations = [
            "No process or decoder attribution.",
            "Voice state timing is not acoustic end-to-end latency.",
            "Microphone overflow and speaker underrun are unavailable.",
        ]
        return {
            "recording_enabled": self.enabled,
            "say": say,
            "text": say + " " + " ".join(limitations),
            "evidence": evidence[:3],
            "metrics": metrics,
            "coverage": {
                "system_samples": len(system),
                "event_samples": len(events),
                "dropped_events": dropped,
                "event_coverage_s": max(x.at for x in events)
                - min(x.at for x in events)
                if events
                else 0,
                "valid_cpu_samples": sum(
                    row.get("cpu_busy_pct") is not None for _, row in system
                ),
                "system_gaps": sum(
                    b[0] - a[0] > 7.5 for a, b in zip(system, system[1:])
                ),
                "system_coverage_s": system[-1][0] - system[0][0] if system else 0,
                "latest_sample_age_s": now - system[-1][0] if system else None,
                "latest_event_age_s": now - max(x.at for x in events)
                if events
                else None,
            },
            "limitations": limitations,
        }
