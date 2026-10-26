"""Read-only history cursor over a saved chess game.

Ply convention: ply 0 = start position, ply k = position after the k-th half-move.
"Move N" means the full-move number; by default it denotes the position after
WHITE's N-th move = ply 2N-1 (black's N-th = ply 2N). See ply_for_move().
So "rewind to move 12" lands on ply 23.

Navigation (open/step/goto/fen) never writes to the store. Only branch_here()
writes, and only a NEW record via store.branch(); the original is untouched.
Store API used: find(ref), load(id), branch(rec, ply).
"""

from __future__ import annotations

from typing import Any

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class HistoryError(Exception):
    """Raised for unopenable games or use before open()."""


def ply_for_move(n: int, side: str = "white") -> int:
    """Ply after full move n: white -> 2n-1, black -> 2n."""
    if n < 1:
        raise ValueError("move number must be >= 1")
    return 2 * n - 1 if side == "white" else 2 * n


def _moves(rec: Any) -> list:
    return rec["moves"] if isinstance(rec, dict) else rec.moves


def _id(rec: Any) -> str:
    return rec["id"] if isinstance(rec, dict) else rec.id


class HistoryView:
    def __init__(self, store: Any) -> None:
        self.store = store
        self.record: Any = None
        self.current_ply = 0

    def open(self, ref: int | str) -> Any:
        """Open by 1-based list index, 'yyyy-mm-dd', or id prefix; cursor at end."""
        found = self.store.find(ref)
        if found is None:
            raise HistoryError(f"No such game: {ref}")
        rec = found
        if not (isinstance(found, dict) and "moves" in found) and not hasattr(found, "moves"):
            rec = self.store.load(found["id"])
        elif isinstance(found, dict) and "moves" not in found:
            rec = self.store.load(found["id"])
        self.record = rec
        self.current_ply = len(_moves(rec))
        return rec

    def _need(self) -> list:
        if self.record is None:
            raise HistoryError("No game is open.")
        return _moves(self.record)

    @property
    def total_plies(self) -> int:
        return len(self._need())

    @property
    def at_start(self) -> bool:
        return self.current_ply == 0

    @property
    def at_end(self) -> bool:
        return self.current_ply >= self.total_plies

    def goto(self, ply: int) -> int:
        self.current_ply = max(0, min(int(ply), self.total_plies))
        return self.current_ply

    def step(self, delta: int) -> int:
        return self.goto(self.current_ply + delta)

    def goto_move(self, n: int, side: str = "white") -> int:
        return self.goto(ply_for_move(n, side))

    def fen(self, ply: int | None = None) -> str:
        moves = self._need()
        p = self.current_ply if ply is None else max(0, min(int(ply), len(moves)))
        if p == 0:
            return START_FEN
        m = moves[p - 1]
        return m["fen_after"] if isinstance(m, dict) else m.fen_after

    def branch_here(self) -> Any:
        """New record (parent_id, branched_at_ply) from current position; original untouched."""
        self._need()
        return self.store.branch(self.record, self.current_ply)

    @staticmethod
    def undo_last(game_state: Any) -> bool:
        """Take back one ply on a live GameState (state.undo(1)); True if something was undone."""
        return bool(game_state.undo(1))
