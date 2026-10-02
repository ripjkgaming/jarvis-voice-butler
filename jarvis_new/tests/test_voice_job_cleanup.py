"""Job shutdown owns the per-job native voice even when startup aborts."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import agent
import goodnight
from intent import needle_router


async def test_job_shutdown_releases_owned_piper_after_startup_failure(monkeypatch):
    class StopStartupError(Exception):
        pass

    plugin = agent.PiperTTS()
    # Create the private worker without loading a model or playing audio.
    await plugin._run_worker(lambda **kwargs: None)
    plugin._voice = object()
    workers = set(plugin._executor._threads)
    callbacks = []
    ctx = SimpleNamespace(
        room=SimpleNamespace(name="private-fixture"),
        add_shutdown_callback=callbacks.append,
    )

    def stop_at_handlers(*args):
        raise StopStartupError

    session = SimpleNamespace(tts=plugin, on=stop_at_handlers)
    monkeypatch.setenv("JARVIS_LOCAL", "0")
    monkeypatch.setattr(goodnight, "cancel", lambda: None)
    monkeypatch.setattr(needle_router, "warm_up", lambda: None)
    monkeypatch.setattr(agent, "silero", None)
    monkeypatch.setattr(
        agent, "BrowserManager", lambda **kwargs: SimpleNamespace(close=AsyncMock())
    )
    monkeypatch.setattr(agent, "_session_for_pipeline", lambda *args, **kwargs: session)
    try:
        with pytest.raises(StopStartupError):
            await agent.my_agent(ctx)
        for callback in callbacks:
            await callback()
        assert plugin._voice is None, "job shutdown retained the native voice model"

        async def wait_for_worker_exit():
            while any(worker.is_alive() for worker in workers):
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait_for_worker_exit(), 2.0)
    finally:
        await plugin.aclose()
