"""Independent, bounded poll components with persistent single-flight jobs.

A timeout bounds how long a poll waits, not the lifetime of a browser operation.
The next poll attaches to the original operation; it never starts a second one.
Daemon workers cannot block process shutdown. Readers check controls between IO.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from aware import log as alog
from aware import state


def allowed(now=None):
    s = state.load()
    return (
        state.env_allows()
        and s.get("enabled")
        and not state.killed()
        and not state.paused(s, now)
    )


@dataclass
class Job:
    started: float
    done: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: str = ""
    finished: float | None = None


class Coordinator:
    def __init__(
        self, components: dict[str, Callable], *, timeouts=None, now=time.time
    ):
        self.components = components
        self.timeouts = {
            "whatsapp": 45.0,
            "diagnostics": 5.0,
            "screen": 45.0,
            "calendar": 45.0,
            "memory": 10.0,
        }
        self.timeouts.update(timeouts or {})
        self.now = now
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self.latest = {}

    def _start(self, name, fn):
        job = Job(time.monotonic())
        self.jobs[name] = job

        def run():
            try:
                if allowed(self.now()):
                    job.result = fn()
                else:
                    job.result = {"outcome": "skip", "reason": "controls"}
            except Exception as exc:
                job.error = type(exc).__name__
            finally:
                job.finished = time.monotonic()
                job.done.set()

        threading.Thread(target=run, name=f"aware-{name}", daemon=True).start()
        return job

    def poll(self):
        now = self.now()
        if not allowed(now):
            return {"outcome": "skip", "reason": "controls"}
        with self._lock:
            # Completed results from prior polls are consumed once before a new run.
            for name, fn in self.components.items():
                if name not in self.jobs:
                    self._start(name, fn)
            for name, job in self.jobs.items():
                remaining = (
                    job.started + self.timeouts.get(name, 10.0) - time.monotonic()
                )
                job.done.wait(max(0.0, remaining))
            results = {}
            for name, job in list(self.jobs.items()):
                if job.done.is_set():
                    results[name] = (
                        {"status": "error", "error": job.error, "retry": True}
                        if job.error
                        else {"status": "ok", "result": job.result}
                    )
                    results[name]["elapsed_s"] = round(job.finished - job.started, 3)
                    results[name]["deadline_exceeded"] = (
                        job.finished - job.started > self.timeouts.get(name, 10.0)
                    )
                    del self.jobs[name]
                else:
                    results[name] = {
                        "status": "running",
                        "timed_out": True,
                        "retry": "attach",
                        "elapsed_s": round(time.monotonic() - job.started, 2),
                    }
            row = {"outcome": "poll", "components": results, "timestamp": now}
            self.latest = row
            # Operational status carries outcomes, never entire chat payloads.
            summary = {
                name: summarize(name, result) for name, result in results.items()
            }
            state.write_private(
                state.aware_dir() / "poll-status.json",
                json.dumps({"timestamp": now, "components": summary}),
            )
            alog.write({"outcome": "poll", "event": "poll", "components": summary}, now)
            return row


def summarize(name, result):
    summary = {k: v for k, v in result.items() if k != "result"}
    payload = result.get("result")
    if not isinstance(payload, dict):
        return summary
    for key in (
        "outcome",
        "reason",
        "selected",
        "started_at",
        "staged",
        "duplicates",
        "processed",
        "recorded",
        "skipped",
        "retrying",
        "retries",
    ):
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)):
            summary[key] = value
    if name == "whatsapp":
        summary["chats"] = [
            {
                "position": i + 1,
                "status": chat.get("status", "unknown"),
                "messages": chat.get("messages", 0),
            }
            for i, chat in enumerate(payload.get("chats", []))
            if isinstance(chat, dict)
        ]
    if name == "diagnostics":
        summary["readings"] = {
            metric: {
                k: reading[k]
                for k in ("status", "value", "unit", "checked_at", "cached")
                if k in reading
            }
            for metric, reading in payload.get("readings", {}).items()
        }
        summary["incidents"] = len(payload.get("incidents", []))
    if name == "screen" and isinstance(payload.get("exercise"), dict):
        summary["exercise"] = payload["exercise"]
    return summary


def live(screen, speak=None):
    """Lazy imports let the sidecar start even when an integration is unavailable."""

    from aware.expansion_runtime import Runtime

    runtime = Runtime()

    def assess():
        row = screen()
        decision = runtime.assess(row)
        return {**row, "exercise": decision}

    def scan():
        import asyncio

        from aware.whatsapp_scan import scan

        return asyncio.run(scan())

    def diagnostics():
        from aware.diagnostics import sample

        return runtime.health(sample(), speak=speak)

    def calendar():
        from aware.calendar_stream import process

        return process()

    def memory():
        from aware.expansion_memory import process_pending, retry_pending, status

        result = process_pending()
        if allowed():
            retry_pending()
        return {**result, **status()}

    return Coordinator(
        {
            "whatsapp": scan,
            "diagnostics": diagnostics,
            "screen": assess,
            "calendar": calendar,
            "memory": memory,
        }
    )


def status():
    report = {}
    with contextlib.suppress(OSError, ValueError):
        loaded = json.loads((state.aware_dir() / "poll-status.json").read_text())
        if isinstance(loaded, dict):
            report = loaded
    with contextlib.suppress(OSError, ValueError, TypeError):
        receipt = json.loads((state.aware_dir() / "whatsapp-scan.json").read_text())
        if isinstance(receipt, dict):
            report["whatsapp_scan"] = summarize("whatsapp", {"result": receipt})
    with contextlib.suppress(OSError, ValueError):
        decision = json.loads((state.aware_dir() / "exercise-status.json").read_text())
        if isinstance(decision, dict):
            report["exercise"] = decision
    return report
