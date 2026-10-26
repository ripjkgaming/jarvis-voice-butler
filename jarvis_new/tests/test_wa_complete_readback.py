"""Exercise actual local Piper chunking/SDK frames with fake PCM only."""

import asyncio
from types import SimpleNamespace

import pytest
import wa_approvals

import local_voice


class Emitter:
    def __init__(self):
        self.pcm = []
        self.flushed = False

    def initialize(self, **kwargs):
        pass

    def push(self, pcm):
        self.pcm.append(pcm)

    def flush(self):
        self.flushed = True


@pytest.fixture
def plugin(monkeypatch):
    plugin = local_voice.PiperTTS(strict_sentence_audio=True)
    monkeypatch.setattr(plugin, "_get_voice", lambda: None)
    monkeypatch.setattr(plugin, "_render_sentence", lambda sentence: b"\x01\x02" * 512)

    async def worker(fn, *args):
        return fn(*args)

    monkeypatch.setattr(plugin, "_run_worker", worker)
    yield plugin
    # No executor jobs were submitted by these tests, including cleanup.
    plugin._closed = True
    plugin._executor.shutdown(wait=False, cancel_futures=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["message", "recipient", "code", "malformed"])
async def test_actual_chunked_render_never_emits_partial_review(
    plugin, monkeypatch, missing
):
    def render(sentence):
        if missing == "malformed":
            return b"x"
        if {
            "message": "Exact message",
            "recipient": "WhatsApp group",
            "code": "Review code",
        }[missing] in sentence:
            return b""
        return b"\x01\x02" * 512

    monkeypatch.setattr(plugin, "_render_sentence", render)
    emitter = Emitter()
    stream = SimpleNamespace(
        _tts=plugin,
        _input_text="WhatsApp group: Exact Group. Exact message: hello. Review code: one two three four.",
    )
    with pytest.raises(RuntimeError, match="sentence audio"):
        await local_voice._PiperChunkedStream._run(stream, emitter)
    assert not emitter.pcm and not emitter.flushed


@pytest.mark.asyncio
async def test_cancellation_mid_render_emits_no_prefix_or_code(plugin, monkeypatch):
    started, resume = asyncio.Event(), asyncio.Event()

    async def worker(fn, *args):
        if args and "Exact message" in args[0]:
            started.set()
            await resume.wait()
        return fn(*args)

    monkeypatch.setattr(plugin, "_run_worker", worker)
    emitter = Emitter()
    stream = SimpleNamespace(
        _tts=plugin,
        _input_text="WhatsApp group: Exact Group. Exact message: hello. Review code: one two three four.",
    )
    task = asyncio.create_task(local_voice._PiperChunkedStream._run(stream, emitter))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not emitter.pcm and not emitter.flushed


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, True])
async def test_spoken_gate_requires_exact_message_audio_through_actual_piper_stream(
    plugin, monkeypatch, tmp_path, complete
):
    rendered = []

    def render(sentence):
        rendered.append(sentence)
        if "Exact message" in sentence and not complete:
            return b""
        return b"\x01\x02" * 512

    monkeypatch.setattr(plugin, "_render_sentence", render)
    frames = []

    class Speech:
        interrupted = False

        def __init__(self, audio):
            self.audio = audio

        async def wait_for_playout(self):
            async for frame in self.audio:
                frames.append(frame.samples_per_channel)

        def exception(self):
            return None

    class Session:
        def say(self, text, **kwargs):
            return Speech(kwargs["audio"])

    async def resolve(name):
        return {"ok": True, "name": name, "jid": "123@g.us"}

    async def forbidden_send(*a):
        pytest.fail("synthesis test attempted send")

    path = tmp_path / "drafts.json"
    draft = wa_approvals.queue(path, "Exact Group", "hello")
    gate = wa_approvals.SpokenApproval(
        Session(),
        "owner",
        lambda: "owner",
        resolve=resolve,
        send=forbidden_send,
        tts=plugin,
    )
    result = await gate.review(path, draft["id"])
    assert any("Exact message" in s for s in rendered)
    assert bool(gate.confirmation) == complete
    if not complete:
        assert not frames and "unavailable" in result["say"]
    else:
        assert frames and all(samples > 0 for samples in frames)


@pytest.mark.asyncio
async def test_normal_chunked_tts_preserves_optional_empty_sentence_behavior(
    plugin, monkeypatch
):
    plugin._strict_sentence_audio = False
    monkeypatch.setattr(
        plugin,
        "_render_sentence",
        lambda sentence: b"" if sentence == "Missing." else b"\x01\x02",
    )
    emitter = Emitter()
    stream = SimpleNamespace(
        _tts=plugin, _input_text="Present. Missing. Present again."
    )
    await local_voice._PiperChunkedStream._run(stream, emitter)
    assert len(emitter.pcm) == 2 and emitter.flushed


def test_strict_review_voice_cannot_use_partial_incremental_text_path(plugin):
    with pytest.raises(RuntimeError, match="requires synthesize"):
        plugin.stream()


@pytest.mark.asyncio
async def test_owner_cancellation_during_actual_readback_render_cannot_arm(
    plugin, monkeypatch, tmp_path
):
    started, resume = asyncio.Event(), asyncio.Event()

    async def worker(fn, *args):
        if args and "Exact message" in args[0]:
            started.set()
            await resume.wait()
        return fn(*args)

    monkeypatch.setattr(plugin, "_run_worker", worker)

    class Speech:
        interrupted = False

        def __init__(self, audio):
            self.audio = audio

        async def wait_for_playout(self):
            async for _ in self.audio:
                pass

        def exception(self):
            return None

    class Session:
        def say(self, text, **kwargs):
            return Speech(kwargs["audio"])

    async def resolve(name):
        return {"ok": True, "name": name, "jid": "123@g.us"}

    path = tmp_path / "drafts.json"
    draft = wa_approvals.queue(path, "Exact Group", "hello")
    gate = wa_approvals.SpokenApproval(
        Session(), "owner", lambda: "owner", resolve=resolve, tts=plugin
    )
    task = asyncio.create_task(gate.review(path, draft["id"]))
    await started.wait()
    gate.user_state(SimpleNamespace(new_state="speaking"))
    resume.set()
    await task
    assert not gate.confirmation and not gate._pending
