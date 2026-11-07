"""Offline regressions for provider choice, replay avoidance, and diagnostics."""

import json
import subprocess

import pytest

import backend_pool
import claude_backend
import mercury_decide
import request_router


@pytest.mark.parametrize(
    "warning",
    [
        "Claude failed: upstream unavailable after processing",
        "ChatGPT failed: account disabled after processing",
        "Claude failed: could not parse quota report",
    ],
)
def test_error_keywords_alone_do_not_authorize_replay(warning):
    assert backend_pool.reply(
        "task",
        system="s",
        providers={
            "chatgpt": lambda *a, **kw: ("", warning),
            "claude": lambda *a, **kw: pytest.fail("ambiguous execution replayed"),
        },
    ) == ("", warning)


@pytest.fixture(autouse=True)
def fresh_pool(monkeypatch):
    backend_pool._rest_until.clear()
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_MERCURY", "1")
    monkeypatch.delenv("JARVIS_BACKEND_PROVIDER", raising=False)
    yield
    backend_pool._rest_until.clear()


def test_custom_provider_mapping():
    assert backend_pool.reply(
        "task", system="s", providers={"local": lambda *a, **kw: ("answer", None)}
    ) == ("answer", None)


def test_unknown_explicit_provider_never_substitutes():
    result, warning = backend_pool.reply(
        "task",
        system="s",
        preferred="missing",
        allow_fallback=False,
        providers={"chatgpt": lambda *a, **kw: pytest.fail("wrong provider")},
    )
    assert not result and "Unknown" in warning


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "10"])
def test_invalid_budget_never_dispatches(timeout):
    assert backend_pool.reply(
        "task",
        system="s",
        timeout=timeout,
        providers={
            "chatgpt": lambda *a, **kw: pytest.fail("invalid budget dispatched")
        },
    ) == ("", "Invalid backend timeout")


@pytest.mark.parametrize("response", [None, "answer", (42, None), ("", {}), ("",)])
def test_malformed_provider_response_is_not_replayed(response):
    assert backend_pool.reply(
        "task",
        system="s",
        providers={
            "chatgpt": lambda *a, **kw: response,
            "claude": lambda *a, **kw: pytest.fail("replayed"),
        },
    ) == ("", "Backend returned an invalid response")


def test_exception_contained_without_replay_or_secret_leak():
    def broken(*a, **kw):
        raise RuntimeError("secret credential")

    assert backend_pool.reply(
        "task",
        system="s",
        providers={
            "chatgpt": broken,
            "claude": lambda *a, **kw: pytest.fail("replayed"),
        },
    ) == ("", "Backend failed unexpectedly")


def test_cooldown_snapshot_and_expiry(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(backend_pool.time, "monotonic", lambda: now[0])
    providers = {"chatgpt": lambda *a, **kw: ("", "HTTP 429")}
    backend_pool.reply("task", system="s", providers=providers)
    assert backend_pool.status()["chatgpt"]["retry_after_s"] == 300
    snapshot = backend_pool.status()
    snapshot["chatgpt"]["retry_after_s"] = 999
    assert backend_pool.status()["chatgpt"]["retry_after_s"] == 300
    now[0] += 301
    assert backend_pool.status()["chatgpt"] == {"state": "ready", "retry_after_s": 0}


def test_failover_uses_remaining_budget(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(backend_pool.time, "monotonic", lambda: now[0])
    seen = []

    def unavailable(*a, **kw):
        seen.append(kw["timeout"])
        now[0] += 4
        return "", "Backend unavailable: CLI not installed"

    def ready(*a, **kw):
        seen.append(kw["timeout"])
        return "answer", None

    assert backend_pool.reply(
        "task",
        system="s",
        timeout=10,
        providers={"chatgpt": unavailable, "claude": ready},
    )[0]
    assert seen == [10, 6]


def test_diagnostics_are_offline_and_do_not_expose_configuration_secrets(monkeypatch):
    import backend_diagnostics

    monkeypatch.setattr(
        backend_diagnostics.shutil, "which", lambda name: "/fake/" + name
    )
    monkeypatch.setattr(
        backend_diagnostics.chatgpt_cli, "codex_binary", lambda: "/fake/codex"
    )
    monkeypatch.setattr(
        backend_pool, "reply", lambda *a, **kw: pytest.fail("model call")
    )
    monkeypatch.setenv("JARVIS_BACKEND_PROVIDER", "secret-invalid-value")
    monkeypatch.setenv("JARVIS_CLAUDE", "0")
    report = backend_diagnostics.report()
    assert report["offline"] is True
    assert report["preferred_provider"] == "unknown"
    assert report["providers"]["claude"]["enabled"] is False
    assert report["providers"]["chatgpt"]["cli_installed"] is True
    assert "secret-invalid-value" not in json.dumps(report)


def test_provider_exhausting_deadline_is_not_replayed(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(backend_pool.time, "monotonic", lambda: now[0])

    def late(*a, **kw):
        now[0] = 11
        return "", "Backend unavailable"

    assert backend_pool.reply(
        "task",
        system="s",
        timeout=10,
        providers={
            "chatgpt": late,
            "claude": lambda *a, **kw: pytest.fail("replayed"),
        },
    ) == ("", "Backend timed out")


@pytest.mark.parametrize(
    "text,route,provider,fallback",
    [
        ("Please use only Claude to make a plan", "backend", "claude", False),
        ("Ask ChatGPT to explain it", "backend", "chatgpt", True),
        ("Answer yourself: explain photosynthesis", "direct", "auto", True),
        ("Do not delegate this; explain it", "direct", "auto", True),
        ("Don't ask Claude, just explain it yourself", "direct", "auto", True),
    ],
)
def test_explicit_intent_needs_no_classifier(
    monkeypatch, text, route, provider, fallback
):
    monkeypatch.setattr(
        mercury_decide, "decide", lambda *a, **kw: pytest.fail("classified")
    )
    got = request_router.decide(text)
    assert (got["route"], got["provider"], got["allow_fallback"]) == (
        route,
        provider,
        fallback,
    )


@pytest.mark.parametrize("text", [None, 42, [], "x" * 20001])
def test_invalid_routing_input_does_not_call_classifier(monkeypatch, text):
    monkeypatch.setattr(
        mercury_decide, "decide", lambda *a, **kw: pytest.fail("classified")
    )
    assert request_router.decide(text)["route"] == "direct"


def test_incomplete_claude_execution_is_not_unavailable(monkeypatch):
    monkeypatch.setattr(claude_backend.shutil, "which", lambda _: "/fake/claude")
    proc = subprocess.CompletedProcess(
        [],
        0,
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "tools": [],
                "mcp_servers": [],
            }
        ),
        "",
    )
    _, warning = claude_backend.reply("task", system="s", runner=lambda *a, **kw: proc)
    assert "unavailable" not in warning.lower()


@pytest.mark.asyncio
async def test_disabled_classifier_preserves_exclusive_provider(monkeypatch):
    import backend_model
    from system.workspace_tools import WorkspaceTools

    monkeypatch.setattr(request_router, "enabled", lambda: False)
    seen, jobs = [], []
    monkeypatch.setattr(
        backend_model,
        "backend_think",
        lambda *a, **kw: (
            seen.append(kw) or "answer",
            None,
        ),
    )
    tools = WorkspaceTools()
    monkeypatch.setattr(
        tools, "_in_background", lambda ctx, job, **kw: jobs.append(job)
    )
    await WorkspaceTools.ask_backend(tools, None, task="Use only Claude to make a plan")
    await jobs[0]()
    assert seen[0]["preferred"] == "claude"
    assert seen[0]["allow_fallback"] is False
