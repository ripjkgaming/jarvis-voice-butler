"""jarvis_rt schema generator must match the LiveKit-generated snapshot."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "work/livekit-removal/tool_schemas.json"


def test_schemas_match_snapshot():
    sys.path.insert(0, str(ROOT / "src"))
    import agent as app
    import jarvis_rt

    snap = json.loads(SNAPSHOT.read_text())
    agents = {
        "assistant": app.Assistant(),
        "research": app.ResearchAgent(),
        "system": app.SystemAgent(),
    }
    seen = {}
    for label, ag in agents.items():
        for t in ag.tools:
            if not (jarvis_rt.is_function_tool(t) or jarvis_rt.is_raw_function_tool(t)):
                # livekit-typed tool: convert via its wrapped function
                # livekit-typed tool: rebuild from the original (unwrapped) function
                fn, info = t.__wrapped__, t.info
                if hasattr(info, "raw_schema"):
                    rt = jarvis_rt.RawFunctionTool(
                        fn, jarvis_rt.tools.RawFunctionToolInfo(info.name, info.raw_schema)
                    )
                else:
                    rt = jarvis_rt.FunctionTool(
                        fn,
                        jarvis_rt.tools.FunctionToolInfo(info.name, info.description),
                    )
                    if t._instance is not None:
                        rt = rt.__get__(t._instance)
                t = rt
            s = jarvis_rt.tool_schema(t)
            seen[f"{label}:{s['name']}"] = s
    # tool availability varies with env/flags; compare the overlap
    common = set(seen) & set(snap)
    assert len(common) > 250
    bad = [k for k in common if json.loads(json.dumps(seen[k])) != snap[k]]
    assert not bad, bad[:5]
