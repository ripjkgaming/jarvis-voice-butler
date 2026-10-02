"""Native Piper work must remain bounded after async interruption."""

import asyncio
import contextlib
import threading
from types import SimpleNamespace

import pytest

import local_voice


class Emitter:
    def __init__(self):
        self.pcm = []
        self.initialized = None

    def initialize(self, **kwargs):
        self.initialized = kwargs

    def start_segment(self, **kwargs):
        pass

    def end_segment(self):
        pass

    def push(self, pcm):
        self.pcm.append(pcm)

    def flush(self):
        pass


async def run_stream(plugin, text, kind="chunked", emitter=None):
    emitter = emitter or Emitter()
    if kind == "chunked":
        stream = SimpleNamespace(_tts=plugin, _input_text=text)
        await local_voice._PiperChunkedStream._run(stream, emitter)
    else:

        async def tokens():
            yield text
            yield local_voice.tts.SynthesizeStream._FlushSentinel()

        stream = SimpleNamespace(_tts=plugin, _input_ch=tokens())
        await local_voice._PiperSynthesizeStream._run(stream, emitter)
    return emitter


async def until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(poll(), 2)


@pytest.fixture
def fake_piper(monkeypatch, tmp_path):
    import piper

    model_path = tmp_path / "voice.onnx"
    model_path.touch()
    state = SimpleNamespace(
        calls=[],
        loads=0,
        active=0,
        peak=0,
        yielded=0,
        configs=[],
        started=threading.Event(),
        release=threading.Event(),
        loading=threading.Event(),
        release_load=threading.Event(),
        block_load=False,
    )
    state.release_load.set()

    class Voice:
        config = SimpleNamespace(sample_rate=16000)

        def synthesize(self, sentence, syn_config):
            state.calls.append(sentence)
            state.configs.append(syn_config)
            state.active += 1
            state.peak = max(state.peak, state.active)
            try:
                if sentence == "Old.":
                    state.started.set()
                    assert state.release.wait(3), (
                        "test did not release fake native work"
                    )
                for data in (b"\x01\x02", b"\x03\x04"):
                    state.yielded += 1
                    yield SimpleNamespace(audio_int16_bytes=data)
            finally:
                state.active -= 1

    def load(path):
        state.loads += 1
        state.loading.set()
        if state.block_load:
            assert state.release_load.wait(3), "test did not release fake model load"
        return Voice()

    monkeypatch.setattr(piper.PiperVoice, "load", load)
    plugin = local_voice.PiperTTS(
        model_path=model_path, speaker_id=7, length_scale=0.85
    )
    yield plugin, state
    state.release.set()
    state.release_load.set()


@pytest.mark.parametrize("kind", ["chunked", "streaming"])
@pytest.mark.asyncio
async def test_cancelled_native_render_serializes_new_turn_and_skips_old_queue(
    fake_piper, kind
):
    plugin, state = fake_piper
    tasks = []
    try:
        old = asyncio.create_task(run_stream(plugin, "Old.", kind))
        tasks.append(old)
        await until(state.started.is_set)
        old.cancel()
        with pytest.raises(asyncio.CancelledError):
            await old
        for i in range(4):
            queued = asyncio.create_task(run_stream(plugin, f"Obsolete {i}.", kind))
            tasks.append(queued)
            await asyncio.sleep(0.015)
            queued.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await queued
        current = asyncio.create_task(run_stream(plugin, "Current.", kind))
        tasks.append(current)
        await asyncio.sleep(0.025)
        assert state.calls == ["Old."]
        assert state.peak == 1
        state.release.set()
        emitter = await current
        assert state.calls == ["Old.", "Current."]
        assert state.yielded == 3  # one old chunk, then two current chunks
        assert emitter.pcm == [b"\x01\x02\x03\x04"]
    finally:
        state.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await plugin.aclose()


@pytest.mark.asyncio
async def test_concurrent_preloads_load_one_native_model(fake_piper):
    plugin, state = fake_piper
    state.block_load = True
    state.release_load.clear()
    tasks = [asyncio.create_task(run_stream(plugin, "First."))]
    try:
        await until(state.loading.is_set)
        tasks.append(asyncio.create_task(run_stream(plugin, "Second.", "streaming")))
        await asyncio.sleep(0.03)
        assert state.loads == 1
        state.release_load.set()
        await asyncio.gather(*tasks)
        assert state.loads == 1
    finally:
        state.release_load.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await plugin.aclose()


@pytest.mark.asyncio
async def test_render_timeout_keeps_native_work_serialized(fake_piper, monkeypatch):
    plugin, state = fake_piper
    monkeypatch.setattr(local_voice, "RENDER_TIMEOUT_S", 0.05)
    old = asyncio.create_task(run_stream(plugin, "Old."))
    tasks = [old]
    try:
        await until(state.started.is_set)
        with pytest.raises(asyncio.TimeoutError):
            await old
        current = asyncio.create_task(run_stream(plugin, "Current."))
        tasks.append(current)
        await asyncio.sleep(0.02)
        assert state.calls == ["Old."]
        state.release.set()
        await current
        assert state.peak == 1
    finally:
        state.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await plugin.aclose()


@pytest.mark.asyncio
async def test_close_drains_native_work_before_releasing_model(fake_piper):
    plugin, state = fake_piper
    old = asyncio.create_task(run_stream(plugin, "Old."))
    closer = None
    try:
        await until(state.started.is_set)
        old.cancel()
        with pytest.raises(asyncio.CancelledError):
            await old
        closer = asyncio.create_task(plugin.aclose())
        await asyncio.sleep(0.02)
        assert not closer.done()
        assert plugin._voice is not None
        closer.cancel()
        await asyncio.sleep(0)
        closer.cancel()
        state.release.set()
        await asyncio.gather(closer, return_exceptions=True)
        await plugin.aclose()
        assert state.active == 0
        assert plugin._voice is None
        with pytest.raises(RuntimeError, match="closed"):
            await run_stream(plugin, "Too late.")
        assert state.loads == 1
    finally:
        state.release.set()
        await asyncio.gather(old, *([closer] if closer else []), return_exceptions=True)
        await plugin.aclose()


@pytest.mark.parametrize("kind", ["chunked", "streaming"])
@pytest.mark.asyncio
async def test_worker_preserves_pcm_sample_rate_and_synthesis_options(fake_piper, kind):
    plugin, state = fake_piper
    try:
        emitter = await run_stream(plugin, "First. Second.", kind)
        assert emitter.pcm == [b"\x01\x02\x03\x04"] * 2
        assert emitter.initialized["sample_rate"] == 16000
        assert state.calls == ["First.", "Second."]
        assert all(config.speaker_id == 7 for config in state.configs)
        assert all(config.length_scale == 0.85 for config in state.configs)
    finally:
        await plugin.aclose()


@pytest.mark.asyncio
async def test_close_releases_dedicated_worker(fake_piper):
    plugin, _state = fake_piper
    await run_stream(plugin, "Hello.")
    workers = set(plugin._executor._threads)
    assert len(workers) == 1
    await plugin.aclose()
    await until(lambda: all(not worker.is_alive() for worker in workers))
