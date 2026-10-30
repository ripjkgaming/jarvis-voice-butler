"""Dump name/description/JSON schema of every agent tool for regression diffs.

Usage: uv run python scripts/dump_tool_schemas.py [DEST]
DEST defaults to work/jarvis_rt-tool-schemas.json. The frozen LiveKit-era
snapshot at work/livekit-removal/tool_schemas.json is never the default target.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jarvis_rt import is_function_tool, is_raw_function_tool, tool_schema

DEFAULT_DEST = "work/jarvis_rt-tool-schemas.json"


def _schema(tool) -> dict:
    return tool_schema(tool)


def dump(agents: dict) -> dict:
    out: dict = {}
    for label, agent in agents.items():
        for tool in agent.tools:
            if not is_function_tool(tool) and not is_raw_function_tool(tool):
                continue
            s = _schema(tool)
            out[f"{label}:{s.get('name')}"] = s
    return dict(sorted(out.items()))


def main() -> None:
    import agent as app

    agents = {
        "assistant": app.Assistant(),
        "research": app.ResearchAgent(),
        "system": app.SystemAgent(),
    }
    data = dump(agents)
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DEST)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=1, sort_keys=True))
    print(f"{len(data)} tools -> {dest}")


if __name__ == "__main__":
    main()
