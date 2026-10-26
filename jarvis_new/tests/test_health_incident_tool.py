"""Test the exact staged integration nodes without importing the live agent."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import health_recorder_client
import pytest

SRC = Path(__file__).resolve().parents[1] / "src"


def extract_function(path, name, namespace):
    tree = ast.parse(path.read_text())
    node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    node.decorator_list = []
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


@pytest.mark.asyncio
async def test_exact_agent_tool_delegates_to_guarded_report(monkeypatch):
    calls = []

    async def report(window):
        calls.append(window)
        return {"say": "disabled", "text": "disabled"}

    monkeypatch.setattr(health_recorder_client, "incident_report", report)
    function = extract_function(
        SRC / "agent.py", "system_health_incident", {"RunContext": object}
    )
    assert await function(None, None, 60) == {"say": "disabled", "text": "disabled"}
    assert calls == [60]


def test_owner_guard_private_revoked_guest_and_missing_providers(monkeypatch):
    import sys

    state = {"private": False, "guest": False, "watch": False}
    phone = SimpleNamespace(
        private=lambda: state["private"], guest_required=lambda: state["guest"]
    )
    watch = SimpleNamespace(guest_required=lambda: state["watch"])
    monkeypatch.setitem(sys.modules, "phone_session", phone)
    monkeypatch.setitem(sys.modules, "watch_mode", watch)
    assert health_recorder_client.owner_allowed()
    for field in state:
        state[field] = True
        assert not health_recorder_client.owner_allowed()
        state[field] = False
    monkeypatch.setitem(sys.modules, "phone_session", None)
    assert not health_recorder_client.owner_allowed()


def test_shared_hunks_preserve_auth_and_no_model_actions():
    text = (SRC / "bridge.py").read_text()
    for name in ("do_GET", "do_POST"):
        tree = ast.parse(text)
        node = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        )
        source = ast.get_source_segment(text, node)
        assert source.index("if not self._authorized()") < source.index(
            '"/health-recorder/"'
        )
    source = (SRC / "agent.py").read_text()
    function = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "system_health_incident"
    )
    tool = ast.get_source_segment(source, function)
    assert "generate_reply" not in tool and "incident_report" in tool
    assert '"system_health_incident"' in source


def test_cli_disabled_and_mocked_report(monkeypatch, capsys):
    import importlib.util

    path = SRC.parent / "scripts" / "system_health_incident.py"
    spec = importlib.util.spec_from_file_location("health_cli", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.delenv("JARVIS_HEALTH_RECORDER", raising=False)
    assert cli.main([]) == 1
    assert "unavailable" in capsys.readouterr().out
    calls = []

    class Client:
        def request(self, route, payload=None):
            calls.append((route, payload))
            return {"ok": True, "say": "numeric evidence"}

    assert cli.main(["--window-s", "60"], client=Client()) == 0
    assert calls == [("/health-recorder/incident?window_s=60", None)]
    assert cli.main(["--clear"], client=Client()) == 0
    assert calls[-1] == ("/health-recorder/clear", {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [TimeoutError("SECRET_CANARY"), asyncio.CancelledError()]
)
async def test_exact_tool_wrapper_records_numeric_failure_and_preserves_exception(
    monkeypatch, error
):
    import contextlib
    import os
    import sys
    import time

    from latency import TRACKER

    calls = []

    class Context:
        session = SimpleNamespace(
            _jarvis_health=(None, lambda *args: calls.append(args))
        )

    phone = SimpleNamespace(
        private=lambda: False,
        guest_required=lambda: False,
        private_tool_error=lambda _: None,
    )
    modules = {
        "system": SimpleNamespace(log_action=lambda *args: None),
        "watch_mode": SimpleNamespace(),
        "livekit.agents.llm": SimpleNamespace(ToolError=RuntimeError),
        "action_guard": SimpleNamespace(
            request_scope=lambda _: contextlib.nullcontext(),
            request_id=lambda _: "opaque",
        ),
        "hud_voice_state": SimpleNamespace(
            execution_scope=lambda _: contextlib.nullcontext()
        ),
        "handoff_recovery": SimpleNamespace(remember=lambda *args: None),
        "acks": SimpleNamespace(record=lambda *args: None),
        "tool_usage": SimpleNamespace(record=lambda *args: None),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    namespace = {
        "asyncio": asyncio,
        "time": time,
        "contextlib": contextlib,
        "os": os,
        "phone_session": phone,
        "RunContext": Context,
        "SLOW_TOOL_S": 15,
        "_say_ack": lambda *args: None,
    }
    monkeypatch.setenv("JARVIS_GOODNIGHT_V2", "0")
    wrap = extract_function(SRC / "agent.py", "_wrap_tools_with_timing", namespace)

    async def failing(context):
        raise error

    tool = SimpleNamespace(id="PRIVATE_DYNAMIC_NAME", _func=failing)
    TRACKER.pending = 0
    wrap([tool])
    with pytest.raises(type(error)) as raised:
        await tool._func(Context())
    assert raised.value is error
    expected = "timeout" if isinstance(error, TimeoutError) else "cancelled"
    assert calls[0] == (None, None, "tool_call", expected)
    assert calls[1][0] == "tool_duration_s"
    assert type(calls[1][1]) is float
    assert "SECRET_CANARY" not in repr(calls)
    assert "PRIVATE_DYNAMIC_NAME" not in repr(calls)
    assert TRACKER.pending == 0
