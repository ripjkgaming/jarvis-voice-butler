"""Observation-only activity integration and sourced incident memories.

Desktop locking is intentionally gated by installed-session verification; this
adapter cannot turn a model classification into an unverified input lock.
"""

from __future__ import annotations

import contextlib
import json
import time

from aware import state
from aware.coordinator import allowed


def _record(**kwargs):
    from aware.expansion_memory import record

    return record(**kwargs)


def _inhibitors():
    call = None
    with contextlib.suppress(Exception):
        import speak

        status = speak.wake_status()
        if isinstance(status, dict) and "in_call" in status:
            call = status["in_call"]
    # A negative finding requires evidence; absent integrations stay unknown.
    return {"in_call": call, "presentation": None, "scheduled_break": None}


class Runtime:
    def __init__(
        self, *, policy=None, camera=None, record=_record, inhibitors=_inhibitors
    ):
        self.policy = policy
        self._camera_source = None
        self.camera = camera or self._poll_camera
        self.record = record
        self.inhibitors = inhibitors

    def _poll_camera(self):
        from aware.exercise import ExerciseCamera

        if self._camera_source is None:
            self._camera_source = ExerciseCamera(who="aware-activity")
        return self._camera_source.poll()

    def assess(self, row, now=None):
        now = time.time() if now is None else now
        if not allowed(now):
            return {"eligible": False, "reason": "controls", "mode": "observation_only"}
        if self.policy is None:
            from aware.break_policy import Policy

            self.policy = Policy()
        row = row if isinstance(row, dict) else {}
        seen = row.get("outcome") == "seen" and not row.get("sensitive")
        observed_at = row.get("ts", now)
        observation = {
            "activity": "uncertain",
            "observed_at": observed_at,
            "present": False,
            "confidence": 0.0,
            "content": "",
            "exercise_active": False,
        }
        if seen:
            presence = self.camera()
            observation.update(self.inhibitors())
            observation.update(
                activity=row.get("activity", "uncertain"),
                content=row.get("summary", ""),
                task=row.get("subject", ""),
                observed_at=observed_at,
                present=presence.get("status") == "present",
                presence_at=presence.get("timestamp"),
                confidence=0.7,
            )
        decision = self.policy.observe(observation, now=now)
        decision.update(
            mode="observation_only", would_intervene=bool(decision.get("eligible"))
        )
        if seen and allowed(now):
            source = "screen:" + str(row.get("tick_id") or observed_at)
            self.record(
                kind="activity",
                source=source,
                timestamp=observed_at,
                confidence=observation["confidence"],
                data={
                    "summary": f"Activity classified as {decision.get('activity', 'uncertain')}: {decision.get('reason', 'unknown')}. Exercise decision observed without locking.",
                    "classification": decision.get("activity"),
                    "reason": decision.get("reason"),
                    "would_intervene": decision["would_intervene"],
                },
                key=source,
            )
        state.write_private(
            state.aware_dir() / "exercise-status.json",
            __import__("json").dumps(decision, default=str),
        )
        return decision

    def health(self, result, speak=None):
        path = state.aware_dir() / "health-deliveries.json"
        pending = {}
        with contextlib.suppress(OSError, ValueError):
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict):
                pending = loaded
        errors = []
        for incident in result.get("incidents", []):
            if not allowed():
                break
            key = (
                str(incident.get("key") or incident.get("name") or "health")
                + ":"
                + str(incident.get("timestamp", result.get("timestamp")))
            )
            pending[key] = {
                "kind": "health_incident",
                "source": "diagnostics",
                "timestamp": result.get("timestamp", time.time()),
                "confidence": 1.0,
                "data": {
                    **incident,
                    "summary": incident.get("detail")
                    or incident.get("message")
                    or str(incident),
                },
                "key": key,
            }
            state.write_private(path, json.dumps(pending))
            speech = incident.get("speech") or incident.get("speak")
            if speak and speech and allowed():
                try:
                    speak(str(speech))
                except Exception as exc:
                    errors.append(type(exc).__name__)
        for key, payload in list(pending.items()):
            if not allowed():
                break
            try:
                self.record(**payload)
                del pending[key]
            except Exception as exc:
                errors.append(type(exc).__name__)
        state.write_private(path, json.dumps(pending))
        return {**result, "delivery_errors": errors, "memory_retries": len(pending)}
