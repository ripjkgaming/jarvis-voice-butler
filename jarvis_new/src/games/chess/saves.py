"""Autosave, named saves and PGN import/export."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import chess
import chess.pgn

from games.chess.settings import saves_dir

AUTOSAVE = "autosave.json"


def _dir(base: Path | None) -> Path:
    d = base or saves_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(name: str) -> str:
    n = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-")
    return n or "game"


def write(data: dict, name: str | None = None, base: Path | None = None) -> Path:
    path = _dir(base) / (AUTOSAVE if name is None else f"{safe_name(name)}.json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)
    return path


def read(name: str | None = None, base: Path | None = None) -> dict | None:
    path = _dir(base) / (AUTOSAVE if name is None else f"{safe_name(name)}.json")
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_saves(base: Path | None = None) -> list[str]:
    return sorted(p.stem for p in _dir(base).glob("*.json") if p.name != AUTOSAVE)


def delete(name: str, base: Path | None = None) -> bool:
    path = _dir(base) / f"{safe_name(name)}.json"
    try:
        path.unlink()
        return True
    except OSError:
        return False


def last_save_name(base: Path | None = None) -> str | None:
    files = [p for p in _dir(base).glob("*.json") if p.name != AUTOSAVE]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime).stem


def export_pgn(moves: list[str], start_fen: str | None, headers: dict) -> str:
    board = chess.Board(start_fen) if start_fen else chess.Board()
    game = chess.pgn.Game()
    if start_fen and start_fen != chess.STARTING_FEN:
        game.setup(board)
    for k, v in headers.items():
        game.headers[k] = str(v)
    node = game
    for uci in moves:
        node = node.add_variation(chess.Move.from_uci(uci))
    out = io.StringIO()
    print(game, file=out, end="\n")
    return out.getvalue()


def import_pgn(text: str) -> tuple[list[str], str | None, dict]:
    """Return (uci moves, start fen or None, headers). Raises ValueError."""
    game = chess.pgn.read_game(io.StringIO(text))
    if game is None:
        raise ValueError("no game found in PGN")
    board = game.board()
    start = board.fen() if board.fen() != chess.STARTING_FEN else None
    moves = []
    for mv in game.mainline_moves():
        if mv not in board.legal_moves:
            raise ValueError("illegal move in PGN")
        moves.append(mv.uci())
        board.push(mv)
    return moves, start, dict(game.headers)
