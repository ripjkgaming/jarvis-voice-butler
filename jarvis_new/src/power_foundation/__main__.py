"""Foundation diagnostics: no agent startup, network or mutations except SQLite init."""

import argparse
import json
import os
from pathlib import Path

from .connectors import basic_connectors
from .engine import FoundationStore
from .workspace import snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("status", "recover", "inspect-mcp"),
        nargs="?",
        default="status",
    )
    parser.add_argument(
        "--live", action="store_true", help="Read-only local service health probes"
    )
    parser.add_argument(
        "--allowlist",
        default=None,
        help="Explicit JSON allowlist for local MCP inspection",
    )
    args = parser.parse_args()
    if args.command == "inspect-mcp":
        if not args.allowlist:
            parser.error("inspect-mcp requires an explicitly supplied --allowlist")
        from .mcp_transport import inspect_approved

        print(json.dumps(inspect_approved(args.allowlist), indent=2))
        return
    home = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
    store = FoundationStore(home / "foundation" / "state.sqlite3")
    registry = basic_connectors(store, live=args.live)
    if args.command == "recover":
        print(json.dumps({"paused_interrupted": store.pause_interrupted()}))
    else:
        print(json.dumps(snapshot(store, registry), indent=2))


if __name__ == "__main__":
    main()
