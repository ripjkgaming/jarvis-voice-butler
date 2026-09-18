"""venv bootstrap commands (uv) for both interpreters.

Main venv: Python 3.14 (agent worker, livekit stack). Wake venv: Python
3.11 (openwakeword's wheel floor) — installed with the dead
``tflite-runtime`` dependency dropped (ONNX backend only) and onnxruntime
pinned to the same major/minor as the main venv so the two don't drift.

Pure command builders + thin exec helper. Builders are unit-tested; the
exec path shells out to ``uv`` which the system ships.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# onnxruntime must match across both venvs (plan: "unify onnxruntime").
ONNXRUNTIME_VERSION = "1.30.0"

MAIN_PYTHON = "3.14"
WAKE_PYTHON = "3.11"


@dataclass(frozen=True)
class VenvPlan:
    repo: Path
    main_python: str = MAIN_PYTHON
    wake_python: str = WAKE_PYTHON

    @property
    def main_venv(self) -> Path:
        return self.repo / ".venv"

    @property
    def wake_venv(self) -> Path:
        return self.repo / ".venv-wake"


def main_venv_exists(plan: VenvPlan) -> bool:
    return (plan.main_venv / "bin" / "python").is_file()


def wake_venv_exists(plan: VenvPlan) -> bool:
    return (plan.wake_venv / "bin" / "python").is_file()


def create_venv_commands(plan: VenvPlan) -> list[list[str]]:
    """Two venv creation commands. Pure."""
    return [
        ["uv", "venv", str(plan.main_venv), "--python", plan.main_python],
        ["uv", "venv", str(plan.wake_venv), "--python", plan.wake_python],
    ]


def main_sync_command(plan: VenvPlan) -> list[str]:
    """Sync the main venv from the repo's pyproject + lockfile. Pure."""
    return ["uv", "sync", "--python", plan.main_python]


def wake_install_commands(plan: VenvPlan) -> list[list[str]]:
    """Install wake-client deps into the 3.11 venv without tflite_runtime.

    openwakeword declares ``tflite-runtime`` as a Linux-only dependency,
    but we run its ONNX backend exclusively, so the wheel is dead weight.
    We install the package with --no-deps and add exactly the runtime
    deps the ONNX path needs (audited from the openwakeword source).

    Returns argv lists (no shell). Pure.
    """
    py = str(plan.wake_venv / "bin" / "python")
    return [
        # Project + its real deps (livekit-agents, piper-tts, sounddevice,
        # python-dotenv...) first, so the editable install is complete.
        ["uv", "pip", "install", "--python", py, "-e", "."],
        # openwakeword WITHOUT tflite-runtime (Linux-only dep we never use;
        # ONNX backend only). --no-deps then add exactly its runtime deps.
        ["uv", "pip", "install", "--python", py, "--no-deps", "openwakeword"],
        [
            "uv",
            "pip",
            "install",
            "--python",
            py,
            f"onnxruntime=={ONNXRUNTIME_VERSION}",
            "tqdm",
            "scipy",
            "scikit-learn",
            "requests",
            "numpy",
        ],
    ]


def uv_available() -> bool:
    return shutil.which("uv") is not None


def run(cmd: list[str], *, cwd: Path | None = None) -> tuple[int, str]:
    """Run argv (no shell), return (rc, stderr_or_stdout). Thin."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=1800,  # first uv sync can take a while
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    text = (proc.stderr or proc.stdout or "").strip()
    return proc.returncode, text[-2000:]


def sync_all(plan: VenvPlan) -> list[str]:
    """Run every bootstrap command in order. Returns failure strings ([] = ok)."""
    failures: list[str] = []
    if not uv_available():
        return ["uv is not on PATH (install via https://astral.sh/uv)"]
    for cmd in create_venv_commands(plan):
        rc, err = run(cmd, cwd=plan.repo)
        if rc != 0:
            failures.append(f"{cmd[0]} {cmd[1]}: {err}")
    rc, err = run(main_sync_command(plan), cwd=plan.repo)
    if rc != 0:
        failures.append(f"main uv sync: {err}")
    for cmd in wake_install_commands(plan):
        rc, err = run(cmd, cwd=plan.repo)
        if rc != 0:
            failures.append(f"wake install {cmd[2]}: {err}")
    return failures
