import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pytest
import session_awareness as sa


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    sa.reset_cache()
    return tmp_path


def _caps(home, base):
    rows = [(base, "sir", "open the browser"), (base + 2, "jarvis", "Done, Sir."),
            (base + 5, "sir", "fix the login bug")]
    (home / "captions.log").write_text(
        "old garbage\n" + "\n".join(f"{t}\t{s}\t{x}\tjarvis" for t, s, x in rows) + "\n")


def test_empty_home_ok(home):
    b = sa.build_awareness_block()
    assert "Now:" in b and "carry on" in b


def test_full_block(home):
    _caps(home, time.time() - 7200)
    (home / "code-jobs").mkdir()
    (home / "code-jobs" / "a.status.json").write_text(json.dumps(
        {"id": "a", "state": "done", "summary": "built page", "cwd": "/x", "task": "make page"}))
    (home / "code-jobs" / "b.status.json").write_text("{garbled")
    sa.record_session_end()
    d = json.loads((home / "last_session.json").read_text())
    d["end_epoch"] = time.time() - 7200
    (home / "last_session.json").write_text(json.dumps(d))
    sa.reset_cache()
    b = sa.build_awareness_block()
    assert "2 hours ago" in b and "fix the login bug" in b
    assert "make page" in b and "/x" in b and len(b) <= sa.MAX_BLOCK + 40
    d = json.loads((home / "last_session.json").read_text())
    assert len(d["summary"]) <= 600 and len(d["requests"]) <= 6


def test_cap(home):
    (home / "code-jobs").mkdir()
    for i in range(5):
        (home / "code-jobs" / f"{i}.status.json").write_text(json.dumps(
            {"id": str(i), "state": "done", "summary": "s" * 900, "task": "t" * 500, "cwd": "/c" * 300}))
    assert len(sa.build_awareness_block()) <= sa.MAX_BLOCK + 40


def test_relative():
    assert sa._relative(10) == "moments"
    assert sa._relative(600) == "10 minutes"
    assert sa._relative(3 * 86400) == "3 days"


def test_now_line():
    assert len(sa.now_line().split(",")) >= 3


def test_guest_never_gets_it():
    import watch_mode
    assert "Session awareness" not in watch_mode.GUEST_INSTRUCTIONS
