"""The ChatGPT headless protocol, isolation and bounded reasoning."""

import io
import json
import subprocess
import sys
import time

import pytest

import chatgpt_cli


def line(value):
    return json.dumps(value) + "\n"


def answer(text="Ready, Sir."):
    return line(
        {"type": "item.completed", "item": {"type": "agent_message", "text": text}}
    ) + line({"type": "turn.completed"})


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("JARVIS_CHATGPT", "1")


def test_subscription_isolation_and_medium_reasoning(enabled, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CODEX_API_KEY", "test-key")
    seen = {}

    def run(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return subprocess.CompletedProcess(argv, 0, answer(), "")

    assert chatgpt_cli.chatgpt_reply(
        "hello",
        model="gpt-6.1-sol",
        system="You are Jarvis.",
        runner=run,
        which=lambda _: "/bin/codex",
    ) == ("Ready, Sir.", None)
    argv = seen["argv"]
    assert argv[:3] == ["/bin/codex", "--ask-for-approval", "never"]
    assert "exec" in argv and "--json" in argv and argv[-1] == "-"
    assert "--ignore-user-config" in argv and "--ignore-rules" in argv
    assert "--ephemeral" in argv and "--skip-git-repo-check" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert 'model_reasoning_effort="medium"' in argv
    assert 'forced_login_method="chatgpt"' in argv
    assert "features.shell_tool=false" in argv
    assert "features.plugins=false" in argv
    assert "project_doc_max_bytes=0" in argv
    assert 'web_search="disabled"' in argv
    assert seen["input"] == "hello"
    assert seen["cwd"] == str(tmp_path / "chatgpt-cwd")
    assert "OPENAI_API_KEY" not in seen["env"] and "CODEX_API_KEY" not in seen["env"]


def test_tools_images_and_fast_reasoning():
    argv = chatgpt_cli.build_argv(
        "codex", model=chatgpt_cli.FAST_MODEL, system="s", images=["/tmp/photo.png"]
    )
    assert 'model_reasoning_effort="low"' in argv
    assert argv[argv.index("--image") + 1] == "/tmp/photo.png"
    assert "features.shell_tool=false" in argv
    read = chatgpt_cli.build_argv(
        "codex", model="gpt-6.1-sol", system="s", tools="Read"
    )
    assert "features.shell_tool=true" in read
    web = chatgpt_cli.build_argv(
        "codex", model="gpt-6.1-sol", system="s", tools="WebSearch,WebFetch"
    )
    assert 'web_search="live"' in web
    assert "features.shell_tool=false" in web


def test_cli_is_resolved_for_background_service_without_desktop_path(monkeypatch):
    monkeypatch.setattr(chatgpt_cli.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        chatgpt_cli.os,
        "access",
        lambda path, _: str(path) == "/usr/lib/chatgpt/resources/codex",
    )
    assert chatgpt_cli.codex_binary() == "/usr/lib/chatgpt/resources/codex"
    monkeypatch.setenv("JARVIS_CODEX_BIN", "/custom/codex")
    monkeypatch.setattr(
        chatgpt_cli.os, "access", lambda path, _: str(path) == "/custom/codex"
    )
    assert chatgpt_cli.codex_binary() == "/custom/codex"


def test_unknown_tools_and_reasoning_fail_soft(enabled):
    assert (
        "Unsupported"
        in chatgpt_cli.chatgpt_reply(
            "x", model="gpt-6.1-sol", system="s", tools="Bash", which=lambda _: "codex"
        )[1]
    )
    assert (
        "reasoning"
        in chatgpt_cli.chatgpt_reply(
            "x",
            model="gpt-6.1-sol",
            system="s",
            reasoning="ultra",
            which=lambda _: "codex",
        )[1]
    )


def test_completed_final_message_only(enabled):
    output = line(
        {
            "type": "item.completed",
            "item": {"type": "reasoning", "text": "private thought"},
        }
    )
    output += line(
        {
            "type": "item.completed",
            "item": {"type": "command_execution", "aggregated_output": "noise"},
        }
    )
    output += line(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "phase": "commentary", "text": "Working"},
        }
    )
    output += answer("[1, 2]")
    reply, warning = chatgpt_cli.chatgpt_reply(
        "x",
        model="m",
        system="s",
        which=lambda _: "codex",
        runner=lambda *a, **k: subprocess.CompletedProcess(a, 0, output, ""),
    )
    assert (reply, warning) == ("[1, 2]", None)


@pytest.mark.parametrize(
    "out,rc,part",
    [
        ("plain unexpected stdout", 0, "returned nothing"),
        (
            line(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "partial"},
                }
            ),
            0,
            "incomplete",
        ),
        (
            answer("partial")
            + line({"type": "turn.failed", "error": {"message": "rate limit"}}),
            0,
            "rate limit",
        ),
        ("", 1, "exited 1"),
    ],
)
def test_failures_do_not_become_answers(enabled, out, rc, part):
    reply, warning = chatgpt_cli.chatgpt_reply(
        "x",
        model="m",
        system="s",
        which=lambda _: "codex",
        runner=lambda *a, **k: subprocess.CompletedProcess(a, rc, out, "oops"),
    )
    assert reply == "" and part in warning


def test_missing_disabled_timeout_and_start_failure(enabled, monkeypatch):
    opts = {"model": "m", "system": "s", "which": lambda _: "codex"}

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("codex", 2)

    def missing(*a, **k):
        raise OSError("missing executable")

    assert "timed out" in chatgpt_cli.chatgpt_reply("x", runner=timeout, **opts)[1]
    assert (
        "failed to start" in chatgpt_cli.chatgpt_reply("x", runner=missing, **opts)[1]
    )
    assert (
        "not installed"
        in chatgpt_cli.chatgpt_reply("x", model="m", system="s", which=lambda _: None)[
            1
        ]
    )
    monkeypatch.setenv("JARVIS_CHATGPT", "0")
    assert "disabled" in chatgpt_cli.chatgpt_reply("x", **opts)[1]


class StreamProcess:
    def __init__(self, output, code=0):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(output)
        self.stderr = io.StringIO("")
        self.returncode = code

    def wait(self):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_stream_progress_and_result(enabled):
    output = line(
        {
            "type": "item.started",
            "item": {
                "type": "web_search",
                "action": {"type": "search", "query": "jarvis"},
            },
        }
    )
    output += line(
        {
            "type": "item.started",
            "item": {
                "type": "web_search",
                "action": {"type": "open_page", "url": "https://example.com"},
            },
        }
    )
    output += answer("report")
    events = []
    result = chatgpt_cli.chatgpt_stream(
        "p",
        model="m",
        system="s",
        tools="WebSearch",
        which=lambda _: "codex",
        popen=lambda *a, **k: StreamProcess(output),
        on_event=events.append,
    )
    assert result == ("report", None)
    assert events == [
        {"kind": "search", "detail": "jarvis"},
        {"kind": "fetch", "detail": "https://example.com"},
        {"kind": "writing"},
        {"kind": "result", "text": "report", "error": False},
    ]


def test_stream_failure_and_callback_failure(enabled):
    def bad_callback(_):
        raise ValueError("UI went away")

    opts = {
        "model": "m",
        "system": "s",
        "which": lambda _: "codex",
        "on_event": bad_callback,
    }
    assert chatgpt_cli.chatgpt_stream(
        "p", popen=lambda *a, **k: StreamProcess(answer()), **opts
    ) == ("Ready, Sir.", None)
    failure = line({"type": "turn.failed", "error": {"message": "quota exhausted"}})
    reply, warning = chatgpt_cli.chatgpt_stream(
        "p", popen=lambda *a, **k: StreamProcess(failure), **opts
    )
    assert reply == "" and "quota exhausted" in warning


def test_real_cli_web_progress_waits_for_tool_details():
    pending = {
        "type": "item.started",
        "item": {
            "id": "web-1",
            "type": "web_search",
            "query": "",
            "action": {"type": "other"},
        },
    }
    done = {
        "type": "item.completed",
        "item": {
            "id": "web-1",
            "type": "web_search",
            "action": {"type": "open_page", "url": "https://example.com/report.pdf"},
        },
    }
    assert chatgpt_cli.stream_event(line(pending)) == []
    assert chatgpt_cli.stream_event(line(done)) == [
        {"kind": "pdf", "detail": "https://example.com/report.pdf"}
    ]
    comment = {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "I'll browse this now."},
    }
    assert chatgpt_cli.stream_event(line(comment)) == []


def test_watchdog_kills_stalled_worker(enabled):
    workers = []

    def spawn(argv, **kw):
        worker = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"], **kw
        )
        workers.append(worker)
        return worker

    start = time.monotonic()
    reply, warning = chatgpt_cli.chatgpt_stream(
        "x", model="m", system="s", timeout=0.15, popen=spawn, which=lambda _: "codex"
    )
    assert reply == "" and "timed out" in warning
    assert workers[0].poll() is not None
    assert time.monotonic() - start < 2
