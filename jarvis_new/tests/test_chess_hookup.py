"""Hookup: level intents on ChessGame, IntentRouter wired into handle_text."""

import json
import socket

import pytest

from games.chess.engine import MiniEngine
from games.chess.game import ChessGame
from games.chess.settings import ChessSettings


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network touched")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("games.chess.commentary.api_key", lambda: None)


async def mk(tmp_path, **kw):
    g = ChessGame(
        ChessSettings(announce_moves=False, difficulty="club", **kw),
        engine=MiniEngine(7),
        save_dir=tmp_path,
        autosave=False,
        step_delay=0.0,
    )
    g.listening = True
    await g.new_game()
    return g


async def test_set_level_methods(tmp_path):
    g = await mk(tmp_path)
    r = await g._i_set_level(7)
    assert r.ok and g.settings.difficulty == "level:7"
    r = await g._i_level_adjust(1)
    assert g.settings.difficulty == "level:8"
    r = await g._i_level_adjust("down")
    assert g.settings.difficulty == "level:7"
    r = await g._i_level_query()
    assert "seven" in r.say.lower()
    assert not (await g._i_set_level(11)).ok
    await g.close()


async def test_legacy_difficulty_delegates(tmp_path):
    g = await mk(tmp_path)
    await g._i_difficulty(level="expert")
    assert g.settings.difficulty == "level:8"
    await g._i_difficulty_adjust(-1)
    assert g.settings.difficulty == "level:7"
    assert g.settings.difficulty_level() == 7
    await g.close()


async def test_voice_level_command(tmp_path):
    g = await mk(tmp_path)
    await g.handle_text("set level to six")
    assert g.settings.difficulty == "level:6"
    await g.close()


async def test_router_fuzzy_paraphrase_runs_intent(tmp_path):
    g = await mk(tmp_path)
    assert g.router.llm_ready() is False
    # a paraphrase not in the strict catalogue
    r = await g.handle_text("could you make it a bit tougher")
    assert r.kind == "difficulty" and g.settings.difficulty == "level:5"
    await g.close()


async def test_router_noop_without_key_and_moves_untouched(tmp_path):
    g = await mk(tmp_path)
    r = await g.handle_text("e4")
    assert g.state.uci_history() == ["e2e4"]
    assert g.router.client is not None
    r = await g.handle_text("blorp zork quux")
    assert r.kind == "miss"
    await g.close()


async def test_smart_routing_setting_disables(tmp_path):
    g = await mk(tmp_path, smart_routing=False)
    assert g.router.enabled is False
    await g.close()


async def test_route_confirm_yes_no(tmp_path):
    from games.chess import commands, router

    g = await mk(tmp_path)
    it = commands.Intent("set_level", {"n": 9}, "game")
    g.router.route = _fake_route(router.RouteResult(None, "Did you mean level 9, Sir?", "llm", candidate=it))
    r = await g.handle_text("something vague about strength please")
    assert r.kind == "offer" and g.pending["type"] == "route_confirm"
    json.dumps(g.snapshot(), default=str)
    await g.handle_text("yes")
    assert g.settings.difficulty == "level:9" and g.pending is None
    g.router.route = _fake_route(router.RouteResult(None, "Did you mean level 2, Sir?", "llm",
                                 candidate=commands.Intent("set_level", {"n": 2}, "game")))
    await g.handle_text("something vague about strength please")
    r = await g.handle_text("no")
    assert g.settings.difficulty == "level:9" and g.pending is None
    await g.close()


async def test_stop_cancels_router(tmp_path):
    g = await mk(tmp_path)
    rt = g.router
    gen = rt.generation
    await g.handle_text("stop")
    assert rt.generation == gen + 1
    await g.close()


def _fake_route(result):
    async def route(text, ctx=None):
        return result

    return route
