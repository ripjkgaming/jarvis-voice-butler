"""Offline reasoning-backend diagnostics: python -m backend_diagnostics.

CLI presence is not authentication, quota, or verified service availability.
Cooldowns describe only the current process; the standalone command starts fresh.
"""

from __future__ import annotations

import json
import os
import shutil

import backend_pool
import chatgpt_cli


def report():
    states = backend_pool.status()
    providers = {}
    for name, binary, flag in (
        ("chatgpt", "codex", "JARVIS_CHATGPT"),
        ("claude", "claude", "JARVIS_CLAUDE"),
    ):
        enabled = os.environ.get(flag, "1").lower() not in (
            "0",
            "false",
            "off",
            "no",
        )
        installed = bool(
            chatgpt_cli.codex_binary() if name == "chatgpt" else shutil.which(binary)
        )
        providers[name] = {
            "enabled": enabled,
            "cli_installed": installed,
            "authentication": "unverified",
            "quota": "unknown",
            **states.get(name, {"state": "ready", "retry_after_s": 0}),
        }
    preferred = os.environ.get("JARVIS_BACKEND_PROVIDER", "chatgpt")
    return {
        "offline": True,
        "cooldown_scope": "current_process",
        "preferred_provider": preferred if preferred in providers else "unknown",
        "providers": providers,
    }


def main():
    print(json.dumps(report(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
