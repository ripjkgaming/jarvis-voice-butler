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

import json
import os
import shutil
import subprocess
import threading
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


def build_argv(
    claude: str, *, model: str, system: str, tools: str = "", stream: bool = False
) -> list[str]:
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
        *(["stream-json", "--verbose"] if stream else ["text"]),
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


def stream_event(line: str) -> list[dict]:
    """One stream-json line -> progress events. Pure.

    {"kind": "search", "detail": query} / {"kind": "fetch", "detail": url}
    per web tool call, {"kind": "writing"} when prose starts, and
    {"kind": "result", "text", "error"} at the end.
    """
    try:
        d = json.loads(line)
    except (ValueError, TypeError):
        return []
    if not isinstance(d, dict):
        return []
    if d.get("type") == "result":
        return [
            {
                "kind": "result",
                "text": str(d.get("result") or ""),
                "error": bool(d.get("is_error")) or d.get("subtype") != "success",
            }
        ]
    if d.get("type") != "assistant":
        return []
    out = []
    for block in (d.get("message") or {}).get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            args = block.get("input") or {}
            if block.get("name") == "WebSearch":
                out.append({"kind": "search", "detail": str(args.get("query", ""))[:120]})
            elif block.get("name") == "WebFetch":
                out.append({"kind": "fetch", "detail": str(args.get("url", ""))[:200]})
        elif block.get("type") == "text" and str(block.get("text", "")).strip():
            out.append({"kind": "writing"})
    return out


def claude_stream(
    prompt: str,
    *,
    model: str,
    system: str,
    tools: str = "",
    timeout: float = 600.0,
    on_event=None,
    popen=None,
    which=shutil.which,
) -> tuple[str, str | None]:
    """claude_reply with live progress: on_event(ev) per stream_event.

    Same contract (reply, warning), never raises. A watchdog kills the
    run at `timeout`.
    """
    if not enabled():
        return "", "Claude disabled (JARVIS_CLAUDE=0)"
    claude = which("claude")
    if not claude:
        return "", "claude CLI not installed"
    argv = build_argv(claude, model=model, system=system, tools=tools, stream=True)
    try:
        proc = (popen or subprocess.Popen)(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(claude_cwd()),
        )
    except OSError as exc:
        return "", f"Claude failed to start: {exc}"[:200]
    timed_out = threading.Event()

    def _kill() -> None:
        timed_out.set()
        try:
            proc.kill()
        except OSError:
            pass

    timer = threading.Timer(timeout, _kill)
    timer.daemon = True
    timer.start()
    result: dict | None = None
    try:
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except OSError:
            pass
        for line in proc.stdout:
            for ev in stream_event(line):
                if ev["kind"] == "result":
                    result = ev
                if on_event is not None:
                    try:
                        on_event(ev)
                    except Exception:
                        pass
        proc.wait()
    finally:
        timer.cancel()
    if timed_out.is_set():
        return "", f"Claude timed out after {int(timeout)}s"
    if result is None or result["error"] or not result["text"].strip():
        err = ""
        try:
            err = (proc.stderr.read() or "").strip().splitlines()[-1]
        except Exception:
            pass
        if result is not None and result["error"]:
            return "", f"Claude failed: {result['text'] or err}"[:200]
        return "", f"Claude exited {proc.returncode}: {err}"[:200] if proc.returncode else (
            "Claude returned nothing"
        )
    return result["text"].strip(), None
