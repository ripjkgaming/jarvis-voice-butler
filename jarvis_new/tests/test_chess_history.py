"""Tests for games.chess.history using an in-memory fake store (+ real store if present)."""

from __future__ import annotations

import copy
import os
import types

import pytest

from games.chess.history import START_FEN, HistoryError, HistoryView, ply_for_move


def _rec(id_="20260101-000000-abcd", n=30, **kw):
    moves = [
        {"ply": i, "san": f"m{i}", "uci": f"u{i}", "fen_after": f"fen{i}"}
        for i in range(1, n + 1)
    ]
    d = dict(id=id_, moves=moves, parent_id=None, branched_at_ply=None, started_at="2026-01-01T00:00:00")
    d.update(kw)
    return types.SimpleNamespace(**d)


class FakeStore:
    def __init__(self, recs):
        self.recs = {r.id: r for r in recs}
        self.writes = 0

    def list(self, limit=None):
        return [{"id": r.id, "started_at": r.started_at} for r in self.recs.values()]

    def load(self, id_):
        return self.recs[id_]

    def find(self, ref):
        rows = self.list()
        if isinstance(ref, int):
            if 1 <= ref <= len(rows):
                return self.recs[rows[ref - 1]["id"]]
            return None
        for r in self.recs.values():
            if r.started_at.startswith(ref) or r.id.startswith(ref):
                return r
        return None

    def branch(self, rec, ply):
        self.writes += 1
        new = _rec(id_=rec.id + "-b", n=0, parent_id=rec.id, branched_at_ply=ply)
        new.moves = copy.deepcopy(rec.moves[:ply])
        self.recs[new.id] = new
        return new

    def save(self, *a, **k):
        self.writes += 1


@pytest.fixture
def view():
    s = FakeStore([_rec(), _rec("20260102-000000-ffff", n=5, started_at="2026-01-02T10:00:00")])
    return HistoryView(s), s


def test_ply_for_move():
    assert ply_for_move(12) == 23
    assert ply_for_move(12, "black") == 24
    assert ply_for_move(1) == 1
    with pytest.raises(ValueError):
        ply_for_move(0)


def test_open_by_index_and_date(view):
    v, _ = view
    v.open(1)
    assert v.record.id.startswith("20260101") and v.current_ply == 30
    v.open("2026-01-02")
    assert v.record.id.startswith("20260102") and v.current_ply == 5
    with pytest.raises(HistoryError):
        v.open(99)


def test_step_clamped(view):
    v, _ = view
    v.open(1)
    assert v.step(-1) == 29
    assert v.step(5) == 30 and v.step(1) == 30
    v.goto(0)
    assert v.step(-3) == 0 and v.fen() == START_FEN
    assert v.at_start and not v.at_end


def test_goto_and_fen(view):
    v, _ = view
    v.open(1)
    assert v.goto(10) == 10 and v.fen() == "fen10"
    assert v.goto(999) == 30 and v.goto(-4) == 0
    assert v.goto_move(12) == 23 and v.fen() == "fen23"
    assert v.fen(24) == "fen24"
    assert v.goto_move(12, "black") == 24


def test_not_open_raises():
    v = HistoryView(FakeStore([]))
    with pytest.raises(HistoryError):
        v.step(1)


def test_navigation_zero_writes(view):
    v, s = view
    v.open(1)
    v.step(-1); v.step(1); v.goto(3); v.goto_move(5); v.fen()
    assert s.writes == 0


def test_branch_here(view):
    v, s = view
    v.open(1)
    orig = copy.deepcopy(s.recs[v.record.id].moves)
    v.goto_move(12)
    new = v.branch_here()
    assert new.parent_id == "20260101-000000-abcd" and new.branched_at_ply == 23
    assert len(new.moves) == 23
    assert s.recs["20260101-000000-abcd"].moves == orig
    assert s.writes == 1


def test_undo_last():
    class GS:
        def __init__(self, n): self.n = n
        def undo(self, k):
            d = min(k, self.n); self.n -= d; return d
    assert HistoryView.undo_last(GS(3)) is True
    assert HistoryView.undo_last(GS(0)) is False


def test_real_store_branch_leaves_original(tmp_path, monkeypatch):
    store_mod = pytest.importorskip("games.chess.store")
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    st = store_mod.GameStore(root=tmp_path)
    rec = st.new_record(mode="play", white="user", black="jarvis")
    import chess
    b = chess.Board()
    for i, mv in enumerate(["e2e4", "e7e5", "g1f3", "b8c6"], 1):
        san = b.san(chess.Move.from_uci(mv)); b.push_uci(mv)
        st.append_move(rec, {"ply": i, "san": san, "uci": mv, "fen_after": b.fen(), "eval_cp": None,
                             "eval_mate": None, "depth": None, "best_uci": None, "think_ms": 0,
                             "cls": None, "annotation": None})
    files = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
    v = HistoryView(st)
    v.open(1)
    v.step(-1); v.goto(2)
    assert {p for p in tmp_path.rglob("*") if p.is_file()} == set(files)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files} == before
    new = v.branch_here()
    assert new.parent_id == rec.id and new.branched_at_ply == 2 and len(new.moves) == 2
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files} == before
