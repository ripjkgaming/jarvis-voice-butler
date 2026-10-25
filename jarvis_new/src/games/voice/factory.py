"""Wire a VoiceEngine from settings (real mic, Piper, whisper, wake watcher)."""

from __future__ import annotations

from games.voice.capture import MicCapture
from games.voice.engine import VoiceEngine
from games.voice.stt import LocalSTT
from games.voice.tts import PiperSpeaker
from games.voice.wake_watch import WakeWatcher


def build_voice_engine(game, *, watch_wake: bool = True) -> VoiceEngine:
    s = game.settings
    stt = LocalSTT(s.whisper_model, s.device)
    speaker = PiperSpeaker(
        length_scale=s.piper_length_scale,
        volume=s.commentary_volume,
        device=s.speaker_device,
    )
    capture = MicCapture(lambda f, t: None, device=s.mic_device)
    watcher = WakeWatcher(lambda: None, lambda: None) if watch_wake else None
    eng = VoiceEngine(game, stt=stt, speaker=speaker, capture=capture, watcher=watcher)
    return eng
