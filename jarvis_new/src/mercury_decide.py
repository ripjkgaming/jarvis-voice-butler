"""Mercury Decide (OpenRouter Decisions API): fast typed yes/no and choice calls.

inception/mercury-decide:free is a classifier, not a chat model. It answers
typed questions (noul = yes/no probability, choice, score) in ~0.6s with no
generation, so it replaces a whole ChatGPT/codex spawn for triage decisions.

Key limits (this account's OpenRouter key, read from GET /api/v1/key 2026-10-25):
  * free-model quota: 1000 requests/day (free_model_daily_requests); a 429 or
    402 means spent, so callers must fall back rather than retry.
  * spend cap: $1/month on the key; the :free variant costs $0 per call.
  * no hard per-second limit on the key itself.
Second key (OPENROUTER_API_KEY_2 in env or $JARVIS_HOME/keys.env, read the same
way; verified 2026-10-25): $0.10/month spend cap, its own 1000 free requests/day.
Calls alternate between the keys, and a key that returns 402/429 sits out for
COOLDOWN_S while the other serves, so the free allowance doubles to ~2000/day.
Re-check with:  curl -H "Authorization: Bearer $KEY" https://openrouter.ai/api/v1/key

Stdlib only. Fail-soft like openrouter_chat: functions return (answers,
warning) and never raise. The key is never logged or printed.
"""

from __future__ import annotations

import contextlib
import json
import itertools
import os
import threading
import time
import urllib.error
import urllib.request

import openrouter_chat

MODEL = os.environ.get("JARVIS_DECIDE_MODEL", "inception/mercury-decide:free")
URL = "https://openrouter.ai/api/alpha/decisions"
ENV = "JARVIS_MERCURY"
_OFF = ("0", "false", "off", "no")
#: Seconds a key rests after 402/429 before it is tried again.
COOLDOWN_S = 300.0
_lock = threading.Lock()
_rest_until: dict[str, float] = {}
_turn = itertools.count()


def api_keys(environ=None) -> list[str]:
    """OPENROUTER_API_KEY then OPENROUTER_API_KEY_2, de-duplicated. Never logged."""
    env = os.environ if environ is None else environ
    keys: list[str] = []
    primary = openrouter_chat.api_key()
    second = (env.get("OPENROUTER_API_KEY_2") or "").strip()
    if not second:
        try:
            second = (
                openrouter_chat._parse_keys_file(
                    openrouter_chat._jarvis_home(env) / "keys.env"
                ).get("OPENROUTER_API_KEY_2")
                or ""
            ).strip()
        except OSError:
            second = ""
    for key in (primary, second):
        if key and key not in keys:
            keys.append(key)
    return keys


def _order(keys: list[str], now: float) -> list[str]:
    """Alternate the start key; rested keys go last. Pure given state."""
    if not keys:
        return []
    start = next(_turn) % len(keys)
    ordered = keys[start:] + keys[:start]
    with _lock:
        ready = [k for k in ordered if _rest_until.get(k, 0.0) <= now]
        resting = [k for k in ordered if k not in ready]
    return ready + resting


def enabled() -> bool:
    """Kill switch: JARVIS_MERCURY=0 skips Mercury everywhere."""
    return os.environ.get(ENV, "1").strip().lower() not in _OFF


def _post(key: str, body: bytes, timeout: float, opener) -> tuple[bytes | None, str | None]:
    """One request with one key -> (raw, None) or (None, warning). Never raises."""
    req = urllib.request.Request(
        URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/jarvis-voice-butler",
            "X-Title": "Jarvis",
        },
        method="POST",
    )
    try:
        resp = (opener or urllib.request.urlopen)(req, timeout=timeout)
        try:
            return resp.read(), None
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()
    except urllib.error.HTTPError as exc:
        if exc.code in (402, 429):
            return None, f"Mercury quota spent (HTTP {exc.code})"
        return None, f"Mercury unavailable: HTTP {exc.code}"
    except Exception as exc:
        return None, f"Mercury unavailable: {type(exc).__name__}"[:200]


def decide(
    state, questions: dict, *, timeout: float = 8.0, opener=None
) -> tuple[dict | None, str | None]:
    """One Decisions call -> (answers, None) or (None, warning). Never raises.

    `state` is untrusted data (str, list or dict); `questions` maps a name to a
    typed question dict. Short timeout: callers have a slower fallback.
    """
    if not enabled():
        return None, f"Mercury disabled ({ENV}=0)"
    keys = api_keys()
    if not keys:
        return None, "Mercury unavailable: no OpenRouter key"
    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    warning = "Mercury unavailable"
    raw = None
    for key in _order(keys, time.monotonic()):
        raw, warning = _post(key, body, timeout, opener)
        if raw is not None:
            break
        if "quota spent" in (warning or ""):
            with _lock:
                _rest_until[key] = time.monotonic() + COOLDOWN_S
    if raw is None:
        return None, warning
    try:
        data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        answers = data["answers"]
    except (ValueError, UnicodeDecodeError, KeyError, TypeError, AttributeError):
        return None, "Mercury returned a bad response"
    if not isinstance(answers, dict):
        return None, "Mercury returned a bad response"
    return answers, None


def yes_prob(answers: dict, name: str) -> float | None:
    """Probability of a noul (yes/no) answer, or None when absent. Pure."""
    entry = answers.get(name) if isinstance(answers, dict) else None
    value = entry.get("noul") if isinstance(entry, dict) else None
    return float(value) if isinstance(value, (int, float)) else None
