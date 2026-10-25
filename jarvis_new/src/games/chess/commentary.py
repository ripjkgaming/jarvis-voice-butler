"""Grounded move explanations from a free OpenRouter model (async httpx).

Stockfish supplies the facts; the model only words them. Anything it says
that is not in the facts is rejected and replaced by the offline template.
"""

from __future__ import annotations

import asyncio
import collections
import datetime as dt
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chess
import httpx

from games.chess import templates
from games.chess.settings import games_dir

URL = "https://openrouter.ai/api/v1/chat/completions"
REFERER = "https://github.com/ripjkgaming/jarvis-voice-butler"
TITLE = "Jarvis Chess"
PER_MINUTE = 20
PER_DAY = 50
TIMEOUT_S = 8.0
MAX_TOKENS = 220

SYSTEM_PROMPT = (
    "You are Jarvis, a dry, courteous British valet who coaches chess. "
    "Use ONLY the engine facts given. Never invent moves, lines or evaluations. "
    "Pitch it for a {preset} player. Return JSON only: "
    '{{"headline": "<≤10 words>", "spoken": "<1–3 sentences for speech: no symbols, '  # noqa: RUF001
    "squares as 'e four', at most one wry aside>\", "
    '"detail": "<2–4 sentences for the panel>"}}.'  # noqa: RUF001
)

_SQ_NUM = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
}
_SAN_RE = re.compile(
    r"\b(O-O-O|O-O|[NBRQK][a-h]?[1-8]?x?[a-h][1-8](?:=[NBRQ])?|[a-h]x[a-h][1-8](?:=[NBRQ])?)[+#]?"
)
_SQ_RE = re.compile(r"\b([a-h][1-8])\b")
_SPOKEN_SQ_RE = re.compile(r"\b([a-h])[ -](one|two|three|four|five|six|seven|eight)\b")


def redact(text: str) -> str:
    """Strip anything key-shaped before logging."""
    return re.sub(r"(sk-or-[A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]+", r"\1…", text)


def api_key() -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    try:
        from games.chess.settings import jarvis_home

        for line in (jarvis_home() / "keys.env").read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                v = line.split("=", 1)[1].strip().strip("'\"")
                return v or None
    except OSError:
        pass
    return None


# ----------------------------------------------------------------------- quota
class Quota:
    def __init__(self, path: Path | None = None, now=time.time, today=None):
        self.path = path or (games_dir() / "openrouter_quota.json")
        self._now = now
        self._today = today or (lambda: dt.date.today().isoformat())
        self._minute: collections.deque[float] = collections.deque()
        self.notified = False
        self.count = 0
        self.day = self._today()
        self._load()

    def _load(self) -> None:
        try:
            d = json.loads(self.path.read_text())
            if d.get("day") == self._today():
                self.count = int(d.get("count", 0))
                self.day = d["day"]
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"day": self.day, "count": self.count}))
        except OSError:
            pass

    def _roll(self) -> None:
        if self.day != self._today():
            self.day, self.count, self.notified = self._today(), 0, False

    def left_today(self) -> int:
        self._roll()
        return max(0, PER_DAY - self.count)

    def allow(self) -> bool:
        self._roll()
        t = self._now()
        while self._minute and t - self._minute[0] > 60:
            self._minute.popleft()
        return self.count < PER_DAY and len(self._minute) < PER_MINUTE

    def record(self) -> None:
        self._roll()
        self._minute.append(self._now())
        self.count += 1
        self._save()


# ---------------------------------------------------------------------- client
class Cancelled(Exception):  # noqa: N818 - reads as a signal, not an error
    """The request was aborted by a stop; never speak its reply."""


@dataclass
class Explanation:
    headline: str
    spoken: str
    detail: str
    source: str  # "llm" | "template"
    model: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "spoken": self.spoken,
            "detail": self.detail,
            "source": self.source,
            "model": self.model,
        }


class OpenRouter:
    def __init__(
        self,
        models: list[str],
        *,
        client: httpx.AsyncClient | None = None,
        quota: Quota | None = None,
        sleep=asyncio.sleep,
        key: str | None = None,
    ):
        self.models = list(models)
        self._client = client
        self.quota = quota or Quota()
        self._sleep = sleep
        self._key = key
        self.calls = 0  # HTTP requests actually made (tests assert on this)
        self._inflight: set[asyncio.Task] = set()
        self.generation = 0

    @property
    def key(self) -> str | None:
        return self._key or api_key()

    def available(self) -> bool:
        return bool(self.key) and self.quota.allow()

    def cancel(self) -> None:
        """Abort every in-flight request; late replies are dropped."""
        self.generation += 1
        for t in list(self._inflight):
            t.cancel()
        self._inflight.clear()

    async def complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int = MAX_TOKENS,
        json_mode: bool = False,
    ) -> tuple[str, str]:
        """Return (content, model). Raises Cancelled / RuntimeError."""
        gen = self.generation
        task = asyncio.ensure_future(self._complete(messages, max_tokens, json_mode))
        self._inflight.add(task)
        try:
            out = await task
        except asyncio.CancelledError:
            if gen != self.generation:
                raise Cancelled from None
            raise
        finally:
            self._inflight.discard(task)
        if gen != self.generation:
            raise Cancelled
        return out

    async def _complete(self, messages, max_tokens, json_mode) -> tuple[str, str]:
        key = self.key
        if not key:
            raise RuntimeError("no OpenRouter key")
        if not self.quota.allow():
            raise RuntimeError("quota exhausted")
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_S)
        own = self._client is None
        try:
            last_err: Exception | None = None
            backoff = 0.5
            for model in self.models:
                body: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.4,
                    "models": self.models,
                }
                if json_mode:
                    body["response_format"] = {"type": "json_object"}
                headers = {
                    "Authorization": f"Bearer {key}",
                    "HTTP-Referer": REFERER,
                    "X-Title": TITLE,
                }
                self.calls += 1
                try:
                    r = await client.post(
                        URL, json=body, headers=headers, timeout=TIMEOUT_S
                    )
                except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                    last_err = exc
                    continue
                self.quota.record()
                if r.status_code == 429:
                    last_err = RuntimeError("429")
                    await self._sleep(backoff)
                    backoff = min(backoff * 2, 4.0)
                    if not self.quota.allow():
                        break
                    continue
                if r.status_code >= 400:
                    last_err = RuntimeError(f"http {r.status_code}")
                    continue
                try:
                    data = r.json()
                    content = data["choices"][0]["message"]["content"]
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    last_err = exc
                    continue
                if content:
                    return content, data.get("model", model)
            raise RuntimeError(f"all models failed: {last_err}")
        finally:
            if own:
                await client.aclose()


# --------------------------------------------------------------- explanations
def validate(parsed: Any, facts: dict) -> Explanation | None:
    """Accept only well-formed JSON whose moves and squares are grounded."""
    if not isinstance(parsed, dict):
        return None
    try:
        head = str(parsed["headline"]).strip()
        spoken = str(parsed["spoken"]).strip()
        detail = str(parsed["detail"]).strip()
    except KeyError:
        return None
    if not (head and spoken and detail):
        return None
    if len(head.split()) > 14 or len(spoken) > 600 or len(detail) > 900:
        return None
    allowed_san, allowed_sq = _grounding(facts)
    blob = f"{head} {spoken} {detail}"
    for m in _SAN_RE.finditer(blob):
        tok = m.group(0).rstrip("+#")
        if tok not in allowed_san and tok.replace("x", "") not in {
            s.replace("x", "") for s in allowed_san
        }:
            return None
    for sq in _SQ_RE.findall(blob):
        if sq not in allowed_sq:
            return None
    for f, n in _SPOKEN_SQ_RE.findall(blob.lower()):
        if f"{f}{_SQ_NUM[n]}" not in allowed_sq:
            return None
    return Explanation(head, spoken, detail, "llm")


def _grounding(facts: dict) -> tuple[set[str], set[str]]:
    san: set[str] = set()
    sq: set[str] = set()
    for key in ("move_san", "best_san", "best_reply_san"):
        v = facts.get(key)
        if v:
            san.add(str(v).rstrip("+#"))
    for v in facts.get("pv_san", []) or []:
        san.add(str(v).rstrip("+#"))
    fen = facts.get("fen_before")
    if fen:
        b = chess.Board(fen)
        for m in b.legal_moves:
            san.add(b.san(m).rstrip("+#"))
        for s in b.piece_map():
            sq.add(chess.square_name(s))
        for uci in [
            *facts.get("pv_uci", []),
            facts.get("move_uci"),
            facts.get("best_uci"),
        ]:
            if uci:
                sq.update([uci[:2], uci[2:4]])
        for m in b.legal_moves:
            sq.add(chess.square_name(m.to_square))
    return san, sq


def build_facts(
    *,
    fen_before: str,
    move: chess.Move,
    eval_before: dict,
    eval_after: dict,
    cp_loss: int,
    classification: str,
    best: chess.Move | None,
    pv: list[chess.Move],
    preset: str,
    phase: str,
    material: dict,
) -> dict:
    b = chess.Board(fen_before)
    mover = "white" if b.turn else "black"
    san = b.san(move)
    best_san = b.san(best) if best else None
    b2 = b.copy()
    pv_san: list[str] = []
    for m in pv[:5]:
        if m in b2.legal_moves:
            pv_san.append(b2.san(m))
            b2.push(m)
        else:
            break
    after = b.copy()
    after.push(move)
    reply = None
    if pv and best == move and len(pv) > 1:
        reply = pv_san[1] if len(pv_san) > 1 else None
    return {
        "fen_before": fen_before,
        "mover": mover,
        "move_san": san,
        "move_uci": move.uci(),
        "eval_before": eval_before,
        "eval_after": eval_after,
        "centipawn_loss": cp_loss,
        "classification": classification,
        "best_san": best_san,
        "best_uci": best.uci() if best else None,
        "pv_san": pv_san,
        "pv_uci": [m.uci() for m in pv[:5]],
        "best_reply_san": reply,
        "material": material,
        "phase": phase,
        "preset": preset,
    }


def classify(cp_loss: int, mate_missed_or_allowed: bool = False) -> str:
    if mate_missed_or_allowed or cp_loss > 250:
        return "Blunder"
    if cp_loss > 100:
        return "Mistake"
    if cp_loss > 50:
        return "Inaccuracy"
    if cp_loss <= 5:
        return "Best"
    return "Good"


class Commentary:
    """Explain one move. Cached by FEN + move + preset; offline fallback."""

    def __init__(self, router: OpenRouter, *, enabled: bool = True):
        self.router = router
        self.enabled = enabled
        self._cache: dict[tuple[str, str, str], Explanation] = {}
        self.last_notice: str | None = None

    def left_today(self) -> int:
        return self.router.quota.left_today()

    async def explain(
        self, facts: dict, *, force_template: bool = False
    ) -> Explanation:
        key = (facts["fen_before"], facts["move_uci"], facts["preset"])
        if key in self._cache:
            return self._cache[key]
        tpl = Explanation(**templates.template_explanation(facts))
        if force_template or not self.enabled or not self.router.key:
            return tpl
        if not self.router.quota.allow():
            if not self.router.quota.notified:
                self.router.quota.notified = True
                self.last_notice = (
                    "The free explanation quota is spent for now, Sir; "
                    "I'll use plain engine notes."
                )
            return tpl
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(preset=facts["preset"])},
            {"role": "user", "content": json.dumps(facts)},
        ]
        try:
            content, model = await self.router.complete(messages, json_mode=True)
        except Cancelled:
            raise
        except Exception:
            return tpl
        parsed = _loads_loose(content)
        ok = validate(parsed, facts)
        if ok is None:
            return tpl
        ok.model = model
        self._cache[key] = ok
        return ok

    def cancel(self) -> None:
        self.router.cancel()


def _loads_loose(text: str) -> Any:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
    return None
