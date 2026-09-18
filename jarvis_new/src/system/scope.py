"""Scope allowlist + workspace confinement for pentest tools.

Refuse-closed: missing/empty ~/.jarvis/scope.txt means every active tool
refuses. Read per run — never cached — so edits take effect immediately.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from urllib.parse import urlparse

SCOPE_PATH = Path.home() / ".jarvis" / "scope.txt"
PENTEST_DIR = Path.home() / ".jarvis" / "pentest"


class ScopeError(Exception):
    """User-facing scope refusal."""


def read_scope_lines() -> list[str]:
    """Raw non-comment entries, per run (no cache)."""
    try:
        text = SCOPE_PATH.read_text()
    except OSError:
        return []
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line[:200])
    return lines


def _host_from_target(target: str) -> str:
    """Extract a bare host from a URL, host:port, or plain host."""
    t = (target or "").strip()
    if "://" in t:
        try:
            return (urlparse(t).hostname or t).strip()
        except Exception:
            return t
    return t.split("/")[0].split(":")[0].strip().strip("[]")


def _entry_matches(entry: str, host: str) -> bool:
    entry = entry.strip().lower()
    host = host.strip().lower().rstrip(".")
    if not entry or not host:
        return False
    # CIDR entry: match literal IPs inside it.
    if "/" in entry:
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            return False
        try:
            return ipaddress.ip_address(host) in net
        except ValueError:
            return False
    # Plain IP entry: exact match.
    try:
        return ipaddress.ip_address(host) == ipaddress.ip_address(entry)
    except ValueError:
        pass
    # Hostname entry: exact or subdomain match.
    return host == entry or host.endswith("." + entry)


def check_scope(target: str) -> str:
    """Return the matched host or raise ScopeError (refuse-closed)."""
    entries = read_scope_lines()
    if not entries:
        raise ScopeError(
            "Testing scope is empty, Sir — add a host to ~/.jarvis/scope.txt "
            "with scope_allow before any active scan."
        )
    host = _host_from_target(target)
    if not host:
        raise ScopeError("No target to check against the scope allowlist.")
    for entry in entries:
        if _entry_matches(entry, host):
            return host
    raise ScopeError(
        f"{host} is outside the testing scope, Sir — allowlist it with "
        "scope_allow first."
    )


def target_slug(target: str) -> str:
    """Filesystem-safe slug for workspace confinement."""
    host = _host_from_target(target) or "unknown"
    slug = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")[:60]
    return slug or "unknown"


def workspace_for(target: str) -> Path:
    """Confined output dir ~/.jarvis/pentest/<slug>/ (created)."""
    d = PENTEST_DIR / target_slug(target)
    d.mkdir(parents=True, exist_ok=True)
    return d
