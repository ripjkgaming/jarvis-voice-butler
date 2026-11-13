"""Local runtime paths for the paper-only workers; no global CLI configuration."""

from __future__ import annotations

import os
from pathlib import Path


def codex_binary() -> str | None:
    configured = os.environ.get("PAPER_MARKET_CODEX_BIN", "").strip()
    if configured:
        return configured
    bundled = Path("/usr/lib/chatgpt/resources/codex")
    # This bundled CLI is checked against VERIFIED_VERSION by CodexWorker;
    # the newer npm CLI on PATH is not implicitly trusted by this transport.
    return str(bundled) if bundled.is_file() else None
