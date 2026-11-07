"""Env helpers + legacy LiveKit migration (no LiveKit server any more).

LiveKit was replaced by the in-repo WebSocket voice hub hosted by the agent
worker (see docs/voice-transport.md). The wizard no longer vendors
livekit-server, writes ``~/.jarvis/livekit.yaml`` or generates a LIVEKIT
keypair. This module keeps the generic .env read/merge helpers used by the
keys step, and a read-only migration report so stale LiveKit state left by
older installs is tolerated: it is reported, never parsed for secrets, and
never deleted or rewritten.
"""

from __future__ import annotations

import re
from pathlib import Path

LEGACY_ENV_KEYS = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
LEGACY_YAML_NAME = "livekit.yaml"

_ENV_PAT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def read_env(path: Path) -> dict[str, str]:
    """Parse a .env file (last value wins, quotes stripped). Pure."""
    out: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            m = _ENV_PAT.match(line)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip()
            if val[:1] == val[-1:] and val[:1] in ("'", '"') and len(val) >= 2:
                val = val[1:-1]
            out[key] = val
    except OSError:
        pass
    return out


def write_env(path: Path, updates: dict[str, str], template: str = "") -> None:
    """Merge updates into a .env file preserving other keys/comments.

    Pure-ish (file I/O). ``template`` is the file body to start from when
    the file does not exist yet.
    """
    existing = (
        path.read_text().splitlines()
        if path.exists()
        else (template.splitlines() if template else [])
    )
    keys_seen: set[str] = set()
    out: list[str] = []
    for line in existing:
        m = _ENV_PAT.match(line.strip())
        if m and m.group(1) in updates:
            key = m.group(1)
            if key in keys_seen:
                continue  # dedupe; last writer wins
            keys_seen.add(key)
            out.append(f"{key}={updates[key]}")
        else:
            out.append(line)
    for key, val in updates.items():
        if key not in keys_seen:
            out.append(f"{key}={val}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")


def legacy_livekit_state(home: Path, env_files: list[Path]) -> dict[str, object]:
    """Report leftover LiveKit state from older installs. Read-only.

    Returns presence flags only (never secret values):
    ``{"yaml": bool, "env_keys": [names present in any env file]}``.
    Nothing is deleted or modified; the voice hub ignores these entries.
    """
    present: list[str] = []
    for path in env_files:
        values = read_env(path)
        for key in LEGACY_ENV_KEYS:
            if values.get(key) and key not in present:
                present.append(key)
    return {
        "yaml": (Path(home) / LEGACY_YAML_NAME).is_file(),
        "env_keys": present,
    }
