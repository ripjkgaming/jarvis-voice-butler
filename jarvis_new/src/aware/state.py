"""Always Aware persistent state: ~/.jarvis/aware/state.json + kill file.

Ships OFF: ``enabled`` defaults to False and the env kill switch
``JARVIS_AWARE=0`` beats everything. Every reader is fail-soft and every
writer is atomic (tmp + rename, 0600). The voice tools (agent process) and
the sidecar (bridge process) share state only through these files.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

DEFAULTS: dict = {
    "enabled": False,
    "paused_until": 0.0,
    "shadow": False,
    "spend_cap": None,  # per-transaction cap in major units, None = unlimited
    "spend_currency": "GBP",
    "muted": {},  # kind or subject -> until ts (0 = until re-enabled)
    "last_reason": "",
    "last_tick": 0.0,
    "next_tick": 0.0,
    "phase": "idle",  # idle | capturing | skipped | offer
    "phase_until": 0.0,
    "parked": False,  # idle: no ticks until input or media resumes
}


def jarvis_home() -> Path:
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def aware_dir() -> Path:
    """$JARVIS_HOME/aware, created 0700 on demand. Never raises."""
    path = jarvis_home() / "aware"
    with contextlib.suppress(OSError):
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    return path


def sub(name: str) -> Path:
    path = aware_dir() / name
    with contextlib.suppress(OSError):
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    return path


def state_path() -> Path:
    return aware_dir() / "state.json"


def kill_path() -> Path:
    return aware_dir() / "KILL"


def env_allows(env: dict | None = None) -> bool:
    """False when JARVIS_AWARE is 0/off/false/no. Pure (env)."""
    env = os.environ if env is None else env
    return str(env.get("JARVIS_AWARE", "1")).strip().lower() not in (
        "0",
        "off",
        "false",
        "no",
    )


def killed() -> bool:
    try:
        return kill_path().exists()
    except OSError:
        return True  # cannot tell: fail closed


def load() -> dict:
    out = dict(DEFAULTS)
    out["muted"] = {}
    try:
        data = json.loads(state_path().read_text())
        if isinstance(data, dict):
            for key in DEFAULTS:
                if key in data:
                    out[key] = data[key]
    except (OSError, ValueError):
        pass
    if not isinstance(out.get("muted"), dict):
        out["muted"] = {}
    return out


def write_private(path: Path, text: str) -> bool:
    """Atomic 0600 write. Never raises."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def append_private(path: Path, line: str) -> bool:
    """Append one line to a 0600 file. Never raises."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(line.rstrip("\n") + "\n")
        return True
    except OSError:
        return False


def save(state: dict) -> bool:
    clean = {k: state.get(k, DEFAULTS[k]) for k in DEFAULTS}
    return write_private(state_path(), json.dumps(clean, sort_keys=True))


def update(**fields) -> dict:
    state = load()
    state.update({k: v for k, v in fields.items() if k in DEFAULTS})
    save(state)
    return state


def paused(state: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    try:
        return float(state.get("paused_until") or 0) > now
    except (TypeError, ValueError):
        return False


def is_muted(state: dict, key: str, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    until = (state.get("muted") or {}).get((key or "").lower())
    if until is None:
        return False
    try:
        until = float(until)
    except (TypeError, ValueError):
        return True
    return until == 0 or until > now


def status_word(state: dict | None = None, now: float | None = None) -> str:
    """ "on" | "off" | "paused" for the phone/HUD. Pure over state."""
    state = load() if state is None else state
    if not env_allows() or killed() or not state.get("enabled"):
        return "off"
    if paused(state, now):
        return "paused"
    return "on"
