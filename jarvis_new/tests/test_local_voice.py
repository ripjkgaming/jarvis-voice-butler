import pytest

from local_voice import (
    DEFAULT_LENGTH_SCALE,
    DEFAULT_VOICE_PATH,
    PiperTTS,
    resolve_length_scale,
    resolve_voice_path,
    sentence_split,
)


def test_sentence_split_basic() -> None:
    assert sentence_split("Hello, Sir. At your service! Ready?") == [
        "Hello, Sir.",
        "At your service!",
        "Ready?",
    ]


def test_sentence_split_empty_and_blank() -> None:
    assert sentence_split("") == []
    assert sentence_split("   ") == []


def test_sentence_split_keeps_abbreviations_together() -> None:
    assert sentence_split("Meet me at 5 p.m. sharp") == ["Meet me at 5 p.m. sharp"]
    assert sentence_split("Dr. Smith arrived. Hello there.") == [
        "Dr. Smith arrived.",
        "Hello there.",
    ]


def test_resolve_voice_path_defaults_and_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("JARVIS_VOICE_MODEL", raising=False)
    assert resolve_voice_path() == DEFAULT_VOICE_PATH
    monkeypatch.setenv("JARVIS_VOICE_MODEL", str(tmp_path / "custom.onnx"))
    assert resolve_voice_path() == tmp_path / "custom.onnx"
    assert resolve_voice_path(str(tmp_path / "explicit.onnx")) == tmp_path / (
        "explicit.onnx"
    )


def test_plugin_labels_and_capabilities() -> None:
    plugin = PiperTTS()
    assert plugin.provider == "piper-local"
    assert plugin.model == DEFAULT_VOICE_PATH.stem
    assert plugin.num_channels == 1
    assert plugin.capabilities.streaming is True
    assert plugin._length_scale == DEFAULT_LENGTH_SCALE


def test_length_scale_default_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_length_scale() == DEFAULT_LENGTH_SCALE
    assert resolve_length_scale(1.0) == 1.0
    monkeypatch.setenv("JARVIS_VOICE_LENGTH_SCALE", "0.7")
    assert resolve_length_scale() == pytest.approx(0.7)
    assert resolve_length_scale(1.0) == 1.0
    monkeypatch.setenv("JARVIS_VOICE_LENGTH_SCALE", "junk")
    assert resolve_length_scale() == DEFAULT_LENGTH_SCALE


@pytest.mark.asyncio
async def test_faster_scale_shortens_audio() -> None:
    """length_scale 0.7 renders strictly less audio than 1.0."""
    slow = PiperTTS(length_scale=1.0)
    fast = PiperTTS(length_scale=0.7)
    if not slow._model_path.exists():
        pytest.skip("voice model not downloaded")
    text = "Good evening, Sir. All systems are at your disposal."
    slow_bytes = slow._render_sentence(text)
    fast_bytes = fast._render_sentence(text)
    assert len(slow_bytes) > 0
    assert len(fast_bytes) < len(slow_bytes)


def test_missing_voice_model_raises_helpfully(tmp_path) -> None:
    plugin = PiperTTS(model_path=tmp_path / "nope.onnx")
    with pytest.raises(RuntimeError, match="Voice model not found"):
        plugin._get_voice()


@pytest.mark.asyncio
async def test_real_voice_renders_audio() -> None:
    """End-to-end through the real Alan voice: PCM bytes flow."""
    plugin = PiperTTS()
    if not plugin._model_path.exists():
        pytest.skip("voice model not downloaded")
    stream = plugin.synthesize("Good evening, Sir.")
    pcm = bytearray()
    async for event in stream:
        pcm += event.frame.data.tobytes()
    await stream.aclose()
    assert len(pcm) > 10_000
    assert plugin.sample_rate == 22050


@pytest.mark.asyncio
async def test_empty_text_pushes_no_audio() -> None:
    plugin = PiperTTS()
    stream = plugin.synthesize("   ")
    frames = [event async for event in stream]
    await stream.aclose()
    assert frames == []


@pytest.mark.asyncio
async def test_stream_adapter_path_renders_audio() -> None:
    """The exact framework path: StreamAdapter wraps us (streaming TTS).

    This is the path that failed in the first live session (only
    synthesize() existed, so the adapter's stream() hit the abstract
    base and the turn died silently).
    """
    from livekit.agents import tokenize, tts

    plugin = PiperTTS()
    if not plugin._model_path.exists():
        pytest.skip("voice model not downloaded")
    adapter = tts.StreamAdapter(
        tts=plugin,
        sentence_tokenizer=tokenize.blingfire.SentenceTokenizer(),
    )
    stream = adapter.stream()
    stream.push_text("Good evening, Sir. At your service.")
    stream.end_input()
    pcm = bytearray()
    async for event in stream:
        pcm += event.frame.data.tobytes()
    await stream.aclose()
    assert len(pcm) > 10_000


@pytest.mark.asyncio
async def test_interruption_closes_stream_promptly() -> None:
    """aclose mid-synthesis terminates instead of hanging the turn."""
    import asyncio
    import time

    plugin = PiperTTS()
    if not plugin._model_path.exists():
        pytest.skip("voice model not downloaded")
    stream = plugin.stream()
    stream.push_text(
        "This is a long first sentence that takes a moment to render. "
        "Followed by a second sentence that should never be heard. "
        "And a third for good measure."
    )
    consumer = asyncio.create_task(_drain(stream))
    await asyncio.sleep(0.5)
    started = time.monotonic()
    await stream.aclose()
    await consumer
    assert time.monotonic() - started < 10


async def _drain(stream) -> None:
    try:
        async for _ in stream:
            pass
    except Exception:
        pass
