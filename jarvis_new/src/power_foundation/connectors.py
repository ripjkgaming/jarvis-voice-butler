"""Read-only connector discovery and health checks for existing Jarvis systems.

The shared registry never sends, types, clicks, runs shell code, or starts a service.
Import availability is intentionally NOT reported as a working connection.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import urllib.request
from pathlib import Path

DEFAULTS = {
    "desktop": "system.desktop",
    "whatsie": "system.whatsapp",
    "browser": "browser",
    "mobile": "phone_session",
    "memory": "memory",
    "tasks": "agent_tasks",
    "voice": "local_voice",
    "mcp": "system.codex_worker",
    "workers": "system.codex_worker",
    "downloads": "system.files_tools",
}


class ConnectorRegistry:
    def __init__(self, store, enabled=False):
        self.store = store
        self.enabled = enabled
        self._probes = {}

    @property
    def names(self):
        return sorted(self._probes)

    def register(self, name, probe):
        if not name or not callable(probe):
            raise ValueError("Connector requires a name and probe")
        self._probes[name] = probe

    def probe(self, name):
        if name not in self._probes:
            return {"ready": False, "status": "unregistered"}
        try:
            result = self._probes[name]()
            if not isinstance(result, dict):
                raise ValueError("Invalid probe response")
            return {
                "ready": bool(result.get("ready")),
                "status": result.get(
                    "status",
                    "available" if result.get("ready") else "unavailable",
                ),
                **result,
            }
        except Exception:
            # Do not leak exception strings: they may include credentials.
            return {"ready": False, "status": "error"}

    def invoke_read(self, name):
        if not self.enabled:
            raise PermissionError("Foundation read connectors are disabled")
        result = self.probe(name)
        self.store.event("connector_probe", name)
        return result

    def execute_action(self, name, action, payload):
        # No foundation side effects. Existing verified Jarvis confirmation tools
        # remain responsible for mutating the desktop or external accounts.
        self.store.event("action_blocked", name)
        raise PermissionError(
            "Foundation connectors are read-only; use existing approved Jarvis tools"
        )


def _module_probe(module_name):
    def probe():
        spec = importlib.util.find_spec(module_name)
        return {
            "ready": False,
            "installed": spec is not None,
            "status": "scaffold_only" if spec else "missing",
            "module": module_name,
        }

    return probe


def _desktop_probe():
    module = _module_probe("system.desktop")()
    has_display = bool(os.getenv("WAYLAND_DISPLAY") or os.getenv("DISPLAY"))
    return {
        **module,
        "ready": module["installed"] and has_display,
        "status": "display_detected" if has_display else "no_display",
        "display_detected": has_display,
    }


def _whatsie_probe():
    module = _module_probe("system.whatsapp")()
    if not module["installed"]:
        return module
    # Loopback-only CDP health request. Never opens, marks read, or sends a chat.
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:9223/json/version", timeout=0.5
        ) as response:
            ready = response.status == 200
        return {
            **module,
            "ready": ready,
            "status": "cdp_reachable" if ready else "cdp_unavailable",
        }
    except Exception:
        return {**module, "status": "cdp_unavailable"}


def _browser_probe():
    module = _module_probe("browser")()
    binary = next(
        (
            name
            for name in ("brave-browser", "brave", "google-chrome", "chromium")
            if shutil.which(name)
        ),
        None,
    )
    return {
        **module,
        "ready": module["installed"] and binary is not None,
        "status": "binary_detected" if binary else "browser_binary_unknown",
        "browser_binary": binary,
    }


def _memory_probe():
    module = _module_probe("memory")()
    root = Path(
        os.environ.get("JARVIS_VAULT")
        or Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis")) / "vault"
    )
    return {
        **module,
        "ready": module["installed"] and root.is_dir(),
        "status": "vault_detected" if root.is_dir() else "vault_not_detected",
    }


def basic_connectors(store, enabled=None, live=False):
    """Register all major interfaces; optional live probes never mutate state.

    live=False only reports scaffold availability, not service readiness.
    """
    if enabled is None:
        enabled = os.environ.get("JARVIS_POWER_FOUNDATION", "0") == "1"
    registry = ConnectorRegistry(store, enabled=enabled)
    for name, module in DEFAULTS.items():
        registry.register(name, _module_probe(module))
    if live:
        for name, probe in {
            "desktop": _desktop_probe,
            "whatsie": _whatsie_probe,
            "browser": _browser_probe,
            "memory": _memory_probe,
        }.items():
            registry.register(name, probe)
    return registry
