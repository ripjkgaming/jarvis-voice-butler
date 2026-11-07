"""Subscription-authenticated, text-only Claude worker with no tools or MCP."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile


def reply(task: str, *, system: str, timeout: float = 180, runner=None):
    if os.environ.get("JARVIS_CLAUDE", "1").lower() in ("0", "false", "off", "no"):
        return "", "Claude unavailable: disabled"
    binary = shutil.which("claude")
    if not binary:
        return "", "Claude unavailable: CLI not installed"
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("ANTHROPIC_", "CLAUDE_")) and k != "CLAUDECODE"
    }
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    argv = [
        binary,
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--restricted",
        "--tools",
        "",
        "--disable-slash-commands",
        "--setting-sources",
        "",
        "--settings",
        json.dumps({"disableAllHooks": True, "enabledPlugins": {}}),
        "--mcp-config",
        '{"mcpServers":{}}',
        "--strict-mcp-config",
        "--no-session-persistence",
        "--permission-mode",
        "dontAsk",
        "--model",
        os.environ.get("JARVIS_CLAUDE_MODEL", "sonnet"),
        "--system-prompt",
        system
        + "\nThis is an isolated text-only Jarvis worker. Use only the supplied task. No tools, filesystem access, external actions or delegation. Never claim to have changed files or sent anything.",
    ]
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-claude-backend-") as cwd:
            proc = (runner or subprocess.run)(
                argv,
                input=task,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env=env,
            )
    except subprocess.TimeoutExpired:
        return "", "Claude timed out"
    except OSError:
        return "", "Claude unavailable: could not start CLI"
    initialized = False
    result = None
    try:
        for line in proc.stdout.splitlines():
            record = json.loads(line)
            if record.get("type") == "system" and record.get("subtype") == "init":
                if record.get("tools") != [] or record.get("mcp_servers") != []:
                    return "", "Claude isolation verification failed"
                initialized = True
            if record.get("type") == "assistant" and any(
                item.get("type") == "tool_use"
                for item in record.get("message", {}).get("content", [])
                if isinstance(item, dict)
            ):
                return "", "Claude isolation verification failed"
            if record.get("type") == "result":
                result = record
        if result and result.get("is_error"):
            error = str(
                result.get("result")
                or "; ".join(str(e) for e in result.get("errors", []))
                or result.get("subtype")
                or "backend error"
            )
            return "", "Claude failed: " + error[:200]
        if (
            proc.returncode
            or not initialized
            or not result
            or not isinstance(result.get("result"), str)
            or not result["result"].strip()
        ):
            return "", "Claude failed: no verified completed answer"
        return result["result"].strip(), None
    except (ValueError, TypeError, AttributeError):
        return "", "Claude failed: invalid worker response"
