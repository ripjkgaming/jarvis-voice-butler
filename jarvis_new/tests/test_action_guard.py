"""One action per spoken request: the fast path and Gemini never both act.

Regression: "play some music" opened two Brave windows on the realtime
pipeline (desktop fast path via the bridge route, then Gemini Live's own
play_media call for the same audio).
"""

import asyncio

import pytest

import action_guard
import bridge


def test_second_source_is_a_duplicate() -> None:
    assert action_guard.claim("play_media", {"query": ""}, "route", now=0.0) is None
    action_guard.settle("play_media", {"query": ""}, "route", True, "Playing, Sir.")
    dup = action_guard.claim("play_media", {"query": ""}, "model", now=1.0)
    assert dup is not None and dup["say"] == "Playing, Sir."


def test_in_flight_search_outlives_echo_window() -> None:
    assert action_guard.claim("play_media", {"query": "lofi"}, "model", now=0) is None
    assert action_guard.claim("play_media", {"query": "lofi"}, "route", now=10)


def test_finished_slow_action_retains_its_echo_window() -> None:
    assert action_guard.claim("play_media", {"query": "lofi"}, "model", now=0) is None
    action_guard.settle("play_media", {"query": "lofi"}, "model", True, now=12)
    assert action_guard.claim("play_media", {"query": "lofi"}, "route", now=13)


def test_pending_duplicate_never_claims_completion() -> None:
    action_guard.claim("play_media", {}, "route")
    duplicate = action_guard.claim("play_media", {}, "model")
    reply = action_guard.duplicate_reply(duplicate, "Playing it now, Sir.")
    assert "in progress" in reply["say"].lower()
    assert "already done" not in reply["note"].lower()


def test_burst_does_not_evict_a_running_action():
    action_guard.claim("play_media", {"query": "lofi"}, "route", now=0)
    for index in range(260):
        action_guard.claim("open_app", {"app": f"app-{index}"}, "route", now=1)
    assert action_guard.claim("play_media", {"query": "lofi"}, "model", now=2)


def test_abandoned_pending_claim_eventually_expires():
    action_guard.claim("play_media", {}, "route", now=0)
    assert (
        action_guard.claim("play_media", {}, "model", now=action_guard.IN_FLIGHT_S + 1)
        is None
    )


def test_non_latin_play_requests_are_distinct() -> None:
    action_guard.claim("play_media", {"query": "周杰倫"}, "route", now=0)
    assert action_guard.claim("play_media", {"query": "林俊傑"}, "model", now=1) is None


def test_url_paths_remain_case_sensitive() -> None:
    action_guard.claim("open_app", {"url": "https://example.com/A"}, "route", now=0)
    assert (
        action_guard.claim("open_app", {"url": "https://example.com/a"}, "model", now=1)
        is None
    )


def test_generic_queries_match_each_other() -> None:
    # Route gets "" for "play some music"; Gemini may pass "some music".
    assert action_guard.claim("play_media", {"query": ""}, "route", now=0.0) is None
    assert (
        action_guard.claim("play_media", {"query": "some music"}, "model", now=1.0)
        is not None
    )


def test_overlapping_query_is_the_same_request() -> None:
    assert action_guard.claim("play_media", {"query": "lofi"}, "route", now=0.0) is None
    assert (
        action_guard.claim("play_media", {"query": "lofi hip hop"}, "model", now=1.0)
        is not None
    )


def test_different_play_request_still_plays() -> None:
    # "play some music" then "actually, play lofi" (a phrasing only Gemini
    # catches) is a new request, not an echo.
    assert action_guard.claim("play_media", {"query": ""}, "route", now=0.0) is None
    assert action_guard.claim("play_media", {"query": "lofi"}, "model", now=2.0) is None


def test_same_source_repeats_run() -> None:
    assert action_guard.claim("volume_up", {}, "route", now=0.0) is None
    assert action_guard.claim("volume_up", {}, "route", now=1.0) is None


def test_each_other_source_is_absorbed_once() -> None:
    assert action_guard.claim("volume_down", {}, "route", now=0.0) is None
    assert action_guard.claim("set_volume", {"action": "down"}, "model", now=0.5)
    assert action_guard.claim("set_volume", {"action": "down"}, "fast", now=0.6)
    # A second model call for the same family is a new request.
    assert (
        action_guard.claim("set_volume", {"action": "down"}, "model", now=0.7) is None
    )


def test_window_expires() -> None:
    assert action_guard.claim("open_app", {"app": "youtube"}, "route", now=0.0) is None
    late = action_guard.WINDOW_S + 0.1
    assert action_guard.claim("open_app", {"app": "brave"}, "model", now=late) is None


def test_failed_first_try_may_be_retried() -> None:
    assert action_guard.claim("open_app", {"app": "x"}, "route", now=0.0) is None
    action_guard.settle("open_app", {"app": "x"}, "route", False)
    assert action_guard.claim("open_app", {"app": "x"}, "model", now=1.0) is None


def test_unguarded_tools_always_run() -> None:
    assert action_guard.claim("tell_time", {}, "route", now=0.0) is None
    assert action_guard.claim("tell_time", {}, "model", now=0.1) is None
    assert action_guard.family("set_volume", {"action": "status"}) is None
    assert action_guard.family("media_control", {"action": "seek"}) is None


@pytest.mark.parametrize("is_open", [True, False])
def test_suit_diagnostics_absorbs_matching_cross_source_echo(is_open):
    args = {"open": is_open}
    assert action_guard.claim("set_suit_diagnostics", args, "route") is None
    action_guard.settle("set_suit_diagnostics", args, "route", True, "Updated.")
    duplicate = action_guard.claim("set_suit_diagnostics", args, "model")
    assert duplicate is not None and duplicate["ok"] is True
    # Opening and closing are different effects, even across executors.
    assert (
        action_guard.claim("set_suit_diagnostics", {"open": not is_open}, "model")
        is None
    )


def test_suit_close_keeps_guard_for_a_delayed_open_echo():
    assert action_guard.claim("set_suit_diagnostics", {"open": True}, "route") is None
    action_guard.settle("set_suit_diagnostics", {"open": True}, "route", True)
    assert action_guard.claim("set_suit_diagnostics", {"open": False}, "ui") is None
    action_guard.settle("set_suit_diagnostics", {"open": False}, "ui", True)
    duplicate = action_guard.claim("set_suit_diagnostics", {"open": True}, "model")
    assert duplicate is not None and duplicate["ok"] is True


def test_suit_same_source_can_reopen_and_each_request_absorbs_its_echo():
    for is_open in (True, False, True):
        assert (
            action_guard.claim("set_suit_diagnostics", {"open": is_open}, "route")
            is None
        )
        action_guard.settle("set_suit_diagnostics", {"open": is_open}, "route", True)
    for _ in range(2):
        assert action_guard.claim("set_suit_diagnostics", {"open": True}, "model")


def test_play_some_music_opens_one_window(monkeypatch) -> None:
    """End to end: route first, then Gemini's play_media -> one Brave."""
    from system import core

    launches: list[list[str]] = []

    class _Popen:
        def __init__(self, argv, **_kw):
            launches.append(list(argv))

    monkeypatch.setattr(bridge.subprocess, "Popen", _Popen)
    monkeypatch.setattr(bridge, "_which", lambda name: f"/usr/bin/{name}")

    code, payload = bridge.handle_route({"text": "play some music"})
    assert code == 200 and payload["action"]["ok"] is True
    assert "duplicate" not in payload
    assert len(launches) == 1

    async def _exec(*argv, **_kw):
        launches.append(list(argv))
        raise AssertionError("Gemini's call must not open a second window")

    monkeypatch.setattr(core, "require_local", lambda: None)
    monkeypatch.setattr(core.asyncio, "create_subprocess_exec", _exec)
    tools = core.SystemTools()
    out = asyncio.run(
        core.SystemTools.play_media(tools, object(), "", show=True)  # type: ignore[arg-type]
    )
    assert "already done" in out["note"].lower()
    assert len(launches) == 1


def test_route_stays_quiet_when_gemini_went_first(monkeypatch) -> None:
    launches: list[list[str]] = []

    class _Popen:
        def __init__(self, argv, **_kw):
            launches.append(list(argv))

    monkeypatch.setattr(bridge.subprocess, "Popen", _Popen)
    monkeypatch.setattr(bridge, "_which", lambda name: f"/usr/bin/{name}")
    assert action_guard.claim("play_media", {"query": "music"}, "model") is None
    code, payload = bridge.handle_route({"text": "play some music"})
    assert code == 200 and payload["duplicate"] is True
    assert launches == []


@pytest.mark.parametrize("text", ["open youtube", "turn the volume up"])
def test_route_claims_window_and_volume_actions(text, monkeypatch) -> None:
    monkeypatch.setattr(
        bridge, "_run_voice_action", lambda tool, args: (200, {"ok": True})
    )
    code, payload = bridge.handle_route({"text": text})
    assert code == 200 and payload["action"]["ok"] is True
    tool = payload["action"]["tool"]
    assert (
        action_guard.claim(
            tool, {"app": "youtube"} if tool == "open_app" else {}, "model"
        )
        is not None
    )


@pytest.mark.parametrize(
    "first,second",
    [
        (("open_app", {"app": "youtube"}), ("open_app", {"app": "calculator"})),
        (("close_app", {"name": "youtube"}), ("close_app", {"name": "calculator"})),
        (("volume_up", {}), ("set_volume", {"action": "down"})),
        (("volume_set", {"level": 30}), ("set_volume", {"action": "set", "level": 60})),
        (("media_next", {}), ("media_control", {"action": "previous"})),
        (
            ("play_media", {"query": "beatles yesterday"}),
            ("play_media", {"query": "beatles help"}),
        ),
    ],
)
def test_different_actions_are_not_suppressed(first, second):
    assert action_guard.claim(*first, "route", now=0.0) is None
    assert action_guard.claim(*second, "model", now=1.0) is None


def test_youtube_site_and_browser_url_match():
    assert action_guard.claim("open_app", {"app": "youtube"}, "route", now=0.0) is None
    assert (
        action_guard.claim(
            "open_app",
            {"app": "brave", "url": "https://www.youtube.com/"},
            "model",
            now=1.0,
        )
        is not None
    )


def test_two_fast_requests_each_absorb_their_model_echo():
    for now in (0.0, 0.1):
        assert action_guard.claim("volume_up", {}, "route", now=now) is None
    for now in (0.2, 0.3):
        assert (
            action_guard.claim("set_volume", {"action": "up"}, "model", now=now)
            is not None
        )


def test_interleaved_actions_keep_their_echoes():
    assert action_guard.claim("open_app", {"app": "youtube"}, "route", now=0.0) is None
    assert (
        action_guard.claim("open_app", {"app": "calculator"}, "route", now=0.1) is None
    )
    for app in ("youtube", "calculator"):
        assert (
            action_guard.claim("open_app", {"app": app}, "model", now=0.2) is not None
        )


def test_failed_model_launch_can_be_retried_by_route(monkeypatch):
    from livekit.agents.llm import ToolError

    from system import core, launcher

    monkeypatch.setattr(core, "require_local", lambda: None)
    monkeypatch.setattr(core, "_resolve_app", lambda app: "/usr/bin/example")
    monkeypatch.setattr(launcher, "priority_app", lambda app: None)

    async def run_cmd(*args, **kwargs):
        return 0, "", ""

    async def fail(*args, **kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(core, "run_cmd", run_cmd)
    monkeypatch.setattr(core.asyncio, "create_subprocess_exec", fail)
    with pytest.raises(ToolError):
        asyncio.run(core.SystemTools.open_app(core.SystemTools(), object(), "example"))
    assert action_guard.claim("open_app", {"app": "example"}, "route") is None


@pytest.mark.asyncio
async def test_cancelled_media_search_is_killed_and_reaped(monkeypatch):
    from system import core

    started = asyncio.Event()

    class Search:
        returncode = None
        killed = waited = False

        async def communicate(self):
            started.set()
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.waited = True
            return self.returncode

    search = Search()

    async def spawn(*argv, **kwargs):
        assert argv[0].endswith("yt-dlp")
        return search

    monkeypatch.setattr(core, "require_local", lambda: None)
    monkeypatch.setattr(core.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(core.asyncio, "create_subprocess_exec", spawn)
    pending = asyncio.create_task(
        core.SystemTools.play_media(core.SystemTools(), object(), "lofi", show=True)
    )
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert search.killed and search.waited
    assert action_guard.claim("play_media", {"query": "lofi"}, "route") is None
