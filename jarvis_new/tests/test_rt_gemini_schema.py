import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_declarations_match_livekit_for_every_tool():
    import agent as app
    import jarvis_rt
    from jarvis_rt.gemini_schema import function_declarations
    from livekit.agents import llm

    for ag in (app.Assistant(), app.ResearchAgent(), app.SystemAgent()):
        ctx = llm.ToolContext(ag.tools)
        theirs = ctx.parse_function_tools("google", tool_behavior=None, use_parameters_json_schema=False)
        mine = []
        for t in ag.tools:
            fn, info = t.__wrapped__, t.info
            if hasattr(info, "raw_schema"):
                rt = jarvis_rt.RawFunctionTool(fn, jarvis_rt.tools.RawFunctionToolInfo(info.name, info.raw_schema))
            else:
                rt = jarvis_rt.FunctionTool(fn, jarvis_rt.tools.FunctionToolInfo(info.name, info.description))
                if t._instance is not None:
                    rt = rt.__get__(t._instance)
            mine.append(rt)
        got = function_declarations(mine)
        decls = [d.model_dump(exclude_none=True) for t in got for d in t.function_declarations]
        from google.genai import types

        want = [types.FunctionDeclaration.model_validate(s).model_dump(exclude_none=True) for s in theirs]
        key = lambda d: d["name"]  # noqa: E731
        assert sorted(decls, key=key) == sorted(want, key=key)
