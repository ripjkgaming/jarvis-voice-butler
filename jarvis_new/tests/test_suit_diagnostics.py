"""Suit UI commands: hermetic state, handler, fast-route and model-tool tests."""

import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from livekit.agents.llm import ToolError

import bridge
import projects
import suit_diagnostics as suit
from system import projects_tools
from system.suit_tools import SuitTools

_shell_probe = suit._shell_running


@pytest.fixture(autouse=True)
def isolated_suit(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(suit, "_state", suit.CommandState())
    monkeypatch.setattr(suit, "_shell_running", lambda: True)
    monkeypatch.setattr(projects, "shell_verb", lambda _verb: True)
    monkeypatch.setattr(
        projects_tools,
        "_bridge_call",
        lambda *_args, **_kwargs: pytest.fail("Unexpected real bridge request"),
    )


def handle(path, body=None, *, authorized=True, method="POST", raw=None):
    """Execute the real HTTP handler without creating a server or socket."""
    handler = object.__new__(bridge._Handler)
    data = raw if raw is not None else json.dumps(body).encode()
    handler.path = path
    handler.token = "test-token"
    handler.headers = {
        "Authorization": "Bearer test-token" if authorized else "Bearer wrong",
        "Content-Length": str(len(data)),
    }
    handler.rfile = io.BytesIO(data)
    replies = []
    handler._send = lambda code, payload: replies.append((code, payload))
    getattr(handler, "do_" + method)()
    assert len(replies) == 1
    return replies[0]


def test_new_process_state_starts_closed_and_is_not_persisted():
    first = suit.CommandState()
    assert first.snapshot() == {
        "op": "close",
        "revision": 0,
        "issued_at": 0,
        "session": first.snapshot()["session"],
    }
    first.set_open(True)
    restarted = suit.CommandState().snapshot()
    assert restarted["op"] == "close" and restarted["revision"] == 0
    assert restarted["session"] != first.snapshot()["session"]


def test_every_explicit_command_is_fresh_including_reopen_after_reload():
    a = suit.set_open(True)
    b = suit.set_open(True)
    c = suit.set_open(False)
    assert [v["op"] for v in (a, b, c)] == ["open", "open", "close"]
    assert [v["revision"] for v in (a, b, c)] == [1, 2, 3]
    assert 0 < a["issued_at"] < b["issued_at"] < c["issued_at"]
    assert a["session"] == b["session"] == c["session"]
    c["op"] = "open"
    assert suit.snapshot()["op"] == "close", "Readers cannot mutate canonical state"


def test_parallel_commands_have_unique_revisions():
    with ThreadPoolExecutor(max_workers=8) as pool:
        states = list(pool.map(suit.set_open, [True, False] * 32))
    assert sorted(s["revision"] for s in states) == list(range(1, 65))
    assert suit.snapshot()["revision"] == 64


@pytest.mark.parametrize(
    ("text", "opened"),
    [
        ("bring up the suit diagnostics", True),
        ("Jarvis, please bring up the suit diagnostics.", True),
        ("Could you show my suit diagnostics please?", True),
        ("open simulated suit diagnostics panel", True),
        ("pull up suit diagnostics", True),
        ("dismiss suit diagnostics", False),
        ("close the suit diagnostics", False),
        ("hide suit diagnostics please", False),
    ],
)
def test_exact_parser_and_route_precede_generic_apps(text, opened):
    assert suit.parse_command(text) is opened
    command = bridge._match_voice_tool(text)
    assert command[:2] == ("set_suit_diagnostics", {"open": opened})


@pytest.mark.parametrize(
    "text",
    [
        "",
        "what are suit diagnostics",
        "do not open suit diagnostics",
        "don't close the suit diagnostics",
        "never bring up the suit diagnostics",
        "open suit diagnostics and open youtube",
        "close suit diagnostics then lock",
        "open suit diagnostics without closing chrome",
        "open system diagnostics",
        "hide suit diagnostics files",
        "show the actual suit diagnostics hardware",
    ],
)
def test_parser_rejects_questions_negation_and_unrelated_compounds(text):
    assert suit.parse_command(text) is None
    if "suit diagnostics" in text:
        assert bridge._match_voice_tool(text) is None


def test_rejected_suit_phrase_cannot_fall_through_to_generic_resolver(monkeypatch):
    monkeypatch.setattr(
        bridge, "run_phone_tool", lambda *_: pytest.fail("Unexpected action")
    )
    code, result = bridge.handle_route(
        {"text": "open suit diagnostics and lock the pc"}
    )
    assert code == 404 and result["error"] == "no-route"


def test_authenticated_post_and_existing_sys_share_one_canonical_state(monkeypatch):
    monkeypatch.setattr(bridge, "_sys_stats", lambda: {"mode": "normal"})
    assert handle("/suit", {"open": True}, authorized=False)[0] == 401
    assert suit.snapshot()["revision"] == 0
    code, result = handle("/suit", {"open": True})
    assert code == 200 and result["ok"] is True
    state = result["suit_diagnostics"]
    assert state["op"] == "open" and state["revision"] == 1
    code, stats = handle("/sys", method="GET")
    assert code == 200 and stats["suit_diagnostics"] == state
    assert stats["mode"] == "normal"
    assert handle("/suit", {"open": False})[1]["suit_diagnostics"]["op"] == "close"


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"open": 1},
        {"open": "true"},
        {"open": None},
        {"open": True, "hardware": True},
    ],
)
def test_post_rejects_ambiguous_or_extra_fields_without_mutation(body):
    code, result = handle("/suit", body)
    assert code == 400 and result["ok"] is False
    assert suit.snapshot()["revision"] == 0


@pytest.mark.parametrize("raw", [b"{broken", b"x" * 257])
def test_post_rejects_malformed_and_oversized_json(raw):
    assert handle("/suit", raw=raw)[0] == 400
    assert suit.snapshot()["revision"] == 0


def test_fast_executor_posts_to_bridge_instead_of_mutating_its_imported_state(
    monkeypatch,
):
    calls = []
    remote = {"op": "open", "revision": 9, "issued_at": 123, "session": "remote"}

    def post(*args):
        calls.append(args)
        return {"ok": True, "suit_diagnostics": remote}

    monkeypatch.setattr(projects_tools, "_bridge_call", post)
    result = bridge.run_phone_tool("set_suit_diagnostics", {"open": True})
    assert result["ok"] is True and result["suit_diagnostics"] == remote
    assert calls == [("POST", "/suit", {"open": True})]
    assert suit.snapshot()["revision"] == 0


def test_desktop_fast_command_uses_canonical_bridge(monkeypatch):
    import agent

    monkeypatch.setattr(agent, "_desktop_fastpath_enabled", lambda: True)
    monkeypatch.setattr(agent, "_pipeline_name", lambda: "local")
    calls = []

    def post(*args):
        calls.append(args)
        return {
            "ok": True,
            "suit_diagnostics": {
                "op": "close",
                "revision": 4,
                "issued_at": 123,
                "session": "remote",
            },
        }

    monkeypatch.setattr(projects_tools, "_bridge_call", post)
    result = agent.desktop_fast_command("dismiss suit diagnostics")
    assert result["action"] == {"tool": "set_suit_diagnostics", "ok": True}
    assert calls == [("POST", "/suit", {"open": False})]
    assert suit.snapshot()["revision"] == 0


@pytest.mark.asyncio
async def test_model_tool_routes_to_bridge_and_describes_a_simulation(monkeypatch):
    calls = []
    monkeypatch.setattr(
        projects_tools,
        "_bridge_call",
        lambda *args: (
            calls.append(args) or {"ok": True, "suit_diagnostics": {"op": "open"}}
        ),
    )
    tools = SuitTools()
    result = await tools.set_suit_diagnostics(None, on=True)
    assert "simulat" in result["say"].lower()
    assert calls == [("POST", "/suit", {"open": True})]
    assert suit.snapshot()["revision"] == 0
    assert [tool.id for tool in tools.tools] == ["set_suit_diagnostics"]


@pytest.mark.asyncio
async def test_model_and_fast_paths_refuse_when_nonlocal(monkeypatch):
    monkeypatch.delenv("JARVIS_LOCAL")
    with pytest.raises(ToolError, match="own machine"):
        await SuitTools().set_suit_diagnostics(None, on=True)
    assert bridge.run_phone_tool("set_suit_diagnostics", {"open": True})["ok"] is False


@pytest.mark.asyncio
async def test_unavailable_bridge_never_claims_success(monkeypatch):
    monkeypatch.setattr(projects_tools, "_bridge_call", lambda *_: None)
    with pytest.raises(ToolError, match="respond"):
        await SuitTools().set_suit_diagnostics(None, on=True)
    assert bridge.run_phone_tool("set_suit_diagnostics", {"open": False})["ok"] is False
    assert suit.snapshot()["revision"] == 0


def test_main_assistant_registers_suit_tool_once(monkeypatch):
    import agent

    monkeypatch.setattr(agent, "_default_agent_llm", lambda: object())
    monkeypatch.setattr(agent.tool_usage, "plan_cold", lambda *_a, **_k: set())
    assistant = agent.Assistant()
    assert [tool.id for tool in assistant.tools].count("set_suit_diagnostics") == 1


def test_open_shows_native_overlay_but_close_never_does(monkeypatch):
    calls = []
    monkeypatch.setattr(projects, "shell_verb", lambda verb: calls.append(verb) or True)
    assert handle("/suit", {"open": True})[1]["suit_diagnostics"]["op"] == "open"
    assert handle("/suit", {"open": True})[1]["suit_diagnostics"]["revision"] == 2
    assert handle("/suit", {"open": False})[1]["suit_diagnostics"]["op"] == "close"
    assert calls == ["suitshow", "suitshow"]


@pytest.mark.parametrize("raises", [False, True])
def test_failed_native_show_does_not_publish_open(monkeypatch, raises):
    before = suit.snapshot()

    def failed(_verb):
        if raises:
            raise OSError("shell unavailable")
        return False

    monkeypatch.setattr(projects, "shell_verb", failed)
    code, result = handle("/suit", {"open": True})
    assert code == 503 and result["ok"] is False
    assert "respond" in result["error"]
    assert suit.snapshot() == before
    assert handle("/suit", {"open": False})[0] == 200


def test_close_is_immediate_and_wins_while_native_open_is_pending(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def showing(_verb):
        started.set()
        assert release.wait(3), "Test did not release the fake native show"
        return True

    monkeypatch.setattr(projects, "shell_verb", showing)
    with ThreadPoolExecutor(max_workers=2) as pool:
        opening = pool.submit(handle, "/suit", {"open": True})
        try:
            assert started.wait(1)
            assert suit.snapshot()["op"] == "close"
            close = pool.submit(handle, "/suit", {"open": False}).result(timeout=1)
            closed_state = close[1]["suit_diagnostics"]
        finally:
            release.set()
        code, result = opening.result(timeout=1)
    assert code == 200 and result["ok"] is True and result["superseded"] is True
    assert result["suit_diagnostics"] == closed_state == suit.snapshot()
    assert "superseded" in result["say"].lower()


def bridge_via_handler(monkeypatch):
    calls = []

    def call(method, path, body):
        calls.append(body)
        code, result = handle(path, body, method=method)
        return result if code == 200 else None

    monkeypatch.setattr(projects_tools, "_bridge_call", call)
    return calls


@pytest.mark.asyncio
async def test_delayed_model_echo_cannot_reopen_after_user_closes(monkeypatch):
    calls = bridge_via_handler(monkeypatch)
    assert bridge.handle_route({"text": "open suit diagnostics"})[1]["action"]["ok"]
    closed_state = handle("/suit", {"open": False})[1]["suit_diagnostics"]
    reply = await SuitTools().set_suit_diagnostics(object(), on=True)
    assert "note" in reply
    assert suit.snapshot() == closed_state
    assert calls == [{"open": True}]


@pytest.mark.asyncio
async def test_same_source_reopens_are_fresh_and_opposite_direction_is_not_deduped(
    monkeypatch,
):
    calls = bridge_via_handler(monkeypatch)
    tools = SuitTools()
    await tools.set_suit_diagnostics(object(), on=True)
    await tools.set_suit_diagnostics(object(), on=True)
    assert suit.snapshot()["revision"] == 2
    code, result = bridge.handle_route({"text": "hide suit diagnostics"})
    assert code == 200 and result["action"]["ok"] is True
    assert suit.snapshot()["op"] == "close"
    assert calls == [{"open": True}, {"open": True}, {"open": False}]


@pytest.mark.asyncio
async def test_failed_model_open_can_retry_successfully(monkeypatch):
    bridge_via_handler(monkeypatch)
    monkeypatch.setattr(projects, "shell_verb", lambda _verb: False)
    with pytest.raises(ToolError, match="respond"):
        await SuitTools().set_suit_diagnostics(object(), on=True)
    monkeypatch.setattr(projects, "shell_verb", lambda _verb: True)
    code, result = bridge.handle_route({"text": "show suit diagnostics"})
    assert code == 200 and result["action"]["ok"] is True
    assert suit.snapshot()["op"] == "open"


@pytest.mark.asyncio
async def test_superseded_fast_open_consumes_delayed_model_echo(monkeypatch):
    calls = bridge_via_handler(monkeypatch)

    def showing(_verb):
        handle("/suit", {"open": False})
        return True

    monkeypatch.setattr(projects, "shell_verb", showing)
    code, result = bridge.handle_route({"text": "show suit diagnostics"})
    assert code == 200 and "superseded" in result["reply"].lower()
    closed_state = suit.snapshot()
    reply = await SuitTools().set_suit_diagnostics(object(), on=True)
    assert "note" in reply and "superseded" in reply["say"].lower()
    assert suit.snapshot() == closed_state
    assert calls == [{"open": True}]


def test_missing_primary_shell_fails_without_launching_or_publishing(monkeypatch):
    monkeypatch.setattr(suit, "_shell_running", lambda: False)
    monkeypatch.setattr(
        projects, "shell_verb", lambda *_: pytest.fail("Must not launch a shell")
    )
    before = suit.snapshot()
    code, result = handle("/suit", {"open": True})
    assert code == 503 and result["ok"] is False
    assert "running" in result["error"]
    assert suit.snapshot() == before
    assert handle("/suit", {"open": False})[0] == 200


@pytest.mark.parametrize(
    "reply, expected", [("true", True), ("false", False), ("", False)]
)
def test_primary_shell_probe_checks_actual_single_instance_owner(
    monkeypatch, reply, expected
):
    import active_window

    calls = []
    monkeypatch.setattr(
        active_window,
        "_qdbus",
        lambda argv, timeout: calls.append((argv, timeout)) or reply,
    )
    # The autouse fixture replaces only the public probe to keep every other
    # test off the session bus; retain the original for these isolated calls.
    assert _shell_probe() is expected
    config = json.loads(
        (
            Path(__file__).resolve().parent.parent / "shell/src-tauri/tauri.conf.json"
        ).read_text()
    )
    assert calls == [
        (
            [
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus.NameHasOwner",
                config["identifier"] + ".SingleInstance",
            ],
            1.0,
        )
    ]
