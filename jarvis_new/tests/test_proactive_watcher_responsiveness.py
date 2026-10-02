"""Voice-loop responsiveness and bounded sensor collection; no real sensors."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from context.telemetry import TelemetrySnapshot
from proactive.watcher import ProactiveWatcher


def _watcher(snapshot_fn, spoken: list[str]) -> ProactiveWatcher:
    async def speak(line: str) -> None:
        spoken.append(line)

    policy = SimpleNamespace(
        consider=lambda line, **kwargs: SimpleNamespace(texts=[line]),
        drain_queue=lambda now: SimpleNamespace(texts=[]),
    )
    return ProactiveWatcher(
        snapshot_fn, speak, policy=policy, log_fn=lambda *args: None
    )


async def _wait_until(condition) -> None:
    async def poll() -> None:
        while not condition():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(poll(), timeout=2)


async def test_slow_sensor_allows_voice_loop_to_run() -> None:
    entered = threading.Event()
    release = threading.Event()
    released_while_collecting: list[bool] = []
    loop_thread = threading.get_ident()
    sensor_threads: list[int] = []

    def snapshot() -> TelemetrySnapshot:
        sensor_threads.append(threading.get_ident())
        entered.set()
        released_while_collecting.append(release.wait(0.5))
        return TelemetrySnapshot(battery_pct=99)

    async def voice_callback() -> None:
        await _wait_until(entered.is_set)
        release.set()

    callback = asyncio.create_task(voice_callback())
    try:
        await _watcher(snapshot, [])._tick(1)
        await callback
    finally:
        release.set()
        await callback

    assert released_while_collecting == [True]
    assert sensor_threads != [loop_thread]


@pytest.mark.parametrize("sensor_fails", [False, True])
async def test_cancelled_collection_is_bounded_and_its_result_is_discarded(
    sensor_fails: bool,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls: list[int] = []
    spoken: list[str] = []

    def snapshot() -> TelemetrySnapshot:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            entered.set()
            try:
                release.wait(0.5)
                if sensor_fails:
                    raise RuntimeError("abandoned sensor failure")
                return TelemetrySnapshot(battery_pct=4)
            finally:
                finished.set()
        return TelemetrySnapshot(battery_pct=99)

    watcher = _watcher(snapshot, spoken)
    tick = asyncio.create_task(watcher._tick(1))
    try:
        await _wait_until(entered.is_set)
        assert not tick.done(), "sensor blocked the event loop until collection ended"
        tick.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tick
        for _ in range(3):
            await watcher._tick(2)
        assert calls == [1], "cancellation must not launch overlapping sensor workers"
        assert watcher._history == []
        assert spoken == []

        release.set()
        await _wait_until(finished.is_set)

        # The worker signals just before it returns; executor callbacks may
        # still be arriving. A new tick must recover once they have settled.
        async def recover() -> None:
            while len(calls) == 1:
                await watcher._tick(3)
                await asyncio.sleep(0)

        await asyncio.wait_for(recover(), timeout=2)
        assert calls == [1, 2]
        assert watcher._history == [(3, 99)]
        assert watcher._failures == 0
        assert spoken == []
    finally:
        release.set()
        await asyncio.gather(tick, return_exceptions=True)


async def test_stop_restart_does_not_overlap_or_announce_abandoned_snapshot() -> None:
    entered = threading.Event()
    release = threading.Event()
    fresh = threading.Event()
    calls: list[int] = []
    spoken: list[str] = []

    def snapshot() -> TelemetrySnapshot:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            entered.set()
            release.wait(0.5)
            return TelemetrySnapshot(battery_pct=4)
        fresh.set()
        return TelemetrySnapshot(battery_pct=99)

    watcher = _watcher(snapshot, spoken)
    watcher._poll_s = 0.005
    tasks = [watcher.start()]
    try:
        await _wait_until(entered.is_set)
        watcher.stop()
        await asyncio.gather(tasks[-1], return_exceptions=True)
        for _ in range(3):
            tasks.append(watcher.start())
            await asyncio.sleep(0.01)
            watcher.stop()
            await asyncio.gather(tasks[-1], return_exceptions=True)
        assert calls == [1]
        assert spoken == []

        release.set()
        tasks.append(watcher.start())
        await _wait_until(fresh.is_set)
        await _wait_until(lambda: bool(watcher._history))
        assert all(pct == 99 for _, pct in watcher._history)
        assert spoken == []
    finally:
        release.set()
        watcher.stop()
        await asyncio.gather(*tasks, return_exceptions=True)
