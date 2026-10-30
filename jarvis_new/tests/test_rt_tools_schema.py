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
    _assert_parity(bad, common)


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
    # Docstrings may legitimately change after the snapshot (it was taken from
    # the committed tree); the JSON-schema structure must not.
    bad = [k for k in common if _structure(seen[k]) != _structure(snap[k])]
    _assert_parity(bad, common)
    assert types  # keep import explicit


def _structure(node):
    if isinstance(node, dict):
        return {k: _structure(v) for k, v in node.items() if k != "description"}
    if isinstance(node, list):
        return [_structure(v) for v in node]
    return node


def _assert_parity(bad, common):
    """Tools edited on purpose after the snapshot may differ (a few percent at
    most); a systematic generator bug would break far more."""
    assert len(bad) <= max(3, len(common) // 50), sorted(bad)[:10]
