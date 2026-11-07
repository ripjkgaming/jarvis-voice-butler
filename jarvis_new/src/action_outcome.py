"""Small, content-free postcondition receipts. Dispatch never proves success."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from uuid import uuid4


class OutcomeState(str, Enum):
    PENDING = "pending"
    BLOCKED = "blocked"
    FAILED = "failed"
    LAUNCH_REQUESTED = "launch_requested"
    DISPATCHED_UNVERIFIED = "dispatched_unverified"
    VERIFIED = "verified"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class Dispatch(str, Enum):
    NOT_STARTED = "not_started"
    STARTED = "started"
    UNKNOWN = "unknown"


class Check(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    UNAVAILABLE = "unavailable"
    TIMED_OUT = "timed_out"


_SURFACES = {
    "owner_desktop",
    "backend_browser",
    "filesystem",
    "audio_service",
    "google_calendar",
    "none",
}
_EVIDENCE = {
    "file.write": {"bytes_matched", "bytes"},
    "setting.volume": {
        "before_pct",
        "expected_pct",
        "actual_pct",
        "expected_muted",
        "actual_muted",
    },
    "setting.mute": {"expected_muted", "actual_muted"},
    "setting.volume_status": {"actual_pct", "actual_muted"},
    "url.navigate": {"url_matched"},
    "url.launch": {"launcher_exit_code"},
    **{
        "power." + action: {"launcher_exit_code"}
        for action in ("reboot", "shutdown", "suspend", "logout")
    },
    "power.lock": {"launcher_exit_code", "lock_active"},
    "calendar.read": {"provider_response", "event_count"},
    "calendar.create": {"provider_id_matched", "fields_matched"},
    "calendar.update": {"provider_id_matched", "fields_matched"},
    "calendar.delete": {"provider_absent"},
    "input.click": set(),
    "input.type": set(),
}


@dataclass(frozen=True)
class ActionOutcome:
    effect: str
    state: OutcomeState | str
    dispatch: Dispatch | str = Dispatch.NOT_STARTED
    check: Check | str = Check.NOT_ATTEMPTED
    surface: str = "none"
    reason: str = ""
    evidence: dict = field(default_factory=dict)
    request_id: str | None = None
    action_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self):
        object.__setattr__(self, "state", OutcomeState(self.state))
        object.__setattr__(self, "dispatch", Dispatch(self.dispatch))
        object.__setattr__(self, "check", Check(self.check))
        if self.effect not in _EVIDENCE or self.surface not in _SURFACES:
            raise ValueError("unsupported effect or surface")
        evidence = dict(self.evidence or {})
        if set(evidence) - _EVIDENCE[self.effect]:
            raise ValueError("evidence must be content-free and allowlisted")
        object.__setattr__(self, "evidence", MappingProxyType(evidence))
        if (
            not isinstance(self.reason, str)
            or len(self.reason) > 80
            or any(
                c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in self.reason
            )
        ):
            raise ValueError("reason must be a stable code")
        if self.action_id is None:
            object.__setattr__(self, "action_id", uuid4().hex)
        if (
            not isinstance(self.action_id, str)
            or not self.action_id
            or len(self.action_id) > 128
        ):
            raise ValueError("invalid action identity")
        if self.request_id is not None and (
            not isinstance(self.request_id, str) or len(self.request_id) > 128
        ):
            raise ValueError("invalid request identity")
        for key, value in evidence.items():
            if key in {
                "bytes_matched",
                "url_matched",
                "expected_muted",
                "actual_muted",
                "lock_active",
                "provider_id_matched",
                "fields_matched",
                "provider_absent",
                "provider_response",
            }:
                if type(value) is not bool:
                    raise ValueError("boolean evidence required")
            elif key in {
                "bytes",
                "before_pct",
                "expected_pct",
                "actual_pct",
                "event_count",
            }:
                if (
                    type(value) is not int
                    or value < 0
                    or (key not in {"bytes", "event_count"} and value > 150)
                ):
                    raise ValueError("integer evidence required")
            elif (
                key == "launcher_exit_code"
                and value is not None
                and type(value) is not int
            ):
                raise ValueError("invalid exit code")
        if self.verified:
            if self.check is not Check.MATCHED:
                raise ValueError("verified requires matching read-back")
            self._validate_proof(evidence)
        if self.state is OutcomeState.LAUNCH_REQUESTED and (
            self.effect != "url.launch" or self.dispatch is Dispatch.NOT_STARTED
        ):
            raise ValueError("launch request requires dispatched URL launch")
        if (
            self.state is OutcomeState.DISPATCHED_UNVERIFIED
            and self.dispatch is Dispatch.NOT_STARTED
        ):
            raise ValueError("unverified dispatch requires started or unknown dispatch")

    def _validate_proof(self, evidence):
        if self.effect == "file.write" and self.surface == "filesystem":
            valid = (
                evidence.get("bytes_matched") is True
                and type(evidence.get("bytes")) is int
            )
        elif self.effect == "setting.volume" and self.surface == "audio_service":
            want, actual = evidence.get("expected_pct"), evidence.get("actual_pct")
            valid = (
                type(want) is int and type(actual) is int and abs(want - actual) <= 1
            )
            if "expected_muted" in evidence:
                valid = (
                    valid and evidence.get("actual_muted") is evidence["expected_muted"]
                )
        elif self.effect == "setting.mute" and self.surface == "audio_service":
            valid = (
                type(evidence.get("expected_muted")) is bool
                and evidence.get("actual_muted") is evidence["expected_muted"]
            )
        elif self.effect == "setting.volume_status" and self.surface == "audio_service":
            valid = type(evidence.get("actual_pct")) is int
        elif self.effect == "power.lock" and self.surface == "owner_desktop":
            valid = (
                self.dispatch is Dispatch.STARTED
                and evidence.get("lock_active") is True
                and evidence.get("launcher_exit_code") == 0
            )
        elif self.effect == "calendar.read" and self.surface == "google_calendar":
            valid = (
                evidence.get("provider_response") is True
                and type(evidence.get("event_count")) is int
            )
        elif (
            self.effect in {"calendar.create", "calendar.update"}
            and self.surface == "google_calendar"
        ):
            valid = (
                evidence.get("provider_id_matched") is True
                and evidence.get("fields_matched") is True
            )
        elif self.effect == "calendar.delete" and self.surface == "google_calendar":
            valid = evidence.get("provider_absent") is True
        elif self.effect == "url.navigate" and self.surface == "backend_browser":
            valid = evidence.get("url_matched") is True
        else:
            valid = False
        if not valid:
            raise ValueError("effect-specific matching evidence required")

    @property
    def verified(self):
        return self.state is OutcomeState.VERIFIED

    @property
    def retry_safe(self):
        return self.dispatch is Dispatch.NOT_STARTED and self.state in {
            OutcomeState.FAILED,
            OutcomeState.BLOCKED,
            OutcomeState.CANCELLED,
        }

    @property
    def accepted(self):
        return self.state in {
            OutcomeState.VERIFIED,
            OutcomeState.LAUNCH_REQUESTED,
            OutcomeState.DISPATCHED_UNVERIFIED,
            OutcomeState.PENDING,
        }

    @property
    def say(self):
        if self.verified:
            if self.effect.startswith("calendar."):
                return "The Google Calendar change was verified by provider readback."
            if self.effect == "power.lock":
                return "Locked; KDE reports the screen lock active."
            if self.effect == "file.write":
                return f"Saved the file and checked {self.evidence['bytes']} bytes."
            if self.effect in {"setting.volume", "setting.volume_status"}:
                suffix = ", muted" if self.evidence.get("actual_muted") is True else ""
                return f"Volume reads back at {self.evidence['actual_pct']} percent{suffix}."
            if self.effect == "setting.mute":
                return "Muted." if self.evidence["actual_muted"] else "Unmuted."
            return "The requested URL was checked in the separate backend browser."
        if self.effect.startswith("power."):
            if self.state is OutcomeState.BLOCKED:
                return "That power action was blocked before dispatch."
            if self.state is OutcomeState.FAILED:
                return "The power command failed; its result is not verified."
            if self.dispatch is Dispatch.UNKNOWN:
                return "The power request could not be confirmed; do not retry automatically."
            return "The power request was submitted; its result is not verified."
        if self.state is OutcomeState.LAUNCH_REQUESTED:
            return "Requested a new tab in your desktop Brave; the page is not visually verified."
        if self.state is OutcomeState.PENDING:
            return "That action is still in progress; its result is not yet verified."
        if self.dispatch is not Dispatch.NOT_STARTED:
            return (
                "The command was dispatched, but its requested result is not verified."
            )
        return {
            "blocked": "That action was blocked before it started.",
            "failed": "That action failed before it started.",
            "cancelled": "That action was cancelled before it started.",
            "superseded": "A newer request superseded that action.",
        }.get(self.state.value, "The result is not verified.")

    def to_dict(self):
        return {
            "version": 1,
            "action_id": self.action_id,
            "request_id": self.request_id,
            "effect": self.effect,
            "state": self.state.value,
            "dispatch": self.dispatch.value,
            "check": self.check.value,
            "surface": self.surface,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "say": self.say,
        }

    def result(self, **legacy_fields):
        result = dict(legacy_fields)
        result.update(ok=self.accepted, say=self.say, outcome=self.to_dict())
        return result


def parse_outcome(result) -> ActionOutcome | None:
    if isinstance(result, ActionOutcome):
        return result
    if not isinstance(result, dict):
        return None
    raw = result.get("outcome", result)
    if not isinstance(raw, dict) or raw.get("version") != 1:
        return None
    expected = {
        "version",
        "action_id",
        "request_id",
        "effect",
        "state",
        "dispatch",
        "check",
        "surface",
        "reason",
        "evidence",
        "say",
    }
    if set(raw) != expected:
        return None
    try:
        value = ActionOutcome(
            **{k: v for k, v in raw.items() if k not in {"version", "say"}}
        )
        return value if raw["say"] == value.say else None
    except (TypeError, ValueError, KeyError):
        return None
