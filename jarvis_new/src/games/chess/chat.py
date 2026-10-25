"""Free-form questions in Jarvis's butler voice (OpenRouter), 1-3 sentences."""

from __future__ import annotations

from games.chess import templates
from games.chess.commentary import Cancelled, OpenRouter

CHAT_PROMPT = (
    "You are Jarvis, a dry, courteous British valet who plays chess with Sir. "
    "Answer in 1 to 3 short spoken sentences: no symbols, squares as 'e four'. "
    "Use ONLY the position and engine facts supplied. Never invent moves or "
    "evaluations. If you do not know, say so briefly."
)


class Chat:
    def __init__(self, router: OpenRouter, *, enabled: bool = True):
        self.router = router
        self.enabled = enabled

    def online(self) -> bool:
        return self.enabled and self.router.available()

    async def ask(
        self,
        question: str,
        *,
        fen: str,
        last_moves: list[str],
        engine_facts: dict | None = None,
    ) -> str:
        """Return the reply, or the offline line. Raises Cancelled on stop."""
        if not self.online():
            return templates.OFFLINE_CHAT
        ctx = {"fen": fen, "last_moves": last_moves[-6:]}
        if engine_facts:
            ctx["engine_facts"] = engine_facts
        messages = [
            {"role": "system", "content": CHAT_PROMPT},
            {"role": "user", "content": f"{ctx}\n\nSir asks: {question}"},
        ]
        try:
            content, _ = await self.router.complete(messages, max_tokens=160)
        except Cancelled:
            raise
        except Exception:
            return templates.OFFLINE_CHAT
        return content.strip().replace("\n", " ")[:500] or templates.OFFLINE_CHAT
