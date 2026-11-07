"""Small stdlib-only reverse log reader shared by bridge and HUD telemetry."""

from __future__ import annotations

import os
from pathlib import Path


def tail_lines(path: Path, limit: int) -> list[str]:
    """Read the final lines in blocks, tolerating invalid bytes and missing logs."""
    if limit <= 0:
        return []
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            remaining = stream.tell()
            chunks: list[bytes] = []
            newlines = 0
            while newlines <= limit and remaining > 0:
                step = min(4096, remaining)
                remaining -= step
                stream.seek(remaining)
                block = stream.read(step)
                chunks.append(block)
                newlines += block.count(b"\n")
        return (
            b"".join(reversed(chunks))
            .decode("utf-8", errors="replace")
            .splitlines()[-limit:]
        )
    except (OSError, ValueError):
        return []
