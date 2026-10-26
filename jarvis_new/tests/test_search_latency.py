import asyncio

import pytest

import tools


@pytest.mark.asyncio
async def test_search_returns_valid_fallback_without_waiting_for_slow_provider(
    monkeypatch,
):
    stopped = asyncio.Event()

    async def slow(_):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def fast(_):
        return {"say": "result", "url": "https://example.com", "fallback": "brave-api"}

    monkeypatch.setattr(tools, "you_search", slow)
    monkeypatch.setattr(tools, "brave_api_search", fast)
    monkeypatch.setattr(tools, "ddg_lite_search", slow)
    result = await asyncio.wait_for(tools.api_search_parallel("q"), 0.2)
    assert result["fallback"] == "brave-api"
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_search_caller_cancellation_drains_every_provider(monkeypatch):
    active = 0
    ready = asyncio.Event()

    async def slow(_):
        nonlocal active
        active += 1
        if active == 3:
            ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    for name in tools._API_PROVIDERS:
        monkeypatch.setattr(tools, name, slow)
    task = asyncio.create_task(tools.api_search_parallel("q"))
    await asyncio.wait_for(ready.wait(), 0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active == 0


@pytest.mark.asyncio
async def test_search_skips_empty_invalid_and_failed_results(monkeypatch):
    async def empty(_):
        return {}

    async def broken(_):
        raise RuntimeError("provider down")

    monkeypatch.setattr(tools, "you_search", empty)
    monkeypatch.setattr(tools, "brave_api_search", broken)
    monkeypatch.setattr(tools, "ddg_lite_search", empty)
    assert await tools.api_search_parallel("q") is None
