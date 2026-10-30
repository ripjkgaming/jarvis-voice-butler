"""Dry-run routing tests: which tool the model picks, never executing it.

Every tool's implementation is stubbed after Assistant construction, so
schemas and descriptions stay intact (the model still sees the real
surface) while NO action can run: no files, no browser I/O, no settings,
no power. Assertions read FunctionCallEvent names only.
"""

import pytest
from _free_llm import live_session, patient_run, run_turn, throttle

from agent import Assistant


async def _dry_stub(*args, **kwargs):  # type: ignore[no-untyped-def]
    return {"say": "dry run, not executed"}


def _dry_assistant() -> Assistant:
    assistant = Assistant(browser=None, llm=None)
    for tool in assistant.tools:
        tool._func = _dry_stub  # type: ignore[attr-defined]
    return assistant


async def _calls(session, prompt: str) -> list[str]:
    # Pace every prompt (not just test starts): multi-prompt tests burst
    # through the ~10 RPM combined free quota otherwise.
    await throttle()
    turn = await patient_run(run_turn, session, prompt)
    return turn.calls


@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_routing_shutdown_uses_narrow_handoff() -> None:
    await throttle()
    async with live_session(_dry_assistant()) as session:
        calls = await _calls(session, "Shut down the laptop now.")
        assert "transfer_to_system_control" in calls
        assert "power_control" not in calls


@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_routing_open_app_uses_narrow_handoff() -> None:
    await throttle()
    async with live_session(_dry_assistant()) as session:
        calls = await _calls(session, "Open the Files app on my laptop.")
        assert "transfer_to_system_control" in calls
        assert "open_app" not in calls


@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_routing_gmail_never_touches_browser() -> None:
    await throttle()
    async with live_session(_dry_assistant()) as session:
        calls = await _calls(session, "Check my gmail inbox please.")
        assert "gmail_inbox" in calls
        assert "open_url" not in calls


@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_routing_common_tools_stay_direct() -> None:
    await throttle()
    async with live_session(_dry_assistant()) as session:
        assert await _calls(session, "What time is it?") == ["tell_time"]
        assert "transfer_to_system_control" not in await _calls(
            session, "What's the weather in London?"
        )
        volume_calls = await _calls(session, "Turn the volume down a bit.")
        assert "set_volume" in volume_calls
        assert "transfer_to_system_control" not in volume_calls


@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_routing_search_uses_search_tool() -> None:
    await throttle()
    async with live_session(_dry_assistant()) as session:
        calls = await _calls(session, "Search the web for the capital of France.")
        assert "search_the_web" in calls


@pytest.mark.live_llm
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "Download this YouTube video.",
        "Download https://www.youtube.com/watch?v=aqz-KE-bpKQ to Downloads.",
    ],
)
async def test_routing_youtube_download_uses_direct_tool(prompt: str) -> None:
    async with live_session(_dry_assistant()) as session:
        calls = await _calls(session, prompt)
        assert "download_youtube_video" in calls
        assert not {"open_url", "transfer_to_system_control", "play_media"} & set(calls)
