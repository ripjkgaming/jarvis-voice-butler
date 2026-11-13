import asyncio
from types import SimpleNamespace

import numpy as np

from games.chess.settings import ChessSettings
from games.chess.game import ChessGame
from games.voice.engine import VoiceEngine


class Speaker:
    speaking = False
    out_rms = 0.01

    def __init__(self):
        self.texts = []
        self.stops = 0

    def stop(self):
        self.stops += 1

    async def speak(self, text):
        self.texts.append(text)

    def close(self):
        pass


def make_voice(muted=False):
    game = SimpleNamespace(
        settings=ChessSettings(commentary_muted=muted),
        speaking=False,
        _changed_soon=lambda: None,
    )
    speaker = Speaker()
    voice = VoiceEngine(
        game, stt=None, speaker=speaker, endpointer=SimpleNamespace(in_speech=False)
    )
    return voice, game, speaker


def test_muted_by_default():
    assert ChessSettings().commentary_muted


async def test_direct_voice_replies_respect_mute():
    voice, _, speaker = make_voice(muted=True)
    await voice.start(load_models=False)
    try:
        await voice.say("Your move, Sir.")
        await asyncio.sleep(0.04)
        assert speaker.texts == []
    finally:
        await voice.stop()


async def test_stop_discards_dequeued_speech_waiting_for_user():
    voice, _, speaker = make_voice()
    await voice.start(load_models=False)
    try:
        voice.ep.in_speech = True
        await voice.say("Stale dialogue")
        await asyncio.sleep(0.04)
        voice.cancel_speech()
        voice.ep.in_speech = False
        await asyncio.sleep(0.05)
        assert speaker.texts == []
        await voice.say("Fresh dialogue")
        await asyncio.sleep(0.04)
        assert speaker.texts == ["Fresh dialogue"]
    finally:
        await voice.stop()


def test_speech_start_cancels_even_during_synthesis():
    voice, game, speaker = make_voice()
    game.speaking = True
    voice._speech_started(np.ones(512, dtype=np.float32))
    assert speaker.stops == 1
    assert not game.speaking


async def test_setting_mute_stops_current_dialogue(tmp_path):
    game = ChessGame(
        ChessSettings(commentary_muted=False), save_dir=tmp_path, autosave=False
    )
    stops = []
    game.on_stop_speech = lambda: stops.append(True)
    game._set_settings({"commentary_muted": True})
    assert stops == [True]
    game.settings.commentary_muted = False
    await game._i_mute(True)
    assert stops == [True, True]
    await game.close()


async def test_fast_moves_cannot_build_an_unbounded_dialogue_backlog():
    voice, _, _ = make_voice()
    for i in range(100):
        await voice.say(str(i))
    assert voice._speech_q.qsize() <= 2
    assert voice._speech_q.get_nowait() == "98"
    assert voice._speech_q.get_nowait() == "99"


def test_pending_audio_keeps_latest_command_instead_of_a_backlog():
    voice, _, _ = make_voice()
    voice.running = True
    voice.ep.feed = lambda frame, t: t
    frame = np.zeros(512, dtype=np.float32)
    for i in range(10):
        voice.on_frame(frame, i)
    assert voice._q.qsize() == 1
    assert voice._q.get_nowait() == 9
