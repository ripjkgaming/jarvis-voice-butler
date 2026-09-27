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


def _line(obj):
    import json

    return json.dumps(obj) + "\n"


def test_stream_event_parses_progress():
    from claude_cli import stream_event

    search = _line({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "WebSearch", "input": {"query": "sober roblox linux"}}]}})
    fetch = _line({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "WebFetch", "input": {"url": "https://github.com/x"}}]}})
    text = _line({"type": "assistant", "message": {"content": [{"type": "text", "text": "## Summary"}]}})
    done = _line({"type": "result", "subtype": "success", "is_error": False, "result": "report"})
    assert stream_event(search) == [{"kind": "search", "detail": "sober roblox linux"}]
    assert stream_event(fetch) == [{"kind": "fetch", "detail": "https://github.com/x"}]
    assert stream_event(text) == [{"kind": "writing"}]
    assert stream_event(done) == [{"kind": "result", "text": "report", "error": False}]
    assert stream_event("not json") == []
    assert stream_event(_line({"type": "system"})) == []


class _StreamProc:
    def __init__(self, lines, rc=0):
        import io

        self.stdin = io.StringIO()
        self.stdout = iter(lines)
        self.stderr = io.StringIO("")
        self.returncode = rc

    def wait(self):
        return self.returncode

    def kill(self):
        pass


def test_claude_stream_reports_events_and_result(monkeypatch):
    import claude_cli

    monkeypatch.setenv("JARVIS_CLAUDE", "1")
    lines = [
        _line({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "WebSearch", "input": {"query": "q"}}]}}),
        _line({"type": "result", "subtype": "success", "is_error": False, "result": "## Summary\nok"}),
    ]
    seen = []
    argv_seen = {}

    def popen(argv, **kw):
        argv_seen["argv"] = argv
        return _StreamProc(lines)

    reply, warning = claude_cli.claude_stream(
        "p", model="m", system="s", tools="WebSearch", on_event=seen.append,
        popen=popen, which=lambda n: "/usr/bin/claude",
    )
    assert (reply, warning) == ("## Summary\nok", None)
    assert [e["kind"] for e in seen] == ["search", "result"]
    assert "stream-json" in argv_seen["argv"] and "--verbose" in argv_seen["argv"]
    bad = [_line({"type": "result", "subtype": "error_max_turns", "is_error": True, "result": ""})]
    reply, warning = claude_cli.claude_stream(
        "p", model="m", system="s", popen=lambda a, **k: _StreamProc(bad, rc=1),
        which=lambda n: "/usr/bin/claude",
    )
    assert reply == "" and warning
