"""Live-model helpers for behavior tests (jarvis_rt, no LiveKit).

Live tests drive the production voice brain (``agent._realtime_llm()``
through ``jarvis_rt.live.LiveBackend``) and judge replies with the free
text chain (AI Studio key, $0). Nothing here touches the network at
import time, and every entry point that would call a model skips unless
``JARVIS_LIVE_LLM=1`` is set, so default runs stay offline.

Free-tier quotas are per model ID, so the judge falls back across
``JUDGE_MODEL_CHAIN`` and ``patient_run`` sleeps through 429s.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

JUDGE_MODEL_CHAIN = ["gemini-3.8-flash", "gemini-3.6-flash"]

# Free-tier is ~5 req/min/model. Tests burst several calls each, so pace
# test starts (>=13s apart) to stay inside the combined ~10 RPM budget.
_MIN_GAP_SECONDS = 13.0
_last_start: float = 0.0
_TURN_TIMEOUT_S = 60.0


def live_enabled() -> bool:
    return os.environ.get("JARVIS_LIVE_LLM", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def require_live() -> None:
    """Skip (never call out) unless live model tests were requested."""
    if not live_enabled():
        pytest.skip("live model call: set JARVIS_LIVE_LLM=1 (and real keys) to run")


async def throttle() -> None:
    global _last_start
    now = time.monotonic()
    wait = _MIN_GAP_SECONDS - (now - _last_start)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_start = time.monotonic()


def _is_quota_error(exc: BaseException) -> bool:
    """True when the failure chain shows free-tier rate limiting.

    Only quota evidence retries — deterministic errors (400s, auth,
    assertions) raise immediately instead of burning minutes sleeping.
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        text = f"{type(exc).__name__} {exc}".lower()
        if (
            "429" in text
            or "quota exceeded" in text
            or "resource_exhausted" in text
            or "rate limited" in text
            or "rate_limit" in text
        ):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


async def patient_run(
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    attempts: int = 5,
    wait_seconds: float = 45.0,
    **kwargs: Any,
) -> Any:
    """Run an LLM call, sleeping through free-tier 429s.

    The server's own RetryInfo says ~35s until refill; 45s almost always
    lands a fresh bucket. Non-quota errors raise at once.
    """
    last: BaseException | None = None
    for _ in range(attempts):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            if not _is_quota_error(exc):
                raise
            last = exc
            await asyncio.sleep(wait_seconds)
    assert last is not None
    raise last


@dataclass
class Turn:
    """What one user turn produced: assistant texts and called tool names."""

    messages: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)


async def run_turn(
    session: Any, text: str, *, timeout: float = _TURN_TIMEOUT_S
) -> Turn:
    """Send typed user text through a live session and collect the reply."""
    require_live()
    turn = Turn()

    def on_item(event: Any) -> None:
        item = event.item
        if getattr(item, "role", None) == "assistant":
            turn.messages.append(item.text_content or "")

    def on_tools(event: Any) -> None:
        turn.calls.extend(call.name for call in event.function_calls)

    session.on("conversation_item_added", on_item)
    session.on("function_tools_executed", on_tools)
    try:
        handle = session.generate_reply(user_input=text)
        async with asyncio.timeout(timeout):
            await handle.wait_for_playout()
            while session.agent_state != "listening":
                await asyncio.sleep(0.05)
    finally:
        session.off("conversation_item_added", on_item)
        session.off("function_tools_executed", on_tools)
    return turn


async def judge(intent: str, reply: str) -> bool:
    """Free LLM judge: does ``reply`` satisfy ``intent``? Live only."""
    require_live()
    from google import genai

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    prompt = (
        "You grade a voice assistant's reply.\n"
        f"Criterion:\n{intent}\n\nReply:\n{reply}\n\n"
        "Answer with exactly one word: PASS if the reply meets the criterion, "
        "otherwise FAIL."
    )
    last: BaseException | None = None
    for model in JUDGE_MODEL_CHAIN:
        try:
            response = await client.aio.models.generate_content(
                model=model, contents=prompt
            )
        except Exception as exc:  # fall through to the backup model
            last = exc
            continue
        return "PASS" in (response.text or "").upper()
    assert last is not None
    raise last


@contextlib.asynccontextmanager
async def live_session(agent: Any) -> AsyncIterator[Any]:
    """Start ``agent`` on a live voice session with the production chain."""
    require_live()
    import agent as agent_mod
    from jarvis_rt import AgentSession
    from jarvis_rt.live import LiveBackend

    session = AgentSession(
        backend=LiveBackend(
            agent_mod._realtime_llm(), http_options=agent_mod._live_http_options()
        )
    )
    await session.start(agent)
    try:
        yield session
    finally:
        await session.aclose()
