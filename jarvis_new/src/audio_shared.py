"""LiveKit-free audio helpers shared by Jarvis and the chess voice engine.

Pure DSP, faster-whisper loading and Piper synthesis. Importing this module
must never pull in ``livekit`` or ``google.genai``: games depend on that.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import numpy as np

TARGET_RATE = 16000

VOICE_DIR = Path.home() / ".jarvis" / "voices"
DEFAULT_VOICE_NAME = "en_GB-alan-medium"
DEFAULT_VOICE_PATH = VOICE_DIR / f"{DEFAULT_VOICE_NAME}.onnx"

_SENTENCE_END = re.compile(r"[.!?…]+(?=\s|$)")

_ABBREVIATIONS = frozenset(
    {
        "mr", "mrs", "ms", "dr", "st", "sr", "jr", "prof", "etc", "vs", "pm", "am",
        "eg", "ie", "us", "uk", "no", "ave", "blvd", "rd",
    }
)  # fmt: skip

DEFAULT_LENGTH_SCALE = 0.85


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


def resolve_length_scale(explicit: float | None = None) -> float:
    """Speaking pace: explicit > JARVIS_VOICE_LENGTH_SCALE > default."""
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


# ------------------------------------------------------------------- resample
def pcm_to_mono16k(pcm: np.ndarray, rate: int, channels: int = 1) -> np.ndarray:
    """int16 / float PCM at any rate -> mono float32 at 16 kHz.

    48 kHz -> 16 kHz (the hot path) is low-pass filtered first; other integer
    ratios decimate directly and any other rate is linearly resampled.
    """
    pcm = np.asarray(pcm)
    if pcm.dtype.kind == "f":
        mono = pcm.astype(np.float32)
        if channels > 1:
            mono = mono.reshape(-1, channels).mean(axis=1)
    else:
        if channels > 1:
            pcm = pcm.reshape(-1, channels).mean(axis=1)
        mono = pcm.astype(np.float32) / 32768.0
    if rate == TARGET_RATE or mono.size == 0:
        return mono
    if rate == 48000:
        from audio_dsp import downsample_48k_to_16k

        # Anti-aliased: naive [::3] folds 8-24 kHz mic noise into the speech band.
        x = downsample_48k_to_16k(np.clip(mono * 32768.0, -32768, 32767).astype(np.int16))
        return x.astype(np.float32) / 32768.0
    if rate % TARGET_RATE == 0:
        return mono[:: rate // TARGET_RATE]
    n_out = round(mono.size * TARGET_RATE / rate)
    x_old = np.linspace(0.0, 1.0, mono.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, n_out, endpoint=False)
    return np.interp(x_new, x_old, mono).astype(np.float32)


# -------------------------------------------------------------------- whisper
# Vocabulary bias per mode: WORD LISTS only. Prompts that spell out whole
# phrases/moves make base.en echo them back (see games/voice/stt.py). Chess
# words live only in the "chess" mode so they never bias ordinary speech.
_GENERAL_WORDS = (
    "Jarvis, Firefox, Spotify, Discord, Brave, Visual Studio Code, Chrome, "
    "Telegram, WhatsApp, Steam, Obsidian, Nautilus, Terminal."
)
VOCAB_PROMPTS = {
    "general": _GENERAL_WORDS,
    "chess": (
        "Chess commands: pawn, knight, bishop, rook, queen, king, castle, takes, "
        "en passant, alpha, bravo, charlie, delta, echo, foxtrot, golf, hotel, "
        "undo, hint, stop."
    ),
}
VOCAB_HOTWORDS = {
    "general": "Jarvis",
    "chess": "pawn knight bishop rook queen king castle takes",
}


def vocab_bias(mode: str | None) -> tuple[str | None, str | None]:
    """(initial_prompt, hotwords) for a mode; (None, None) when unknown/off."""
    if not mode or mode not in VOCAB_PROMPTS:
        return None, None
    return VOCAB_PROMPTS[mode], VOCAB_HOTWORDS[mode]


def load_whisper_model(
    model_size: str,
    device: str = "cpu",
    compute_type: str = "int8",
    *,
    offline_first: bool = False,
):
    """Load a faster-whisper model.

    ``offline_first`` tries the local cache with no network at all (the HF hub
    otherwise pings huggingface.co even for a cached model) and only downloads
    when the model is genuinely missing. Chess uses it so a game makes no
    network calls.
    """
    from faster_whisper import WhisperModel

    if offline_first:
        try:
            return WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type,
                local_files_only=True,
            )
        except Exception:
            pass
    return WhisperModel(model_size, device=device, compute_type=compute_type)


def transcribe_audio(
    model,
    audio: np.ndarray,
    *,
    beam_size: int = 1,
    cancelled: threading.Event | None = None,
    initial_prompt: str | None = None,
    hotwords: str | None = None,
    temperature: float | None = None,
    max_new_tokens: int | None = None,
):
    """Run faster-whisper to completion on the calling thread; returns segments."""
    kwargs: dict = {
        "language": "en",
        "task": "transcribe",
        "beam_size": beam_size,
        "vad_filter": False,
    }
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    if hotwords:
        kwargs["hotwords"] = hotwords
    if max_new_tokens is not None:
        kwargs["max_new_tokens"] = max_new_tokens
    if temperature is not None:
        # One greedy pass, no temperature-fallback re-decodes (each one costs
        # a full decode; a prompt that echoes the audio can trigger several).
        kwargs["temperature"] = temperature
        kwargs["condition_on_previous_text"] = False
    segments, _info = model.transcribe(audio, **kwargs)
    result = []
    try:
        while cancelled is None or not cancelled.is_set():
            try:
                result.append(next(segments))
            except StopIteration:
                break
    finally:
        close = getattr(segments, "close", None)
        if close is not None:
            close()
    return result


# ---------------------------------------------------------------------- piper
def load_piper_voice(model_path: Path):
    if not model_path.exists():
        raise RuntimeError(
            f"Voice model not found: {model_path}. "
            "Download it into ~/.jarvis/voices/ first."
        )
    from piper import PiperVoice

    return PiperVoice.load(str(model_path))


def render_sentence_pcm(
    voice,
    sentence: str,
    *,
    speaker_id: int | None = None,
    length_scale: float = 1.0,
    cancelled: threading.Event | None = None,
    closed=lambda: False,
) -> bytes:
    """Render one sentence to 16-bit PCM bytes (blocking; run in a thread)."""
    from piper import SynthesisConfig

    if closed() or (cancelled is not None and cancelled.is_set()):
        return b""
    config = SynthesisConfig(speaker_id=speaker_id, length_scale=length_scale)
    out = bytearray()
    chunks = voice.synthesize(sentence, syn_config=config)
    try:
        for chunk in chunks:
            if closed() or (cancelled is not None and cancelled.is_set()):
                break
            data = chunk.audio_int16_bytes
            if data:
                out += data
    finally:
        close = getattr(chunks, "close", None)
        if close is not None:
            close()
    return bytes(out)
