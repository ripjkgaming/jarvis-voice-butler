import pytest
from games.chess.series import SeriesController, SeriesState, parse_count


class FakeStore:
    def __init__(self):
        self.d = {}

    def save_series(self, d):
        self.d[d["id"]] = dict(d)

    def load_series(self, i):
        return self.d.get(i)

    def active_series(self):
        for d in reversed(list(self.d.values())):
            if d["status"] in ("running", "paused"):
                return dict(d)
        return None


@pytest.mark.parametrize("t,n", [
    ("five", 5), ("fifteen", 15), ("10", 10), ("a hundred", 100),
    ("one hundred", 100), ("twenty five", 25), ("twenty-five games", 25),
    ("101", None), ("0", None), ("five ten", None), ("two hundred", None),
    ("", None), (None, None), ("banana", None), ("fifty fifteen", None),
])
def test_parse_count(t, n):
    assert parse_count(t) == n


def mk(total=3, colour="alternate"):
    st = FakeStore()
    c = SeriesController(st)
    c.start(total, colour)
    return st, c


def test_validation():
    c = SeriesController(FakeStore())
    for bad in (0, 101, -1, True):
        with pytest.raises(ValueError):
            c.start(bad)
    with pytest.raises(ValueError):
        c.start(3, colour="green")
    assert c.start(None).total is None


def test_alternation():
    _, c = mk(4)
    seen = []
    for i in range(4):
        u = c.next_colours()["user"]
        seen.append(u)
        c.record_result(f"g{i}", "1/2-1/2", u)
    assert seen == ["white", "black", "white", "black"]


def test_fixed_colour_black():
    _, c = mk(3, "black")
    assert c.next_colours() == {"user": "black", "jarvis": "white"}
    c.record_result("a", "x", "black")
    assert c.next_colours()["user"] == "black"


def test_score_user_black():
    _, c = mk(3)
    assert c.record_result("a", "0-1", "black") == "win"
    assert c.record_result("b", "0-1", "white") == "loss"
    assert c.record_result("c", "1/2-1/2", "black") == "draw"
    assert c.record_result("c", "1-0", "black") is None  # duplicate
    s = c.state
    assert (s.wins, s.draws, s.losses) == (1, 1, 1)
    assert c.is_done() and s.status == "complete" and c.remaining() == 0
    assert c.score_text() == "1 win, 1 draw, 1 loss"


def test_unfinished_result_ignored():
    _, c = mk()
    assert c.record_result("a", "*", "white") is None
    assert c.state.game_ids == []


def test_pause_resume_cancel():
    st, c = mk(5)
    assert not c.resume()
    assert c.pause() and c.state.status == "paused"
    assert not c.pause()
    c.record_result("a", "1-0", "white")  # in-flight game still counts
    assert c.state.wins == 1
    assert c.resume() and c.state.status == "running"
    assert c.cancel()
    assert c.state.status == "cancelled" and c.is_done()
    assert c.state.game_ids == ["a"] and c.state.wins == 1
    assert st.d[c.state.id]["status"] == "cancelled"
    assert not c.cancel() and not c.resume()
    c.record_result("b", "1-0", "white")
    assert c.state.status == "cancelled"


def test_until_stop_no_cap():
    _, c = mk(None)
    for i in range(150):
        c.record_result(f"g{i}", "1-0", "white")
    assert c.state.status == "running" and not c.is_done()
    assert c.remaining() is None and c.state.index == 151


def test_resume_offer_after_restart():
    st, c = mk(5)
    for i in range(2):
        c.record_result(f"g{i}", "1-0", "white")
    c.pause()
    c2 = SeriesController(st)
    o = c2.resume_offer()
    assert isinstance(o, SeriesState) and o.index == 3 and o.wins == 2
    assert c2.next_colours()["user"] == "white"
    assert c2.resume() and c2.state.status == "running"
    assert c2.remaining() == 3


def test_no_offer_when_done():
    st, c = mk(1)
    c.record_result("a", "1-0", "white")
    assert SeriesController(st).resume_offer() is None
    st2, c2 = mk(3)
    c2.cancel()
    assert SeriesController(st2).resume_offer() is None


def _g(gid, evals, white="user"):
    mv = [{"ply": i + 1, "san": f"m{i+1}", "eval_cp": e} for i, e in enumerate(evals)]
    return {"id": gid, "white": white,
            "black": "jarvis" if white == "user" else "user", "moves": mv}


def test_summary():
    _, c = mk(2)
    c.record_result("g1", "1-0", "white")
    c.record_result("g2", "0-1", "white")
    g1 = _g("g1", [20, 10, 300, 290])          # ply3 user +290 best
    g2 = _g("g2", [0, 0, -600, -600, -600, -600], white="user")  # ply3 user -600 blunder
    sm = c.summary([g1, g2])
    assert sm["score"] == "1 win, 0 draws, 1 loss"
    assert sm["longest_game"] == {"game_id": "g2", "moves": 6}
    assert sm["biggest_blunder"]["game_id"] == "g2" and sm["biggest_blunder"]["ply"] == 3
    assert sm["best_move"]["game_id"] == "g1" and sm["best_move"]["ply"] == 3
    assert len(sm["key_moments"]) == 3
    assert sm["key_moments"][0]["ply"] == 3 and sm["key_moments"][0]["game_id"] == "g2"


def test_summary_mate_and_empty():
    _, c = mk(1)
    g = {"id": "x", "white": "jarvis", "black": "user",
         "moves": [{"ply": 1, "san": "e4", "eval_cp": 30},
                   {"ply": 2, "san": "f6", "eval_mate": 3}]}
    sm = c.summary({"x": g})
    assert sm["biggest_blunder"]["swing"] == -970  # user (black) lost 970
    assert c.summary([])["longest_game"] is None


def test_integration_real_store():
    store_mod = pytest.importorskip("games.chess.store")
    import tempfile
    s = store_mod.GameStore(root=tempfile.mkdtemp())
    c = SeriesController(s)
    c.start(3)
    c.record_result("g1", "1-0", "white")
    o = SeriesController(s).resume_offer()
    assert o is not None and o.wins == 1 and o.index == 2
