import json
import subprocess

import claude_backend


def runner(tools=(), error=None):
    def run(argv, **kw):
        assert argv[argv.index("--tools") + 1] == ""
        assert "--restricted" in argv and "--strict-mcp-config" in argv
        assert "--no-session-persistence" in argv and "--disable-slash-commands" in argv
        assert kw["input"] == "task"
        assert "ANTHROPIC_API_KEY" not in kw["env"]
        rows = [
            {
                "type": "system",
                "subtype": "init",
                "tools": list(tools),
                "mcp_servers": [],
            },
            {
                "type": "result",
                "is_error": bool(error),
                "result": error or "A careful answer",
            },
        ]
        return subprocess.CompletedProcess(
            argv, 0, "\n".join(json.dumps(r) for r in rows), ""
        )

    return run


def test_isolated_claude_reply(monkeypatch):
    monkeypatch.setattr(claude_backend.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "do-not-use-api-billing")
    assert claude_backend.reply("task", system="s", runner=runner()) == (
        "A careful answer",
        None,
    )


def test_rejects_exposed_tools(monkeypatch):
    monkeypatch.setattr(claude_backend.shutil, "which", lambda name: "/usr/bin/claude")
    assert (
        claude_backend.reply("task", system="s", runner=runner(tools=["Bash"]))[0] == ""
    )


def test_limit_error_is_preserved(monkeypatch):
    monkeypatch.setattr(claude_backend.shutil, "which", lambda name: "/usr/bin/claude")
    assert (
        "limit"
        in claude_backend.reply(
            "task", system="s", runner=runner(error="Usage limit reached")
        )[1]
    )
