"""Owner-requested build observation; no launch, signals or log access."""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading

from jarvis_rt import RunContext, function_tool

import action_guard
import build_monitor
import jobs
import window_monitor
import window_sampler
from system import LocalSystemError, require_local

_NAMED_REQUEST = (
    r"(?:please\s+|can you\s+|could you\s+)?(?:monitor|watch|track|register)\s+"
)
_PID = re.compile(r"\b(?:pid|process id)\s*[:#]?\s*([0-9]+)\b", re.I)
_INTENT = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+)?(?:monitor|watch|track|register)\b", re.I
)


class BuildMonitorTools:
    def __init__(
        self,
        *,
        service_factory=build_monitor.service,
        resolver_factory=window_monitor.Resolver,
        sampler_factory=window_sampler.ensure_started,
    ):
        self._service_factory = service_factory
        self._tools = None
        self._resolver_factory = resolver_factory
        self._resolver = None
        self._resolver_session = None
        self._window_lock = threading.RLock()
        self._sampler_factory = sampler_factory

    def _windows(self, mon, session=None):
        if (
            self._resolver is None
            or self._resolver.monitor is not mon
            or self._resolver_session is not session
        ):
            self._resolver = self._resolver_factory(mon)
            self._resolver_session = session
        return self._resolver

    @property
    def tools(self):
        if self._tools is None:
            self._tools = [
                function_tool()(self.build_monitor_register),
                function_tool()(self.build_monitor_status),
            ]
        return self._tools

    @staticmethod
    def _guard(ticket):
        import phone_session

        require_local()
        if not build_monitor.tools_enabled() or not build_monitor.owner_allowed(
            "127.0.0.1"
        ):
            raise LocalSystemError("Owner build observation unavailable.")
        phone_session.check_approval_effect(ticket, current=True)

    @staticmethod
    def _input(context):
        """Only SDK user messages, never model-provided proof or tool output."""
        speech = getattr(context, "speech_handle", None)
        if speech is None or getattr(speech, "interrupted", True):
            return None
        session = getattr(context, "session", None)
        if getattr(session, "current_speech", speech) is not speech:
            return None
        history = getattr(session, "history", None)
        for item in reversed(getattr(history, "items", ())):
            if getattr(item, "role", None) == "user":
                text = getattr(item, "text_content", "")
                if (
                    isinstance(text, str)
                    and 0 < len(text) <= 2000
                    and isinstance(getattr(item, "id", None), str)
                    and 0 < len(item.id) <= 256
                ):
                    return item.id, text
                return None
        return None

    async def _call(self, context, operation, *, expected_input=None):
        import phone_session

        ticket = phone_session.approval_ticket()
        cancelled = threading.Event()

        def check():
            if cancelled.is_set():
                raise LocalSystemError("Build observation cancelled.")
            self._guard(ticket)
            if expected_input is not None and self._input(context) != expected_input:
                raise LocalSystemError("Build input changed.")

        def work():
            check()
            mon = self._service_factory()
            check()
            result = operation(mon, check)
            check()
            return result

        try:
            check()
            return await asyncio.to_thread(work)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        except LocalSystemError:
            return {
                "status": "denied",
                "say": "Owner build authority is unavailable or changed; no result can be disclosed.",
            }
        except jobs.JobError as exc:
            if exc.code == 404:
                return {
                    "status": "not_registered",
                    "say": "No registered build with that ID. Provide its exact registered job ID.",
                }
            return {
                "status": "unavailable",
                "say": "Window/build observation was refused or unavailable. Check the window name or registered task; no build was launched or stopped.",
            }
        except (OSError, ValueError):
            return {
                "status": "unavailable",
                "say": "Build observation is unavailable; no build was launched or stopped.",
            }

    async def build_monitor_register(
        self,
        context: RunContext,
        pid: int = 0,
        label: str = "",
        window_name: str = "",
        scope: str = "registered_build",
        choice: int = 0,
    ) -> dict:
        """Observe an explicitly identified existing owner build; never start one.

        Prefer a user-named window: 'monitor the build in VS Code' or an exact
        window title, passing window_name verbatim from the current user turn.
        To associate an existing registered build, the user must say
        'monitor registered build <exact label> in <window name>'; pass label.
        Never guess that an IDE/browser PID is the build. If it cannot map to
        an existing registered process, ask which registered task, or offer
        window_activity scope only after the user explicitly requests window
        activity/exit only. Ambiguous windows require a displayed option number
        in the user's follow-up (choice). No screenshots, logs, focus or control.
        The legacy numeric pid path still requires an explicitly named PID.
        """
        import phone_session

        try:
            self._guard(phone_session.approval_ticket())
        except LocalSystemError:
            return {
                "status": "denied",
                "say": "Owner build observation is unavailable.",
            }
        source = self._input(context)
        request = action_guard.request_id(context)
        text = source[1].strip() if source else ""
        if window_name or choice:
            option = re.fullmatch(
                r"(?:please\s+)?(?:option|choice|window)\s+([1-6])[.!?]?", text, re.I
            )
            selection = (
                type(choice) is int
                and choice > 0
                and option
                and int(option[1]) == choice
            )
            named = (
                isinstance(window_name, str)
                and 0 < len(window_name.strip()) <= 300
                and window_monitor.normalized(window_name)
                in window_monitor.normalized(text)
                and _INTENT.match(text)
            )
            activity_allowed = (
                scope != "window_activity"
                or selection
                or (
                    isinstance(window_name, str)
                    and bool(
                        re.fullmatch(
                            _NAMED_REQUEST
                            + r"(?:the\s+)?"
                            + re.escape(window_name.strip())
                            + r"\s+(?:window\s+activity(?:\s+only)?|window\s+(?:and\s+)?exit(?:\s+only)?|activity\s+(?:and\s+)?exit(?:\s+only)?|window\s+only)[.!?]?",
                            text,
                            re.I,
                        )
                    )
                )
            )
            association_allowed = (
                isinstance(label, str)
                and isinstance(window_name, str)
                and (
                    not label
                    or bool(
                        re.fullmatch(
                            _NAMED_REQUEST
                            + r"(?:the\s+)?registered\s+(?:build|task)\s+[\"']?"
                            + re.escape(label.strip())
                            + r"[\"']?\s+(?:in|for)\s+"
                            + re.escape(window_name.strip())
                            + r"[.!?]?\s*$",
                            text,
                            re.I,
                        )
                    )
                )
            )
            if (
                not source
                or not request
                or not (selection or named)
                or not activity_allowed
                or not association_allowed
                or (label and (scope != "registered_build" or selection))
            ):
                return {
                    "status": "needs_selection",
                    "say": "Name the window, for example 'monitor the build in VS Code'. For activity-only scope, say 'monitor VS Code window activity only'.",
                }

            def by_window(mon, check):
                with self._window_lock:
                    check()
                    resolver = self._windows(mon, context.session)
                    registration_id = (
                        "voice-window-" + hashlib.sha256(source[0].encode()).hexdigest()
                    )
                    if selection:
                        result = resolver.choose(choice, registration_id, check=check)
                    else:
                        result = resolver.select(
                            window_name,
                            registration_id,
                            scope=scope,
                            build_label=label,
                            check=check,
                        )
                    if result["status"] == "registered_window_activity":
                        check()
                        try:
                            active = self._sampler_factory(mon)
                        except (jobs.JobError, OSError):
                            active = False
                        result["automatic_sampling"] = bool(active)
                        result["say"] += (
                            " Automatic bounded sampling is active; ask for status."
                            if active
                            else " Automatic sampling is unavailable; this is an on-demand observation only."
                        )
                    return result

            return await self._call(context, by_window, expected_input=source)
        pids = _PID.findall(text)
        if (
            not source
            or not request
            or not _INTENT.match(text)
            or not re.search(r"\bbuild\b", text, re.I)
            or len(pids) != 1
            or type(pid) is not int
            or not 1 < pid <= 2147483647
            or int(pids[0]) != pid
            or not isinstance(label, str)
            or not 1 <= len(label.strip()) <= 80
        ):
            return {
                "status": "needs_selection",
                "say": "Which existing build should I observe? Say 'Monitor the build in VS Code' or give the window's exact title. Nothing was registered.",
            }

        def register(mon, check):
            check()
            registration_id = (
                "voice-build-" + hashlib.sha256(source[0].encode()).hexdigest()
            )
            row = mon.register(registration_id, pid, label)
            return {
                "status": "registered",
                "job": row,
                "say": f"Build registration {row['id']} has observation state {row['status']}. This tool does not start a build or establish success.",
            }

        return await self._call(context, register, expected_input=source)

    async def build_monitor_status(
        self, context: RunContext, job_id_or_label: str = ""
    ) -> dict:
        """Check one registered build on demand, using its job ID or exact label.

        Empty selects only when exactly one registration exists. Ambiguous
        labels/jobs require the user to select a returned job ID; never pick
        latest. A process disappearance or exit has unknown success/failure.
        Explicit window-activity observations use bounded automatic sampling.
        No logs, new processes or signal control.
        """

        def status(mon, check):
            with self._window_lock:
                check()
                return self._windows(mon, context.session).status_by_name(
                    job_id_or_label, check=check
                )

        return await self._call(context, status)
