"""Claude via Sir's Pro plan: headless `claude -p`, never an API key."""

import subprocess

import claude_cli


class _Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _which(name):
    return f"/usr/bin/{name}"


def test_argv_uses_subscription_login_and_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_CLAUDE", "1")
    seen = {}

    def runner(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return _Proc(0, "Good evening, Sir.\n")

    reply, warn = claude_cli.claude_reply(
        "hello",
        model="claude-haiku-4-5",
        system="You are Jarvis.",
        runner=runner,
        which=_which,
    )
    assert (reply, warn) == ("Good evening, Sir.", None)
    argv = seen["argv"]
    assert argv[:2] == ["/usr/bin/claude", "-p"]
    assert "--bare" not in argv  # would drop the Pro OAuth login
    assert argv[argv.index("--model") + 1] == "claude-haiku-4-5"
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == "local"
    assert "--no-session-persistence" in argv
    assert "--allowedTools" not in argv  # chat: no tools to approve
    assert seen["kw"]["input"] == "hello"  # prompt on stdin, not argv
    assert seen["kw"]["cwd"] == str(tmp_path / "claude-cwd")


def test_failures_are_warnings_not_exceptions(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_CLAUDE", "1")
    kw = {"model": "m", "system": "s", "which": _which}

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("claude", 5)

    assert claude_cli.claude_reply("x", runner=boom, **kw)[0] == ""
    assert "timed out" in claude_cli.claude_reply("x", runner=boom, **kw)[1]
    bad = claude_cli.claude_reply(
        "x", runner=lambda *a, **k: _Proc(1, "", "nope"), **kw
    )
    assert bad[0] == "" and "exited 1" in bad[1]
    empty = claude_cli.claude_reply("x", runner=lambda *a, **k: _Proc(0, "  "), **kw)
    assert empty == ("", "Claude returned nothing")
    missing = claude_cli.claude_reply("x", model="m", system="s", which=lambda n: None)
    assert missing == ("", "claude CLI not installed")


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("JARVIS_CLAUDE", "0")
    reply, warn = claude_cli.claude_reply(
        "x",
        model="m",
        system="s",
        runner=lambda *a, **k: _Proc(0, "hi"),
        which=_which,
    )
    assert reply == "" and "disabled" in warn
