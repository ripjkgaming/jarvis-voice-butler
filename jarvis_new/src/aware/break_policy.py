"""Conservative, observation-only pull-up break eligibility policy."""

from __future__ import annotations

import math
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SINGAPORE = ZoneInfo("Asia/Singapore")
LEISURE_SECONDS = 120.0
MAX_OBSERVATION_AGE = 180.0
MAX_OBSERVATION_GAP = 300.0
MIN_INTERVENTION_GAP = 3600.0
DAILY_TARGET = 5
DAILY_MAXIMUM = 6

_WORK = re.compile(
    r"\b(work|working|write|writing|code|coding|program|programming|meeting|"
    r"study|studying|research|read|reading|document|report|email|class|lesson|"
    r"lecture|tutorial|training|educational|technical|walkthrough|spreadsheet|"
    r"debug|design|project|task|presentation)\b",
    re.I,
)
_LEISURE = re.compile(
    r"\b(movie|film|episode|comedy|entertainment|gaming|game|sitcom|"
    r"relax(?:ing)?|leisure|vacation|standup|stand-up)\b",
    re.I,
)


class Policy:
    """Evaluate sustained leisure and persist intervention counts by Singapore date.

    ``observe`` is deliberately side-effect free with respect to interventions. A caller
    must explicitly invoke ``record_intervention`` after making its own decision.
    """

    def __init__(self, path: str | Path | None = None):
        default_path = path is None
        if default_path:
            base = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
            path = base / "aware" / "break_policy.sqlite3"
        self.path = str(path)
        self._lock = threading.RLock()
        if self.path != ":memory:":
            parent = Path(self.path).parent
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if default_path:
                parent.chmod(0o700)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS interventions (
                id INTEGER PRIMARY KEY,
                timestamp REAL NOT NULL,
                singapore_date TEXT NOT NULL,
                reason TEXT NOT NULL,
                counts_toward_daily INTEGER NOT NULL
            )"""
        )
        self._db.commit()
        if self.path != ":memory:":
            Path(self.path).chmod(0o600)
        self._leisure_since: float | None = None
        self._last_leisure_at: float | None = None
        self._leisure_observations = 0
        self._leisure_times: set[float] = set()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _day(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp, SINGAPORE).date().isoformat()

    @staticmethod
    def _timestamp(value, fallback: float) -> float | None:
        if value is None:
            return fallback
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return None
        return timestamp if math.isfinite(timestamp) else None

    @staticmethod
    def _text(observation: dict) -> str:
        values = [
            observation.get(k, "") for k in ("content", "task", "title", "window_title")
        ]
        return " ".join(str(value) for value in values if value)

    @classmethod
    def _classify(cls, observation: dict) -> str:
        activity = str(observation.get("activity", "uncertain") or "uncertain").lower()
        if activity not in {
            "work",
            "leisure",
            "intentional_break",
            "uncertain",
            "away",
        }:
            activity = "uncertain"
        text = cls._text(observation)
        # Visible/task context is required even if a caller labels an observation leisure.
        if _WORK.search(text):
            return "work"
        if activity == "work":
            return "work"
        if activity in {"intentional_break", "away"}:
            return activity
        if _LEISURE.search(text):
            try:
                confidence = float(
                    observation.get("confidence", 0.7 if activity == "leisure" else 0.0)
                )
            except (TypeError, ValueError):
                confidence = 0.0
            return (
                "leisure"
                if math.isfinite(confidence) and confidence >= 0.6
                else "uncertain"
            )
        return "uncertain"

    def _counts(self, day: str) -> tuple[int, int, float | None]:
        rows = self._db.execute(
            "SELECT timestamp, reason, counts_toward_daily FROM interventions "
            "WHERE singapore_date = ? ORDER BY timestamp, id",
            (day,),
        ).fetchall()
        count = sum(int(row["counts_toward_daily"]) for row in rows)
        emergency = sum(row["reason"] == "emergency_release" for row in rows)
        latest_row = self._db.execute(
            "SELECT MAX(timestamp) AS timestamp FROM interventions"
        ).fetchone()
        latest = (
            float(latest_row["timestamp"])
            if latest_row and latest_row["timestamp"] is not None
            else None
        )
        return count, emergency, latest

    def _reset_streak(self) -> None:
        self._leisure_since = None
        self._last_leisure_at = None
        self._leisure_observations = 0
        self._leisure_times.clear()

    def observe(self, observation: dict, now: float | None = None) -> dict:
        """Return a decision dictionary; never trigger or record an intervention."""
        with self._lock:
            return self._observe(observation, now)

    def _observe(self, observation: dict, now: float | None = None) -> dict:
        if not isinstance(observation, dict):
            observation = {}
        invalid_now = False
        if now is None:
            now = time.time()
        else:
            try:
                now = float(now)
                invalid_now = not math.isfinite(now)
            except (TypeError, ValueError):
                invalid_now = True
            if invalid_now:
                now = time.time()
        observed_at = self._timestamp(observation.get("observed_at"), None)
        presence_at = self._timestamp(observation.get("presence_at"), None)
        day = self._day(now)
        daily_count, emergency_releases, last_intervention = self._counts(day)

        reason = "invalid_timestamp" if invalid_now else None
        if reason is None and (
            observed_at is None
            or now - observed_at > MAX_OBSERVATION_AGE
            or observed_at > now + 5
        ):
            reason = "stale_observation"
        if reason is None and not bool(observation.get("present")):
            reason = "not_present"
        if reason is None and (
            presence_at is None
            or now - presence_at > MAX_OBSERVATION_AGE
            or presence_at > now + 5
        ):
            reason = "stale_presence"
        if reason is None:
            for key in (
                "in_call",
                "presentation",
                "scheduled_break",
                "exercise_active",
            ):
                if key not in observation or observation[key] is None:
                    reason = "inhibitor_unknown"
                    break
                if observation[key]:
                    reason = key
                    break

        activity = self._classify(observation)
        history = observation.get("recent_observations", [])
        if activity == "uncertain" and isinstance(history, list):
            # Recent contextual evidence can classify the current browser/app view;
            # the app name itself is never used as evidence.
            history_acts = []
            for item in history:
                if not isinstance(item, dict):
                    continue
                item_at = self._timestamp(item.get("observed_at"), now)
                if item_at is not None and -5 <= now - item_at <= MAX_OBSERVATION_AGE:
                    history_acts.append(self._classify(item))
            if history_acts and history_acts[-1] == "leisure":
                activity = "leisure"
        if reason is None:
            if activity == "work":
                reason = "work"
            elif activity == "intentional_break":
                reason = "intentional_break"
            elif activity == "away":
                reason = "not_present"
            elif activity != "leisure":
                reason = "uncertain_activity"

        if reason is not None:
            self._reset_streak()
        else:
            assert observed_at is not None
            recent_times = set()
            if isinstance(history, list):
                for item in history:
                    if not isinstance(item, dict) or self._classify(item) != "leisure":
                        continue
                    item_at = self._timestamp(item.get("observed_at"), now)
                    if (
                        item_at is not None
                        and 0 <= now - item_at <= MAX_OBSERVATION_AGE
                    ):
                        recent_times.add(item_at)
            if (
                self._last_leisure_at is None
                or observed_at <= self._last_leisure_at
                or observed_at - self._last_leisure_at > MAX_OBSERVATION_GAP
            ):
                self._leisure_times = recent_times | {observed_at}
                self._leisure_since = min(self._leisure_times)
            else:
                self._leisure_times.update(recent_times)
                self._leisure_times.add(observed_at)
                self._leisure_since = min(self._leisure_times)
            self._leisure_observations = len(self._leisure_times)
            self._last_leisure_at = observed_at
            if (
                self._leisure_observations < 2
                or self._leisure_since is None
                or observed_at - self._leisure_since < LEISURE_SECONDS
            ):
                reason = "leisure_not_persistent"
            elif daily_count >= DAILY_MAXIMUM:
                reason = "daily_maximum"
            elif (
                last_intervention is not None
                and now - last_intervention < MIN_INTERVENTION_GAP
            ):
                reason = "cooldown"
            else:
                reason = "eligible"

        return {
            "eligible": reason == "eligible",
            "reason": reason,
            "activity": activity,
            "observed_at": observed_at,
            "leisure_since": self._leisure_since,
            "leisure_observations": self._leisure_observations,
            "daily_count": daily_count,
            "daily_target": DAILY_TARGET,
            "daily_maximum": DAILY_MAXIMUM,
            "emergency_releases": emergency_releases,
            "last_intervention_at": last_intervention,
            "mode": "observation_only",
        }

    def record_intervention(
        self, now: float | None = None, reason: str = "prompt"
    ) -> dict:
        """Persist a prompt or emergency release separately from eligibility checks."""
        now = float(now if now is not None else time.time())
        if not math.isfinite(now):
            raise ValueError("now must be a finite timestamp")
        reason = str(reason or "prompt").strip().lower()
        is_emergency = reason in {"emergency", "emergency_release", "emergency release"}
        stored_reason = "emergency_release" if is_emergency else reason
        counts = not is_emergency
        with self._lock:
            with self._db:
                self._db.execute(
                    "INSERT INTO interventions(timestamp, singapore_date, reason, counts_toward_daily) "
                    "VALUES (?, ?, ?, ?)",
                    (now, self._day(now), stored_reason, int(counts)),
                )
            daily_count, emergency_releases, last_intervention = self._counts(
                self._day(now)
            )
            self._reset_streak()
        return {
            "recorded": True,
            "reason": stored_reason,
            "daily_count": daily_count,
            "emergency_releases": emergency_releases,
            "last_intervention_at": last_intervention,
        }
