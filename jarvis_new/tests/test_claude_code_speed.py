import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

FAKE = """#!/usr/bin/env python3
import json, os, sys, time
open(os.environ["FAKE_ARGS"], "a").write(json.dumps(sys.argv[1:]) + "\\n")
sys.stdin.read()
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
print(json.dumps({"type": "system", "subtype": "init", "session_id": "sess-1"}))
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "text", "text": "PROGRESS: on it"},
    {"type": "tool_use", "name": "Bash"}]}}))
print(json.dumps({"type": "result", "session_id": "sess-1", "is_error": False,
                  "result": "DONE: made it"}))
"""

REAL_FLAGS = {
    "--print", "--verbose", "--output-format", "--model", "--permission-mode",
    "--allowedTools", "--effort", "--strict-mcp-config", "--mcp-config",
    "--disable-slash-commands", "--setting-sources", "--resume",
}


def _setup(tmp_path, monkeypatch, sleep="0"):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    claude = bindir / "claude"
    claude.write_text(FAKE)
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_ARGS", str(tmp_path / "args.txt"))
    monkeypatch.setenv("FAKE_SLEEP", sleep)
    monkeypatch.delenv("JARVIS_CODE_MODEL", raising=False)
    monkeypatch.delenv("JARVIS_CODE_EFFORT", raising=False)
    import claude_code_launch as L
    L.claude_binary.cache_clear()
    monkeypatch.setattr(L, "_has_systemd_run", lambda: "")
    procs = []
    real = L.subprocess.Popen

    def popen(argv, **kw):
        p = real(argv, **kw)
        procs.append(p)
        return p

    monkeypatch.setattr(L.subprocess, "Popen", popen)
    return L, procs


def _st(L, jid):
    return json.loads((L.jobs_dir() / f"{jid}.status.json").read_text())


def _args(tmp_path):
    return [json.loads(x) for x in (tmp_path / "args.txt").read_text().splitlines()]


def test_routing():
    import claude_code_launch as L
    assert L.route("make a python script fizzbuzz.py and run it")[::2] == (L.HAIKU, "trivial")
    assert L.route("write a hello world html page")[0] == L.HAIKU
    assert L.route("rename the variable foo to bar")[0] == L.HAIKU
    m, e, t = L.route("add a settings screen with dark mode toggle")
    assert (m, e, t) == (L.SONNET, "medium", "normal")
    m, e, t = L.route("refactor the auth module and debug the race condition")
    assert (m, e, t) == (L.SONNET, "high", "hard")
    assert L.route("a python package with 3 small modules and a pytest for each")[2] == "normal"
    assert L.route("x" * 300)[2] == "hard"


def test_env_override(monkeypatch):
    import claude_code_launch as L
    monkeypatch.setenv("JARVIS_CODE_MODEL", "claude-opus-x")
    monkeypatch.setenv("JARVIS_CODE_EFFORT", "max")
    assert L.route("hello world")[:2] == ("claude-opus-x", "max")


def test_argv_flags_and_timings(tmp_path, monkeypatch):
    L, procs = _setup(tmp_path, monkeypatch)
    job = L.launch("write hello world", str(tmp_path / "p"))
    procs[-1].wait(30)
    a = _args(tmp_path)[0]
    assert a[a.index("--model") + 1] == L.HAIKU
    assert a[a.index("--effort") + 1] == "low"
    assert "Task" in a[a.index("--allowedTools") + 1].split(",")
    assert "--no-session-persistence" not in a
    assert a[a.index("--setting-sources") + 1] == "project,local"
    assert {x for x in a if x.startswith("--")} <= REAL_FLAGS
    st = _st(L, job["id"])
    assert st["state"] == "done" and st["model"] == L.HAIKU and st["steps"] == 1
    for k in ("spawn_ms", "first_token_ms", "first_progress_ms", "total_ms"):
        assert isinstance(st[k], int) and st[k] >= 0, k
    assert st["first_token_ms"] <= st["total_ms"]
    spec = json.loads((L.jobs_dir() / f"{job['id']}.json").read_text())
    assert spec["binary"] == str(tmp_path / "bin" / "claude")


def test_legacy_argv(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_CODE_LEGACY", "1")
    L, procs = _setup(tmp_path, monkeypatch)
    L.launch("write hello world", str(tmp_path / "p"))
    procs[-1].wait(30)
    a = _args(tmp_path)[0]
    assert "--effort" not in a and "--strict-mcp-config" not in a
    assert a[a.index("--model") + 1] == L.SONNET


def test_same_cwd_serialised_other_parallel(tmp_path, monkeypatch):
    L, procs = _setup(tmp_path, monkeypatch, sleep="1.5")
    t = time.time()
    j1 = L.launch("task one", str(tmp_path / "same"))
    j2 = L.launch("task two", str(tmp_path / "same"))
    for p in procs:
        p.wait(60)
    serial = time.time() - t
    assert serial >= 2.9, serial
    waits = sorted([_st(L, j1["id"])["lock_wait_ms"], _st(L, j2["id"])["lock_wait_ms"]])
    assert waits[1] >= 1200

    procs.clear()
    t = time.time()
    L.launch("task one", str(tmp_path / "a"))
    L.launch("task two", str(tmp_path / "b"))
    for p in procs:
        p.wait(60)
    par = time.time() - t
    assert par < serial - 1.0, (par, serial)
