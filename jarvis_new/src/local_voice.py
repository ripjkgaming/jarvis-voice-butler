"""Local neural voice (Piper): $0 TTS with zero inference burn.

The voice model lives on disk (~/.jarvis/voices/*.onnx, downloaded once)
and synthesis runs on CPU, so speaking costs nothing and touches no
cloud meter. Default voice is en_GB-alan-medium (British male).

Interruption behavior (promised to the user): text is split into
sentences, each rendered in a worker thread. Cancelling the stream
lands between sentences, so the worst case is hearing out the tail of
the current sentence (~1s), same as cloud voices.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from pathlib import Path

from livekit.agents import tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

VOICE_DIR = Path.home() / ".jarvis" / "voices"
DEFAULT_VOICE_NAME = "en_GB-alan-medium"
DEFAULT_VOICE_PATH = VOICE_DIR / f"{DEFAULT_VOICE_NAME}.onnx"

_SENTENCE_END = re.compile(r"[.!?…]+(?=\s|$)")

_ABBREVIATIONS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "st",
        "sr",
        "jr",
        "prof",
        "etc",
        "vs",
        "pm",
        "am",
        "eg",
        "ie",
        "us",
        "uk",
        "no",
        "ave",
        "blvd",
        "rd",
    }
)


def _ends_with_abbreviation(head: str) -> bool:
    """True when the text so far ends in Dr./p.m./initials (not a boundary)."""
    match = re.search(r"([A-Za-z][A-Za-z.]*)$", head.rstrip())
    if not match:
        return False
    token = match.group(1).lower().replace(".", "")
    return token in _ABBREVIATIONS or len(token) == 1


def sentence_split(text: str) -> list[str]:
    """Split reply text into speakable sentences. Pure."""
    text = (text or "").strip()
    if not text:
        return []
    bounds = [0]
    for match in _SENTENCE_END.finditer(text):
        if not _ends_with_abbreviation(text[: match.end()]):
            bounds.append(match.end())
    bounds.append(len(text))
    return [
        text[start:end].strip()
        for start, end in zip(bounds, bounds[1:])
        if text[start:end].strip()
    ]


DEFAULT_LENGTH_SCALE = 0.85


def resolve_length_scale(explicit: float | None = None) -> float:
    """Speaking pace: explicit > JARVIS_VOICE_LENGTH_SCALE > default.

    Lower is faster (0.85 ≈ brisk butler, 1.0 = model default).
    """
    if explicit is not None:
        return explicit
    try:
        return float(
            os.environ.get("JARVIS_VOICE_LENGTH_SCALE", str(DEFAULT_LENGTH_SCALE))
        )
    except ValueError:
        return DEFAULT_LENGTH_SCALE


def resolve_voice_path(explicit: str | None = None) -> Path:
    """Voice model file: explicit > JARVIS_VOICE_MODEL > default."""
    if explicit:
        return Path(explicit).expanduser()
    override = os.environ.get("JARVIS_VOICE_MODEL", "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_VOICE_PATH


class PiperTTS(tts.TTS):
    """LiveKit TTS plugin backed by a local Piper voice."""

    def __init__(
        self,
        *,
        model_path: str | Path | None = None,
        speaker_id: int | None = None,
        length_scale: float | None = None,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=22050,
            num_channels=1,
        )
        self._model_path = resolve_voice_path(str(model_path) if model_path else None)
        self._speaker_id = speaker_id
        self._length_scale = resolve_length_scale(length_scale)
        self._voice = None
        self._lock = asyncio.Lock()

    @property
    def model(self) -> str:
        return self._model_path.stem

    @property
    def provider(self) -> str:
        return "piper-local"

    def _get_voice(self):
        if self._voice is None:
            if not self._model_path.exists():
                raise RuntimeError(
                    f"Voice model not found: {self._model_path}. "
                    "Download it into ~/.jarvis/voices/ first."
                )
            from piper import PiperVoice

            self._voice = PiperVoice.load(str(self._model_path))
        # Adopt the voice's real sample rate (config-dependent).
        with contextlib.suppress(Exception):
            self._sample_rate = int(self._voice.config.sample_rate)
        return self._voice

    def _render_sentence(self, sentence: str) -> bytes:
        """Render one sentence to 16-bit PCM bytes (blocking; run in thread)."""
        from piper import SynthesisConfig

        voice = self._get_voice()
        config = SynthesisConfig(
            speaker_id=self._speaker_id,
            length_scale=self._length_scale,
        )
        out = bytearray()
        for chunk in voice.synthesize(sentence, syn_config=config):
            data = chunk.audio_int16_bytes
            if data:
                out += data
        return bytes(out)

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return _PiperChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    def stream(
        self,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.SynthesizeStream:
        return _PiperSynthesizeStream(tts=self, conn_options=conn_options)

    async def aclose(self) -> None:
        self._voice = None


class _PiperSynthesizeStream(tts.SynthesizeStream):
    """Sentence-by-sentence streaming synthesis.

    Consumes the framework's token channel; each flushed segment is
    rendered sentence by sentence in a worker thread. Cancelling lands
    between sentences, so interruption costs at most a sentence tail.
    """

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        plugin: PiperTTS = self._tts  # type: ignore[assignment]
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=plugin.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=True,
        )
        buffer = ""
        async with plugin._lock:
            async for item in self._input_ch:
                if isinstance(item, str):
                    buffer += item
                elif isinstance(item, tts.SynthesizeStream._FlushSentinel):
                    if buffer.strip():
                        output_emitter.start_segment(segment_id=utils.shortuuid())
                        for sentence in sentence_split(buffer):
                            pcm = await asyncio.to_thread(
                                plugin._render_sentence, sentence
                            )
                            if pcm:
                                output_emitter.push(pcm)
                        output_emitter.end_segment()
                    buffer = ""


class _PiperChunkedStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        plugin: PiperTTS = self._tts  # type: ignore[assignment]
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=plugin.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
        )
        async with plugin._lock:
            for sentence in sentence_split(self._input_text):
                pcm = await asyncio.to_thread(plugin._render_sentence, sentence)
                if pcm:
                    output_emitter.push(pcm)
        output_emitter.flush()
