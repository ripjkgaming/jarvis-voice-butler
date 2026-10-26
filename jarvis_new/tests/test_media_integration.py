"""Real staged entry points with fake selection/process/speech effects."""

import asyncio

import pytest

import action_guard
import bridge
import media_selection
from action_outcome import parse_outcome
from system.core import SystemTools


@pytest.mark.parametrize(
    "query",
    [
        "live fireworks",
        "live spectacular fireworks",
        "live waterfront fireworks",
        "live fireworks footage over 15 minutes",
    ],
)
def test_bridge_uncertain_location_never_launches(monkeypatch, query):
    monkeypatch.setattr(media_selection.shutil, "which", lambda _: None)
    result = bridge._play_media(query)
    assert result["selection"]["reason"] == "location_required"
    assert "Which city" in result["say"]
    assert parse_outcome(result).dispatch.value == "not_started"


def test_bridge_brave_only_truthful_receipt(monkeypatch):
    monkeypatch.setattr(
        media_selection,
        "resolve",
        lambda *a, **k: media_selection.Selection(
            "selected", "metadata_match", "aaaaaaaaaaa", 2
        ),
    )
    monkeypatch.setattr(bridge, "_which", lambda _: "/fixture/brave")
    launches = []
    monkeypatch.setattr(bridge, "_spawn_desktop", lambda argv: launches.append(argv))
    result = bridge._play_media("F1 British Grand Prix")
    assert launches == [
        [
            "/fixture/brave",
            "--new-window",
            "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        ]
    ]
    assert parse_outcome(result).state.value == "launch_requested"
    assert "not verified" in result["say"] and "Playing" not in result["say"]


def test_bridge_never_xdg_fallback(monkeypatch):
    monkeypatch.setattr(bridge, "_which", lambda _: None)
    assert parse_outcome(bridge._play_media("")).dispatch.value == "not_started"


@pytest.mark.parametrize("logging_failure", [False, True])
def test_sdk_uses_same_policy_and_receipt(monkeypatch, logging_failure):
    monkeypatch.setenv("JARVIS_LOCAL", "1")

    async def resolve(*args, **kwargs):
        return media_selection.Selection("selected", "metadata_match", "bbbbbbbbbbb", 2)

    monkeypatch.setattr(media_selection, "resolve_async", resolve)
    monkeypatch.setattr(media_selection.shutil, "which", lambda _: "/fixture/brave")
    launches = []

    async def spawn(*argv, **kwargs):
        launches.append(argv)
        return type("Process", (), {"pid": 12})()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    if logging_failure:
        from system import core

        def fail_log(*args):
            raise OSError("fixture logging failure after launch")

        monkeypatch.setattr(core, "log_action", fail_log)
    tools = SystemTools()
    result = asyncio.run(tools.play_media(None, "F1 British Grand Prix", show=True))
    assert launches == [
        (
            "/fixture/brave",
            "--new-window",
            "https://www.youtube.com/watch?v=bbbbbbbbbbb",
        )
    ]
    assert parse_outcome(result).state.value == "launch_requested"


def test_sdk_uncertain_query_blocks_before_launch(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    result = asyncio.run(SystemTools().play_media(None, "live fireworks", show=True))
    assert parse_outcome(result).state.value == "blocked"
    assert "Which city" in result["say"]


def test_new_constraints_are_not_duplicate():
    assert action_guard.claim("play_media", {"query": "live F1"}, "route") is None
    assert (
        action_guard.claim(
            "play_media", {"query": "live F1 British Grand Prix"}, "model"
        )
        is None
    )


def test_legacy_play_claim_cannot_report_playback():
    action_guard.claim("play_media", {"query": "jazz"}, "route")
    action_guard.settle("play_media", {"query": "jazz"}, "route", True, "Playing jazz")
    duplicate = action_guard.claim("play_media", {"query": "jazz"}, "model")
    result = action_guard.duplicate_reply(duplicate, "Playing")
    assert "not verified" in result["say"] and "Playing" not in result["say"]


def test_route_and_talk_keep_specific_clarification(monkeypatch):
    monkeypatch.setattr(bridge, "_speak_quietly", lambda _: ("", 0))
    cmd = ("play_media", {"query": "live fireworks"}, None)
    code, result = bridge._voice_tool_reply(cmd, "play live fireworks", False)
    assert code == 200 and "Which city" in result["reply"]
    code, result = bridge.handle_route({"text": "play live fireworks"})
    assert code == 200 and "Which city" in result["reply"]


def test_background_thread_failure_preserves_launch_receipt(monkeypatch):
    from system import window_ctl

    monkeypatch.setattr(bridge, "_which", lambda _: "/fixture/brave")
    monkeypatch.setattr(window_ctl, "brave_window_ids", lambda: set())
    monkeypatch.setattr(bridge, "_spawn_desktop", lambda _: None)

    class BrokenThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("fixture-only bookkeeping failure")

    monkeypatch.setattr(bridge, "Thread", BrokenThread)
    assert parse_outcome(bridge._play_media("")).state.value == "launch_requested"
