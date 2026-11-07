"""Muted chess dialogue still reaches the text interface."""

import pytest

from games.chess.game import ChessGame
from games.chess.settings import ChessSettings


@pytest.mark.parametrize("muted", [True, False])
@pytest.mark.parametrize("listening", [True, False])
async def test_text_output_is_independent_of_voice_gates(tmp_path, muted, listening):
    game = ChessGame(
        ChessSettings(commentary_muted=muted, speak_explanations=False),
        save_dir=tmp_path,
        autosave=False,
    )
    shown, spoken = [], []
    game.listening = listening
    game.on_log = shown.append
    game.on_say = spoken.append
    try:
        await game._say("Shall I review the game, Sir?")
        assert shown == ["Shall I review the game, Sir?"]
        assert spoken == (shown if listening and not muted else [])
        assert game.speech_log == spoken
    finally:
        await game.close()


async def test_text_output_awaits_async_display_callback(tmp_path):
    game = ChessGame(ChessSettings(), save_dir=tmp_path, autosave=False)
    shown = []

    async def show(text):
        shown.append(text)

    game.on_log = show
    try:
        await game._say("Series complete, Sir.")
        assert shown == ["Series complete, Sir."]
        assert game.speech_log == []
    finally:
        await game.close()
