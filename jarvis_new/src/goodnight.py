"""Retired unsafe five-minute watcher. Never spawn, arm or lock.

Compatibility tombstone prevents stale callers from reviving the old path.
Coordinator may call cancel() once during reviewed migration to invalidate
an already-running legacy watcher. No module import performs migration.
"""

import os
from pathlib import Path


def enabled():
    return False


def is_goodnight(text):
    return False


def state_path():
    return (
        Path(os.environ.get("JARVIS_HOME", str(Path.home() / ".jarvis")))
        / "goodnight.json"
    )


def pending():
    return state_path().exists()


def cancel():
    had = pending()
    state_path().unlink(missing_ok=True)
    return had


def arm(*args, **kwargs):
    raise RuntimeError("Legacy goodnight watcher retired; use trusted v2 owner ingress")


def watch(*args, **kwargs):
    return "retired"


def decide(*args, **kwargs):
    return "hold"
