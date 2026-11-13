"""Perplexity Decider v1.3 free, with v1.2 fallback, via the OpenRouter Decisions API.

The historical module name and JARVIS_MERCURY kill switch remain compatible
with existing callers. The Perplexity models handle email urgency,
phishing and the calendar extraction gate; it does not generate text.
JARVIS_DECIDE_MODEL and JARVIS_DECIDE_FALLBACK_MODEL override the defaults.
Both models share one request deadline. There is no automatic routing
to Jev, Flash or the unavailable Mercury free model.

JARVIS_DECIDE_API_KEY in env or $JARVIS_HOME/keys.env pins a dedicated
OpenRouter key. Without it, the existing general OpenRouter keys are used.
A key/model pair returning 402/429 rests for COOLDOWN_S; callers retain
ChatGPT fallback after both decision models fail.

Stdlib only. Fail-soft like openrouter_chat: functions return (answers,
warning) and never raise. The key is never logged or printed.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request

import openrouter_chat

DEFAULT_MODEL = "perplexity/pplx-decider-v1.3:free"
FALLBACK_MODEL = os.environ.get(
    "JARVIS_DECIDE_FALLBACK_MODEL", "perplexity/pplx-decider-v1.2"
).strip()
MODEL = os.environ.get("JARVIS_DECIDE_MODEL", "").strip() or DEFAULT_MODEL
URL = "https://openrouter.ai/api/alpha/decisions"
ENV = "JARVIS_MERCURY"
_OFF = ("0", "false", "off", "no")
#: Seconds a key rests after 402/429 before it is tried again.
COOLDOWN_S = 300.0
_lock = threading.Lock()
_rest_until: dict[tuple[str, str], float] = {}
_turn = itertools.count()


def api_keys(environ=None) -> list[str]:
    """Dedicated decision key, else legacy OpenRouter keys. Never logged."""
    env = os.environ if environ is None else environ
    dedicated = (env.get("JARVIS_DECIDE_API_KEY") or "").strip()
    file_keys = {}
    if not dedicated:
        try:
            file_keys = openrouter_chat._parse_keys_file(
                openrouter_chat._jarvis_home(env) / "keys.env"
            )
            dedicated = (file_keys.get("JARVIS_DECIDE_API_KEY") or "").strip()
        except OSError:
            pass
    if dedicated:
        return [dedicated]
    keys: list[str] = []
    primary = openrouter_chat.api_key()
    second = (env.get("OPENROUTER_API_KEY_2") or "").strip()
    if not second:
        second = (file_keys.get("OPENROUTER_API_KEY_2") or "").strip()
    for key in (primary, second):
        if key and key not in keys:
            keys.append(key)
    return keys


def _order(keys: list[str], now: float, model: str) -> list[str]:
    """Alternate the start key, omitting keys still in quota cooldown."""
    if not keys:
        return []
    start = next(_turn) % len(keys)
    ordered = keys[start:] + keys[:start]
    with _lock:
        return [k for k in ordered if _rest_until.get((model, k), 0.0) <= now]


def enabled() -> bool:
    """Legacy kill switch: JARVIS_MERCURY=0 skips decision-model calls."""
    return os.environ.get(ENV, "1").strip().lower() not in _OFF


def _post(
    key: str, body: bytes, timeout: float, opener
) -> tuple[bytes | None, str | None]:
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
            return None, f"Decision model quota spent (HTTP {exc.code})"
        return None, f"Decision model unavailable: HTTP {exc.code}"
    except Exception as exc:
        return None, f"Decision model unavailable: {type(exc).__name__}"[:200]


def decide(
    state, questions: dict, *, timeout: float = 8.0, opener=None
) -> tuple[dict | None, str | None]:
    """One Decisions call -> (answers, None) or (None, warning). Never raises.

    `state` is untrusted data (str, list or dict); `questions` maps a name to a
    typed question dict. Short timeout: callers have a slower fallback.
    """
    if not enabled():
        return None, f"Decision model disabled ({ENV}=0)"
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        return None, "Invalid decision model timeout"
    deadline = time.monotonic() + timeout
    keys = api_keys()
    if not keys:
        return None, "Decision model unavailable: no OpenRouter key"
    models = list(dict.fromkeys(m for m in (MODEL, FALLBACK_MODEL) if m))
    try:
        bodies = [
            json.dumps(
                {"model": model, "state": state, "questions": questions}
            ).encode()
            for model in models
        ]
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return None, "Decision model received invalid input"
    warning = "Decision model quota spent (keys cooling down)"
    for model, body in zip(models, bodies):
        for key in _order(keys, time.monotonic(), model):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, "Decision model timed out"
            raw, warning = _post(key, body, remaining, opener)
            # A free-model quota must not suppress the paid fallback.
            if "quota spent" in (warning or ""):
                with _lock:
                    _rest_until[(model, key)] = time.monotonic() + COOLDOWN_S
            if time.monotonic() > deadline:
                return None, "Decision model timed out"
            if raw is None:
                continue
            try:
                data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                answers = data["answers"]
            except (
                ValueError,
                UnicodeDecodeError,
                KeyError,
                TypeError,
                AttributeError,
            ):
                warning = "Decision model returned a bad response"
                continue
            if isinstance(answers, dict):
                return answers, None
            warning = "Decision model returned a bad response"
    return None, warning


def yes_prob(answers: dict, name: str) -> float | None:
    """Probability of a noul (yes/no) answer, or None when absent. Pure."""
    entry = answers.get(name) if isinstance(answers, dict) else None
    value = entry.get("noul") if isinstance(entry, dict) else None
    return float(value) if isinstance(value, (int, float)) else None
