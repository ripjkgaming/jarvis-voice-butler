"""Queued chess speech and takeover follow the microphone's VAD state."""

import asyncio
from types import SimpleNamespace

from games.chess.settings import ChessSettings
from games.voice.engine import VoiceEngine


class Speaker:
    speaking = False

    def __init__(self):
        self.texts = []

    async def speak(self, text):
        self.texts.append(text)

    def close(self):
        pass


def make_voice():
    game = SimpleNamespace(
        settings=ChessSettings(commentary_muted=False), user_speaking=False,
        _changed_soon=lambda: None,
    )
    speaker = Speaker()
    ep = SimpleNamespace(in_speech=False)
    voice = VoiceEngine(game, stt=None, speaker=speaker, endpointer=ep)
    return voice, game, speaker, ep


async def test_takeover_reads_live_vad_state():
    voice, game, _, ep = make_voice()
    await voice.start(load_models=False)
    try:
        assert callable(game.user_speaking)
        assert not game.user_speaking()
        ep.in_speech = True
        assert game.user_speaking()
        voice.paused = True
        assert not game.user_speaking()
        voice.paused = False
        ep.in_speech = False
        assert not game.user_speaking()
    finally:
        await voice.stop()
    ep.in_speech = True
    assert not game.user_speaking()


async def test_queued_speech_waits_for_user_to_finish():
    voice, _, speaker, ep = make_voice()
    await voice.start(load_models=False)
    try:
        ep.in_speech = True
        await voice.say("White wins.")
        await asyncio.sleep(0.05)
        assert speaker.texts == []
        ep.in_speech = False
        async with asyncio.timeout(1):
            while not speaker.texts:
                await asyncio.sleep(0.01)
        assert speaker.texts == ["White wins."]
    finally:
        await voice.stop()


async def test_wake_discards_speech_waiting_for_the_microphone():
    voice, _, speaker, ep = make_voice()
    await voice.start(load_models=False)
    try:
        ep.in_speech = True
        await voice.say("White wins.")
        await asyncio.sleep(0.05)
        # Wake ownership supersedes queued chess speech.
        voice.paused = True
        await asyncio.sleep(0.05)
        assert speaker.texts == []
    finally:
        await voice.stop()
