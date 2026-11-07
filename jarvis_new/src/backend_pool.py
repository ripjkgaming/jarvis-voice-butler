"""Choose a reasoning backend; react to real limit errors, never guessed quota."""

from __future__ import annotations

import math
import os
import threading
import time

import chatgpt_cli
import claude_backend

_lock = threading.Lock()
_rest_until: dict[str, float] = {}
COOLDOWN_S = 300


def status():
    """In-process cooldown state only; never queries a provider or its quota."""
    with _lock:
        now = time.monotonic()
        return {
            name: {
                "state": "cooldown" if until > now else "ready",
                "retry_after_s": max(0, math.ceil(until - now)),
            }
            for name, until in _rest_until.items()
        }


def _limit(warning):
    text = (warning or "").lower()
    return any(
        marker in text
        for marker in (
            "rate limit",
            "usage limit",
            "limit reached",
            "quota exceeded",
            "quota exhausted",
            "quota_exceeded",
            "insufficient_quota",
            "http 429",
            "credits depleted",
            "out of extra usage",
            "hit your limit",
            "rate_limit",
            "usage_limit",
        )
    )


def _unavailable(warning):
    # These adapter messages mean execution never started. A failure merely
    # mentioning "unavailable" or "disabled" is not sufficient evidence.
    text = (warning or "").lower()
    return text in {
        "claude unavailable: disabled",
        "claude unavailable: cli not installed",
        "claude unavailable: could not start cli",
        "backend unavailable: cli not installed",
        "chatgpt disabled (jarvis_chatgpt=0)",
        "codex cli not installed; install it and run codex login",
    }


def reply(
    task,
    *,
    system,
    timeout=180,
    preferred="auto",
    allow_fallback=True,
    providers=None,
    runner=None,
):
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        return "", "Invalid backend timeout"
    if providers is None:
        providers = {
            "chatgpt": lambda text, **kw: chatgpt_cli.chatgpt_reply(
                text, model=chatgpt_cli.BACKEND_MODEL, runner=runner, **kw
            ),
            "claude": claude_backend.reply,
        }
    if not providers:
        return "", "No backend is available"
    if preferred != "auto" and preferred not in providers:
        return "", "Unknown requested backend provider"
    first = (
        preferred
        if preferred != "auto"
        else os.environ.get("JARVIS_BACKEND_PROVIDER", "chatgpt")
    )
    if first not in providers:
        first = next(iter(providers))
    order = [first] + (
        [name for name in providers if name != first] if allow_fallback else []
    )
    with _lock:
        ready = [name for name in order if _rest_until.get(name, 0) <= time.monotonic()]
    if not ready:
        return "", "Requested backend providers are temporarily at their usage limits"
    deadline = time.monotonic() + timeout
    warning = "No backend is available"
    for name in ready:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "", "Backend timed out"
        try:
            response = providers[name](task, system=system, timeout=remaining)
        except Exception:
            # Execution may have started. Never replay or expose exception text.
            return "", "Backend failed unexpectedly"
        if (
            not isinstance(response, tuple)
            or len(response) != 2
            or not isinstance(response[0], str)
            or (response[1] is not None and not isinstance(response[1], str))
        ):
            return "", "Backend returned an invalid response"
        result, warning = response
        if time.monotonic() > deadline:
            return "", "Backend timed out"
        if result.strip() and not warning:
            return result, None
        if _limit(warning):
            with _lock:
                _rest_until[name] = time.monotonic() + COOLDOWN_S
        elif not _unavailable(warning):
            # An ambiguous timeout/error is not grounds to replay a job.
            return "", warning or "Backend returned no answer"
    return "", warning
