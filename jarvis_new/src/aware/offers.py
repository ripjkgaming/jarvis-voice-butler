"""Always Aware offer delivery and learning (PLAN.md §3.2, §3.3).

``maybe_offer(opp, gate)`` is the contract entry. In order it:
  * never offers when the gate did not pass (or has no plan);
  * suppresses: muted kind, same fingerprint answered in the last 24 h,
    guest/private/locked, offer text that cannot state the sends/payments;
  * defers (caller may retry; expires 10 min after the first deferral):
    focus/school/DND/quiet hours/live call, no natural pause (idle 30-600 s),
    rate (<= 1 per 30 min, <= 6 per day), another offer pending;
  * shadow mode: logs the offer, does not speak, returns suppressed("shadow");
  * otherwise speaks one sentence via the injected speaker, opens a listening
    turn via the injected listener (optional), and waits up to 20 s for
    ``answer(offer_id, reply)`` (voice tool / bridge fast route). Silence is a
    soft decline. "never offer that" mutes the kind until re-enabled. Three
    declines of a kind in 7 days mute it for 7 days, said once.

Files under ~/.jarvis/aware/: ``offers.jsonl`` (append-only history: offer,
answer and outcome events) and ``offers/<id>.json`` (the pending offer, TTL
90 s). ``history()`` merges events per offer for feasibility.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation

from aware import state as st
from aware.types import (
    Consequential,
    GateResult,
    OfferAnswer,
    OfferOutcome,
    OfferStatus,
    Opportunity,
)

RATE_GAP_S = 30 * 60
DAILY_MAX = 6
COOLDOWN_S = 24 * 3600
ANSWER_TIMEOUT_S = 20.0
PENDING_TTL_S = 90.0
DEFER_EXPIRY_S = 10 * 60
IDLE_MIN_S = 30.0
IDLE_MAX_S = 600.0
MUTE_DECLINES = 3
MUTE_WINDOW_S = 7 * 86400
MUTE_FOR_S = 7 * 86400
MAX_WORDS = 30
POLL_S = 0.25
ASSIST_KINDS = ("worksheet_help",)
ASSIST_GAP_S = 5 * 60
ASSIST_DAILY_MAX = 12
ASSIST_COOLDOWN_S = 2 * 3600

SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}
NUMBERS = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
]
KIND_WORDS = {
    "code_fix": "code fixes",
    "test_fix": "test fixes",
    "web_form": "web forms",
    "web_research": "research",
    "web_lookup": "web lookups",
    "file_organise": "file tidying",
    "file_convert": "file conversions",
    "doc_draft": "drafts",
    "download": "downloads",
    "worksheet_help": "worksheet hints",
}

Speaker = Callable[[str], Awaitable[None]]
Listener = Callable[[str, str], Awaitable[None]]
_HOOKS: dict = {"speaker": None, "listener": None}
_DEFERRED: dict[str, float] = {}  # fingerprint -> first deferral ts


def set_speaker(fn: Speaker | None) -> None:
    """P1 injects ``async fn(text)`` that speaks one line."""
    _HOOKS["speaker"] = fn


def set_listener(fn: Listener | None) -> None:
    """Optional: P1 injects ``async fn(offer_id, text)`` that opens one
    listening turn (bridge ``_launch_voice({"talk": True})``)."""
    _HOOKS["listener"] = fn


# ------------------------------------------------------------------ storage
def history_path():
    return st.aware_dir() / "offers.jsonl"


def pending_dir():
    return st.sub("offers")


def _append(row: dict) -> None:
    st.append_private(history_path(), json.dumps(row, default=str, sort_keys=True))


def events() -> list[dict]:
    try:
        lines = history_path().read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        with contextlib.suppress(ValueError):
            row = json.loads(line)
            if isinstance(row, dict):
                out.append(row)
    return out


def history() -> list[dict]:
    """One merged dict per offer: {id, ts, kind, subject, fingerprint, text,
    shadow, answer, answer_ts, outcome}. Oldest first."""
    merged: dict[str, dict] = {}
    for row in events():
        oid = str(row.get("id") or "")
        if not oid:
            continue
        event = row.get("event", "offer")
        if event == "offer":
            merged[oid] = {**row, "answer": row.get("answer"), "outcome": None}
        elif oid in merged:
            if event == "answer":
                merged[oid]["answer"] = row.get("answer")
                merged[oid]["answer_ts"] = row.get("ts")
            elif event == "outcome":
                merged[oid]["outcome"] = row.get("outcome")
    return list(merged.values())


# ------------------------------------------------------------- pure helpers
def _num(n: float) -> str:
    n = max(1, round(n))
    return NUMBERS[n] if n < len(NUMBERS) else str(n)


def _minutes(est: float) -> str:
    n = max(1, round(est or 1))
    return f"about {_num(n)} minute{'s' if n != 1 else ''}"


def _money_text(amount: str, currency: str) -> str:
    cur = (currency or "").upper()
    try:
        amt = f"{Decimal(str(amount)):.2f}"
    except (InvalidOperation, ValueError):
        amt = str(amount)
    sym = SYMBOLS.get(cur)
    return f"{sym}{amt}" if sym else f"{amt} {cur}"


def _clean(text: str) -> str:
    return " ".join(str(text or "").split()).strip().rstrip(".")


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text[:2] != text[:2].upper() else text


def action_sentence(act: Consequential) -> str:
    kind = (act.kind or "").lower()
    if kind == "payment":
        items = ", ".join(_clean(i) for i in act.items if _clean(i))
        method = _clean(act.payment_method) or "saved card"
        money = _money_text(act.amount, act.currency)
        return (
            f"That means paying {_clean(act.merchant)} for {items} with your "
            f"{method}, total {money}."
        )
    if kind == "send":
        gist = _clean(act.content)[:140]
        return f'It sends {_clean(act.recipient)} this: "{gist}".'
    if kind == "submit":
        return f"It ends by submitting the form on {_clean(act.site)}."
    return ""


def offer_text(opp: Opportunity, gate: GateResult) -> str:
    """One spoken sentence (<= 30 words) plus one exact sentence per
    send/submit/payment, ending with a yes/no question. Pure."""
    plan = gate.plan
    title = (
        _clean(
            opp.title
            or (plan.title if plan else "")
            or opp.summary
            or opp.evidence.get("title", "")
        )
        or "finish that task"
    )
    subject = _clean(opp.subject or opp.evidence.get("subject", ""))
    outcome = _clean(opp.outcome or opp.evidence.get("outcome", ""))
    est = opp.est_minutes or (plan.est_minutes if plan else 0) or 5
    what = _lower_first(title)
    if subject and subject.lower() not in what.lower():
        what = f"{what} in {subject}"
    tail = "Shall I take over?"
    head = f"Sir, I can {what} myself, {_minutes(est)}"
    line = (
        f"{head}, so that {_lower_first(outcome)}. {tail}"
        if outcome
        else f"{head}. {tail}"
    )
    if len(line.split()) > MAX_WORDS:
        line = f"{head}. {tail}"
    words = line.split()
    if len(words) > MAX_WORDS:  # very long title: trim it, keep the question
        keep = MAX_WORDS - len(tail.split())
        line = " ".join(words[:keep]).rstrip(",.") + ". " + tail
    actions = list(plan.actions) if plan else []
    if actions:
        exact = " ".join(s for s in (action_sentence(a) for a in actions) if s)
        line = line[: -len(tail)] + exact + " " + tail
    return line


def states_actions(text: str, actions: list[Consequential]) -> bool:
    """True when the text names every send/submit/payment exactly. Pure."""
    low = (text or "").lower()
    for act in actions:
        kind = (act.kind or "").lower()
        if kind == "payment":
            money = _money_text(act.amount, act.currency).lower()
            if not act.merchant.strip() or act.merchant.lower() not in low:
                return False
            if money not in low:
                return False
            if not act.items or any(i.lower() not in low for i in act.items):
                return False
        elif kind == "send":
            if not act.recipient.strip() or act.recipient.lower() not in low:
                return False
            if not act.content.strip() or _clean(act.content)[:40].lower() not in low:
                return False
        elif kind == "submit":
            if not act.site.strip() or act.site.lower() not in low:
                return False
        else:
            return False
    return True


_YES = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|go ahead|do it|please do|"
    r"take over|go for it|carry on|proceed|please|affirmative)\b",
    re.I,
)
_NEVER = re.compile(
    r"\b(?:never (?:offer|ask|suggest)|stop offering|don'?t (?:ever )?"
    r"offer (?:that|this|those) again|don'?t offer)\b",
    re.I,
)
_NO = re.compile(
    r"^\s*(?:no|nope|nah|not now|later|leave it|i'?ve got it|i got it|"
    r"don'?t|no thanks|cancel|negative)\b",
    re.I,
)


def parse_answer(text: str) -> OfferAnswer | None:
    """Fast regex route for a spoken reply. "never" beats "no". Pure."""
    text = (text or "").strip()
    if not text:
        return None
    if _NEVER.search(text):
        return OfferAnswer.NEVER
    if _NO.search(text):
        return OfferAnswer.NO
    if _YES.search(text):
        return OfferAnswer.YES
    return None


def _kind(opp: Opportunity) -> str:
    return (
        opp.task_kind or str(opp.evidence.get("vision_kind") or "") or opp.kind
    ).lower()


def _start_of_day(now: float) -> float:
    d = datetime.datetime.fromtimestamp(now)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def check(
    opp: Opportunity,
    gate: GateResult,
    *,
    state: dict,
    hist: list[dict],
    ctx: dict,
    now: float,
    pending: bool = False,
) -> tuple[str, str] | None:
    """(status, reason) when the offer must not be made now, else None. Pure."""
    if not gate or not gate.passed or gate.plan is None:
        return OfferStatus.SUPPRESSED.value, "gate_fail"
    kind = _kind(opp)
    subject = (opp.subject or str(opp.evidence.get("subject") or "")).strip().lower()
    if st.is_muted(state, kind, now) or (subject and st.is_muted(state, subject, now)):
        return OfferStatus.SUPPRESSED.value, "muted"
    fp = opp.fingerprint()
    for row in hist:
        if (
            row.get("fingerprint") == fp
            and now - float(row.get("ts") or 0) < COOLDOWN_S
        ):
            return OfferStatus.SUPPRESSED.value, "cooldown"
    for flag in ("guest", "private", "locked"):
        if ctx.get(flag):
            return OfferStatus.SUPPRESSED.value, flag
    for flag in ("in_call", "focus", "school", "dnd", "quiet_hours"):
        if ctx.get(flag):
            return OfferStatus.DEFERRED.value, flag
    if pending:
        return OfferStatus.DEFERRED.value, "offer_pending"
    offered = [float(r.get("ts") or 0) for r in hist]
    if any(now - ts < RATE_GAP_S for ts in offered):
        return OfferStatus.DEFERRED.value, "rate_30m"
    day0 = _start_of_day(now)
    if sum(1 for ts in offered if ts >= day0) >= DAILY_MAX:
        return OfferStatus.DEFERRED.value, "daily_cap"
    idle = ctx.get("idle_s")
    if idle is None:
        return OfferStatus.DEFERRED.value, "pause_unknown"
    if float(idle) < IDLE_MIN_S:
        return OfferStatus.DEFERRED.value, "busy"
    if float(idle) > IDLE_MAX_S:
        return OfferStatus.DEFERRED.value, "away"
    return None


# --------------------------------------------------------------- live context
def live_context(now: float) -> dict:
    """Hold flags from the machine. Fail-soft; guest/private/locked fail closed."""
    from aware import skip

    def safe(fn, default):
        try:
            return fn()
        except Exception:
            return default

    def quiet():
        from proactive import policy

        return policy.in_quiet_hours(
            datetime.datetime.fromtimestamp(now), policy.quiet_hours()
        )

    ctx = {
        "focus": safe(lambda: __import__("focus").is_active(), False),
        "school": skip.school_mode(),
        "dnd": safe(lambda: __import__("dnd").active(now), False),
        "quiet_hours": safe(quiet, False),
        "idle_s": safe(lambda: __import__("input_idle").idle_seconds(now=now), None),
    }
    try:
        import phone_session
        import watch_mode

        ctx["guest"] = skip._safe(phone_session.guest_required, True) or skip._safe(
            watch_mode.guest_required, True
        )
        ctx["private"] = skip._safe(phone_session.has_private_calls, True)
        ctx["locked"] = skip._safe(watch_mode.session_locked, True)
    except Exception:
        ctx.update(guest=True, private=True, locked=True)
    return ctx


# ------------------------------------------------------------------ pending
def _pending_path(offer_id: str):
    safe = re.sub(r"[^A-Za-z0-9_-]", "", offer_id)[:40] or "x"
    return pending_dir() / f"{safe}.json"


def _read(path) -> dict | None:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def pending(now: float | None = None) -> dict | None:
    """The newest unexpired, unanswered pending offer (voice agent seed)."""
    now = time.time() if now is None else now
    best = None
    with contextlib.suppress(OSError):
        for path in pending_dir().glob("*.json"):
            data = _read(path)
            if not data or data.get("answer") or float(data.get("expires", 0)) < now:
                continue
            if best is None or data.get("created", 0) > best.get("created", 0):
                best = data
    return best


ACK = {
    "yes": "Right away, Sir.",
    "no": "Very well, Sir.",
    "silent": "",
    "never": "Very good, Sir. I won't offer {what} again until you ask.",
}


def answer(offer_id: str, reply, now: float | None = None) -> dict:
    """Record Sir's reply for a pending offer (cross-process, sync).

    ``reply`` is an OfferAnswer, its value (yes|no|never|silent) or raw text
    (routed by ``parse_answer``); ``offer_id`` "" means the current pending
    offer. Called by the voice tool ``aware_offer(id, answer)`` and the bridge
    fast routes. The waiting ``maybe_offer`` in the sidecar picks the answer up
    and returns accepted/declined there, so the sidecar starts the takeover.

    Returns {"ok": bool, "say": str, "plan": dict | None, "answer": str,
    "reason": str}: ``say`` is the short acknowledgement for the voice agent
    to speak (the sidecar does not repeat it); ``plan`` is the TakeoverPlan
    dict on yes, for display only."""
    now = time.time() if now is None else now
    fail = {"ok": False, "say": "", "plan": None, "answer": "", "reason": ""}
    if not offer_id:
        current = pending(now)
        offer_id = current["id"] if current else ""
    path = _pending_path(offer_id)
    data = _read(path) if offer_id else None
    if not data:
        return {**fail, "reason": "no_offer"}
    if data.get("answer"):
        return {**fail, "reason": "already_answered"}
    if float(data.get("expires", 0)) < now:
        return {**fail, "reason": "expired"}
    if str(data.get("kind") or "") in ASSIST_KINDS:
        return _answer_choice(path, data, reply, now, fail)
    if isinstance(reply, OfferAnswer):
        ans = reply
    else:
        try:
            ans = OfferAnswer(str(reply).strip().lower())
        except ValueError:
            ans = parse_answer(str(reply))
    if ans is None:
        return {**fail, "reason": "unparsed"}
    data["answer"] = ans.value
    data["answer_ts"] = now
    if not st.write_private(path, json.dumps(data)):
        return {**fail, "reason": "write_failed"}
    what = KIND_WORDS.get(str(data.get("kind") or ""), "those")
    return {
        "ok": True,
        "say": ACK[ans.value].format(what=what),
        "plan": data.get("plan") if ans == OfferAnswer.YES else None,
        "answer": ans.value,
        "reason": "",
    }


CHOICE_ACK = {
    "complete": "Right away, Sir. Filling it in now.",
    "hint": "Of course, Sir.",
    "leave": "Very well, Sir.",
    "never": "Very good, Sir. I won't offer worksheet help again until you ask.",
}
_CHOICE_ANSWER = {"complete": "yes", "hint": "yes", "leave": "no", "never": "never"}


def _answer_choice(path, data: dict, reply, now: float, fail: dict) -> dict:
    if isinstance(reply, OfferAnswer):
        choice = {"yes": "hint", "no": "leave", "silent": None, "never": "never"}[
            reply.value
        ]
    else:
        choice = parse_choice(str(reply))
    if choice is None:
        return {**fail, "reason": "unparsed"}
    data.update(answer=_CHOICE_ANSWER[choice], choice=choice, answer_ts=now)
    if not st.write_private(path, json.dumps(data)):
        return {**fail, "reason": "write_failed"}
    return {
        "ok": True,
        "say": CHOICE_ACK[choice],
        "plan": None,
        "answer": _CHOICE_ANSWER[choice],
        "choice": choice,
        "reason": "",
    }


async def _wait_choice(offer_id: str, deadline: float, clock) -> str:
    path = _pending_path(offer_id)
    while clock() < deadline:
        data = _read(path)
        if data and data.get("choice"):
            return str(data["choice"])
        await asyncio.sleep(POLL_S)
    return "silent"


async def _wait_answer(offer_id: str, deadline: float, clock) -> OfferAnswer:
    path = _pending_path(offer_id)
    while clock() < deadline:
        data = _read(path)
        if data and data.get("answer"):
            with contextlib.suppress(ValueError):
                return OfferAnswer(data["answer"])
        await asyncio.sleep(POLL_S)
    return OfferAnswer.SILENT


# ------------------------------------------------------------------ learning
def _declines(kind: str, now: float, hist: list[dict]) -> int:
    return sum(
        1
        for r in hist
        if r.get("kind") == kind
        and not r.get("shadow")
        and r.get("answer") in ("no", "silent", "never")
        and now - float(r.get("answer_ts") or r.get("ts") or 0) < MUTE_WINDOW_S
    )


def mute(kind: str, until: float = 0.0) -> None:
    """Mute a kind (0 = until re-enabled, e.g. "stop offering X")."""
    state = st.load()
    muted = dict(state.get("muted") or {})
    muted[kind.lower()] = until
    st.update(muted=muted)


def unmute(kind: str) -> None:
    state = st.load()
    muted = dict(state.get("muted") or {})
    muted.pop(kind.lower(), None)
    st.update(muted=muted)


def set_shadow(on: bool) -> None:
    st.update(shadow=bool(on))


def record_outcome(
    offer_id: str,
    outcome: str,
    *,
    fixture: dict | None = None,
    now: float | None = None,
) -> None:
    """Takeover end (verified|failed|stuck|cancelled). Stuck raises the kind's
    threshold and logs a gate miss (feasibility.py)."""
    now = time.time() if now is None else now
    _append({"ts": now, "id": offer_id, "event": "outcome", "outcome": outcome})
    if outcome == "stuck":
        row = next((r for r in history() if r.get("id") == offer_id), None)
        if row and row.get("kind"):
            from aware import feasibility

            feasibility.record_stuck(
                row["kind"], fixture or {"offer": offer_id}, now=now
            )


# ------------------------------------------------------------------ contract
async def _say(text: str) -> bool:
    fn = _HOOKS["speaker"]
    if fn is None:
        return False
    try:
        await fn(text)
        return True
    except Exception:
        return False


async def maybe_offer(
    opp: Opportunity,
    gate: GateResult,
    *,
    now: float | None = None,
    ctx: dict | None = None,
    clock=None,
    answer_timeout: float = ANSWER_TIMEOUT_S,
) -> OfferOutcome:
    """Contract entry (see module doc). Never raises."""
    clock = clock or time.time
    now = clock() if now is None else now
    try:
        return await _maybe_offer(opp, gate, now, ctx, clock, answer_timeout)
    except Exception as exc:
        return OfferOutcome(OfferStatus.SUPPRESSED.value, f"error:{type(exc).__name__}")


async def _maybe_offer(opp, gate, now, ctx, clock, answer_timeout) -> OfferOutcome:
    t0 = clock()

    def at() -> float:  # event time on the caller's timeline (now may be injected)
        return now + max(0.0, clock() - t0)

    state = st.load()
    hist = history()
    live = {} if ctx is not None else live_context(now)
    merged = {**live, **(ctx or {})}
    merged.update(
        {k: v for k, v in (opp.evidence.get("context") or {}).items() if v is not None}
    )
    fp = opp.fingerprint()
    verdict = check(
        opp,
        gate,
        state=state,
        hist=hist,
        ctx=merged,
        now=now,
        pending=pending(now) is not None,
    )
    if verdict:
        status, reason = verdict
        if status == OfferStatus.DEFERRED.value:
            first = _DEFERRED.setdefault(fp, now)
            if now - first > DEFER_EXPIRY_S:
                return OfferOutcome(OfferStatus.SUPPRESSED.value, "expired")
        return OfferOutcome(status, reason)
    _DEFERRED.pop(fp, None)

    text = offer_text(opp, gate)
    if not states_actions(text, list(gate.plan.actions)):
        return OfferOutcome(
            OfferStatus.SUPPRESSED.value, "offer_text_incomplete", text=text
        )
    gate.plan.offer_text = text
    kind = _kind(opp)
    offer_id = "of-" + hashlib.sha256(f"{fp}|{now}".encode()).hexdigest()[:12]
    row = {
        "ts": now,
        "id": offer_id,
        "event": "offer",
        "fingerprint": fp,
        "kind": kind,
        "subject": opp.subject or opp.evidence.get("subject", ""),
        "text": text,
        "plan_id": gate.plan.id,
        "shadow": bool(state.get("shadow")),
    }

    if state.get("shadow"):
        _append(row)
        return OfferOutcome(
            OfferStatus.SUPPRESSED.value, "shadow", offer_id, text, False
        )
    if _HOOKS["speaker"] is None:
        return OfferOutcome(OfferStatus.SUPPRESSED.value, "no_speaker", text=text)

    st.write_private(
        _pending_path(offer_id),
        json.dumps(
            {
                "id": offer_id,
                "text": text,
                "kind": kind,
                "plan_id": gate.plan.id,
                "plan": gate.plan.to_dict(),
                "created": now,
                "expires": now + PENDING_TTL_S,
                "answer": None,
            }
        ),
    )
    _append(row)
    if not await _say(text):
        _append({"ts": at(), "id": offer_id, "event": "answer", "answer": "silent"})
        with contextlib.suppress(OSError):
            _pending_path(offer_id).unlink()
        return OfferOutcome(
            OfferStatus.SUPPRESSED.value, "speak_failed", offer_id, text
        )
    listener = _HOOKS["listener"]
    if listener is not None:
        with contextlib.suppress(Exception):
            await listener(offer_id, text)
    ans = await _wait_answer(offer_id, clock() + answer_timeout, clock)
    t_ans = at()
    _append({"ts": t_ans, "id": offer_id, "event": "answer", "answer": ans.value})
    with contextlib.suppress(OSError):
        _pending_path(offer_id).unlink()

    if ans == OfferAnswer.YES:
        return OfferOutcome(OfferStatus.ACCEPTED.value, "", offer_id, text, True)
    if ans == OfferAnswer.NEVER:
        mute(kind, 0.0)  # answer() returned the spoken ack to the voice agent
        return OfferOutcome(OfferStatus.DECLINED.value, "never", offer_id, text, True)
    if _declines(kind, t_ans, history()) >= MUTE_DECLINES and not st.is_muted(
        st.load(), kind, t_ans
    ):
        mute(kind, t_ans + MUTE_FOR_S)
        await _say(
            f"Understood, Sir. I'll hold off on {KIND_WORDS.get(kind, 'those')} "
            "for a week."
        )
    return OfferOutcome(OfferStatus.DECLINED.value, ans.value, offer_id, text, True)


# ------------------------------------------------------------------ assists
def is_assist(opp: Opportunity) -> bool:
    """Hint-only kinds: spoken help, no gate, no takeover."""
    return _kind(opp) in ASSIST_KINDS


def assist_text(opp: Opportunity) -> str:
    """Short spoken question offering a hint. Pure."""
    title = _clean(opp.title or opp.summary) or "that question"
    tail = "Shall I complete the worksheet, give you a hint, or leave it?"
    line = f"Sir, {_lower_first(title)} looks a little stubborn. {tail}"
    if len(line.split()) > MAX_WORDS:
        line = f"Sir, that question looks a little stubborn. {tail}"
    return line


_COMPLETE = re.compile(
    r"\b(?:complete|finish|fill (?:it|them|that|the \w+) in|fill in|do (?:it|the "
    r"worksheet|all of it|them)|answer (?:it|them)|you do it|go ahead)\b",
    re.I,
)
_HINT = re.compile(
    r"\b(?:hint|help|clue|explain|how do i|show me how|yes|sure|please)\b", re.I
)
_LEAVE = re.compile(
    r"\b(?:leave (?:it|me)|no|nope|nah|not now|later|i'?ve got it|i got it|"
    r"i'?m (?:fine|good|ok)|cancel)\b",
    re.I,
)
CHOICES = ("complete", "hint", "leave", "never")


def parse_choice(text: str) -> str | None:
    """Reply to a worksheet offer -> complete | hint | leave | never. Pure.
    "never" beats everything, then complete, leave, hint (a bare yes = hint)."""
    text = (text or "").strip()
    if not text:
        return None
    if text.lower() in CHOICES:
        return text.lower()
    if _NEVER.search(text):
        return "never"
    if _COMPLETE.search(text):
        return "complete"
    if _LEAVE.search(text):
        return "leave"
    if _HINT.search(text):
        return "hint"
    return None


def parse_pending(text: str, now: float | None = None) -> str | None:
    """Reply text -> the answer value for whatever offer is pending (a choice
    for a worksheet offer, yes/no/never otherwise). None = not an answer."""
    current = pending(now)
    if current is None:
        return None
    if str(current.get("kind") or "") in ASSIST_KINDS:
        return parse_choice(text)
    ans = parse_answer(text)
    return ans.value if ans is not None else None


def assist_check(
    opp: Opportunity,
    *,
    state: dict,
    hist: list[dict],
    ctx: dict,
    now: float,
    pending: bool = False,
) -> tuple[str, str] | None:
    """Like ``check`` for hint offers: no gate, shorter gaps. Pure."""
    kind = _kind(opp)
    if not (opp.outcome or "").strip():
        return OfferStatus.SUPPRESSED.value, "no_hint"
    if st.is_muted(state, kind, now):
        return OfferStatus.SUPPRESSED.value, "muted"
    fp = opp.fingerprint()
    mine = [r for r in hist if r.get("kind") == kind]
    for row in mine:
        if (
            row.get("fingerprint") == fp
            and now - float(row.get("ts") or 0) < ASSIST_COOLDOWN_S
        ):
            return OfferStatus.SUPPRESSED.value, "cooldown"
    for flag in ("guest", "private", "locked"):
        if ctx.get(flag):
            return OfferStatus.SUPPRESSED.value, flag
    for flag in ("in_call", "focus", "dnd", "quiet_hours"):
        if ctx.get(flag):
            return OfferStatus.DEFERRED.value, flag
    if pending:
        return OfferStatus.DEFERRED.value, "offer_pending"
    offered = [float(r.get("ts") or 0) for r in mine]
    if any(now - ts < ASSIST_GAP_S for ts in offered):
        return OfferStatus.DEFERRED.value, "rate_5m"
    day0 = _start_of_day(now)
    if sum(1 for ts in offered if ts >= day0) >= ASSIST_DAILY_MAX:
        return OfferStatus.DEFERRED.value, "daily_cap"
    idle = ctx.get("idle_s")
    if idle is None:
        return OfferStatus.DEFERRED.value, "pause_unknown"
    if float(idle) < IDLE_MIN_S:
        return OfferStatus.DEFERRED.value, "busy"
    if float(idle) > IDLE_MAX_S:
        return OfferStatus.DEFERRED.value, "away"
    return None


async def maybe_assist(
    opp: Opportunity,
    *,
    now: float | None = None,
    ctx: dict | None = None,
    clock=None,
    answer_timeout: float = ANSWER_TIMEOUT_S,
) -> OfferOutcome:
    """Offer a spoken hint; on yes, speak it. Never raises, never takes over."""
    clock = clock or time.time
    now = clock() if now is None else now
    try:
        return await _maybe_assist(opp, now, ctx, clock, answer_timeout)
    except Exception as exc:
        return OfferOutcome(OfferStatus.SUPPRESSED.value, f"error:{type(exc).__name__}")


async def _maybe_assist(opp, now, ctx, clock, answer_timeout) -> OfferOutcome:
    t0 = clock()
    state = st.load()
    hist = history()
    live = {} if ctx is not None else live_context(now)
    merged = {**live, **(ctx or {})}
    merged.update(
        {k: v for k, v in (opp.evidence.get("context") or {}).items() if v is not None}
    )
    fp = opp.fingerprint()
    verdict = assist_check(
        opp,
        state=state,
        hist=hist,
        ctx=merged,
        now=now,
        pending=pending(now) is not None,
    )
    if verdict:
        status, reason = verdict
        if status == OfferStatus.DEFERRED.value:
            first = _DEFERRED.setdefault(fp, now)
            if now - first > DEFER_EXPIRY_S:
                return OfferOutcome(OfferStatus.SUPPRESSED.value, "expired")
        return OfferOutcome(status, reason)
    _DEFERRED.pop(fp, None)

    text = assist_text(opp)
    kind = _kind(opp)
    offer_id = "of-" + hashlib.sha256(f"{fp}|{now}".encode()).hexdigest()[:12]
    row = {
        "ts": now,
        "id": offer_id,
        "event": "offer",
        "fingerprint": fp,
        "kind": kind,
        "subject": opp.subject or opp.evidence.get("subject", ""),
        "text": text,
        "plan_id": "",
        "shadow": bool(state.get("shadow")),
    }
    if state.get("shadow"):
        _append(row)
        return OfferOutcome(
            OfferStatus.SUPPRESSED.value, "shadow", offer_id, text, False
        )
    if _HOOKS["speaker"] is None:
        return OfferOutcome(OfferStatus.SUPPRESSED.value, "no_speaker", text=text)
    st.write_private(
        _pending_path(offer_id),
        json.dumps(
            {
                "id": offer_id,
                "text": text,
                "kind": kind,
                "plan_id": "",
                "plan": None,
                "created": now,
                "expires": now + PENDING_TTL_S,
                "answer": None,
            }
        ),
    )
    _append(row)
    if not await _say(text):
        _append({"ts": now, "id": offer_id, "event": "answer", "answer": "silent"})
        with contextlib.suppress(OSError):
            _pending_path(offer_id).unlink()
        return OfferOutcome(
            OfferStatus.SUPPRESSED.value, "speak_failed", offer_id, text
        )
    listener = _HOOKS["listener"]
    if listener is not None:
        with contextlib.suppress(Exception):
            await listener(offer_id, text)
    choice = await _wait_choice(offer_id, clock() + answer_timeout, clock)
    t_ans = now + max(0.0, clock() - t0)
    answer_value = _CHOICE_ANSWER.get(choice, "silent")
    _append(
        {
            "ts": t_ans,
            "id": offer_id,
            "event": "answer",
            "answer": answer_value,
            "choice": choice,
        }
    )
    with contextlib.suppress(OSError):
        _pending_path(offer_id).unlink()
    ans = OfferAnswer(answer_value)
    if choice == "hint":
        await _say(_clean(opp.outcome) + ".")
        return OfferOutcome(OfferStatus.ACCEPTED.value, "hint", offer_id, text, True)
    if choice == "complete":
        line = await _complete(opp, offer_id)
        await _say(line)
        return OfferOutcome(
            OfferStatus.ACCEPTED.value, "complete", offer_id, text, True
        )
    if ans == OfferAnswer.NEVER:
        mute(kind, 0.0)
        return OfferOutcome(OfferStatus.DECLINED.value, "never", offer_id, text, True)
    if _declines(kind, t_ans, history()) >= MUTE_DECLINES and not st.is_muted(
        st.load(), kind, t_ans
    ):
        mute(kind, t_ans + 3600.0)  # worksheet help: back off an hour, not a week
        await _say("Understood, Sir. I'll leave your worksheets alone for an hour.")
    return OfferOutcome(OfferStatus.DECLINED.value, ans.value, offer_id, text, True)


_COMPLETER: dict = {"fn": None}  # tests inject; default aware.worksheet.complete


def set_completer(fn) -> None:
    _COMPLETER["fn"] = fn


async def _complete(opp: Opportunity, offer_id: str = "") -> str:
    """Run the worksheet completion off the event loop; returns its spoken line."""
    fn = _COMPLETER["fn"]
    if fn is None:
        from aware import worksheet

        fn = worksheet.complete
    title = str(opp.evidence.get("window_title") or opp.evidence.get("title") or "")
    context = {
        "question": opp.title,
        "current": opp.evidence.get("text", ""),
        "summary": opp.evidence.get("summary", ""),
    }
    try:
        res = await asyncio.to_thread(fn, title, context)
    except Exception as exc:
        return f"I couldn't complete it, Sir: {type(exc).__name__}."
    _append(
        {
            "ts": time.time(),
            "id": offer_id,
            "event": "outcome",
            "kind": "worksheet_help",
            "outcome": "completed" if getattr(res, "ok", False) else "failed",
            "applied": getattr(res, "applied", 0),
            "reason": getattr(res, "reason", ""),
        }
    )
    return str(getattr(res, "spoken", "") or "Done, Sir.")
