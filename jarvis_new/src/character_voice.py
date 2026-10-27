"""Per-character Piper voices with DSP (pitch/effects/whoosh).

Jarvis is the passthrough character: it resolves model, pace and (absent)
effects exactly like the legacy ``PiperTTS`` path, with no DSP and no extra
work. Every other character gets its own warm Piper voice plus
``voice_fx`` pitch/effects applied per sentence.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np

import voice_fx
from audio_shared import (
    VOICE_DIR,
    load_piper_voice,
    render_sentence_pcm,
    resolve_length_scale,
    resolve_voice_path,
)
from local_voice import PiperTTS

logger = logging.getLogger("jarvis-voice")

PASSTHROUGH_ID = "jarvis"
PREWARM_PHRASE = "Online."


def is_passthrough(char) -> bool:
    return getattr(char, "id", PASSTHROUGH_ID) == PASSTHROUGH_ID


def resolve_model_path(char) -> Path:
    """Jarvis: legacy resolution. Others: ~/.jarvis/voices/<model>.onnx."""
    if is_passthrough(char):
        return resolve_voice_path(None)
    model = str(char.voice.get("model", "")).strip()
    if not model:
        return resolve_voice_path(None)
    direct = Path(model).expanduser()
    if direct.suffix == ".onnx":
        return direct
    return VOICE_DIR / f"{model}.onnx"


def length_scale_for(char) -> float:
    if is_passthrough(char):
        return resolve_length_scale(None)
    return resolve_length_scale(char.voice.get("length_scale"))


def apply_fx(pcm: np.ndarray, sr: int, char) -> np.ndarray:
    """Pitch + effects for a character; identity for jarvis."""
    if is_passthrough(char):
        return pcm
    voice = char.voice
    semis = float(voice.get("pitch_semitones", 0) or 0)
    if semis:
        pcm = voice_fx.pitch_shift(pcm, sr, semis)
    effects = list(voice.get("effects") or [])
    if effects:
        pcm = voice_fx.apply_effects(pcm, sr, effects)
    return pcm


class VoiceBank:
    """Lazy, thread-safe, per-model PiperVoice cache (load once, stay warm)."""

    def __init__(self) -> None:
        self._voices: dict[Path, object] = {}
        self._lock = threading.Lock()  # guards the two dicts below only
        self._load_locks: dict[Path, threading.Lock] = {}
        self._synth_locks: dict[Path, threading.Lock] = {}

    def _path_lock(self, table: dict, path: Path) -> threading.Lock:
        with self._lock:
            lock = table.get(path)
            if lock is None:
                lock = table[path] = threading.Lock()
            return lock

    def get_voice(self, char):
        """Load once per model; distinct models load concurrently."""
        path = resolve_model_path(char)
        with self._lock:
            voice = self._voices.get(path)
        if voice is not None:
            return voice
        with self._path_lock(self._load_locks, path):
            with self._lock:
                voice = self._voices.get(path)
            if voice is None:
                voice = load_piper_voice(path)
                with self._lock:
                    self._voices[path] = voice
        return voice

    def sample_rate(self, char) -> int:
        try:
            rate = int(self.get_voice(char).config.sample_rate)
            return rate if rate > 0 else 22050
        except Exception:
            return 22050

    def render_pcm(self, text: str, char) -> tuple[np.ndarray, int]:
        voice = self.get_voice(char)
        sr = self.sample_rate(char)
        # Serialize per voice instance only: different voices synthesize in
        # parallel (an exit line never waits on the target's prewarm).
        with self._path_lock(self._synth_locks, resolve_model_path(char)):
            raw = render_sentence_pcm(
                voice, text, length_scale=length_scale_for(char)
            )
        pcm = np.frombuffer(raw, dtype=np.int16)
        return apply_fx(pcm, sr, char), sr

    def prewarm(self, char) -> bool:
        """Blocking. Load and render a 1-word phrase through fx. Never raises."""
        try:
            pcm, _ = self.render_pcm(PREWARM_PHRASE, char)
            return pcm.size > 0
        except Exception as exc:
            logger.warning("voice prewarm failed for %s: %s", getattr(char, "id", "?"), exc)
            return False

    def whoosh_pcm(self, char, sr: int, dur_ms: float = 800) -> np.ndarray:
        kind = char.voice.get("whoosh")
        return voice_fx.synth_whoosh(kind, sr, dur_ms)


def idle_warm_enabled() -> bool:
    return os.environ.get("JARVIS_CHARACTER_IDLE_WARM", "1").strip() != "0"


def start_idle_warm(
    chars: Iterable,
    busy: Callable[[], bool],
    *,
    delay_s: float = 20.0,
    bank: VoiceBank | None = None,
    sleep: Callable[[float], None] = time.sleep,
    retry_s: float = 5.0,
    max_retries: int = 12,
    background: bool = True,
) -> threading.Thread | None:
    """Warm every non-jarvis voice once, low priority, only while idle.

    Jarvis is never touched. Returns the daemon thread (None when disabled).
    """
    if not idle_warm_enabled():
        return None
    targets = [c for c in chars if not is_passthrough(c)]
    use = bank or _bank

    def run() -> None:
        if background:
            with contextlib.suppress(Exception):
                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
        sleep(delay_s)
        for char in targets:
            tries = 0
            while True:
                try:
                    is_busy = bool(busy())
                except Exception:
                    is_busy = True
                if not is_busy:
                    break
                tries += 1
                if tries > max_retries:
                    return
                sleep(retry_s)
            use.prewarm(char)

    t = threading.Thread(target=run, name="char-idle-warm", daemon=True)
    t.start()
    return t


_bank = VoiceBank()


def get_bank() -> VoiceBank:
    return _bank


def render_pcm(text: str, char) -> tuple[np.ndarray, int]:
    return _bank.render_pcm(text, char)


def prewarm(char) -> bool:
    return _bank.prewarm(char)


def whoosh_pcm(char, sr: int, dur_ms: float = 800) -> np.ndarray:
    return _bank.whoosh_pcm(char, sr, dur_ms)


class _FxPiperTTS(PiperTTS):
    """PiperTTS whose voice comes from the shared bank and gets fx per sentence."""

    def __init__(self, char, bank: VoiceBank) -> None:
        super().__init__(
            model_path=resolve_model_path(char),
            length_scale=length_scale_for(char),
        )
        self._char = char
        self._bank = bank

    def _get_voice(self, *, cancelled: threading.Event | None = None):
        if self._closed:
            raise RuntimeError("Piper voice is closed")
        if cancelled is not None and cancelled.is_set():
            return None
        self._voice = self._bank.get_voice(self._char)
        self._sample_rate = self._bank.sample_rate(self._char)
        return self._voice

    def _render_sentence(self, sentence, *, cancelled=None) -> bytes:
        raw = super()._render_sentence(sentence, cancelled=cancelled)
        if not raw or len(raw) < 2:
            return raw
        pcm = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16)
        return apply_fx(pcm, self._sample_rate, self._char).tobytes()


class CharacterPiperTTS(PiperTTS):
    """Session TTS that follows the active character at synthesize time.

    Jarvis delegates to this very (unmodified) PiperTTS instance's own
    stream classes; other characters delegate to a per-character child.
    """

    def __init__(self, *, runtime=None, bank: VoiceBank | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._runtime = runtime
        self._bank = bank or _bank
        self._children: dict[str, _FxPiperTTS] = {}

    def _active(self):
        try:
            if self._runtime is not None:
                return self._runtime.active()
            from character_runtime import get_runtime

            return get_runtime().active()
        except Exception as exc:
            logger.warning("active character unavailable, using jarvis: %s", exc)
            return None

    def _target(self) -> PiperTTS:
        char = self._active()
        if char is None or is_passthrough(char):
            return self
        child = self._children.get(char.id)
        if child is None:
            child = self._children[char.id] = _FxPiperTTS(char, self._bank)
        return child

    def synthesize(self, text, *, conn_options=None):
        target = self._target()
        kw = {} if conn_options is None else {"conn_options": conn_options}
        if target is self:
            return super().synthesize(text, **kw)
        return target.synthesize(text, **kw)

    def stream(self, *, conn_options=None):
        target = self._target()
        kw = {} if conn_options is None else {"conn_options": conn_options}
        if target is self:
            return super().stream(**kw)
        return target.stream(**kw)

    async def aclose(self) -> None:
        for child in list(self._children.values()):
            try:
                await child.aclose()
            except Exception as exc:  # pragma: no cover
                logger.debug("child tts close failed: %s", exc)
        await super().aclose()


def make_session_tts(**kwargs) -> CharacterPiperTTS:
    """Factory for agent.py: character-aware TTS (jarvis path unchanged)."""
    return CharacterPiperTTS(**kwargs)
