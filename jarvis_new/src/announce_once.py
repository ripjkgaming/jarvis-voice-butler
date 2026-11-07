"""Speak each wake announcement once.

The master participant's `jarvis.announce` attribute outlives the agent, so a
restarted agent re-read and re-spoke it. unconsumed() is True once per text
per TTL, persisted in ~/.jarvis/announce_consumed.json.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from pathlib import Path


def unconsumed(text: str, ttl_s: float = 600.0) -> bool:
    """True once per announcement text within ttl_s."""
    text = " ".join(str(text or "").split())
    if not text:
        return False
    home = os.environ.get("JARVIS_HOME", "").strip()
    path = (Path(home) if home else Path.home() / ".jarvis") / "announce_consumed.json"
    key = hashlib.sha1(text.encode()).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            now = time.time()
            try:
                seen = json.loads(path.read_text())
                if not isinstance(seen, dict):
                    seen = {}
            except (OSError, ValueError):
                seen = {}
            seen = {
                k: t
                for k, t in seen.items()
                if isinstance(t, (int, float)) and 0 <= now - t < ttl_s
            }
            fresh = key not in seen
            seen[key] = now if fresh else seen[key]
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(seen))
            tmp.chmod(0o600)
            os.replace(tmp, path)
            return fresh
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
