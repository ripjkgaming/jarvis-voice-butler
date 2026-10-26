"""Friendly window selection for registered builds or explicit activity-only scope.

Read-only KWin APIs: WindowsRunner.Match and org.kde.KWin.getWindowInfo.
Never queryWindowInfo (interactive), Run, focus, scripts, screenshots or input.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import threading
import time

import build_monitor as bm
import jobs

OWNER = "window-monitor"
UUID = re.compile(r"\{[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\}\Z")
ALIASES = {
    "vs code": "code",
    "vscode": "code",
    "visual studio code": "code",
    "code": "code",
    "google chrome": "chrome",
    "chrome": "chrome",
    "brave": "brave",
    "firefox": "firefox",
    "terminal": "konsole",
    "konsole": "konsole",
}
BROWSERS = {"brave", "chrome", "chromium", "firefox", "google-chrome", "msedge"}
MAX_WINDOWS = 6


class WindowError(jobs.JobError):
    pass


def clean(text, limit=300):
    return " ".join(bm._safe_text(text, limit).split())


def normalized(text):
    return clean(text).casefold()


@dataclasses.dataclass(frozen=True)
class Window:
    id: str
    title: str
    app: str
    pid: int
    service_owner: str

    def key(self):
        return (self.id, self.pid, self.service_owner)


def matches(query, window):
    name = normalized(query)
    title, app = normalized(window.title), normalized(window.app)
    return bool(name) and (name in title or name == app or ALIASES.get(name) == app)


def _scalar(value):
    if isinstance(value, dict) and set(value) == {"type", "data"}:
        value = value["data"]
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    return value


def parse_info(raw, wid, service_owner):
    try:
        data = json.loads(raw)
        if data.get("type") != "a{sv}":
            raise ValueError("Wrong metadata type")
        info = _scalar(data["data"])
        if not isinstance(info, dict):
            raise ValueError("Wrong metadata map")
        if not info:
            return None
        uuid, pid = _scalar(info.get("uuid")), _scalar(info.get("pid"))
        title, app = _scalar(info.get("caption")), _scalar(info.get("resourceClass"))
        local = _scalar(info.get("localhost"))
        if (
            uuid != wid
            or type(pid) is not int
            or not 1 < pid <= 2147483647
            or local is not True
            or not isinstance(title, str)
            or len(title) > 300
            or not isinstance(app, str)
            or len(app) > 120
        ):
            raise ValueError("Incomplete local window metadata")
        return Window(wid, clean(title), clean(app, 120), pid, service_owner)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise WindowError("KWin window metadata unavailable.") from exc


class KWinWindows:
    """Authoritative current read-back; transport injectable, never run in tests."""

    def __init__(self, *, run=subprocess.run):
        self.run = run

    def _run(self, argv, **kwargs):
        # Only callers below choose argv; no shell and no model-controlled method.
        result = self.run(argv, capture_output=True, text=True, timeout=1.0)
        if (
            result.returncode
            or not isinstance(result.stdout, str)
            or len(result.stdout.encode()) > 65536
        ):
            raise WindowError("KWin response unavailable or exceeded bounds.")
        return result

    def _bus(self, destination, path, interface, method, signature, argument):
        try:
            return self._run(
                [
                    "busctl",
                    "--user",
                    "--json=short",
                    "call",
                    destination,
                    path,
                    interface,
                    method,
                    signature,
                    argument,
                ]
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise WindowError("KWin read-only metadata unavailable.") from exc

    def _owner(self):
        raw = self._bus(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetNameOwner",
            "s",
            "org.kde.KWin",
        )
        try:
            data = json.loads(raw)
            owner = _scalar(data["data"])
            if (
                data.get("type") != "s"
                or not isinstance(owner, str)
                or not re.fullmatch(r":[0-9]+\.[0-9]+", owner)
            ):
                raise ValueError("Invalid service owner")
            return owner
        except (ValueError, TypeError, KeyError) as exc:
            raise WindowError("KWin service identity unavailable.") from exc

    def current(self, wid):
        if not isinstance(wid, str) or not UUID.fullmatch(wid):
            raise WindowError("Invalid stable window identity.", 400)
        before = self._owner()
        raw = self._bus(
            "org.kde.KWin", "/KWin", "org.kde.KWin", "getWindowInfo", "s", wid
        )
        row = parse_info(raw, wid, before)
        if self._owner() != before:
            raise WindowError("KWin service changed during observation.")
        return row

    def find(self, query):
        from system import kwin_windows

        if not kwin_windows.available():
            raise WindowError("Named-window monitoring requires KDE Wayland metadata.")
        lookups = [query]
        alias = ALIASES.get(normalized(query))
        if alias and normalized(query) != alias:
            lookups.append(alias)
        ids = set()
        try:
            for name in lookups:
                hits = kwin_windows.find(name, run=self._run)
                ids.update(wid for wid, _ in hits)
                if len(ids) > MAX_WINDOWS:
                    raise WindowError(
                        "Too many matching windows; give a more exact title.", 409
                    )
            rows = [self.current(wid) for wid in sorted(ids)]
        except (OSError, subprocess.SubprocessError) as exc:
            raise WindowError("Named-window metadata unavailable.") from exc
        return [row for row in rows if row and matches(query, row)]


class Resolver:
    def __init__(
        self, monitor, *, windows=None, proc_reader=None, clock=time.monotonic
    ):
        self.monitor = monitor
        self.registry = monitor.registry
        self.windows = windows or KWinWindows()
        self.proc_reader = proc_reader or monitor.proc_reader
        self.clock = clock
        self.pending = None
        self._choice_lock = threading.RLock()

    def _identity(self, window):
        try:
            found = self.proc_reader(window.pid)
            if (
                not isinstance(found, bm.Identity)
                or found.pid != window.pid
                or found.uid != os.getuid()
                or found.state in {"Z", "X", "x"}
            ):
                raise WindowError("Selected owner window process unavailable.")
            return found
        except (OSError, ValueError) as exc:
            raise WindowError("Selected owner window process unavailable.") from exc

    @staticmethod
    def _reply(status, say, **fields):
        return {"status": status, "say": say, **fields}

    def select(
        self,
        name,
        request_id,
        *,
        scope="registered_build",
        build_label="",
        check=lambda: None,
    ):
        with self._choice_lock:
            check()
            return self._select(
                name, request_id, scope=scope, build_label=build_label, check=check
            )

    def _select(
        self,
        name,
        request_id,
        *,
        scope="registered_build",
        build_label="",
        check=lambda: None,
    ):
        self.pending = None
        if (
            not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 300
            or scope not in {"registered_build", "window_activity"}
        ):
            return self._reply(
                "needs_selection",
                "Name one window, such as VS Code, or give its exact title.",
            )
        check()
        try:
            candidates = self.windows.find(name)
            check()
            exact = [w for w in candidates if normalized(w.title) == normalized(name)]
            candidates = exact or candidates
            if not candidates:
                return self._reply(
                    "not_found",
                    "No current matching window was found. Give its exact title or select a registered task.",
                )
            if len(candidates) > MAX_WINDOWS:
                return self._reply(
                    "needs_selection",
                    "Give a more exact window title; there are too many matches.",
                )
            fingerprints = [self._identity(w) for w in candidates]
            if len(candidates) != 1:
                self.pending = (
                    self.clock(),
                    name,
                    candidates,
                    fingerprints,
                    scope,
                    build_label,
                )
                return self._reply(
                    "needs_selection",
                    "Which matching window? Say option 1, option 2, or its exact title.",
                    choices=[
                        {"option": i + 1, "title": w.title, "app": w.app}
                        for i, w in enumerate(candidates)
                    ],
                )
            return self._bind(
                candidates[0],
                fingerprints[0],
                name,
                request_id,
                scope,
                check,
                build_label,
            )
        except WindowError:
            return self._reply(
                "unavailable",
                "Current owner window metadata is unavailable. Nothing was registered; no window exit or task completion is established.",
            )

    def choose(self, index, request_id, *, check=lambda: None):
        with self._choice_lock:
            check()
            return self._choose(index, request_id, check=check)

    def _choose(self, index, request_id, *, check=lambda: None):
        pending = self.pending
        self.pending = None
        if (
            not pending
            or type(index) is not int
            or not 1 <= index <= len(pending[2])
            or self.clock() - pending[0] > 60
        ):
            return self._reply(
                "needs_selection",
                "Name the window again; the previous selection is unavailable or expired.",
            )
        _, name, candidates, identities, scope, build_label = pending
        try:
            check()
            original = candidates[index - 1]
            current = self.windows.current(original.id)
            if (
                current is None
                or current.key() != original.key()
                or self._identity(current).key() != identities[index - 1].key()
            ):
                return self._reply(
                    "selection_changed",
                    "That window or process changed. Name the window again; nothing was registered.",
                )
            return self._bind(
                current,
                identities[index - 1],
                name,
                request_id,
                scope,
                check,
                build_label,
            )
        except WindowError:
            return self._reply(
                "unavailable",
                "Current window identity is unavailable; nothing was registered.",
            )

    def _bind(self, window, identity, name, request_id, scope, check, build_label=""):
        current = self.windows.current(window.id)
        if (
            current is None
            or current.key() != window.key()
            or self._identity(current).key() != identity.key()
        ):
            return self._reply(
                "selection_changed",
                "Window identity changed. Name it again; nothing was registered.",
            )
        check()
        if scope == "window_activity":
            return self._register_activity(current, identity, name, request_id, check)
        # Existing explicitly registered exact process is authoritative. An IDE's
        # unrelated child ancestry is not proof of a build/window association.
        rows = self.registry.list(bm.OWNER)
        if build_label:
            # An explicit owner-selected existing record is an association, not
            # an inference that the IDE process is its child or executor.
            selected = [
                r
                for r in rows
                if normalized(r["label"]) == normalized(build_label)
                or r["id"] == build_label
            ]
            if len(selected) != 1 or len(rows) == 20:
                return self._reply(
                    "needs_selection",
                    "Name one exact registered build label or job ID; no association was guessed.",
                )
            rows = selected
        related = []
        for row in rows:
            saved = self.monitor._data(row)["identity"]
            saved_identity = bm.Identity(**saved)
            binding = row["payload"].get("window_binding")
            attested = False
            if isinstance(binding, dict):
                try:
                    attested = (
                        binding["window_id"] == window.id
                        and binding["service_owner"] == window.service_owner
                        and bm.Identity(**binding["identity"]).key() == identity.key()
                    )
                except (KeyError, TypeError):
                    raise WindowError(
                        "Registered association metadata is invalid."
                    ) from None
            if row["lifecycle"] != "settled" and (
                saved_identity.key() == identity.key() or attested or bool(build_label)
            ):
                actual = self.proc_reader(saved_identity.pid)
                if (
                    isinstance(actual, bm.Identity)
                    and actual.key() == saved_identity.key()
                    and actual.state not in {"Z", "X", "x"}
                ):
                    related.append(row)
        if len(related) != 1:
            browser = normalized(window.app) in BROWSERS
            sentence = (
                "A browser window cannot identify a cloud job. "
                if browser
                else "The window process is not an identified registered build. "
            )
            return self._reply(
                "scope_required",
                sentence
                + "Choose a registered task, or explicitly ask to monitor only this window activity and exit; build success will remain unknown.",
                supported_scopes=["registered_task", "window_activity"],
            )
        row = related[0]
        # Browser PIDs never stand for builds even if previously registered.
        if normalized(window.app) in BROWSERS:
            return self._reply(
                "scope_required",
                "A browser process does not establish cloud job identity. Choose the registered task, or ask for window activity only.",
                supported_scopes=["registered_task", "window_activity"],
            )
        data = dict(row["payload"])
        data["window_query"] = clean(name)
        data["window_binding"] = {
            "window_id": current.id,
            "service_owner": current.service_owner,
            "identity": dataclasses.asdict(identity),
        }
        # Revalidate both identities after all association work and before write.
        latest = self.windows.current(current.id)
        actual = self.proc_reader(bm.Identity(**data["identity"]).pid)
        if (
            latest is None
            or latest.key() != current.key()
            or self._identity(latest).key() != identity.key()
            or not isinstance(actual, bm.Identity)
            or actual.key() != bm.Identity(**data["identity"]).key()
            or actual.state in {"Z", "X", "x"}
        ):
            return self._reply(
                "selection_changed",
                "Window or registered build changed; no association was saved.",
            )
        check()
        self.registry.update(row["id"], bm.OWNER, data, row["revision"])
        check()
        self.monitor.poll_once()
        result = self.monitor.get(row["id"])
        return self._reply(
            "registered_build",
            "I am observing the already registered process associated with that window. Process exit does not establish build success.",
            job=result,
            observation_scope="registered_process",
        )

    def _register_activity(self, window, identity, name, request_id, check):
        if not isinstance(request_id, str) or not bm.REQUEST_RE.fullmatch(request_id):
            raise WindowError("Invalid observation request.", 400)
        existing = self.registry.by_native(OWNER, request_id)
        if existing:
            data = self._activity_data(existing)
            if (
                data["window_id"] != window.id
                or bm.Identity(**data["identity"]).key() != identity.key()
                or data["service_owner"] != window.service_owner
            ):
                raise WindowError("Window registration request conflict.", 409)
            return self._reply(
                "registered_window_activity",
                "Window activity is registered; build completion and success remain unknown.",
                job=self._public(existing),
                observation_scope="window_activity",
            )
        active = [r for r in self.registry.list(OWNER) if r["lifecycle"] != "settled"]
        if active:
            if len(active) != 1:
                raise WindowError("Multiple active observations refused.", 409)
            current = active[0]
            data = self._activity_data(current)
            if (
                current["lifecycle"] != "cancellation_requested"
                and data["window_id"] == window.id
                and data["service_owner"] == window.service_owner
                and bm.Identity(**data["identity"]).key() == identity.key()
            ):
                check()
                return self._reply(
                    "registered_window_activity",
                    "Existing exact window activity registration selected; build success remains unknown.",
                    job=self._public(current),
                    observation_scope="window_activity",
                )
            return self._reply(
                "needs_selection",
                "A different or detaching window observation is active. Check its status before selecting another window.",
            )
        payload = {
            "window_id": window.id,
            "service_owner": window.service_owner,
            "identity": dataclasses.asdict(identity),
            "status": "window_present",
            "reason": None,
            "observation_scope": "window_activity",
            "window_query": clean(name),
        }
        check()
        row = self.registry.register(
            OWNER,
            request_id,
            "monitor_observation",
            clean(name, 80),
            payload,
            single_active=True,
        )
        check()
        return self._reply(
            "registered_window_activity",
            "Window activity is registered. Build completion and success remain unknown.",
            job=self._public(row),
            observation_scope="window_activity",
        )

    @staticmethod
    def _activity_data(row):
        try:
            data = row["payload"]
            required = {
                "window_id",
                "service_owner",
                "identity",
                "status",
                "reason",
                "observation_scope",
                "window_query",
            }
            identity = bm.Identity(**data["identity"])
            if (
                set(data) != required
                or not isinstance(data["window_id"], str)
                or not UUID.fullmatch(data["window_id"])
                or not isinstance(data["service_owner"], str)
                or not re.fullmatch(r":[0-9]+\.[0-9]+", data["service_owner"])
                or data["observation_scope"] != "window_activity"
                or data["status"]
                not in {
                    "window_present",
                    "window_closed",
                    "window_identity_changed",
                    "window_process_ended",
                    "observation_unknown",
                    "monitoring_stopped",
                }
                or type(identity.uid) is not int
                or identity.uid != os.getuid()
                or type(identity.pid) is not int
                or not 1 < identity.pid <= 2147483647
                or type(identity.start_ticks) is not int
                or identity.start_ticks < 0
                or not isinstance(identity.boot_id, str)
                or not 1 <= len(identity.boot_id) <= 80
                or not isinstance(data["window_query"], str)
                or len(data["window_query"]) > 300
            ):
                raise ValueError("Invalid activity metadata")
            return data
        except (KeyError, TypeError, ValueError) as exc:
            raise WindowError("Window observation metadata is invalid.") from exc

    @staticmethod
    def _public(row):
        Resolver._activity_data(row)
        return {
            **jobs.public(row),
            "status": row["payload"]["status"],
            "reason": row["payload"]["reason"],
            "observation_scope": "window_activity",
        }

    def status(self, job_id, *, check=lambda: None):
        check()
        row = self.registry.get(job_id)
        if row is None:
            raise WindowError("Unknown registered observation.", 404)
        if row["owner"] == bm.OWNER:
            self.monitor.poll_once()
            check()
            binding = row["payload"].get("window_binding")
            window_state = "unbound"
            if binding:
                try:
                    current = self.windows.current(binding["window_id"])
                    if current is None:
                        window_state = "window_closed"
                    elif (
                        current.service_owner != binding["service_owner"]
                        or self._identity(current).key()
                        != bm.Identity(**binding["identity"]).key()
                    ):
                        window_state = "window_identity_changed"
                    else:
                        window_state = "window_present"
                except WindowError:
                    window_state = "observation_unknown"
            check()
            return self._reply(
                "observed",
                "Registered process status only; build success or failure is unknown. Window closure does not settle the build.",
                job=self.monitor.get(job_id),
                observation_scope="registered_process",
                window_state=window_state,
            )
        if row["owner"] != OWNER:
            raise WindowError("Select a registered build or window observation.", 403)
        if row["lifecycle"] != "settled":
            data = dict(self._activity_data(row))
            outcome = None
            try:
                if row["lifecycle"] == "cancellation_requested":
                    data.update(status="monitoring_stopped", reason="owner_detached")
                    outcome = "monitoring_stopped"
                else:
                    saved = bm.Identity(**data["identity"])
                    current = self.windows.current(data["window_id"])
                    observed = self.proc_reader(saved.pid)
                    if observed is not None and (
                        not isinstance(observed, bm.Identity)
                        or observed.pid != saved.pid
                    ):
                        raise WindowError("Invalid process observation.")
                    if (
                        observed is None
                        or observed.key() != saved.key()
                        or observed.state in {"Z", "X", "x"}
                    ):
                        data.update(
                            status="window_process_ended",
                            reason="process_identity_ended",
                        )
                        outcome = "ended_unknown"
                    elif current is None:
                        data.update(status="window_closed", reason="window_not_present")
                        outcome = "ended_unknown"
                    elif (
                        current.service_owner != data["service_owner"]
                        or current.pid != saved.pid
                    ):
                        data.update(
                            status="window_identity_changed",
                            reason="window_identity_changed",
                        )
                        outcome = "ended_unknown"
                    else:
                        data.update(status="window_present", reason=None)
            except (WindowError, OSError, ValueError):
                data.update(status="observation_unknown", reason="metadata_unavailable")
            check()
            try:
                if outcome:
                    row = self.registry.settle(
                        row["id"], OWNER, outcome, data, row["revision"]
                    )
                else:
                    row = self.registry.update(row["id"], OWNER, data, row["revision"])
            except jobs.JobError as exc:
                if exc.code != 409:
                    raise
                row = self.registry.get(row["id"], OWNER)
        check()
        return self._reply(
            "observed",
            "Window/process activity status only; build completion and success remain unknown.",
            job=self._public(row),
            observation_scope="window_activity",
        )

    def status_by_name(self, name, *, check=lambda: None):
        if not isinstance(name, str) or len(name) > 300:
            return self._reply(
                "needs_selection", "Give one registered window name or task label."
            )
        if jobs.ID_RE.fullmatch(name):
            return self.status(name, check=check)
        rows = self.registry.list(bm.OWNER) + self.registry.list(OWNER)
        matched = [
            row
            for row in rows
            if not name.strip()
            or normalized(row["label"]) == normalized(name)
            or normalized(row["payload"].get("window_query", "")) == normalized(name)
        ]
        if len(matched) != 1 or any(
            len(self.registry.list(owner)) == 20 for owner in (bm.OWNER, OWNER)
        ):
            return self._reply(
                "needs_selection" if matched else "not_registered",
                "Choose one exact registered task or window label; no latest job was guessed.",
                choices=[{"label": row["label"], "id": row["id"]} for row in matched],
            )
        return self.status(matched[0]["id"], check=check)
