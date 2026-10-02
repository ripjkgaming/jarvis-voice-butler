"""The manual-turn workaround must never alter native Gemini turns."""

import ast
import contextlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import types
from livekit.agents.types import NOT_GIVEN
from livekit.plugins.google.realtime import realtime_api


@pytest.fixture
def patched_session(monkeypatch):
    """Load only the patch, with a fake session and an inert timer queue."""
    scheduled = []

    class Session:
        def __init__(self, *, manual):
            self._manual_activity_detection = manual
            self._in_user_activity = True
            self._opts = SimpleNamespace(model="gemini-3.8-live")
            self._current_generation = None
            self.sent = []
            self.audio_turn_flags = []

        def generate_reply(self, *, instructions=NOT_GIVEN, **kwargs):
            self.audio_turn_flags.append(self._jarvis_audio_turn)
            turns = (
                []
                if instructions is NOT_GIVEN
                else [
                    types.Content(role="model", parts=[types.Part(text=instructions)])
                ]
            )
            return self._send_client_event(
                types.LiveClientContent(turns=turns, turn_complete=True)
            )

        def _send_client_event(self, event):
            self.sent.append(event)
            return "forwarded"

    monkeypatch.setenv("JARVIS_GEMINI_TURN_PATCH", "1")
    monkeypatch.setattr(realtime_api, "RealtimeSession", Session)
    fake_loop = SimpleNamespace(
        call_later=lambda delay, callback: scheduled.append((delay, callback))
    )
    source = Path(__file__).resolve().parents[1] / "src" / "agent.py"
    tree = ast.parse(source.read_text())
    patch = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_patch_gemini_turn_end"
    )
    scope = {
        "os": os,
        "contextlib": contextlib,
        "asyncio": SimpleNamespace(get_running_loop=lambda: fake_loop),
        "_TOOL_NUDGE_S": 1.5,
    }
    exec(compile(ast.Module(body=[patch], type_ignores=[]), str(source), "exec"), scope)
    assert scope["_patch_gemini_turn_end"]()
    return Session, scheduled


def _tool_response():
    return types.LiveClientToolResponse(
        function_responses=[
            types.FunctionResponse(name="synthetic_tool", response={"ok": True})
        ]
    )


@pytest.mark.parametrize("generation_done", [None, True, False])
def test_manual_tool_response_nudges_only_when_no_generation_is_active(
    patched_session, generation_done
):
    session_type, scheduled = patched_session
    session = session_type(manual=True)
    response = _tool_response()

    assert session._send_client_event(response) == "forwarded"
    assert session.sent == [response]
    assert len(scheduled) == 1
    delay, callback = scheduled[0]
    assert delay == 1.5

    # Check at callback time: a natural continuation may have started since
    # the tool result was sent and must not receive another empty turn.
    session._current_generation = (
        None if generation_done is None else SimpleNamespace(_done=generation_done)
    )
    callback()
    if generation_done is False:
        assert session.sent == [response]
    else:
        assert len(session.sent) == 2
        assert isinstance(session.sent[1], types.LiveClientContent)
        assert session.sent[1].turns == []
        assert session.sent[1].turn_complete is True


def test_native_tool_response_never_schedules_manual_nudge(patched_session):
    session_type, scheduled = patched_session
    session = session_type(manual=False)
    response = _tool_response()

    assert session._send_client_event(response) == "forwarded"
    assert session.sent == [response]
    assert scheduled == []


@pytest.mark.parametrize("manual", [True, False])
def test_empty_turn_suppression_requires_manual_activity(patched_session, manual):
    session_type, scheduled = patched_session
    session = session_type(manual=manual)
    # A stale marker must not activate the workaround in native mode.
    session._jarvis_audio_turn = True
    event = types.LiveClientContent(turns=[], turn_complete=True)

    result = session._send_client_event(event)

    assert result == (None if manual else "forwarded")
    assert session.sent == ([] if manual else [event])
    assert scheduled == []


@pytest.mark.parametrize("manual", [True, False])
def test_generate_reply_marks_audio_workaround_only_for_manual_activity(
    patched_session, manual
):
    session_type, scheduled = patched_session
    session = session_type(manual=manual)

    session.generate_reply()

    assert session.audio_turn_flags == [manual]
    assert session._jarvis_audio_turn is False
    assert len(session.sent) == (0 if manual else 1)
    assert scheduled == []


@pytest.mark.parametrize("manual", [True, False])
def test_instructed_reply_is_forwarded_in_both_modes(patched_session, manual):
    session_type, scheduled = patched_session
    session = session_type(manual=manual)

    assert session.generate_reply(instructions="Synthetic greeting.") == "forwarded"

    assert session.audio_turn_flags == [False]
    assert session._jarvis_audio_turn is False
    assert len(session.sent) == 1
    assert session.sent[0].turns[0].parts[0].text == "Synthetic greeting."
    assert scheduled == []
