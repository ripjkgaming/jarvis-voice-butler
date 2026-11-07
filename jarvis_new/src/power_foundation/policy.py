"""Permission preview boundary. Never authorizes or executes tool calls."""

from __future__ import annotations

EFFECTS = frozenset({"read", "draft", "external", "system"})


def preview_action(connector: str, operation: str, effect: str, arguments: dict):
    """Describe the intended effect while deliberately omitting raw arguments.

    This is a metadata-only contract. An explicit user confirmation and
    the existing app-specific execution guard are both required elsewhere.
    """
    if not connector or not operation or effect not in EFFECTS:
        raise ValueError("Invalid action metadata")
    if not isinstance(arguments, dict):
        raise TypeError("Arguments must be a dictionary")
    return {
        "connector": connector,
        "operation": operation,
        "effects": effect,
        "requires_approval": effect in {"external", "system"},
        "executable": False,
        "status": "preview_only",
        "argument_keys": sorted(k for k in arguments if k.isidentifier())[:12],
    }
