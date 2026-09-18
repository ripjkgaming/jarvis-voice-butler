"""API key collection + persistence (env files, 0600).

Required: GOOGLE_API_KEY (Gemini Live / inference). Optional: BRAVE and
YDC (web search), plus the OSINT slots in ~/.jarvis/keys.env. Written to
the same files the code already reads: root ``.env.local`` (worker) and
``frontend/.env.local`` (token route + wake client). The OSINT keys.env
is separate and never logged.

Never echoes values back; prompts read via getpass-style hidden input.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from pathlib import Path

REQUIRED_KEYS = ("GOOGLE_API_KEY",)
OPTIONAL_KEYS = ("BRAVE_API_KEY", "YDC_API_KEY")
OSINT_KEYS = ("SHODAN_API_KEY", "CENSYS_API_ID", "CENSYS_API_SECRET")


def keys_env_path(jarvis_home: Path | str | None = None) -> Path:
    override = os.environ.get("JARVIS_KEYS_FILE", "").strip()
    if override:
        return Path(override)
    if jarvis_home is not None:
        return Path(jarvis_home) / "keys.env"
    home = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(home) / "keys.env") if home else Path.home() / ".jarvis" / "keys.env"


def worker_env_path(repo: Path) -> Path:
    return repo / ".env.local"


def frontend_env_path(repo: Path) -> Path:
    return repo / "frontend" / ".env.local"


@dataclass
class CollectedKeys:
    google: str = ""
    brave: str = ""
    ydc: str = ""
    shodan: str = ""
    censys_id: str = ""
    censys_secret: str = ""

    def worker_updates(self) -> dict[str, str]:
        out = {}
        for key, val in (
            ("GOOGLE_API_KEY", self.google),
            ("BRAVE_API_KEY", self.brave),
            ("YDC_API_KEY", self.ydc),
        ):
            if val.strip():
                out[key] = val.strip()
        return out

    def osint_updates(self) -> dict[str, str]:
        out = {}
        for key, val in (
            ("SHODAN_API_KEY", self.shodan),
            ("CENSYS_API_ID", self.censys_id),
            ("CENSYS_API_SECRET", self.censys_secret),
        ):
            if val.strip():
                out[key] = val.strip()
        return out


def write_keys(
    repo: Path, keys: CollectedKeys, *, jarvis_home: Path | None = None
) -> None:
    """Persist keys into worker + frontend .env.local and keys.env. I/O."""
    from wizard.livekit import write_env

    worker = worker_env_path(repo)
    write_env(worker, keys.worker_updates())
    osint = keys.osint_updates()
    if osint:
        path = keys_env_path(jarvis_home)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_env(path, osint)
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)


def keys_summary(repo: Path) -> dict[str, bool]:
    """Presence flags for the wizard checklist. Pure-ish."""
    from wizard.livekit import read_env

    worker = read_env(worker_env_path(repo))
    osint = read_env(keys_env_path())
    return {
        "google": bool(worker.get("GOOGLE_API_KEY")),
        "brave": bool(worker.get("BRAVE_API_KEY")),
        "ydc": bool(worker.get("YDC_API_KEY")),
        "shodan": bool(osint.get("SHODAN_API_KEY")),
        "censys": bool(osint.get("CENSYS_API_ID") and osint.get("CENSYS_API_SECRET")),
    }
