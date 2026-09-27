"""Claude through Sir's Pro subscription: headless Claude Code (`claude -p`).

No API key: the logged-in CLI bills the claude.ai subscription. Two uses:
the text-chat fallback when Gemini's free text quota is spent (cheapest
model, Haiku) and background research projects (cheapest Sonnet).

Isolation: a neutral cwd (no repo CLAUDE.md auto-discovery) plus
`--setting-sources local` keeps Sir's personal ~/.claude/CLAUDE.md out of
Jarvis's prompts (verified 2026-09-27: without it, both models quoted the
owner's global instructions back). NEVER pass `--bare`: it ignores the
OAuth login and demands ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

CHAT_MODEL = os.environ.get("JARVIS_CLAUDE_CHAT_MODEL", "claude-haiku-4-5")
RESEARCH_MODEL = os.environ.get("JARVIS_CLAUDE_RESEARCH_MODEL", "claude-sonnet-5")


def enabled() -> bool:
    """Kill switch: JARVIS_CLAUDE=0 disables every Claude call."""
    return os.environ.get("JARVIS_CLAUDE", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def claude_cwd() -> Path:
    """Neutral working dir so no project CLAUDE.md is auto-loaded."""
    home = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
    path = home / "claude-cwd"
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_argv(claude: str, *, model: str, system: str, tools: str = "") -> list[str]:
    """argv for one headless turn. Pure. Prompt goes on stdin.

    Tools must be both enabled (--tools) and pre-approved (--allowedTools):
    headless runs can't ask, and `--setting-sources local` drops Sir's saved
    permission rules, so unapproved web tools were silently denied (the
    first live report said "unverified, re-run once web access is up").
    """
    approve = ["--allowedTools", tools] if tools else []
    return [
        claude,
        *approve,
        "-p",
        "--model",
        model,
        "--tools",
        tools,
        "--no-session-persistence",
        "--output-format",
        "text",
        "--setting-sources",
        "local",
        "--system-prompt",
        system,
    ]


def claude_reply(
    prompt: str,
    *,
    model: str,
    system: str,
    tools: str = "",
    timeout: float = 60.0,
    runner=None,
    which=shutil.which,
) -> tuple[str, str | None]:
    """One headless Claude turn. Returns (reply, warning). Never raises."""
    if not enabled():
        return "", "Claude disabled (JARVIS_CLAUDE=0)"
    claude = which("claude")
    if not claude:
        return "", "claude CLI not installed"
    argv = build_argv(claude, model=model, system=system, tools=tools)
    run = runner or subprocess.run
    try:
        proc = run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(claude_cwd()),
        )
    except subprocess.TimeoutExpired:
        return "", f"Claude timed out after {int(timeout)}s"
    except OSError as exc:
        return "", f"Claude failed to start: {exc}"[:200]
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        return "", f"Claude exited {proc.returncode}: {(err[-1] if err else '')}"[:200]
    reply = (proc.stdout or "").strip()
    if not reply:
        return "", "Claude returned nothing"
    return reply, None
