"""UI payload wiring: series / games / history / analysis / takeover fields."""

import json
from types import SimpleNamespace

from games.chess.engine import MiniEngine
from games.chess.game import ChessGame
from games.chess.settings import ChessSettings
from games.server import GamesServer
from games.ui_state import extra_fields, full_snapshot, status_suffix


async def make(tmp_path):
    g = ChessGame(
        ChessSettings(difficulty="novice"),
        engine=MiniEngine(2),
        save_dir=tmp_path,
        autosave=False,
    )
    await g.new_game()
    return g


async def test_defaults_when_modules_absent(tmp_path):
    g = await make(tmp_path)
    f = extra_fields(g)
    assert f["series"] is None and f["games_list"] == []
    assert f["history"]["active"] is False and f["analysis_detail"] is None
    assert f["takeover_active"] is False
    assert status_suffix(g) == ""
    snap = full_snapshot(g)
    assert snap["status"] and snap["history"]["active"] is False
    json.dumps(snap)
    await g.close()


async def test_series_history_analysis_takeover(tmp_path):
    g = await make(tmp_path)
    st = SimpleNamespace(index=2, total=5, status="running", wins=1, draws=0, losses=0)
    g.series = SimpleNamespace(state=st, score_text=lambda: "1-0")
    g.history_view = SimpleNamespace(
        current_ply=7, rec=SimpleNamespace(id="g1", moves=[1] * 20)
    )
    g.last_analysis = {"text": "Qxf7 wins a pawn because ..."}
    g.games_list = [{"id": "g1"}]
    g.state.mode = "takeover"
    f = extra_fields(g)
    assert f["series"]["index"] == 2 and f["series"]["total"] == 5
    assert f["series"]["score"] == "1-0" and f["series"]["status"] == "running"
    assert f["history"] == {"active": True, "ply": 7, "total": 20, "game_id": "g1"}
    assert f["analysis_detail"].startswith("Qxf7")
    assert f["games_list"] == [{"id": "g1"}] and f["takeover_active"] is True
    sfx = status_suffix(g)
    assert "GAME 2/5 1-0" in sfx and "HISTORY ply 7/20" in sfx
    await g.close()


async def test_server_snapshot_carries_fields(tmp_path):
    g = await make(tmp_path)
    srv = GamesServer(g, port=0)
    msg = json.loads(srv._snapshot_msg())
    assert msg["type"] == "snapshot"
    for k in ("series", "games_list", "history", "analysis_detail", "takeover_active"):
        assert k in msg["data"]
    await g.close()
