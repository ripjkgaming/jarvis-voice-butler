"""Free ($0) eval LLM shared by behavior-routing tests.

Mirrors production's TEXT_FALLBACK_CHAIN: gemini-3.8-flash primary with
gemini-2.5-flash backup. Separate free-tier quotas per model ID, so a
saturated primary still leaves options — the same trick agent.py uses.
The old inference.LLM(openai/gpt-4.1-mini) billed LiveKit Cloud credits.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from google.genai.types import HttpOptions
from livekit.agents.llm import FallbackAdapter
from livekit.plugins import google

MODEL_CHAIN = ["gemini-3.8-flash", "gemini-3.6-flash"]

# Free-tier is ~5 req/min/model. Tests burst several calls each, so pace
# test starts (>=13s apart) to stay inside the combined ~10 RPM budget.
_MIN_GAP_SECONDS = 13.0
_last_start: float = 0.0


def free_eval_llm() -> FallbackAdapter:
    # http_options timeout: the plugin's built-in 5s HTTP deadline is
    # rejected by current Gemini models (400 "minimum allowed deadline
    # is 10s"). 60s keeps slow tool-routing turns alive.
    return FallbackAdapter(
        [google.LLM(model=m, http_options=HttpOptions(timeout=60_000)) for m in MODEL_CHAIN]
    )


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
