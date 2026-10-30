"""Dump name/description/JSON schema of every agent tool for regression diffs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from livekit.agents import llm  # noqa: E402


def _schema(tool) -> dict:
    if llm.is_raw_function_tool(tool):
        return tool.info.raw_schema
    return llm.utils.build_legacy_openai_schema(tool, internally_tagged=True)


def dump(agents: dict) -> dict:
    out: dict = {}
    for label, agent in agents.items():
        for tool in agent.tools:
            if not llm.is_function_tool(tool) and not llm.is_raw_function_tool(tool):
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
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "work/livekit-removal/tool_schemas.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=1, sort_keys=True))
    print(f"{len(data)} tools -> {dest}")


if __name__ == "__main__":
    main()
