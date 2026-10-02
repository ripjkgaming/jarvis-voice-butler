"""Opening speech survives room setup; all audio/devices here are synthetic."""

import asyncio
import contextlib
import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest

import wake_client


def block(value):
    return np.full(wake_client.BLOCKSIZE, value, dtype=np.int16).tobytes()


def client():
    obj = wake_client.WakeClient.__new__(wake_client.WakeClient)
    obj._mic_lock = threading.Lock()
    obj._muted = False
    obj._mute_epoch = 0
    obj._in_call = False
    obj._listen_queue_live = True
    obj._threshold = 0.5
    obj._talk_event = threading.Event()
    obj._pending_announce = ""
    obj._model = None
    obj._rewake = None
    obj._last_agent_voice = 0.0
    return obj


class Sink:
    def __init__(self):
        self.audio = []
        self.times = []
        self.changed = asyncio.Event()

    async def capture_frame(self, frame):
        assert frame.sample_rate == wake_client.MIC_RATE
        assert frame.num_channels == 1
        self.audio.append(bytes(frame.data))
        self.times.append(asyncio.get_running_loop().time())
        self.changed.set()

    async def until(self, count, timeout=1):
        async def wait():
            while len(self.audio) < count:
                self.changed.clear()
                await self.changed.wait()

        await asyncio.wait_for(wait(), timeout)


async def cancel(task):
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("trigger", ["wake", "ptt"])
async def test_normal_entry_preserves_onset_on_the_same_input_stream(
    monkeypatch, trigger
):
    """Detection trails the keyword; command onset can precede the summon."""
    import keyword_spot
    import school

    obj = client()
    seen = {}
    opened = []

    class StopListeningError(Exception):
        pass

    class Model:
        calls = 0

        def predict(self, frame):
            self.calls += 1
            return {"hey_jarvis": float(trigger == "wake" and self.calls == 13)}

    class Input:
        def __init__(self, **kwargs):
            opened.append(kwargs)
            self.callback = kwargs["callback"]

        def __enter__(self):
            async def feed():
                for number in range(45):
                    if trigger == "ptt" and number == 32:
                        obj._talk_event.set()
                    self.callback(block(number), wake_client.BLOCKSIZE, None, None)
                    await asyncio.sleep(0)

            self.producer = asyncio.create_task(feed())
            return self

        def __exit__(self, *args):
            self.producer.cancel()

    async def summon(**kwargs):
        seen.update(kwargs)
        raise StopListeningError

    def no_ack():
        pytest.fail("local acknowledgement would be recorded into the opening turn")

    monkeypatch.setitem(
        sys.modules, "sounddevice", SimpleNamespace(RawInputStream=Input)
    )
    monkeypatch.setattr(keyword_spot, "enabled", lambda: False)
    monkeypatch.setattr(school, "is_school", lambda: False)
    monkeypatch.setattr(obj, "_summon_with_rewake", summon)
    monkeypatch.setattr(obj, "_play_ack", no_ack)
    monkeypatch.setattr(wake_client, "summon_overlay", lambda: None)
    monkeypatch.setattr(wake_client, "mark_hud_waking", lambda: None)

    with pytest.raises(StopListeningError):
        await asyncio.wait_for(obj._listen_loop(Model()), 1)
    assert len(opened) == 1
    assert seen["preserve_join_audio"] is True
    assert seen["mic_queue"].maxsize > 0
    # The command starts in block 31, slightly before the wake score fires.
    # Keep its onset, not the full second of keyword audio (blocks 0..30).
    values = [int(np.frombuffer(raw, np.int16)[0]) for raw in seen["preroll"]]
    assert 31 in values
    if trigger == "wake":
        assert 32 in values
    assert values[0] >= 27
    assert len(values) == len(set(values))


async def test_join_audio_waits_for_agent_then_preserves_every_sample_once(monkeypatch):
    monkeypatch.setattr(wake_client, "MIC_READY_SETTLE_S", 0)
    obj, sink = client(), Sink()
    queue, ready = asyncio.Queue(), asyncio.Event()
    before_detection = [block(1), block(2)]
    during_join = [block(3), block(4), block(5)]
    for raw in during_join:
        queue.put_nowait(raw)
    task = asyncio.create_task(
        obj._pump_queue(sink, queue, ready, before_detection, True)
    )
    try:
        await asyncio.sleep(0.01)
        assert sink.audio == [], (
            "publishing before agent input attaches loses the opener"
        )
        ready.set()
        await sink.until(5)
        queue.put_nowait(block(6))
        await sink.until(6)
        assert sink.audio == before_detection + during_join + [block(6)]
    finally:
        await cancel(task)


async def test_school_text_seed_still_discards_join_backlog(monkeypatch):
    monkeypatch.setattr(wake_client, "MIC_READY_SETTLE_S", 0)
    obj, sink = client(), Sink()
    queue, ready = asyncio.Queue(), asyncio.Event()
    queue.put_nowait(block(11))
    ready.set()
    task = asyncio.create_task(obj._pump_queue(sink, queue, ready, []))
    try:
        await asyncio.sleep(0.01)
        queue.put_nowait(block(12))
        await sink.until(1)
        assert sink.audio == [block(12)], "the school question already arrived as text"
    finally:
        await cancel(task)


async def test_never_replays_an_opener_if_no_agent_attaches(monkeypatch):
    monkeypatch.setattr(wake_client, "AGENT_JOIN_TIMEOUT", 0.01)
    monkeypatch.setattr(wake_client, "MIC_READY_SETTLE_S", 0)
    sink = Sink()
    await asyncio.wait_for(
        client()._pump_queue(sink, asyncio.Queue(), asyncio.Event(), [block(1)], True),
        0.1,
    )
    assert sink.audio == []


async def test_mute_during_join_discards_preroll_but_keeps_new_unmuted_speech(
    monkeypatch,
):
    monkeypatch.setattr(wake_client, "MIC_READY_SETTLE_S", 0)
    obj, sink = client(), Sink()
    queue, ready = asyncio.Queue(), asyncio.Event()
    obj._capture_loop = asyncio.get_running_loop()
    obj._capture_queue = queue
    queue.put_nowait(block(2))
    task = asyncio.create_task(obj._pump_queue(sink, queue, ready, [block(1)], True, 0))
    try:
        await asyncio.sleep(0)
        obj.set_muted(True)
        await asyncio.sleep(0)
        obj.set_muted(False)
        queue.put_nowait(block(3))
        ready.set()
        await sink.until(1)
        assert sink.audio == [block(3)]
    finally:
        await cancel(task)


async def test_normal_rewake_ignores_replayed_keyword_but_detects_new_live_keyword(
    monkeypatch,
):
    monkeypatch.setattr(wake_client, "MIC_READY_SETTLE_S", 0)
    obj, sink = client(), Sink()
    obj._rewake = asyncio.Event()
    obj._model = SimpleNamespace(
        reset=lambda: None, predict=lambda frame: {"hey_jarvis": 1.0}
    )
    queue, ready = asyncio.Queue(), asyncio.Event()
    ready.set()
    task = asyncio.create_task(
        obj._pump_queue(sink, queue, ready, [block(1)] * 5, True)
    )
    try:
        await sink.until(5)
        assert not obj._rewake.is_set(), "initial keyword replay must not re-summon"
        for _ in range(5):
            queue.put_nowait(block(2))
        await asyncio.wait_for(asyncio.shield(task), 1)
        assert obj._rewake.is_set()
        assert sink.audio == [block(1)] * 5 + [block(2)] * 5
    finally:
        await cancel(task)


async def test_shared_stream_is_retained_across_stale_call_rewake(monkeypatch):
    obj = client()
    queue = asyncio.Queue()
    calls = []

    async def summon(**kwargs):
        calls.append(kwargs)
        return len(calls) == 1

    monkeypatch.setattr(obj, "_summon_session", summon)
    monkeypatch.setattr(obj, "_play_ack", lambda: pytest.fail("no recorded local ack"))
    monkeypatch.setattr(wake_client, "summon_overlay", lambda: None)
    await obj._summon_with_rewake(
        mic_queue=queue, preroll=[block(1)], preserve_join_audio=True
    )
    assert len(calls) == 2
    assert all(call["mic_queue"] is queue for call in calls)
    assert calls[1].get("preroll", []) == []
    assert calls[1]["preserve_join_audio"] is True


def test_mic_queue_is_bounded_and_never_silently_truncates_the_command():
    queue = asyncio.Queue(maxsize=3)
    for value in range(6):
        wake_client.enqueue_mic_block(queue, block(value))
    assert queue.qsize() == 3
    assert queue.mic_overflowed is True
    assert [queue.get_nowait() for _ in range(3)] == [block(0), block(1), block(2)]


async def test_overflow_aborts_before_forwarding_a_partial_request(monkeypatch):
    import hud_events

    monkeypatch.setattr(wake_client, "MIC_READY_SETTLE_S", 0)
    captions = []
    monkeypatch.setattr(hud_events, "caption", lambda *args: captions.append(args))
    queue, ready, sink = asyncio.Queue(maxsize=1), asyncio.Event(), Sink()
    wake_client.enqueue_mic_block(queue, block(1))
    wake_client.enqueue_mic_block(queue, block(2))
    ready.set()
    await client()._pump_queue(sink, queue, ready, [], True)
    assert sink.audio == []
    assert len(captions) == 1 and "try again" in captions[0][1].lower()


async def test_excess_digital_silence_catches_up_without_speeding_or_losing_speech(
    monkeypatch,
):
    monkeypatch.setattr(wake_client, "MIC_READY_SETTLE_S", 0)
    monkeypatch.setattr(wake_client, "MIC_CATCHUP_SILENCE_S", 0.064)
    monkeypatch.delenv("JARVIS_VAD_MIN_SILENCE", raising=False)
    queue, ready, sink = asyncio.Queue(), asyncio.Event(), Sink()
    # A second opening word follows the first one; then a silence gap
    # gives us room to remove the old join delay before the next phrase.
    for raw in [block(2)] + [block(0)] * 20 + [block(3), block(4)]:
        queue.put_nowait(raw)
    ready.set()
    task = asyncio.create_task(
        client()._pump_queue(sink, queue, ready, [block(1)], True)
    )
    try:
        await asyncio.wait_for(sink.until(6), 0.4)
        assert sink.audio == [
            block(1),
            block(2),
            block(0),
            block(0),
            block(3),
            block(4),
        ]
        queue.put_nowait(block(5))
        await asyncio.wait_for(sink.until(7), 0.1)
        assert sink.audio[-1] == block(5)
    finally:
        await cancel(task)


async def test_mute_during_replay_never_replays_remaining_old_audio(monkeypatch):
    monkeypatch.setattr(wake_client, "MIC_READY_SETTLE_S", 0)
    obj, sink = client(), Sink()
    queue, ready = asyncio.Queue(), asyncio.Event()
    obj._capture_loop = asyncio.get_running_loop()
    obj._capture_queue = queue
    ready.set()
    task = asyncio.create_task(
        obj._pump_queue(sink, queue, ready, [block(1), block(2)], True)
    )
    try:
        await sink.until(1)
        obj.set_muted(True)
        await asyncio.sleep(0)
        obj.set_muted(False)
        queue.put_nowait(block(3))
        await sink.until(2)
        assert sink.audio == [block(1), block(3)]
    finally:
        await cancel(task)


async def test_partial_pcm_frames_keep_their_sample_duration(monkeypatch):
    monkeypatch.setattr(wake_client, "MIC_READY_SETTLE_S", 0)
    obj, sink = client(), Sink()
    queue, ready = asyncio.Queue(), asyncio.Event()
    ready.set()
    short = np.full(480, 100, dtype=np.int16).tobytes()  # 10 ms, not 32 ms
    task = asyncio.create_task(
        obj._pump_queue(sink, queue, ready, [short, short], True)
    )
    try:
        await sink.until(2)
        assert sink.audio == [short, short]
        assert 0.007 <= sink.times[1] - sink.times[0] < 0.028
    finally:
        await cancel(task)


@pytest.mark.parametrize(
    ("vad_silence", "endpoint_delay", "zero_blocks"),
    [(None, None, 19), ("0.9", "0.15", 30), ("0.55", "1.0", 33)],
)
async def test_catchup_preserves_the_configured_endpoint_gap(
    monkeypatch, vad_silence, endpoint_delay, zero_blocks
):
    monkeypatch.setattr(wake_client, "MIC_READY_SETTLE_S", 0)
    for key, value in [
        ("JARVIS_VAD_MIN_SILENCE", vad_silence),
        ("JARVIS_ENDPOINT_DELAY", endpoint_delay),
    ]:
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    queue, ready, sink = asyncio.Queue(), asyncio.Event(), Sink()
    for raw in [block(0)] * 60 + [block(2)]:
        queue.put_nowait(raw)
    ready.set()
    task = asyncio.create_task(
        client()._pump_queue(sink, queue, ready, [block(1)], True)
    )
    try:
        await sink.until(zero_blocks + 2, timeout=2)
        assert sink.audio == [block(1)] + [block(0)] * zero_blocks + [block(2)]
    finally:
        await cancel(task)


async def test_callback_discards_audio_if_mute_changes_while_copying_it(monkeypatch):
    import keyword_spot
    import school

    obj = client()
    scored = []

    class InputData:
        def __bytes__(self):
            # A socket-thread mute cycle occurs between state sampling and
            # completing the PortAudio buffer copy. This remains old audio.
            obj.set_muted(True)
            obj.set_muted(False)
            return block(123)

    class Input:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def __enter__(self):
            self.callback(InputData(), wake_client.BLOCKSIZE, None, None)
            return self

        def __exit__(self, *args):
            pass

    def score(model, pending):
        scored.append(pending.size)
        return [], pending

    monkeypatch.setitem(
        sys.modules, "sounddevice", SimpleNamespace(RawInputStream=Input)
    )
    monkeypatch.setattr(keyword_spot, "enabled", lambda: False)
    monkeypatch.setattr(school, "is_school", lambda: False)
    monkeypatch.setattr(wake_client, "score_frames", score)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(obj._listen_loop(SimpleNamespace()), 0.05)
    assert obj._mute_epoch == 1
    assert scored == [], "pre-mute PCM must not receive the new unmuted epoch"
