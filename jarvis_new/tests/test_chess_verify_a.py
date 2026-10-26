"""Black-box verifier tests: takeover run, kill/resume, branch, controls."""
import asyncio, hashlib, json, os, signal, subprocess, sys, textwrap, time
from pathlib import Path

import chess
import pytest

from games.chess.engine import Analysis, BaseEngine, Eval
from games.chess.game import ChessGame
from games.chess.settings import ChessSettings

SCRIPTS = [
    "f2f3 e7e5 g2g4 d8h4".split(),                       # 0-1
    "e2e4 e7e5 f1c4 b8c6 d1h5 g8f6 h5f7".split(),         # 1-0
    "f2f3 e7e5 g2g4 d8h4".split(),
]


class Fake(BaseEngine):
    name = "fake"

    def __init__(self, delay=0.0):
        self.game = -1
        self.delay = delay
        self.fail = 0
        self.calls = 0

    async def best_move(self, board, preset="club"):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        if self.delay:
            await asyncio.sleep(self.delay)
        n = len(board.move_stack)
        if n == 0:
            self.game += 1
        sc = SCRIPTS[self.game % len(SCRIPTS)]
        if n < len(sc):
            mv = chess.Move.from_uci(sc[n])
            if mv in board.legal_moves:
                return mv
        return next(iter(board.legal_moves))

    async def analyse(self, board, depth=12):
        return Analysis(Eval(cp=20), next(iter(board.legal_moves), None), [], depth)

    def cancel(self): pass
    async def close(self): pass


async def nosleep(_s):
    await asyncio.sleep(0)


def mk(root, engine=None, **kw):
    g = ChessGame(ChessSettings(colour="white", announce_moves=False),
                  engine=engine or Fake(), save_dir=Path(root) / "saves",
                  step_delay=0.0, store_root=Path(root) / "games", **kw)
    g.listening = True
    g._takeover_sleep = nosleep
    return g


def sha_all(root):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def check_files(root):
    root = Path(root)
    assert not list(root.rglob("*.tmp")), list(root.rglob("*.tmp"))
    for p in root.rglob("*.json"):
        json.loads(p.read_text())


# 1 -------------------------------------------------------------
async def test_takeover_three_games(tmp_path):
    g = mk(tmp_path)
    g.settings.takeover_continuous = True  # DEFECT: field absent from ChessSettings; bare "take over" = single game
    order = []
    orig = g._new_record

    def nr():
        r = orig()
        order.append(r.id)
        return r

    g._new_record = nr
    await g.new_game()
    r = await g.handle_text("take over")
    for _ in range(400):
        await asyncio.sleep(0.005)
        if len(g.store.list()) >= 4 and sum(1 for i in g.store.list() if i["status"] == "finished") >= 3:
            break
    # release before checking
    await g.handle_text("release control")
    items = sorted(g.store.list(), key=lambda i: i["id"])
    fin = [i for i in g.store.list() if i["status"] == "finished"]
    assert len(fin) >= 3, [(i["id"], i["status"]) for i in items]
    fids = {i["id"] for i in fin}
    recs = [g.store.load(x) for x in order if x in fids]
    cols = [r.white for r in recs]
    print("COLOURS", cols, [r.result for r in recs])
    assert all(cols[i] != cols[i + 1] for i in range(len(cols) - 1)), cols
    for r in recs:
        assert (tmp_path / "games" / f"{r.id}.pgn").exists() and (tmp_path / "games" / f"{r.id}.json").exists()
        assert r.result in ("1-0", "0-1", "1/2-1/2")
    # speech between games one line only: each result announcement is one sentence/line
    print("SPEECH", g.speech_log)
    assert all("\n" not in s for s in g.speech_log)
    check_files(tmp_path)
    await g.close()


# 2 kill/resume -------------------------------------------------
CHILD = textwrap.dedent('''
import asyncio, sys, os
sys.path.insert(0, %(tests)r)
from test_chess_verify_a import mk, Fake
async def main():
    g = mk(%(root)r, Fake(delay=0.004))
    g._takeover_sleep = lambda s: asyncio.sleep(0.003)
    await g.handle_text("play five games")
    await asyncio.sleep(60)
asyncio.run(main())
''')


def _fin(root):
    return [json.loads(p.read_text()) for p in (Path(root) / "games").glob("*.json")
            if "series" not in p.parts]


async def test_kill_during_game3_and_resume(tmp_path):
    env = dict(os.environ, JARVIS_HOME=str(tmp_path / "home"), PYTHONPATH="src")
    script = tmp_path / "child.py"
    script.write_text(CHILD % dict(tests=str(Path(__file__).parent), root=str(tmp_path)))
    p = subprocess.Popen([sys.executable, str(script)], env=env, cwd=Path(__file__).parent.parent)
    deadline = time.time() + 30
    try:
        while time.time() < deadline:
            recs = _fin(tmp_path)
            if sum(1 for r in recs if r["status"] == "finished") >= 2 and any(
                    r["status"] == "active" and len(r["moves"]) >= 1 for r in recs):
                break
            time.sleep(0.002)
        else:
            pytest.fail("child never reached game 3")
    finally:
        p.send_signal(signal.SIGKILL)
        p.wait()
    pre = _fin(tmp_path)
    pre_fin = sorted(r["id"] for r in pre if r["status"] == "finished")
    pre_active = [r for r in pre if r["status"] == "active"]
    print("PRE finished", pre_fin, "active", [(r['id'], len(r['moves'])) for r in pre_active])
    g2 = mk(tmp_path)
    await g2.resume_last()
    print("SPEECH", g2.speech_log, g2.pending)
    assert g2.pending and g2.pending["type"] == "series_resume"
    r = await g2.handle_text("yes")
    print("YES", r.say)
    await g2.wait_series(timeout=30)
    s = g2.series.state
    assert s.status == "complete", s
    items = g2.store.list()
    ids = [i["id"] for i in items]
    assert len(ids) == len(set(ids))
    sg = [i for i in items if i["series_id"] == s.id]
    finished = [i for i in sg if i["status"] == "finished"]
    print("final", len(items), len(sg), len(finished), s.wins, s.draws, s.losses, s.game_ids)
    assert len(finished) == 5, [(i["series_index"], i["status"], i["id"]) for i in sg]
    assert len(set(s.game_ids)) == len(s.game_ids) == 5
    assert s.wins + s.draws + s.losses == 5
    # score == sum of results from user perspective
    w = d = l = 0
    for i in finished:
        rec = g2.store.load(i["id"])
        user_white = rec.white == "user"
        if rec.result == "1/2-1/2": d += 1
        elif (rec.result == "1-0") == user_white: w += 1
        else: l += 1
    assert (w, d, l) == (s.wins, s.draws, s.losses)
    assert sorted(i["series_index"] for i in finished) == [1, 2, 3, 4, 5]
    check_files(tmp_path)
    assert len(items) == 5, "orphan extra games: %s" % [(i['series_index'], i['status'], i.get('termination')) for i in items]
    await g2.close()


# 3 ---------------------------------------------------------------
async def test_history_nav_and_branch(tmp_path):
    g = mk(tmp_path)
    await g.handle_text("play two games")
    await g.wait_series()
    root = tmp_path / "games"
    before = sha_all(root)
    r = await g.handle_text("open game 1"); print("open", r.say); assert r.ok
    r = await g.handle_text("step back"); print("back", r.say)
    r = await g.handle_text("go to move 2"); print("goto", r.say); assert r.ok
    assert sha_all(root) == before
    r = await g.handle_text("rewind to move 2 and continue from here"); print("branch", r.say)
    assert r.ok
    after = sha_all(root)
    for k, v in before.items():
        assert after.get(k) == v, k
    new = set(after) - set(before)
    assert len(new) >= 2
    rec = g._rec
    assert rec.parent_id and rec.id not in [Path(k).stem for k in before]
    assert Path(str(root / f"{rec.parent_id}.json")).exists()
    check_files(tmp_path)
    await g.close()


# 4 ---------------------------------------------------------------
async def test_controls(tmp_path):
    e = Fake(delay=0.01)
    g = mk(tmp_path, e)
    g._takeover_sleep = lambda s: asyncio.sleep(0.005)
    r = await g.handle_text("play five games"); print("start", r.say)
    await asyncio.sleep(0.03)
    r = await g.handle_text("how many games left"); print("left", r.say); assert r.say
    r = await g.handle_text("what's the score"); print("score", r.say); assert r.say
    r = await g.handle_text("pause the series"); print("pause", r.say)
    assert g.series.state.status == "paused"
    n = len(g.store.list()); await asyncio.sleep(0.15)
    assert len(g.store.list()) == n and g.state.mode == "play"
    r = await g.handle_text("continue the series"); print("cont", r.say)
    assert g.series.state.status == "running"
    r = await g.handle_text("release control"); print("rel", r.say)
    assert g.state.mode == "play"
    n = len(g.store.list()); await asyncio.sleep(0.15)
    assert len(g.store.list()) == n
    st = g.series.state.status
    print("series status after release", st)
    r = await g.handle_text("continue the series")
    r = await g.handle_text("cancel the series"); print("cancel", r.say)
    assert g.series.state.status == "cancelled"
    kept = len(g.store.list()); await asyncio.sleep(0.1)
    assert len(g.store.list()) == kept and kept >= 1
    await g.close()


async def test_engine_errors_stop(tmp_path):
    e = Fake(); e.fail = 999
    g = mk(tmp_path, e)
    await g.handle_text("play three games")
    await asyncio.sleep(0.2)
    c = e.calls
    await asyncio.sleep(0.2)
    print("calls", c, e.calls, g.speech_log, g.state.mode)
    assert e.calls == c and c <= 6, "loops on errors"
    assert g.state.mode == "play"
    assert any(w in " ".join(g.speech_log).lower() for w in ("engine", "error", "problem", "trouble", "stopp"))
    await g.close()


async def test_series_summary_and_user_move(tmp_path):
    g = mk(tmp_path)
    await g.handle_text("play two games")
    await g.wait_series()
    print("SPEECH", g.speech_log)
    assert any("Series complete" in x for x in g.speech_log)
    r = await g.handle_text("how many games left"); print("left after", r.say)
    r = await g.handle_text("what's the score"); print("score after", r.say)
    await g.close()
