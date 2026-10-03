"""Headless ChatGPT through the signed-in Codex CLI, without an API key.

Sol at medium effort handles research and careful backend tasks. Luna at
low effort handles short summaries and extraction. Every call is ephemeral,
uses a neutral cwd and ignores personal config, skills, hooks and plugins.
Only completed assistant messages reach Jarvis; CLI logs and reasoning do not.
"""

from __future__ import annotations

import collections
import contextlib
import json
import os
import shutil
import signal
import subprocess
import threading
from pathlib import Path

BACKEND_MODEL = (
    os.environ.get("JARVIS_CHATGPT_BACKEND_MODEL", "").strip() or "gpt-6.1-sol"
)
RESEARCH_MODEL = (
    os.environ.get("JARVIS_CHATGPT_RESEARCH_MODEL", "").strip() or "gpt-6.1-sol"
)
FAST_MODEL = os.environ.get("JARVIS_CHATGPT_FAST_MODEL", "").strip() or "gpt-6-luna"
_TOOLS = {"Read", "WebSearch", "WebFetch"}
_REASONING = {"none", "low", "medium", "high", "xhigh", "max"}


def enabled() -> bool:
    """JARVIS_CHATGPT=0 disables all headless ChatGPT work."""
    return os.environ.get("JARVIS_CHATGPT", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def codex_binary() -> str | None:
    """Resolve the standalone or desktop-bundled CLI for narrow service PATHs."""
    override = os.environ.get("JARVIS_CODEX_BIN", "").strip()
    if override:
        path = str(Path(override).expanduser())
        return path if os.access(path, os.X_OK) else shutil.which(override)
    installed = shutil.which("codex")
    if installed:
        return installed
    for path in (
        "/usr/lib/chatgpt/resources/codex",
        "/usr/lib/codex/resources/codex",
        "/Applications/ChatGPT.app/Contents/Resources/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
    ):
        if os.access(path, os.X_OK):
            return path
    return None


def chatgpt_cwd() -> Path:
    """Neutral directory, separate from Jarvis and personal project files."""
    home = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
    path = home / "chatgpt-cwd"
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_argv(
    codex: str,
    *,
    model: str,
    system: str,
    tools: str = "",
    allowed: str = "",
    reasoning: str | None = None,
    images: list[str | Path] | tuple[str | Path, ...] = (),
    stream: bool = False,
) -> list[str]:
    """One isolated headless turn. Prompt goes on stdin; output is JSONL.

    Tool names describe the caller's required capabilities, not Claude flags.
    Read enables sandboxed read-only shell access; web tools enable native live
    search with shell disabled. Unsupported scopes fail rather than expanding
    permissions. Images are attached directly, so they do not require Read.
    """
    requested = {t.strip() for t in tools.split(",") if t.strip()}
    approved = (
        {t.strip() for t in allowed.split(",") if t.strip()} if allowed else requested
    )
    if not requested <= _TOOLS or not approved <= requested:
        raise ValueError(f"Unsupported headless tool scope: {allowed or tools}")
    effort = (
        reasoning
        or os.environ.get(
            "JARVIS_CHATGPT_FAST_REASONING"
            if model == FAST_MODEL
            else "JARVIS_CHATGPT_REASONING",
            "low" if model == FAST_MODEL else "medium",
        ).strip()
        or ("low" if model == FAST_MODEL else "medium")
    )
    if effort not in _REASONING:
        raise ValueError(f"Unsupported reasoning effort: {effort}")
    capabilities = requested & approved
    instructions = system + (
        "\nThis is an isolated Jarvis worker. Return the requested answer in the "
        "requested format. Do not load personal instructions or credentials, "
        "modify files, send messages, or take external actions."
    )
    if "Read" in capabilities:
        instructions += " Only read the input files named in the task; use local read-only tools for text/PDFs."
    config = {
        "model_provider": "openai",
        "forced_login_method": "chatgpt",
        "model_reasoning_effort": effort,
        "developer_instructions": instructions,
        "project_doc_max_bytes": 0,
        "web_search": "live"
        if capabilities & {"WebSearch", "WebFetch"}
        else "disabled",
        "features.shell_tool": "Read" in capabilities,
        "features.shell_snapshot": False,
        "features.apps": False,
        "features.plugins": False,
        "features.hooks": False,
        "features.multi_agent": False,
        "features.skill_search": False,
        "features.skip_host_skill_discovery": True,
        "features.browser_use": False,
        "features.computer_use": False,
    }
    argv = [
        codex,
        "--ask-for-approval",
        "never",
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--model",
        model,
        "--json",
        "--color",
        "never",
    ]
    for key, value in config.items():
        argv.extend(["-c", f"{key}={json.dumps(value, ensure_ascii=False)}"])
    for path in images:
        argv.extend(["--image", str(path)])
    return [*argv, "-"]


def _environment() -> dict[str, str]:
    # Reuse the CLI's saved ChatGPT login even when Jarvis has API credentials
    # for unrelated voice/vision providers in its environment.
    env = dict(os.environ)
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
        env.pop(key, None)
    return env


def _record(line: str) -> dict:
    try:
        value = json.loads(line)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def stream_event(line: str) -> list[dict]:
    """Codex JSONL line -> research progress. Pure; no reasoning/log output."""
    record = _record(line)
    if record.get("type") == "turn.completed":
        return [{"kind": "writing"}]
    item = record.get("item")
    if not isinstance(item, dict):
        return []
    if (
        record.get("type") not in {"item.started", "item.updated", "item.completed"}
        or item.get("type") != "web_search"
    ):
        return []
    action = item.get("action")
    action = action if isinstance(action, dict) else {}
    results = item.get("results") or []
    source = next(
        (
            result.get("url")
            for result in results
            if isinstance(result, dict) and result.get("url")
        ),
        "",
    )
    url = str(
        action.get("url")
        or item.get("url")
        or (source if action.get("type") in {"open_page", "find_in_page"} else "")
        or ""
    )
    if action.get("type") in {"open_page", "find_in_page"} or url:
        return [
            {
                "kind": "pdf" if ".pdf" in url or "/pdf/" in url else "fetch",
                "detail": url[:200],
            }
        ]
    queries = action.get("queries") or []
    query = str(
        action.get("query") or item.get("query") or (queries[0] if queries else "")
    )
    return [{"kind": "search", "detail": query[:120]}] if query else []


class _Answer:
    def __init__(self):
        self.text = ""
        self.complete = False
        self.error = ""

    def feed(self, line: str) -> None:
        record = _record(line)
        kind = record.get("type")
        item = record.get("item")
        if (
            kind == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and item.get("phase") != "commentary"
        ):
            self.text = str(item.get("text") or "").strip()
        elif kind == "turn.completed":
            self.complete = True
        elif kind in {"turn.failed", "error"}:
            error = (
                record.get("error") or record.get("message") or "headless turn failed"
            )
            self.error = (
                str(error.get("message") or error)
                if isinstance(error, dict)
                else str(error)
            )

    def result(self, code: int, stderr: str = "") -> tuple[str, str | None]:
        if self.error:
            return "", f"ChatGPT failed: {self.error}"[:200]
        if code:
            lines = stderr.strip().splitlines()
            return "", f"ChatGPT exited {code}: {lines[-1] if lines else ''}"[:200]
        if not self.text:
            return "", "ChatGPT returned nothing"
        if not self.complete:
            return "", "ChatGPT returned an incomplete turn"
        return self.text, None


def chatgpt_reply(
    prompt: str,
    *,
    model: str,
    system: str,
    tools: str = "",
    allowed: str = "",
    reasoning: str | None = None,
    images: list[str | Path] | tuple[str | Path, ...] = (),
    timeout: float = 60.0,
    runner=None,
    which=None,
) -> tuple[str, str | None]:
    """One headless answer, returning (reply, warning); never raises."""
    if runner is None:
        return chatgpt_stream(
            prompt,
            model=model,
            system=system,
            tools=tools,
            allowed=allowed,
            reasoning=reasoning,
            images=images,
            timeout=timeout,
            which=which,
        )
    if not enabled():
        return "", "ChatGPT disabled (JARVIS_CHATGPT=0)"
    codex = which("codex") if which is not None else codex_binary()
    if not codex:
        return "", "codex CLI not installed; install it and run codex login"
    try:
        argv = build_argv(
            codex,
            model=model,
            system=system,
            tools=tools,
            allowed=allowed,
            reasoning=reasoning,
            images=images,
        )
        proc = runner(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(chatgpt_cwd()),
            env=_environment(),
        )
    except subprocess.TimeoutExpired:
        return "", f"ChatGPT timed out after {int(timeout)}s"
    except ValueError as exc:
        return "", str(exc)[:200]
    except OSError as exc:
        return "", f"ChatGPT failed to start: {exc}"[:200]
    result = _Answer()
    for line in (proc.stdout or "").splitlines():
        result.feed(line)
    return result.result(proc.returncode, proc.stderr or "")


def chatgpt_stream(
    prompt: str,
    *,
    model: str,
    system: str,
    tools: str = "",
    allowed: str = "",
    reasoning: str | None = None,
    images: list[str | Path] | tuple[str | Path, ...] = (),
    timeout: float = 600.0,
    on_event=None,
    popen=None,
    which=None,
) -> tuple[str, str | None]:
    """Headless answer with progress, bounded runtime and drained stderr."""
    if not enabled():
        return "", "ChatGPT disabled (JARVIS_CHATGPT=0)"
    codex = which("codex") if which is not None else codex_binary()
    if not codex:
        return "", "codex CLI not installed; install it and run codex login"
    try:
        argv = build_argv(
            codex,
            model=model,
            system=system,
            tools=tools,
            allowed=allowed,
            reasoning=reasoning,
            images=images,
        )
        proc = (popen or subprocess.Popen)(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(chatgpt_cwd()),
            env=_environment(),
            start_new_session=True,
        )
    except ValueError as exc:
        return "", str(exc)[:200]
    except OSError as exc:
        return "", f"ChatGPT failed to start: {exc}"[:200]
    timed_out = threading.Event()
    errors: collections.deque[str] = collections.deque(maxlen=16)

    def emit(event):
        if on_event is not None:
            with contextlib.suppress(Exception):
                on_event(event)

    def kill():
        with contextlib.suppress(OSError):
            if popen is None and os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()

    def expire():
        timed_out.set()
        kill()

    def drain():
        with contextlib.suppress(OSError, ValueError):
            for line in proc.stderr:
                errors.append(line[-500:])

    stderr_reader = threading.Thread(target=drain, daemon=True)
    stderr_reader.start()
    timer = threading.Timer(timeout, expire)
    timer.daemon = True
    timer.start()
    result = _Answer()
    seen_progress = set()
    try:
        with contextlib.suppress(OSError):
            proc.stdin.write(prompt)
            proc.stdin.close()
        for line in proc.stdout:
            result.feed(line)
            for event in stream_event(line):
                item = _record(line).get("item")
                key = (
                    item.get("id") if isinstance(item, dict) else None,
                    event["kind"],
                    event.get("detail"),
                )
                if key[0] is not None and key in seen_progress:
                    continue
                seen_progress.add(key)
                emit(event)
        proc.wait()
    except OSError as exc:
        kill()
        return "", f"ChatGPT stream failed: {exc}"[:200]
    finally:
        timer.cancel()
        stderr_reader.join(timeout=0.5)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            with contextlib.suppress(OSError, ValueError):
                pipe.close()
    if timed_out.is_set():
        return "", f"ChatGPT timed out after {int(timeout)}s"
    reply, warning = result.result(proc.returncode, "".join(errors))
    emit({"kind": "result", "text": reply, "error": warning is not None})
    return reply, warning
