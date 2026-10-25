"""`chess --tui`: Textual front door onto the same core."""

import asyncio
import time

import chess
import pytest

from games.chess.engine import MiniEngine
from games.chess.game import ChessGame
from games.chess.settings import ChessSettings
from games.tui import CELL_H, CELL_W, BoardView, TuiApp, sq_cell


def cell_xy(sq: int, flipped: bool = False) -> tuple[int, int]:
    cx, cy = sq_cell(sq, flipped)
    return 2 + cx * CELL_W + CELL_W // 2, 1 + cy * CELL_H + 1


async def make_app(tmp_path):
    g = ChessGame(
        ChessSettings(announce_moves=True, difficulty="novice"),
        engine=MiniEngine(2),
        save_dir=tmp_path,
        autosave=False,
    )
    g.listening = True
    await g.new_game()
    return g, TuiApp(g)


async def test_board_renders_codes_pieces_and_glyphs(tmp_path):
    g, app = await make_app(tmp_path)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        text = app.board_view.render().plain
        for code in ("a8", "h1", "e2", "d7"):
            assert code[0] + code[1] in text
        assert "♙" in text and "♟" in text and "♔" in text and "♚" in text
    await g.close()


async def test_click_to_move_with_the_mouse(tmp_path):
    g, app = await make_app(tmp_path)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        x, y = cell_xy(chess.E2)
        await pilot.click(BoardView, offset=(x, y))
        assert app.board_view.selected == chess.E2
        dots = app.board_view.render().plain
        assert "•" in dots  # legal-move dots appear
        x, y = cell_xy(chess.E4)
        await pilot.click(BoardView, offset=(x, y))
        await pilot.pause(0.3)
        assert g.state.uci_history()[0] == "e2e4"
    await g.close()


async def test_clicking_the_wrong_square_just_deselects(tmp_path):
    g, app = await make_app(tmp_path)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        await pilot.click(BoardView, offset=cell_xy(chess.E2))
        await pilot.click(BoardView, offset=cell_xy(chess.E5))  # not a legal target
        assert g.state.move_count == 0
        await pilot.click(BoardView, offset=cell_xy(chess.E7))  # opponent's piece
        assert app.board_view.selected is None
    await g.close()


async def test_typed_moves_and_slash_commands(tmp_path):
    g, app = await make_app(tmp_path)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        await pilot.press(*"e4", "enter")
        await pilot.pause(0.3)
        assert g.state.uci_history()[:1] == ["e2e4"]
        await g.drain(5)
        await pilot.press(*"/flip", "enter")
        await pilot.pause(0.1)
        assert g.flipped is True
        await pilot.press(*"/stop", "enter")
        await pilot.press(*"/undo", "enter")
        await pilot.pause(0.3)
        assert g.state.move_count == 0
    await g.close()


async def test_slide_animation_has_a_motion_trail_then_settles(tmp_path):
    g, app = await make_app(tmp_path)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        await g.command("move", **{"from": "g1", "to": "f3"})
        app.board_view.sync()
        assert app.board_view.anim and app.board_view.anim.knight
        app.board_view.anim.t0 = time.monotonic() - 0.13  # mid-flight
        mid = app.board_view.render().plain
        assert "·" in mid
        app.board_view.anim.t0 = time.monotonic() - 5
        done = app.board_view.render().plain
        assert "♘" in done
    await g.close()


async def test_capture_shatters_into_fragments(tmp_path):
    g = ChessGame(
        ChessSettings(announce_moves=False),
        engine=MiniEngine(2),
        save_dir=tmp_path,
        autosave=False,
    )
    await g.new_game(fen="4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1")
    app = TuiApp(g)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        await g.command("move", **{"from": "e4", "to": "d5"})
        app.board_view.sync()
        a = app.board_view.anim
        assert a and a.capture is not None
        a.t0 = time.monotonic() - a.dur - 0.2
        assert any(ch in app.board_view.render().plain for ch in "*+")
    await g.close()


async def test_check_flash_and_mate_sequence(tmp_path):
    g = ChessGame(
        ChessSettings(announce_moves=False),
        engine=MiniEngine(2),
        save_dir=tmp_path,
        autosave=False,
    )
    await g.new_game(fen="6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1")
    app = TuiApp(g)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        await g.command("move", **{"from": "a1", "to": "a8"})
        app.board_view.sync()
        assert g.state.result().reason == "checkmate"
        assert app.board_view.mated_since is not None
        app.board_view.mated_since -= 1.0
        assert "⚰" in app.board_view.render().plain  # the king topples
        await pilot.pause(0.2)
        assert "checkmate" in str(app.query_one("#banner").render())
    await g.close()


async def test_takeover_shows_amber_banner(tmp_path):
    g, app = await make_app(tmp_path)
    g.step_delay = 5
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        await pilot.press(*"/takeover", "enter")
        await pilot.pause(0.4)
        assert "JARVIS IN CONTROL" in str(app.query_one("#banner").render())
        await pilot.press(*"/stop", "enter")
    await g.close()


async def test_commentary_goes_to_the_log_and_quit_exits(tmp_path):
    g, app = await make_app(tmp_path)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        await g._say("Knight to f six.")
        await pilot.pause(0.1)
        assert any(
            "Knight to f six." in str(line.text) for line in app.query_one("#say").lines
        )
        await pilot.press(*"/quit", "enter")
        await pilot.pause(0.3)
    assert app.return_code in (0, None)
    await g.close()


def test_flipped_cells_mirror():
    assert sq_cell(chess.A1, False) == (0, 7) and sq_cell(chess.A1, True) == (7, 0)
    _ = asyncio, pytest
