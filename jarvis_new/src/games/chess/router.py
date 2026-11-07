"""Smarter routing for utterances the rule catalogue misses.

Two passes, both only *after* ``commands.classify`` and move parsing missed:

1. Offline fuzzy/slot pass (always on, no I/O, a few ms): paraphrase patterns
   plus a difflib close-match against the RULES phrase catalogue.
2. LLM tool-call pass (OpenRouter, json mode, hard 3 s budget): only when the
   offline pass missed, the utterance has >= 3 words and the client is
   available.  The model sees the transcript and a whitelist schema only, never
   a FEN or a key.  Everything it returns is validated strictly; anything odd
   becomes "no route".  Destructive intents are not in the whitelist.

A low-confidence LLM answer is never acted on: it yields a one-line spoken
clarification (``RouteResult.clarify``) with the candidate attached so the
caller can run it when the user says yes.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from games.chess import commands, text_norm
from games.chess.commands import Intent
from games.chess.commentary import Cancelled

TIMEOUT_S = 3.0
MIN_CONFIDENCE = 0.7
LLM_MIN_WORDS = 3
FUZZY_CUTOFF = 0.85

# Never offered to the model, never produced by the fuzzy pass.
DESTRUCTIVE = frozenset(
    {
        "quit", "quit_offer", "exit", "resign", "new", "new_game", "restart",
        "delete", "delete_game", "abort", "forfeit", "clear", "reset",
    }
)  # fmt: skip
_SKIP_GROUPS = frozenset({"stop", "exit", "answer", "ctl", "review"})
_PIECE_WORDS = frozenset(
    {
        "knight",
        "night",
        "bishop",
        "rook",
        "queen",
        "king",
        "pawn",
        "castle",
        "castles",
        "castling",
    }
)
_SQ = re.compile(r"\b[a-h][1-8]\b")


@dataclass
class RouteCtx:
    series: str | None = None  # None | "running" | "paused"
    history: bool = False  # a saved game is open in the history view
    takeover: bool = False  # Jarvis is playing for the user
    is_move: Callable[[str], bool] | None = None  # optional move probe


@dataclass
class RouteResult:
    intent: Intent | None
    clarify: str | None = None
    source: str = "fuzzy"  # "fuzzy" | "llm"
    candidate: Intent | None = None  # intent to run if the user confirms clarify


# ------------------------------------------------------------- move guard
def looks_like_move(text: str, ctx: RouteCtx | None = None) -> bool:
    if ctx and ctx.is_move:
        try:
            if ctx.is_move(text):
                return True
        except Exception:
            pass
    t = text_norm.clean(text)
    words = t.split()
    if not words:
        return False
    if len(words) <= 6 and words[0] in _PIECE_WORDS:
        return True
    if len(words) <= 5:
        if _SQ.search(t):
            return True
        try:
            from games.chess import voice_moves

            if voice_moves.find_squares(t):
                return True
        except Exception:
            pass
    return False


# ----------------------------------------------------- paraphrase patterns
_EASY = r"(?:easier|weaker|softer|gentler|simpler|less strong)"
_HARD = r"(?:harder|stronger|tougher|more difficult|more challenging)"
_SOFT = r"(?:(?:a )?(?:bit|little|touch) |slightly |somewhat |much )?"
_LEAD = r"(?:(?:could|can|would|will) you |please |let'?s |let us )*"
_FEW = {"couple": 2, "few": 3, "several": 5, "some": 3}


def _p_level(ctx: RouteCtx, m: re.Match) -> Intent | None:
    d = "down" if re.fullmatch(_EASY, m.group("d")) else "up"
    return Intent("level_adjust", {"direction": d}, "game")


def _p_review(ctx: RouteCtx, m: re.Match) -> Intent | None:
    return Intent("analyse", {}, "analysis")


def _p_more_games(ctx: RouteCtx, m: re.Match) -> Intent | None:
    if ctx.series == "paused":
        return Intent("series_resume", {}, "series")
    if ctx.series == "running":
        return None
    return Intent(
        "series_start", {"total": _FEW[m.group("q")], "colour": "alternate"}, "series"
    )


def _p_series_continue(ctx: RouteCtx, m: re.Match) -> Intent | None:
    if ctx.series == "paused":
        return Intent("series_resume", {}, "series")
    return None


def _p_handback(ctx: RouteCtx, m: re.Match) -> Intent | None:
    return Intent("handback", {}, "takeover")


def _p_from_here(ctx: RouteCtx, m: re.Match) -> Intent | None:
    who = (m.group("who") or "").replace("'", "")
    if who in ("i", "ill", "i will") or ctx.takeover:
        return Intent("handback", {}, "takeover")
    return Intent("takeover", {}, "takeover")


def _p_back(ctx: RouteCtx, m: re.Match) -> Intent | None:
    q = m.group("q")
    n = _FEW.get(q) or {"a": 1, "one": 1, "two": 2, "three": 3}.get(q, 1)
    if ctx.history:
        return Intent("hist_step", {"delta": -n}, "history")
    return Intent("undo", {"n": n}, "game")


def _p_last_game(ctx: RouteCtx, m: re.Match) -> Intent | None:
    return Intent("open_game", {"ref": "1"}, "history")


_PARAPHRASES: list[tuple[re.Pattern, Callable[[RouteCtx, re.Match], Intent | None]]] = [
    (re.compile(rf"^{_LEAD}(?:make|set) it {_SOFT}(?P<d>{_EASY}|{_HARD})(?: please)?$"), _p_level),
    (re.compile(rf"^{_LEAD}(?:turn|bring) (?:it|the (?:level|difficulty)) (?:down|up)(?: a (?:bit|notch|little))?$"),
     lambda c, m: Intent("level_adjust", {"direction": "down" if " down" in m.group(0) else "up"}, "game")),
    (re.compile(r"^(?:i'?d|i would|i want to|i wanna|let me|can i|show me|tell me) (?:like to )?(?:see|know|hear) how i (?:did|played|got on|went|fared)$"), _p_review),
    (re.compile(r"^show me how i (?:did|played|got on|went|fared)$"), _p_review),
    (re.compile(rf"^{_LEAD}(?:keep going|carry on|play on|continue|play)(?: for)? (?:a |an )?(?P<q>few|couple|several|some)(?: of)? more games?$"), _p_more_games),
    (re.compile(rf"^{_LEAD}(?:keep going|carry on|continue|resume) with (?:the |my |that )?series$"), _p_series_continue),
    (re.compile(rf"^{_LEAD}(?:hand|give|pass|return) (?:it|the board|the game|control) back(?: to me)?$"), _p_handback),
    (re.compile(rf"^{_LEAD}(?:(?P<who>you|i|i'?ll|i will) )?(?:can )?take (?:it|over) from here(?: on)?$"), _p_from_here),
    (re.compile(rf"^{_LEAD}go back (?:a |an )?(?P<q>couple|few|one|two|three|a|1|2|3)(?: of)? moves?$"), _p_back),
    (re.compile(rf"^{_LEAD}(?:show|open|pull up|bring up|let me see) (?:me )?(?:the )?(?:last|latest|previous|most recent) game$"), _p_last_game),
]  # fmt: skip


def _paraphrase(cands: list[str], ctx: RouteCtx) -> Intent | None:
    for t in cands:
        for rx, fn in _PARAPHRASES:
            m = rx.match(t)
            if m:
                hit = fn(ctx, m)
                if hit:
                    return hit
    return None


# ----------------------------------------------------------- fuzzy catalogue
class _Catalogue:
    def __init__(self) -> None:
        self.size = -1
        self.items: list[tuple[str, str, dict, str]] = []  # phrase, name, args, group

    def build(self) -> list[tuple[str, str, dict, str]]:
        if self.size == len(commands.RULES):
            return self.items
        items = []
        for rule in commands.RULES:
            if rule.group in _SKIP_GROUPS or rule.name in DESTRUCTIVE:
                continue
            for ph in rule.phrases:
                if len(ph.split()) >= 2 and len(ph) >= 8 and not _SQ.search(ph):
                    items.append((ph, rule.name, dict(rule.fixed), rule.group))
        self.items = items
        self.size = len(commands.RULES)
        return items


_CAT = _Catalogue()


def fuzzy_match(cands: list[str]) -> Intent | None:
    """Unambiguous close match (ratio >= cutoff) against catalogue phrases."""
    items = _CAT.build()
    sm = difflib.SequenceMatcher(autojunk=False)
    for t in cands:
        if len(t) < 8 or len(t.split()) < 2:
            continue
        sm.set_seq2(t)
        found: list[tuple[float, str, dict, str]] = []
        for ph, name, args, grp in items:
            if abs(len(ph) - len(t)) > max(3, int(len(t) * 0.18)):
                continue
            sm.set_seq1(ph)
            if sm.real_quick_ratio() < FUZZY_CUTOFF or sm.quick_ratio() < FUZZY_CUTOFF:
                continue
            r = sm.ratio()
            if r >= FUZZY_CUTOFF:
                found.append((r, name, args, grp))
        if not found:
            continue
        best = max(f[0] for f in found)
        top = {
            (n, json.dumps(a, sort_keys=True)): (n, a, g)
            for r, n, a, g in found
            if best - r <= 0.03
        }
        if len(top) == 1:
            n, a, g = next(iter(top.values()))
            return Intent(n, dict(a), g)
        # ambiguous at this variant: do not guess
    return None


# ------------------------------------------------------------- tool schema
def _int(v: Any, lo: int, hi: int) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int) and lo <= v <= hi:
        return v
    return None


_REF = re.compile(r"^(?:\d{1,4}|latest|last|newest|most recent|\d{4}-\d{2}-\d{2})$")
_NONE = object()


def _v_set_level(a: dict) -> dict | None:
    n = _int(a.get("n"), 1, 10)
    return None if n is None else {"n": n}


def _v_adjust(a: dict) -> dict | None:
    return (
        {"direction": a["direction"]} if a.get("direction") in ("up", "down") else None
    )


def _v_series_start(a: dict) -> dict | None:
    out: dict[str, Any] = {}
    if "total" in a:
        if a["total"] is None:
            out["total"] = None
        else:
            t = _int(a["total"], 1, 100)
            if t is None:
                return None
            out["total"] = t
    col = a.get("colour", "alternate") or "alternate"
    if col not in ("white", "black", "alternate"):
        return None
    out["colour"] = col
    if a.get("level") is not None:
        lv = _int(a["level"], 1, 10)
        if lv is None:
            return None
        out["level"] = lv
    return out


def _v_open(a: dict) -> dict | None:
    ref = a.get("ref")
    if isinstance(ref, int) and not isinstance(ref, bool):
        ref = str(ref)
    if isinstance(ref, str) and _REF.match(ref.strip().lower()):
        return {"ref": ref.strip().lower()}
    return None


def _v_step(a: dict) -> dict | None:
    d = _int(a.get("delta"), -50, 50)
    return None if not d else {"delta": d}


def _v_goto(a: dict) -> dict | None:
    n = _int(a.get("move"), 1, 500)
    return None if n is None else {"n": n}


def _v_branch(a: dict) -> dict | None:
    if a.get("move") is None:
        return {}
    return _v_goto(a)


def _v_undo(a: dict) -> dict | None:
    if a.get("n") is None:
        return {"n": 1}
    n = _int(a.get("n"), 1, 10)
    return None if n is None else {"n": n}


def _v_explain(a: dict) -> dict | None:
    which = a.get("which") or "last"
    if which not in ("last", "number"):
        return None
    if which == "number":
        n = _int(a.get("move"), 1, 500)
        return None if n is None else {"which": "number", "n": n}
    return {"which": "last"}


def _nothing(a: dict) -> dict:
    return {}


# name -> (intent name, group, validator, arg doc, description)
_TOOLS: dict[str, tuple[str, str, Callable[[dict], dict | None], dict, str]] = {
    "set_level": ("set_level", "game", _v_set_level, {"n": "integer 1-10"}, "set engine strength level"),
    "level_adjust": ("level_adjust", "game", _v_adjust, {"direction": "up|down"}, "make the engine stronger (up) or weaker (down)"),
    "series_start": ("series_start", "series", _v_series_start, {"total": "integer 1-100 or null (until stopped)", "colour": "white|black|alternate", "level": "integer 1-10 or null"}, "start a multi-game series"),
    "series_pause": ("series_pause", "series", _nothing, {}, "pause the running series"),
    "series_resume": ("series_resume", "series", _nothing, {}, "resume a paused series"),
    "series_cancel": ("series_cancel", "series", _nothing, {}, "cancel the series"),
    "series_left": ("series_left", "series", _nothing, {}, "how many series games remain"),
    "series_score": ("series_score", "series", _nothing, {}, "series score so far"),
    "takeover": ("takeover", "takeover", _nothing, {}, "Jarvis plays the user's side"),
    "release_control": ("handback", "takeover", _nothing, {}, "user takes the board back from Jarvis"),
    "list_games": ("list_games", "history", _nothing, {}, "list saved games"),
    "open_game": ("open_game", "history", _v_open, {"ref": "list number, date YYYY-MM-DD, or latest"}, "open a saved game"),
    "hist_step": ("hist_step", "history", _v_step, {"delta": "integer -50..50, nonzero"}, "step through the open saved game"),
    "hist_goto": ("hist_goto", "history", _v_goto, {"move": "integer 1-500"}, "jump to a move number in the open saved game"),
    "hist_branch": ("hist_branch", "history", _v_branch, {"move": "integer 1-500 or omitted"}, "continue a new game from the open saved game"),
    "undo": ("undo", "game", _v_undo, {"n": "integer 1-10, default 1"}, "take back moves"),
    "analyse_board": ("analyse_board", "analysis", _nothing, {}, "analyse the current board"),
    "explain": ("explain", "analysis", _v_explain, {"which": "last|number", "move": "integer, required when which=number"}, "explain a move"),
    "difficulty": ("difficulty", "game", _nothing, {}, "ask what the current difficulty is"),
}  # fmt: skip

_PROMPT = (
    "You map a spoken chess-butler command to ONE tool call. Reply with a single "
    'JSON object: {"intent": <tool name or "none">, "args": {...}, "confidence": <0..1>}. '
    'Use "none" when the utterance is not clearly one of the tools, is a chess move, '
    "or is small talk. Never invent arguments; stay inside the stated ranges. "
    "Tools:\n"
)


def tool_schema() -> dict[str, dict]:
    """The whitelist as shown to the model (also handy for docs and tests)."""
    return {
        n: {"description": d, "args": a} for n, (_i, _g, _v, a, d) in _TOOLS.items()
    }


def _prompt(ctx: RouteCtx) -> str:
    lines = [
        f"- {n}({', '.join(f'{k}: {v}' for k, v in s['args'].items())}): {s['description']}"
        for n, s in tool_schema().items()
    ]
    state = {
        "series": ctx.series or "none",
        "history_open": ctx.history,
        "jarvis_playing_for_user": ctx.takeover,
    }
    return _PROMPT + "\n".join(lines) + "\nState: " + json.dumps(state)


def validate(parsed: Any) -> tuple[Intent, float] | None:
    """Strictly validate the model's JSON; None for anything unusable."""
    if not isinstance(parsed, dict):
        return None
    name = parsed.get("intent")
    if not isinstance(name, str) or name not in _TOOLS:
        return None
    conf = parsed.get("confidence")
    if (
        isinstance(conf, bool)
        or not isinstance(conf, (int, float))
        or not 0 <= conf <= 1
    ):
        return None
    args = parsed.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    iname, group, check, _doc, _d = _TOOLS[name]
    if iname in DESTRUCTIVE:
        return None
    clean = check(args)
    if clean is None:
        return None
    return Intent(iname, clean, group), float(conf)


def parse_reply(content: str) -> Any:
    s = (content or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-z]*\s*|\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except ValueError:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
    return None


def describe(it: Intent) -> str:
    a = it.args
    n = it.name
    if n == "set_level":
        return f"level {a['n']}"
    if n == "level_adjust":
        return "make it easier" if a["direction"] == "down" else "make it harder"
    if n == "series_start":
        t = a.get("total")
        return (
            "start a series until you say stop"
            if t is None
            else f"start a series of {t} game{'s' * (t != 1)}"
        )
    if n == "undo":
        return f"take back {a.get('n', 1)} move{'s' * (a.get('n', 1) != 1)}"
    if n == "hist_step":
        return f"step {'back' if a['delta'] < 0 else 'forward'} {abs(a['delta'])}"
    if n in ("hist_goto", "hist_branch") and "n" in a:
        return f"{'go to' if n == 'hist_goto' else 'branch from'} move {a['n']}"
    if n == "open_game":
        return f"open game {a['ref']}"
    if n == "explain" and "n" in a:
        return f"explain move {a['n']}"
    return {
        "series_pause": "pause the series", "series_resume": "resume the series",
        "series_cancel": "cancel the series", "series_left": "how many games are left",
        "series_score": "the series score", "takeover": "play for you",
        "handback": "hand the board back", "list_games": "list your games",
        "analyse_board": "analyse the board", "explain": "explain the last move",
        "difficulty": "the current difficulty", "hist_branch": "continue from here",
    }.get(n, n.replace("_", " "))  # fmt: skip


# ------------------------------------------------------------------- router
class IntentRouter:
    def __init__(
        self,
        client: Any | None = None,
        *,
        enabled: bool = True,
        timeout: float = TIMEOUT_S,
        min_confidence: float = MIN_CONFIDENCE,
    ):
        self.client = client
        self.enabled = enabled
        self.timeout = timeout
        self.min_confidence = min_confidence
        self.generation = 0
        self._inflight: set[asyncio.Future] = set()
        self.llm_calls = 0

    def llm_ready(self) -> bool:
        try:
            return bool(
                self.enabled and self.client is not None and self.client.available()
            )
        except Exception:
            return False

    def cancel(self) -> None:
        """Stop word: abort any in-flight routing call; late replies are dropped."""
        self.generation += 1
        for t in list(self._inflight):
            t.cancel()
        self._inflight.clear()
        if self.client is not None and hasattr(self.client, "cancel"):
            self.client.cancel()

    def route_offline(
        self, text: str, ctx: RouteCtx | None = None
    ) -> RouteResult | None:
        ctx = ctx or RouteCtx()
        if looks_like_move(text, ctx):
            return None
        cands = [v for v in text_norm.variants(text) if v]
        raw = text_norm.clean(text)
        if raw and raw not in cands:
            cands.append(raw)
        hit = _paraphrase(cands, ctx) or fuzzy_match(cands)
        if hit is None or hit.name in DESTRUCTIVE:
            return None
        return RouteResult(hit, None, "fuzzy")

    async def route(self, text: str, ctx: RouteCtx | None = None) -> RouteResult | None:
        ctx = ctx or RouteCtx()
        text = (text or "").strip()
        if not text or looks_like_move(text, ctx):
            return None
        off = self.route_offline(text, ctx)
        if off is not None:
            return off
        if len(text.split()) < LLM_MIN_WORDS or not self.llm_ready():
            return None
        return await self._llm(text, ctx)

    async def _llm(self, text: str, ctx: RouteCtx) -> RouteResult | None:
        gen = self.generation
        messages = [
            {"role": "system", "content": _prompt(ctx)},
            {"role": "user", "content": text},
        ]
        self.llm_calls += 1
        task = asyncio.ensure_future(
            self.client.complete(messages, max_tokens=120, json_mode=True)
        )
        self._inflight.add(task)
        try:
            content, _model = await asyncio.wait_for(task, self.timeout)
        except asyncio.CancelledError:
            if gen != self.generation:
                return None
            raise
        except (
            asyncio.TimeoutError,
            Cancelled,
            RuntimeError,
            ValueError,
            TypeError,
            OSError,
        ):
            return None
        except Exception:
            return None
        finally:
            self._inflight.discard(task)
        if gen != self.generation:
            return None
        ok = validate(parse_reply(content))
        if ok is None:
            return None
        it, conf = ok
        if conf < self.min_confidence:
            return RouteResult(
                None, f"Did you mean {describe(it)}, Sir?", "llm", candidate=it
            )
        return RouteResult(it, None, "llm")
