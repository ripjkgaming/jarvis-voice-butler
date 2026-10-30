import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jarvis_rt.chat_context import (
    AgentHandoff,
    ChatContext,
    ChatMessage,
    FunctionCall,
    FunctionCallOutput,
)


def _msg(role, text, created_at=None):
    kwargs = {} if created_at is None else {"created_at": created_at}
    return ChatMessage(role=role, content=[text], **kwargs)


def _sample(t0=1000.0):
    """system, user, assistant, function_call, function_call_output, user."""
    ctx = ChatContext.empty()
    ctx.add_message(role="system", content="be brief", created_at=t0)
    ctx.add_message(role="user", content="weather in Paris?", created_at=t0 + 1)
    ctx.add_message(role="assistant", content="checking", created_at=t0 + 2)
    ctx.insert(
        FunctionCall(
            call_id="c1",
            name="get_weather",
            arguments='{"city": "Paris"}',
            created_at=t0 + 3,
        )
    )
    ctx.insert(
        FunctionCallOutput(
            call_id="c1",
            name="get_weather",
            output="18C",
            is_error=False,
            created_at=t0 + 4,
        )
    )
    ctx.add_message(role="user", content="thanks", created_at=t0 + 5)
    return ctx


def _signature(ctx):
    out = []
    for item in ctx.items:
        out.append(
            (
                item.type,
                getattr(item, "role", None),
                getattr(item, "text_content", None),
                getattr(item, "name", None),
                getattr(item, "call_id", None),
            )
        )
    return out


def test_add_message_appends_and_insert_orders_by_created_at():
    ctx = ChatContext.empty()
    ctx.add_message(role="user", content="a", created_at=10.0)
    ctx.add_message(role="assistant", content="c", created_at=30.0)
    ctx.add_message(role="user", content="b", created_at=20.0)  # inserted in the middle
    assert [m.text_content for m in ctx.messages()] == ["a", "b", "c"]

    ctx.insert([_msg("user", "z", created_at=5.0), _msg("user", "y", created_at=25.0)])
    assert [m.text_content for m in ctx.messages()] == ["z", "a", "b", "y", "c"]


def test_copy_exclusions_leave_original_untouched():
    ctx = _sample()
    ctx.insert(AgentHandoff(new_agent_id="b", created_at=1010.0))
    before = len(ctx.items)

    no_fn = ctx.copy(exclude_function_call=True)
    assert all(
        i.type not in ("function_call", "function_call_output") for i in no_fn.items
    )

    no_sys = ctx.copy(exclude_instructions=True)
    assert all(not (i.type == "message" and i.role == "system") for i in no_sys.items)

    no_handoff = ctx.copy(exclude_handoff=True)
    assert all(i.type != "agent_handoff" for i in no_handoff.items)

    assert len(ctx.items) == before


def test_truncate_keeps_last_n_and_preserves_system_message():
    ctx = _sample()
    ctx.truncate(max_items=3)
    # last 3 are: function_call, function_call_output, user "thanks". Leading
    # function call/output items are dropped (as in livekit), and the system
    # message is re-added at the front.
    assert [i.type for i in ctx.items] == ["message", "message"]
    assert ctx.items[0].role == "system"
    assert ctx.items[-1].text_content == "thanks"


def test_truncate_is_noop_when_within_limit():
    ctx = _sample()
    ids = [i.id for i in ctx.items]
    ctx.truncate(max_items=100)
    assert [i.id for i in ctx.items] == ids


def test_to_dict_from_dict_roundtrip():
    ctx = _sample()
    data = ctx.to_dict()
    restored = ChatContext.from_dict(data)

    assert [i.id for i in restored.items] == [i.id for i in ctx.items]
    assert _signature(restored) == _signature(ctx)
    fc = restored.items[3]
    assert isinstance(fc, FunctionCall) and fc.arguments == '{"city": "Paris"}'
    out = restored.items[4]
    assert isinstance(out, FunctionCallOutput) and out.output == "18C"
    assert ctx.is_equivalent(restored)


def test_get_by_id_and_index_by_id():
    ctx = _sample()
    target = ctx.items[2]
    assert ctx.get_by_id(target.id) is target
    assert ctx.index_by_id(target.id) == 2
    assert ctx.get_by_id("missing") is None
    assert ctx.index_by_id("missing") is None


def test_to_gemini_contents_mapping():
    genai_types = pytest.importorskip("google.genai.types")
    ctx = _sample()

    contents = ctx.to_gemini_contents()

    # system message is excluded; the remaining five items map one-to-one
    assert len(contents) == 5
    assert all(isinstance(c, genai_types.Content) for c in contents)

    user_text, model_text, call, response, user_last = contents
    assert user_text.role == "user"
    assert user_text.parts[0].text == "weather in Paris?"

    assert model_text.role == "model"
    assert model_text.parts[0].text == "checking"

    assert call.role == "model"
    assert call.parts[0].function_call.name == "get_weather"
    assert call.parts[0].function_call.args == {"city": "Paris"}

    assert response.role == "user"
    assert response.parts[0].function_response.name == "get_weather"
    assert response.parts[0].function_response.response == {"output": "18C"}

    assert user_last.role == "user"
    assert user_last.parts[0].text == "thanks"


def test_to_gemini_contents_error_output_and_bad_arguments():
    genai_types = pytest.importorskip("google.genai.types")
    ctx = ChatContext.empty()
    ctx.insert(
        FunctionCall(call_id="x", name="f", arguments="not json", created_at=1.0)
    )
    ctx.insert(
        FunctionCallOutput(
            call_id="x", name="f", output="boom", is_error=True, created_at=2.0
        )
    )
    call, out = ctx.to_gemini_contents()
    assert isinstance(call.parts[0], genai_types.Part)
    assert call.parts[0].function_call.args == {}
    assert out.parts[0].function_response.response == {"error": "boom"}


def test_chat_context_signature_matches_recorded_livekit_behavior():
    # Literal items recorded from livekit-agents' ChatContext for the same
    # sequence (the livekit comparison itself was removed with the dependency).
    t0 = 2000.0
    ours = _sample(t0)
    assert _signature(ours) == [
        ("message", "system", "be brief", None, None),
        ("message", "user", "weather in Paris?", None, None),
        ("message", "assistant", "checking", None, None),
        ("function_call", None, None, "get_weather", "c1"),
        ("function_call_output", None, None, "get_weather", "c1"),
        ("message", "user", "thanks", None, None),
    ]
