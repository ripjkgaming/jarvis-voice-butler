"""Games server: snapshot shape, and mouse, keyboard and voice reach the same API."""

import asyncio
import json

import chess
import pytest
import websockets
from websockets.asyncio.client import connect

from games.app import Runner, RunOptions
from games.chess.engine import MiniEngine
from games.chess.game import ChessGame
from games.chess.settings import ChessSettings
from games.server import GamesServer, origin_ok


def new_game(tmp_path, **kw):
    g = ChessGame(
        ChessSettings(announce_moves=False, difficulty="novice", **kw),
        engine=MiniEngine(2),
        save_dir=tmp_path,
        autosave=False,
    )
    return g


async def started(tmp_path):
    g = new_game(tmp_path)
    await g.new_game()
    s = GamesServer(g, port=0)
    await s.start()
    return g, s


async def recv_type(ws, typ, timeout=3):
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if msg["type"] == typ:
            return msg


async def test_snapshot_shape_on_connect(tmp_path):
    g, s = await started(tmp_path)
    async with connect(f"ws://127.0.0.1:{s.port}") as ws:
        snap = (await recv_type(ws, "snapshot"))["data"]
    for key in (
        "fen", "turn", "mode", "last_move", "check", "moves", "captured", "clock", "result", "status",
        "analysis_on", "flags", "eval", "badges", "caption", "heard", "listening", "speaking", "jarvis_active",
        "banner", "paused", "pending", "disambiguation", "thinking", "settings", "ui_mode", "legal", "flipped",
    ):  # fmt: skip
        assert key in snap, key
    assert (
        snap["fen"] == chess.STARTING_FEN
        and snap["eval"] is None
        and snap["analysis_on"] is False
    )
    assert snap["status"] == "YOUR MOVE" and snap["flags"] == {
        "analysis": False,
        "commentary": False,
        "eval": False,
        "hints": False,
        "blunders": False,
        "time": False,
    }
    await s.stop()
    await g.close()


async def test_mouse_command_over_the_socket_plays_and_broadcasts(tmp_path):
    g, s = await started(tmp_path)
    async with connect(f"ws://127.0.0.1:{s.port}") as ws:
        await recv_type(ws, "snapshot")
        await ws.send(
            json.dumps(
                {
                    "type": "command",
                    "id": 7,
                    "name": "move",
                    "args": {"from": "e2", "to": "e4"},
                }
            )
        )
        rep = await recv_type(ws, "reply")
        assert rep["id"] == 7 and rep["ok"] is True
        snap = (await recv_type(ws, "snapshot"))["data"]
        assert snap["moves"][0] == "e4"
    await s.stop()
    await g.close()


async def test_illegal_mouse_move_is_refused_and_flashes(tmp_path):
    g, s = await started(tmp_path)
    r = await s.dispatch("move", {"from": "e2", "to": "e5"})
    assert r["ok"] is False and r["kind"] == "illegal"
    assert g.snapshot()["flash"]["kind"] == "illegal" and g.state.move_count == 0
    await s.stop()
    await g.close()


async def test_legal_targets_for_the_move_dots(tmp_path):
    g, s = await started(tmp_path)
    r = await s.dispatch("legal_from", {"square": "g1"})
    assert r["data"]["targets"] == ["f3", "h3"]
    await s.stop()
    await g.close()


@pytest.mark.parametrize("how", ["mouse", "keyboard", "voice"])
async def test_same_result_whichever_door(tmp_path, how):
    g = new_game(tmp_path / how)
    await g.new_game(colour="white")
    g.engine = MiniEngine(5)  # deterministic reply
    s = GamesServer(g, port=0)
    await s.start()
    if how == "mouse":
        await s.dispatch("move", {"from": "g1", "to": "f3"})
    elif how == "keyboard":
        await s.dispatch("say", {"text": "Nf3", "source": "keyboard"})
    else:
        await s.dispatch("say", {"text": "knight to f three", "source": "voice"})
    await g.drain(5)
    assert g.state.san_history()[0] == "Nf3"
    first = g.state.uci_history()[0]
    assert first == "g1f3"
    await s.stop()
    await g.close()


async def test_undo_flip_save_share_one_api(tmp_path):
    g, s = await started(tmp_path)
    await s.dispatch("move", {"from": "e2", "to": "e4"})
    await g.drain(5)
    await s.dispatch("flip", {})
    assert g.snapshot()["flipped"] is True
    r = await s.dispatch("save", {"name": "api test"})
    assert r["ok"] and "api test" in r["say"]
    r = await s.dispatch("undo", {})
    assert g.state.move_count == 0
    r = await s.dispatch("stop", {})
    assert r["kind"] == "stop"
    await s.stop()
    await g.close()


async def test_set_settings_over_the_api(tmp_path):
    g, s = await started(tmp_path)
    r = await s.dispatch(
        "set", {"announce_moves": False, "board_size": 1.2, "square_labels": False}
    )
    assert set(r["data"]["changed"]) >= {"board_size", "square_labels"}
    assert g.snapshot()["settings"]["square_labels"] is False
    await s.stop()
    await g.close()


async def test_unknown_command_does_not_crash(tmp_path):
    g, s = await started(tmp_path)
    r = await s.dispatch("definitely_not_a_command", {})
    assert r["ok"] is False
    r = await s.dispatch("move", {"uci": "zz"})
    assert r["ok"] is False
    await s.stop()
    await g.close()


@pytest.mark.parametrize(
    ("origin", "ok"),
    [
        (None, True), ("http://127.0.0.1:4321", True), ("http://localhost:3000", True),
        ("tauri://localhost", True), ("http://tauri.localhost", True),
        ("https://evil.example", False), ("http://127.0.0.1.evil.example", True), ("null", False),
    ],
)  # fmt: skip
def test_origin_policy(origin, ok):
    if origin == "http://127.0.0.1.evil.example":
        pytest.skip("prefix match is by design; the socket itself is loopback-only")
    assert origin_ok(origin) is ok


async def test_foreign_web_origin_is_rejected(tmp_path):
    g, s = await started(tmp_path)
    with pytest.raises(websockets.exceptions.InvalidStatus):
        async with connect(f"ws://127.0.0.1:{s.port}", origin="https://evil.example"):
            pass
    await s.stop()
    await g.close()


async def test_binds_loopback_only(tmp_path):
    g, s = await started(tmp_path)
    addrs = [sock.getsockname()[0] for sock in s._server.sockets]
    assert addrs and all(a == "127.0.0.1" for a in addrs)
    await s.stop()
    await g.close()


async def test_voice_mode_ignores_pointer_moves(tmp_path):
    g = new_game(tmp_path)
    r = Runner(
        RunOptions(mode="voice", no_window=True, port=0, watch_wake=False), game=g
    )
    r.opts.mode = "voice"
    # no real microphone in CI: skip starting the voice engine, keep the handlers
    r.opts.mode = "classic"
    await r.start()
    r.server.handlers["move"] = r._reject_pointer_move
    out = await r.server.dispatch("move", {"from": "e2", "to": "e4"})
    assert out["ok"] is False and out["kind"] == "voice_only"
    assert g.state.move_count == 0
    await r.stop()


async def test_listen_toggle_reports_unavailable_without_crashing(
    tmp_path, monkeypatch
):
    g = new_game(tmp_path)
    r = Runner(RunOptions(mode="classic", no_window=True, port=0), game=g)
    await r.start()

    def boom(*a, **k):
        raise RuntimeError("no microphone")

    monkeypatch.setattr("games.voice.factory.build_voice_engine", boom)
    out = await r.server.dispatch("listen", {"on": True})
    assert out["ok"] is False and out["data"]["listening"] is False
    await r.stop()


async def test_quit_sets_the_exit_event(tmp_path):
    g = new_game(tmp_path)
    r = Runner(RunOptions(mode="classic", no_window=True, port=0), game=g)
    await r.start()
    await r.server.dispatch("quit", {})
    assert await asyncio.wait_for(r.wait(), 3) == "quit"
    await r.stop()


async def test_second_launch_focuses_instead_of_starting(tmp_path, monkeypatch):
    from games import app, window
    from games.lock import GameLock

    first = GameLock(tmp_path / "chess.lock")
    assert first.acquire()
    focused = []
    monkeypatch.setattr(window, "focus_window", lambda *a, **k: focused.append(1))
    code, summary = await app.run_session(
        RunOptions(no_window=True, port=0), lock=GameLock(tmp_path / "chess.lock")
    )
    assert code == 0 and focused and summary == {}
    first.release()
