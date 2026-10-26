"""Authenticated owner control plane and explicit opt-in collector/text host."""

from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import threading
import time
from pathlib import Path

from event_monitor.api import MonitorAPI, local_owner_guard
from event_monitor.core import Store, Worker
from event_monitor_host import EventHost
from event_monitor_summary import (
    DesktopAlert,
    SummaryProcessor,
    deliver_alerts,
    screen_unlocked,
)


def authority(home):
    """Read-only local privacy boundary. Unknown/active voice state refuses."""
    try:
        local_owner_guard()
        if not screen_unlocked():
            return None
        import phone_session
        import watch_mode

        if phone_session.has_private_calls() or watch_mode.guest_required():
            return None
        # Room/waking markers are not expired here: stale/unknown state stays held.
        for name in ("hud_room", "hud_waking"):
            path = home / name
            if path.exists():
                if path.is_symlink() or path.stat().st_uid != os.getuid():
                    return None
                if name == "hud_waking" or path.read_text().strip():
                    return None
        ticket = phone_session.approval_ticket()
        return (
            "desktop-owner" if ticket is None else f"owner-{id(ticket[0])}-{ticket[1]}"
        )
    except Exception:  # noqa: BLE001 - fail closed at injected host/SDK boundaries
        return None


class DesktopAPI(MonitorAPI):
    processor = None
    stop_callback = None

    def execute(self, operation, body=None):
        if (
            operation == "create"
            and isinstance(body, dict)
            and "session" in body.get("outputs", [])
            and body.get("session_scope") != "desktop"
        ):
            raise ValueError("Only desktop non-audio event sessions are integrated")
        ticket = self.authorize()
        result = super().execute(operation, body)
        if operation == "stop" and self.stop_callback:
            self.stop_callback(body["id"])
        if self.processor and operation == "status":
            result["recent_results"] = self.processor.recent_summaries(
                self.owner, body["id"]
            )
        if self.processor and operation == "list":
            for row in result:
                row["recent_results"] = self.processor.recent_summaries(
                    self.owner, row["id"], 1
                )
        if self.processor and operation in {"status", "list"}:
            status = self.processor.status()
            if operation == "status":
                result["processing_status"] = status
            else:
                for row in result:
                    row["processing_status"] = status
        self.authorize(ticket)
        return result


class Runtime:
    def __init__(
        self,
        home,
        *,
        guard=None,
        adapters=None,
        engine=None,
        summary_client=None,
        alert=None,
    ):
        home = Path(home)
        if home.is_symlink():
            raise PermissionError("Home symlink refused")
        home = home.resolve(strict=True)
        if home.stat().st_uid != os.getuid() or home.stat().st_mode & 0o077:
            raise PermissionError("Existing private owner home required")
        self.home = home
        self.stop = threading.Event()
        source_guard = guard or (lambda: authority(home))

        def guard():
            if self.stop.is_set():
                return None
            value = source_guard()
            return None if self.stop.is_set() else value

        self.guard = guard
        folder = home / "event_monitor"
        if folder.is_symlink() or (
            folder.exists()
            and (folder.stat().st_uid != os.getuid() or folder.stat().st_mode & 0o077)
        ):
            raise PermissionError("Private monitor directory required")
        if (folder / "monitors.sqlite").is_symlink():
            raise PermissionError("Monitor state symlink refused")
        self.store = Store(home / "event_monitor/monitors.sqlite")
        self.processor = SummaryProcessor(
            home / "event_monitor/results.sqlite",
            self.store,
            client=summary_client,
            cancelled=self.stop.is_set,
        )
        self.host = EventHost(
            home / "event_monitor/text-host.sqlite",
            guard=self.guard,
            engine=engine,
            processor=self.processor,
        )
        self.alert = alert or DesktopAlert(
            guard=self.guard, lifecycle_lock=self.host.lock
        )
        self.api = DesktopAPI(self.store)
        self.api.processor = self.processor
        self.api.stop_callback = self.cancel_monitor

        def authorize(ticket=None):
            value = local_owner_guard(ticket)
            if not screen_unlocked():
                raise PermissionError("Local owner/lock/privacy unavailable")
            return value

        self.api.authorize = authorize
        from event_monitor.triage import FreeTriage

        self.triage = FreeTriage(self.store, client=summary_client)
        self.adapters = adapters
        self.thread = None
        self.lock = None
        self.error = None

    def pass_once(self, *, now=None, quiet=None):
        import jobs
        from event_monitor.adapters import F1Adapters

        explicit_now = now is not None
        now = time.time() if now is None else now
        self.host.provider()  # retire expired/revoked sessions even with no batches
        Worker(
            self.store,
            self.adapters or F1Adapters().mapping(),
            triage=self.tracked_triage,
            registry=jobs.Registry(self.home / "job_registry"),
        ).collect(now)
        if self.stop.is_set():
            return 0
        effective_now = now if explicit_now else time.time()
        self.processor.record_status("triage", self.triage.last_status, effective_now)
        self.processor.process_pending(now if explicit_now else None)
        if self.stop.is_set():
            return 0
        self.reconcile_sessions()
        effective_now = now if explicit_now else time.time()
        accepted = self.host.dispatch(self.store, effective_now, quiet=quiet)
        if not self.stop.is_set():
            deliver_alerts(
                self.processor,
                effective_now,
                self.guard,
                self.alert,
                quiet=quiet,
                lifecycle_lock=self.host.lock,
            )
        return accepted

    def tracked_triage(self, events):
        if os.environ.get("JARVIS_MONITOR_TRIAGE", "0") != "1":
            self.triage.last_status = "disabled"
        result = self.triage(events)
        valid = (
            isinstance(result, dict)
            and set(result) == {"ids"}
            and isinstance(result["ids"], list)
            and all(type(i) is str for i in result["ids"])
            and len(set(result["ids"])) == len(result["ids"])
            and not set(result["ids"]) - {e.key for e in events}
        )
        if self.triage.last_status == "response-awaiting-schema-validation":
            self.triage.last_status = (
                "available" if valid else "invalid-response-deterministic-fallback"
            )
        return result

    def cancel_monitor(self, monitor_id):
        with self.store.db() as db:
            keys = {
                r[0]
                for r in db.execute(
                    "SELECT id FROM batches WHERE monitor=?", (monitor_id,)
                )
            }
        self.host.cancel_keys(keys)

    def reconcile_sessions(self):
        with self.store.db() as db:
            keys = {
                r[0]
                for r in db.execute(
                    "SELECT b.id FROM batches b JOIN monitors m ON m.id=b.monitor WHERE m.state!='active'"
                )
            }
        self.host.cancel_keys(keys)

    def start(self):
        if self.thread and self.thread.is_alive():
            return False
        if (self.home / "event_monitor/worker.lock").is_symlink():
            raise PermissionError("Worker lock symlink refused")
        lock = open(self.home / "event_monitor/worker.lock", "a")  # noqa: SIM115 - worker owns lock until its finally block
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return False
        self.lock = lock

        def run():
            try:
                while not self.stop.is_set():
                    try:
                        self.pass_once()
                        self.error = None
                    except Exception as exc:  # noqa: BLE001 - fixed error class only, no raw details
                        self.error = type(exc).__name__
                    self.stop.wait(60)
            finally:
                self.host.close()
                self.lock.close()
                self.lock = None

        self.thread = threading.Thread(
            target=run, name="event-monitor-worker", daemon=True
        )
        self.thread.start()
        return True

    def close(self):
        # Request stop immediately, then drain the shared effect fence.
        # This prevents the next alert/pass from winning repeated lock reacquisitions.
        self.stop.set()
        with self.host.lock:
            self.host.close()
        if self.thread:
            # A bounded pass can contain ten monitors × two eight-second fetches.
            # Do not release singleton ownership while its pass is still running.
            self.thread.join(1)
            return not self.thread.is_alive()
        self.host.close()
        return True


_RUNTIME = None
_RUNTIME_LOCK = threading.Lock()


def runtime():
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = Runtime(
                Path(os.environ.get("JARVIS_HOME") or Path.home() / ".jarvis")
            )
        return _RUNTIME


class LazyAPI:
    """Tool construction has no state writes; actual owner requests initialize it."""

    def execute(self, operation, body=None):
        local_owner_guard()
        return runtime().api.execute(operation, body)


def start_service():
    if os.environ.get("JARVIS_EVENT_MONITOR", "0") != "1":
        return False
    return runtime().start()


def close_service():
    if _RUNTIME is not None:
        return _RUNTIME.close()
    return True


def endpoint(
    method, path, query, body, *, authorized, peer, api_factory=None, host_factory=None
):
    try:
        if not authorized or not ipaddress.ip_address(peer).is_loopback:
            return 403, {"error": "authenticated-local-owner-required"}
        factory = api_factory or (lambda: runtime().api)
        if api_factory is None:
            local_owner_guard()
        if path == "/event-monitor/session" and method == "GET" and not query:
            api = factory()
            ticket = api.authorize()
            host = (host_factory or (lambda: runtime().host))()
            result = host.view(api.owner)
            if getattr(api, "processor", None):
                result["recent_results"] = api.processor.recent_summaries(api.owner)
                result["processing_status"] = api.processor.status()
            api.authorize(ticket)
            return 200, result
        operation = path.removeprefix("/event-monitor/")
        if method == "GET" and operation == "list" and not query:
            payload = {}
        elif (
            method == "GET"
            and operation == "status"
            and set(query) == {"id"}
            and len(query["id"]) == 1
        ):
            payload = {"id": query["id"][0]}
        elif (
            method == "POST"
            and operation in {"create", "list", "status", "stop"}
            and isinstance(body, dict)
            and not query
        ):
            payload = body
        else:
            return 400, {"error": "invalid-monitor-request"}
        return 200, factory().execute(operation, payload)
    except PermissionError:
        return 403, {"error": "owner-authority-unavailable"}
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "invalid-or-inaccessible-monitor"}
    except Exception:  # noqa: BLE001 - fail closed at injected host/SDK boundaries
        return 503, {"error": "monitor-unavailable"}


def handle(handler, method, parsed):
    from urllib.parse import parse_qs

    from bridge import _read_json_body

    body = _read_json_body(handler, 8192) if method == "POST" else None
    code, result = endpoint(
        method,
        parsed.path,
        parse_qs(parsed.query, keep_blank_values=True),
        body,
        authorized=bool(handler.token) and handler._authorized(),
        peer=handler.client_address[0],
    )
    handler._send_bytes(
        code, "application/json", json.dumps(result).encode(), no_store=True
    )
