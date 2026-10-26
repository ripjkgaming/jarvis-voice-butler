import json
import os

import chess
import pytest

from games.chess import store as st
from games.chess.store import GameStore


def mv(board, san):
    m = board.parse_san(san)
    uci = m.uci()
    ply = len(board.move_stack) + 1
    board.push(m)
    return {"ply": ply, "san": san, "uci": uci, "fen_after": board.fen(), "eval_cp": None,
            "eval_mate": None, "depth": None, "best_uci": None, "think_ms": 5,
            "cls": None, "annotation": None}


def make(s, sans=("e4", "e5", "Nf3"), **kw):
    rec = s.new_record(**kw)
    b = chess.Board()
    for san in sans:
        s.append_move(rec, mv(b, san))
    return rec


def test_lazy_dir_and_import(tmp_path, monkeypatch):
    root = tmp_path / "x" / "games"
    s = GameStore(root)
    assert not root.exists()
    s.list(); s.find("1"); s.latest_active(); s.active_series()
    assert not root.exists()
    s.save(s.new_record())
    assert root.exists()
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    assert st.default_root() == tmp_path / "h" / "chess" / "games"
    assert not (tmp_path / "h").exists()


def test_roundtrip_and_pgn(tmp_path):
    s = GameStore(tmp_path)
    rec = make(s)
    got = s.load(rec.id)
    assert got.moves == rec.moves and got.status == "active"
    pgn = (tmp_path / f"{rec.id}.pgn").read_text()
    assert "1. e4 e5 2. Nf3" in pgn
    import chess.pgn, io
    g = chess.pgn.read_game(io.StringIO(pgn))
    assert [m.uci() for m in g.mainline_moves()] == ["e2e4", "e7e5", "g1f3"]


def test_crash_between_tmp_and_rename(tmp_path, monkeypatch):
    s = GameStore(tmp_path)
    rec = make(s)
    before = s.load(rec.id)
    new = s.new_record()
    real = os.replace

    def boom(src, dst):
        if str(dst).endswith(".json"):
            raise OSError("crash")
        return real(src, dst)

    monkeypatch.setattr(st.os, "replace", boom)
    with pytest.raises(OSError):
        s.save(new)  # new game: json never committed
    with pytest.raises(OSError):
        before2 = s.load(rec.id)
        before2.moves.append({"ply": 4, "san": "x", "uci": "a1a2"})
        s.save(before2)
    monkeypatch.undo()
    assert s.load(new.id) is None
    assert [i["id"] for i in s.list()] == [rec.id]
    assert len(s.load(rec.id).moves) == 3  # old committed state intact
    assert list(tmp_path.glob("*.tmp"))  # orphan left behind
    s2 = GameStore(tmp_path)  # init cleans orphans
    assert not list(tmp_path.glob("*.tmp"))
    assert [i["id"] for i in s2.list()] == [rec.id]


def test_orphan_tmp_ignored(tmp_path):
    s = GameStore(tmp_path)
    rec = make(s)
    (tmp_path / "zzz.json.1.tmp").write_text("{partial")
    assert [i["id"] for i in s.list()] == [rec.id]


def test_branch_leaves_original_bytes(tmp_path):
    s = GameStore(tmp_path)
    rec = make(s)
    j, p = (tmp_path / f"{rec.id}.json"), (tmp_path / f"{rec.id}.pgn")
    bj, bp = j.read_bytes(), p.read_bytes()
    b = s.branch(rec, 2)
    assert j.read_bytes() == bj and p.read_bytes() == bp
    assert b.id != rec.id and b.parent_id == rec.id and b.branched_at_ply == 2
    assert [m["san"] for m in s.load(b.id).moves] == ["e4", "e5"]
    s.append_move(b, {"ply": 3, "san": "d4", "uci": "d2d4"})
    assert len(s.load(rec.id).moves) == 3


def test_corrupt_json_skipped(tmp_path):
    s = GameStore(tmp_path)
    rec = make(s)
    (tmp_path / "20200101-000000-abcd.json").write_text("{not json")
    (tmp_path / "20200102-000000-abcd.json").write_text("[1]")
    (tmp_path / "series").mkdir()
    (tmp_path / "series" / "bad.json").write_text("nope")
    assert [i["id"] for i in s.list()] == [rec.id]
    assert s.load("20200101-000000-abcd") is None
    assert s.list_series() == [] and s.active_series() is None


def test_find(tmp_path):
    s = GameStore(tmp_path)
    ids = []
    for i, d in enumerate(("20260101", "20260102", "20260102")):
        r = s.new_record(id=f"{d}-12000{i}-abc{i}")
        s.save(r)
        ids.append(r.id)
    order = [i["id"] for i in s.list()]
    assert order == sorted(ids, reverse=True)
    assert s.find("1").id == order[0] and s.find("3").id == order[2]
    assert s.find("4") is None and s.find("0") is None
    assert s.find("2026-01-01").id == ids[0]
    assert s.find("2026-01-02").id == order[0]
    assert s.find("20260102-120002").id == ids[2]
    assert s.find("nope") is None and s.find("") is None
    assert len(s.list(limit=2)) == 2


def test_active_finish_set_eval(tmp_path):
    s = GameStore(tmp_path)
    a = make(s)
    assert s.latest_active().id == a.id
    assert s.set_eval(a.id, 2, eval_cp=-30, cls="good", annotation="ok")
    assert not s.set_eval(a.id, 99, eval_cp=1)
    m = s.load(a.id).moves[1]
    assert m["eval_cp"] == -30 and m["san"] == "e5"
    s.finish(a, "1-0", "resign")
    got = s.load(a.id)
    assert got.status == "finished" and got.result == "1-0" and got.ended_at
    assert s.latest_active() is None
    assert "1-0" in (tmp_path / f"{a.id}.pgn").read_text()


def test_series_persistence(tmp_path):
    s = GameStore(tmp_path)
    s.save_series({"id": "s1", "status": "complete"})
    s.save_series({"id": "s2", "status": "paused", "game_ids": ["a"]})
    assert s.load_series("s2")["game_ids"] == ["a"]
    assert GameStore(tmp_path).active_series()["id"] == "s2"
    assert {x["id"] for x in s.list_series()} == {"s1", "s2"}
    assert s.load_series("missing") is None
    assert s.new_record(series_id="s2", series_index=1).series_id == "s2"


def test_series_dataclass(tmp_path):
    from dataclasses import dataclass

    @dataclass
    class S:
        id: str
        status: str = "running"

    s = GameStore(tmp_path)
    s.save_series(S("q"))
    assert s.active_series()["id"] == "q"
