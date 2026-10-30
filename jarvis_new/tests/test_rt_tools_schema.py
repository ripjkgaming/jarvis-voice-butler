"""Tool schemas must match the snapshots captured from the LiveKit stack."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
SNAP = ROOT / "work/livekit-removal"


def _agents():
    import agent as app

    return {
        "assistant": app.Assistant(),
        "research": app.ResearchAgent(),
        "system": app.SystemAgent(),
    }


def test_schemas_match_snapshot():
    import jarvis_rt

    snap = json.loads((SNAP / "tool_schemas.json").read_text())
    seen = {}
    for label, ag in _agents().items():
        for t in ag.tools:
            s = jarvis_rt.tool_schema(t)
            seen[f"{label}:{s['name']}"] = s
    common = set(seen) & set(snap)
    assert len(common) > 250
    bad = [k for k in common if json.loads(json.dumps(seen[k])) != snap[k]]
    assert not bad, bad[:5]


def test_gemini_declarations_match_snapshot():
    from google.genai import types
    from jarvis_rt.gemini_schema import function_declarations

    snap = json.loads((SNAP / "gemini_declarations.json").read_text())
    seen = {}
    for label, ag in _agents().items():
        for tool in function_declarations(ag.tools):
            for d in tool.function_declarations:
                seen[f"{label}:{d.name}"] = d.model_dump(exclude_none=True, mode="json")
    common = set(seen) & set(snap)
    assert len(common) > 250
    bad = [k for k in common if seen[k] != snap[k]]
    assert not bad, bad[:5]
    assert types  # keep import explicit
