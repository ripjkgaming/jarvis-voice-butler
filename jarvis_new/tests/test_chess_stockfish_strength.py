"""Strength changes must clear sticky UCI options on the reused engine."""

from types import SimpleNamespace

import chess

from games.chess.engine import StockfishEngine


class UciSpy:
    def __init__(self):
        self.options = {
            "Skill Level": SimpleNamespace(default=20),
            "UCI_LimitStrength": SimpleNamespace(default=False),
            "UCI_Elo": SimpleNamespace(min=1320, max=3190),
        }
        self.config = {"Skill Level": 20, "UCI_LimitStrength": False}

    async def configure(self, values):
        self.config.update(values)

    async def analyse(self, board, limit):
        return {
            "score": chess.engine.PovScore(chess.engine.Cp(20), chess.WHITE),
            "pv": [next(iter(board.legal_moves))],
            "depth": 8,
        }


async def test_low_to_full_strength_restores_skill():
    engine = StockfishEngine()
    engine._engine = spy = UciSpy()
    await engine._configure("level:1")
    assert spy.config["Skill Level"] == 0
    await engine._configure("level:10")
    assert spy.config["Skill Level"] == 20
    assert spy.config["UCI_LimitStrength"] is False


async def test_low_to_elo_strength_restores_skill():
    engine = StockfishEngine()
    engine._engine = spy = UciSpy()
    await engine._configure("level:4")
    await engine._configure("level:7")
    assert spy.config["Skill Level"] == 20
    assert spy.config["UCI_LimitStrength"] is True
    assert spy.config["UCI_Elo"] == 1800


async def test_analysis_restores_full_skill_after_weak_play():
    engine = StockfishEngine()
    engine._engine = spy = UciSpy()
    await engine._configure("level:1")
    await engine.analyse(chess.Board(), depth=8)
    assert spy.config["Skill Level"] == 20
    assert spy.config["UCI_LimitStrength"] is False
