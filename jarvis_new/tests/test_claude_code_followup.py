import json
import os
import stat
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

FAKE = """#!/usr/bin/env python3
import json, os, sys
open(os.environ["FAKE_ARGS"], "a").write(json.dumps(sys.argv[1:]) + "\\n")
sys.stdin.read()
print(json.dumps({"type": "system", "subtype": "init", "session_id": "sess-1"}))
print(json.dumps({"type": "result", "session_id": "sess-1", "is_error": False,
                  "result": "DONE: made it"}))
"""


def _setup(tmp_path, monkeypatch):
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    claude = bindir / "claude"
    claude.write_text(FAKE)
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("JARVIS_HOME", str(home))
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_ARGS", str(tmp_path / "args.txt"))
    import claude_code_launch as L

    # run the runner in-process-like via subprocess, no systemd
    monkeypatch.setattr(
        L.shutil, "which", lambda n: str(bindir / "claude") if n == "claude" else None
    )
    L.claude_binary.cache_clear()
    L._has_systemd_run.cache_clear()
    procs = []
    real = L.subprocess.Popen

    def popen(argv, **kw):
        p = real(argv, **kw)
        procs.append(p)
        return p

    monkeypatch.setattr(L.subprocess, "Popen", popen)
    return L, procs


def _status(L, jid):
    return json.loads((L.jobs_dir() / f"{jid}.status.json").read_text())


def test_launch_followup(tmp_path, monkeypatch):
    L, procs = _setup(tmp_path, monkeypatch)
    job = L.launch("build a thing", str(tmp_path / "proj"))
    assert job["ok"]
    procs[-1].wait(30)
    st = _status(L, job["id"])
    assert st["session_id"] == "sess-1" and st["state"] == "done"
    assert st["cwd"] == str(tmp_path / "proj") and st["finished_at"]
    assert L.latest_job()["id"] == job["id"]

    res = L.followup("latest", "make it blue")
    assert res["ok"], res
    procs[-1].wait(30)
    args = [json.loads(x) for x in (tmp_path / "args.txt").read_text().splitlines()]
    assert "--resume" in args[1] and args[1][args[1].index("--resume") + 1] == "sess-1"
    assert "--resume" not in args[0] and "--no-session-persistence" not in args[0]
    st = _status(L, job["id"])
    assert st["followups"] == ["make it blue"] and st["task"] == "build a thing"
    assert (L.jobs_dir() / f"{job['id']}.2.log").exists()
    assert len(L.list_jobs()) == 1


def test_refusals(tmp_path, monkeypatch):
    L, _ = _setup(tmp_path, monkeypatch)
    assert not L.followup("", "x")["ok"]
    d = L.jobs_dir()
    (d / "cc-a.json").write_text(json.dumps({"id": "cc-a", "task": "t", "cwd": "/tmp"}))
    (d / "cc-a.status.json").write_text(
        json.dumps(
            {"id": "cc-a", "state": "running", "ts": time.time(), "session_id": "s"}
        )
    )
    r = L.followup("cc-a", "more")
    assert not r["ok"] and "running" in r["error"]
    (d / "cc-a.status.json").write_text(
        json.dumps({"id": "cc-a", "state": "done", "ts": time.time()})
    )
    r = L.followup("cc-a", "more")
    assert not r["ok"] and "no saved" in r["error"]
