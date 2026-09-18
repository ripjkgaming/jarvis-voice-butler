"""Persisted wizard step state: load/save, skip-green logic. Pure stdlib.

The state file lives at ``$JARVIS_HOME/wizard-state.json`` (default
``~/.jarvis/wizard-state.json``) and is a simple ``{step_id: status}``
map. A step is "green" when it completed successfully before; reruns
skip green steps unless forced. Structure:

    {
      "version": 1,
      "steps": {
        "sysdeps": {"status": "green", "ts": 1720000000},
        ...
      }
    }

Never stores secrets. Permissions are tightened to 0600 on write.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

STATE_VERSION = 1
DEFAULT_STATE_PATH = Path.home() / ".jarvis" / "wizard-state.json"


def state_path(jarvis_home: Path | str | None = None) -> Path:
    """Resolve the state file path honoring JARVIS_HOME. Pure."""
    if jarvis_home is not None:
        return Path(jarvis_home) / "wizard-state.json"
    override = os.environ.get("JARVIS_HOME", "").strip()
    if override:
        return Path(override) / "wizard-state.json"
    return DEFAULT_STATE_PATH


@dataclass
class StepState:
    status: str  # "pending" | "green" | "amber" | "red"
    ts: float = 0.0
    detail: str = ""

    def to_dict(self) -> dict:
        return {"status": self.status, "ts": int(self.ts), "detail": self.detail}

    @classmethod
    def from_dict(cls, data: dict) -> StepState:
        return cls(
            status=str(data.get("status", "pending")),
            ts=float(data.get("ts", 0) or 0),
            detail=str(data.get("detail", "")),
        )


class WizardState:
    """Immutable-in-place step registry persisted as JSON. Pure I/O."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or state_path()
        self._steps: dict[str, StepState] = {}
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def steps(self) -> dict[str, StepState]:
        return dict(self._steps)

    def load(self) -> None:
        try:
            data = json.loads(self._path.read_text())
            raw = data.get("steps", {}) if isinstance(data, dict) else {}
            self._steps = {
                str(k): StepState.from_dict(v if isinstance(v, dict) else {})
                for k, v in raw.items()
            }
        except (OSError, ValueError):
            self._steps = {}

    def save(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "steps": {k: v.to_dict() for k, v in sorted(self._steps.items())},
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        os.chmod(tmp, 0o600)
        tmp.replace(self._path)

    def get(self, step_id: str) -> StepState:
        return self._steps.get(step_id, StepState(status="pending"))

    def is_green(self, step_id: str) -> bool:
        return self.get(step_id).status == "green"

    def mark(self, step_id: str, status: str, detail: str = "") -> None:
        self._steps[step_id] = StepState(status=status, ts=time.time(), detail=detail)
        self.save()

    def pending(self, step_id: str) -> bool:
        return self.get(step_id).status in ("pending", "")

    def reset(self, step_id: str | None = None) -> None:
        """Forget one step (or all when None). Useful for --force."""
        if step_id is None:
            self._steps = {}
        else:
            self._steps.pop(step_id, None)
        self.save()
