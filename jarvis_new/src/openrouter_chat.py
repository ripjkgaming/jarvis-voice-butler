"""Ling 3.0 Flash via OpenRouter: text-chat fallback when Gemini quota is spent.

Stdlib only (urllib). Fail-soft like claude_cli: chat_reply returns
(reply, warning) and never raises. The key is never logged or printed.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

MODEL = os.environ.get("JARVIS_CHAT_MODEL", "inclusionai/ling-3.0-flash-sante:free")
URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_KILL_VALUES = ("0", "false", "off", "no")


def strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks. Pure."""
    return _THINK.sub("", text or "")


def _jarvis_home(environ=None) -> Path:
    env = environ if environ is not None else os.environ
    home = (env.get("JARVIS_HOME") or "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def _parse_keys_file(path: Path) -> dict:
    """Parse KEY=VALUE lines (optional `export`, optional quotes). Pure-ish."""
    out: dict = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        name, sep, value = line.partition("=")
        if not sep:
            continue
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if name:
            out[name] = value
    return out


def api_key(
    *,
    keys_path: str | Path | None = None,
    auth_path: str | Path | None = None,
    environ=None,
) -> str:
    """OpenRouter key: env, then $JARVIS_HOME/keys.env, then opencode auth.

    Never logs or prints the key. Paths are injectable for tests.
    """
    env = environ if environ is not None else os.environ
    key = (env.get("OPENROUTER_API_KEY") or "").strip()
    if key:
        return key
    try:
        kp = (
            Path(keys_path) if keys_path is not None else _jarvis_home(env) / "keys.env"
        )
        key = (_parse_keys_file(kp).get("OPENROUTER_API_KEY") or "").strip()
        if key:
            return key
    except OSError:
        pass
    try:
        ap = (
            Path(auth_path)
            if auth_path is not None
            else Path.home() / ".local" / "share" / "opencode" / "auth.json"
        )
        data = json.loads(ap.read_text())
        entry = (data.get("openrouter") or {}) if isinstance(data, dict) else {}
        key = (entry.get("key") or "").strip() if isinstance(entry, dict) else ""
        if key:
            return key
    except (OSError, ValueError, AttributeError):
        pass
    return ""


def chat_reply(
    prompt: str,
    *,
    system: str,
    model: str = MODEL,
    timeout: float = 45.0,
    opener=None,
) -> tuple[str, str | None]:
    """One OpenRouter chat turn. Returns (reply, warning). Never raises."""
    if (
        os.environ.get("JARVIS_CHAT_FALLBACK", "1") or ""
    ).strip().lower() in _KILL_VALUES:
        return "", "chat fallback disabled"
    key = api_key()
    if not key:
        return "", "chat unavailable: no OpenRouter key"
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/jarvis-voice-butler",
            "X-Title": "Jarvis",
        },
        method="POST",
    )
    open_fn = opener or urllib.request.urlopen
    try:
        resp = open_fn(req, timeout=timeout)
        try:
            raw = resp.read()
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return "", "chat unavailable: rate limited (429)"
        return "", f"chat unavailable: HTTP {exc.code}"
    except Exception as exc:
        text = f"{type(exc).__name__} {exc}".lower()
        if (
            isinstance(exc, TimeoutError)
            or "timed out" in text
            or "timeout" in text
            or "deadline exceeded" in text
        ):
            return "", f"chat timed out after {int(timeout)}s"
        return "", f"chat unavailable: {exc}"[:200]
    try:
        data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        content = data["choices"][0]["message"]["content"]
    except (
        ValueError,
        UnicodeDecodeError,
        KeyError,
        IndexError,
        TypeError,
        AttributeError,
    ):
        return "", "chat unavailable: bad response"
    reply = strip_thinking(
        content if isinstance(content, str) else str(content)
    ).strip()
    if not reply:
        return "", "chat returned nothing"
    return reply, None
