"""The voice fast path (src/intent/fast_path.py).

Grammar for instant commands, compound/long rejection, resolver gating,
and the once() dedupe that stops the fast path and Gemini both applying
the same request.
"""

from __future__ import annotations

import types

import pytest

from intent import fast_path


def _no_resolver(text):  # the grammar should not need Needle
    raise AssertionError("resolver should not be consulted")


def _resolve(should_act, action, params=None):
    """A stand-in intent resolver returning a fixed verdict."""
    return lambda text: types.SimpleNamespace(
        should_act=should_act, action=action, params=params or {}
    )


@pytest.mark.parametrize(
    "text,tool,action",
    [
        ("turn the volume down", "set_volume", "down"),
        ("turn down the volume", "set_volume", "down"),
        ("turn down the volume a bit", "set_volume", "down"),
        ("Hey Jarvis, can you turn the volume down please", "set_volume", "down"),
        ("volume up", "set_volume", "up"),
        ("turn it up", "set_volume", "up"),
        ("make it quieter", "set_volume", "down"),
        ("louder", "set_volume", "up"),
        ("mute", "set_volume", "mute"),
        ("unmute the sound", "set_volume", "unmute"),
        ("pause", "media_control", "pause"),
        ("pause the music", "media_control", "pause"),
        ("stop the music", "media_control", "pause"),
        ("resume", "media_control", "play"),
        ("next song", "media_control", "next"),
        ("skip this track", "media_control", "next"),
        ("previous track", "media_control", "previous"),
        ("go back to the previous song", "media_control", "previous"),
        ("what time is it", "tell_time", None),
    ],
)
def test_grammar_hits(text, tool, action) -> None:
    got = fast_path.match(text, resolve=_no_resolver)
    assert got is not None, text
    assert got[0] == tool and got[2] == "grammar"
    if action is not None:
        assert got[1]["action"] == action


def test_volume_level_is_parsed() -> None:
    got = fast_path.match("set the volume to 30", resolve=_no_resolver)
    assert got[:2] == ("set_volume", {"action": "set", "level": 30})


@pytest.mark.parametrize(
    "text",
    [
        "turn the volume down and open spotify",
        "pause the music then lock the screen",
        "what is the capital of france",
        "play despacito",
        "can you write me an essay about the roman empire please",
        "tell me a joke",
        "",
    ],
)
def test_passthrough_to_gemini(text) -> None:
    # Resolver returns "not confident" so nothing here should be caught.
    assert fast_path.match(text, resolve=_resolve(False, "chat")) is None


def test_disabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_FAST_PATH", "0")
    assert fast_path.match("turn the volume down") is None


def test_resolver_gate_needs_confidence_and_allowlist() -> None:
    # Confident but not an instant action -> passthrough.
    assert fast_path.match("email mum", resolve=_resolve(True, "send_email")) is None
    # Confident instant action with complete params -> caught via needle.
    got = fast_path.match(
        "silence", resolve=_resolve(True, "media_control", {"action": "pause"})
    )
    assert got == ("media_control", {"action": "pause"}, "needle")


def test_resolver_incomplete_params_passthrough() -> None:
    # "set" with no level is incomplete -> don't guess, hand to Gemini.
    assert (
        fast_path.match(
            "volume", resolve=_resolve(True, "set_volume", {"action": "set"})
        )
        is None
    )


def test_resolver_exception_is_safe() -> None:
    assert fast_path.match("mmm hmm", resolve=_no_resolver.__call__) is None


# --- once() dedupe ---


@pytest.mark.asyncio
async def test_once_runs_the_action() -> None:
    calls = []

    async def run():
        calls.append(1)
        return True, "Volume 40 percent."

    ok, say, dup = await fast_path.once("set_volume", {"action": "down"}, "fast", run)
    assert ok and say == "Volume 40 percent." and dup is False and calls == [1]


@pytest.mark.asyncio
async def test_once_second_source_reuses_first_result() -> None:
    fast_path._RECENT.clear()

    async def run_fast():
        return True, "Volume 40 percent."

    async def run_llm():  # Gemini also calls set_volume for the same turn
        raise AssertionError("should not run twice")

    _ok1, _say1, dup1 = await fast_path.once(
        "set_volume", {"action": "down"}, "fast", run_fast
    )
    ok2, say2, dup2 = await fast_path.once(
        "set_volume", {"action": "down"}, "llm", run_llm
    )
    assert dup1 is False and dup2 is True
    assert (ok2, say2) == (True, "Volume 40 percent.")


@pytest.mark.asyncio
async def test_once_same_source_repeats_run() -> None:
    fast_path._RECENT.clear()
    n = []

    async def run():
        n.append(1)
        return True, "ok"

    await fast_path.once("media_control", {"action": "next"}, "fast", run)
    _, _, dup = await fast_path.once("media_control", {"action": "next"}, "fast", run)
    assert dup is False and n == [1, 1]


@pytest.mark.asyncio
async def test_execute_speaks_tool_say() -> None:
    class FakeTools:
        async def tell_time(self, ctx):
            assert ctx is None
            return {"say": "It is noon."}

    ok, say = await fast_path.execute(FakeTools(), "tell_time", {})
    assert ok and say == "It is noon."


@pytest.mark.asyncio
async def test_execute_reports_tool_error() -> None:
    class FakeTools:
        async def set_volume(self, ctx, action, level=50):
            raise RuntimeError("The volume didn't move, Sir.")

    ok, say = await fast_path.execute(FakeTools(), "set_volume", {"action": "down"})
    assert ok is False and "didn't move" in say
