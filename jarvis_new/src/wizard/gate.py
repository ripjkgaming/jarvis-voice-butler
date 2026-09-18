"""Bridge /health gate + HF cache audit.

The bridge gate is the plan's finale check: the full pipeline is only
"green" when the loopback control plane answers. The HF cache audit is
the plan's delivery pre-win: it inventories the ~3.7GB cache and marks
which models are actually referenced by this codebase (needle2 is; the
five faster-whisper checkpoints and the Mistral-Nemo GGUF stub are not).
"""

from __future__ import annotations

import time
import urllib.request
from pathlib import Path

BRIDGE_DEFAULT_PORT = 4317
HF_HUB = Path.home() / ".cache" / "huggingface" / "hub"
# Models the code actually loads (audited from src/).
USED_HF_REPOS = {"models--Cactus-Compute--needle2"}


def bridge_url(port: int = BRIDGE_DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}/health"


def probe_bridge(
    port: int = BRIDGE_DEFAULT_PORT, timeout: float = 1.5
) -> tuple[bool, dict]:
    """GET /health. Returns (ok, body_or_error). I/O (stdlib http)."""
    try:
        req = urllib.request.Request(bridge_url(port))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode(errors="replace")
            return resp.status == 200 and '"ok": true' in body, {}
    except Exception as exc:
        return False, {"error": str(exc)}


def wait_bridge(port: int = BRIDGE_DEFAULT_PORT, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ok, _ = probe_bridge(port)
        if ok:
            return True
        time.sleep(0.5)
    return False


def hf_cache_summary(cache_dir: Path | None = None) -> dict:
    """Inventory the HF hub cache. Pure-ish (reads dirs).

    Returns {total_bytes, models: [{name, bytes, used}], wasted_bytes}.
    """
    root = cache_dir or HF_HUB
    models: list[dict] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return {"total_bytes": 0, "models": [], "wasted_bytes": 0}
    total = 0
    for entry in entries:
        if not entry.name.startswith("models--"):
            continue
        size = _dir_size(entry)
        total += size
        models.append(
            {
                "name": entry.name,
                "bytes": size,
                "used": entry.name in USED_HF_REPOS,
            }
        )
    wasted = sum(m["bytes"] for m in models if not m["used"])
    return {"total_bytes": total, "models": models, "wasted_bytes": wasted}


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        pass
    return total


def prune_unused(cache_dir: Path | None = None) -> list[str]:
    """Delete HF repos not referenced by the code. Returns removed names."""
    summary = hf_cache_summary(cache_dir)
    removed = []
    for m in summary["models"]:
        if not m["used"]:
            path = (cache_dir or HF_HUB) / m["name"]
            try:
                import shutil

                shutil.rmtree(path)
                removed.append(m["name"])
            except OSError:
                pass
    return removed
