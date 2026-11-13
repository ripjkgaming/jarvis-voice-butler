"""Always Aware voice controls (PLAN.md §6): parse + handle, one spoken line.

``parse(text)`` is the fast regex route (no model). ``handle(action, arg)``
is shared by the fast route and the ``awareness`` voice tool, runs in the
voice-agent process and only touches files (state.json, the logs), so it
works whether or not the bridge sidecar is up. Replies are <= 2 sentences.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import re
import time

from aware import state as st

ACTIONS = (
    "on",
    "off",
    "pause",
    "resume",
    "status",
    "seen",
    "at",
    "shadow_on",
    "shadow_off",
    "mute",
    "unmute",
    "spend_limit",
    "spend_limit_off",
    "paid",
    "sent",
    "cancel",
    "undo",
    "show",
    "tried",
    "answer",
    "carry_on",
    "leave_there",
)
#: spoken words -> vision task kinds (what offers.py mutes by)
KIND_ALIASES = (
    (r"code ?fix|fix(?:ing)? code|code", "code_fix"),
    (r"test", "test_fix"),
    (r"form", "web_form"),
    (r"research", "web_research"),
    (r"look ?up|lookup", "web_lookup"),
    (r"organi[sz]|tid(?:y|ying)|sort(?:ing)? files|moving files", "file_organise"),
    (r"convert", "file_convert"),
    (r"draft|document|write ?up|essay", "doc_draft"),
    (r"download", "download"),
)
WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "ten": 10,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty five": 45,
    "half an": 0.5,
    "half a": 0.5,
}
AWARE = r"(?:always[- ]aware|awareness|aware mode)"

_ROUTES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            rf"^(?:turn |switch )?{AWARE} (?:on|back on)$|^(?:turn|switch) on {AWARE}$"
        ),
        "on",
    ),
    (
        re.compile(
            rf"^(?:turn |switch )?{AWARE} off$|^(?:turn|switch) off {AWARE}$|^stop (?:being )?{AWARE}$"
        ),
        "off",
    ),
    (re.compile(rf"^pause {AWARE}(?: (?P<arg>(?:for|until) .+))?$"), "pause"),
    (re.compile(rf"^(?:resume|unpause) {AWARE}$"), "resume"),
    (re.compile(rf"^{AWARE} status$|^is {AWARE} on$"), "status"),
    (re.compile(r"^what did you (?:see|just see)$|^what am i doing$"), "seen"),
    (re.compile(r"^shadow mode on$|^turn on shadow mode$"), "shadow_on"),
    (re.compile(r"^shadow mode off$|^turn off shadow mode$"), "shadow_off"),
    (re.compile(r"^(?:stop|don'?t|do not) offer(?:ing)? (?:to )?(?P<arg>.+)$"), "mute"),
    (re.compile(r"^you can offer (?P<arg>.+?) again$"), "unmute"),
    (
        re.compile(
            r"^set (?:a |the )?spend(?:ing)? (?:limit|cap) (?:of |to )?(?P<arg>.+)$"
        ),
        "spend_limit",
    ),
    (
        re.compile(r"^(?:remove|clear|no) (?:the )?spend(?:ing)? (?:limit|cap)$"),
        "spend_limit_off",
    ),
    (
        re.compile(
            r"^what (?:have you|did you) (?:paid|pay|bought|buy|purchased?) (?:for )?(?P<arg>.*)$"
        ),
        "paid",
    ),
    (
        re.compile(
            r"^what (?:have you|did you) (?:sent|send|submitted|submit)(?P<arg>.*)$"
        ),
        "sent",
    ),
]


def _norm(text: str) -> str:
    t = (text or "").lower().replace("\u2019", "'")
    t = re.sub(r"^(?:jarvis|hey jarvis)[, ]+", "", t.strip())
    t = re.sub(r"[,.!?]+", " ", t)
    t = re.sub(r"^(?:please |could you |can you )", "", " ".join(t.split()))
    t = re.sub(r" please$", "", t)
    return t.strip()


def mute_key(arg: str) -> str:
    """'code fixes' -> 'code_fix'; 'that' -> the last offer's kind; else the
    subject words themselves (offers.py honours kind and subject mutes)."""
    key = _norm(arg).removeprefix("to ").removeprefix("me ").strip()
    key = key.removesuffix(" again").strip()
    if key in ("that", "this", "those", "that again", "it"):
        last = _last_offer_kind()
        return last or ""
    key = key.removeprefix("that ").removeprefix("the ").strip()
    for pattern, kind in KIND_ALIASES:
        if re.search(pattern, key):
            return kind
    return key


def _last_offer_kind() -> str:
    pend = _lazy("offers", "pending")
    with contextlib.suppress(Exception):
        cur = pend() if pend else None
        if cur and cur.get("kind"):
            return str(cur["kind"])
    hist = _lazy("offers", "history")
    with contextlib.suppress(Exception):
        rows = [r for r in (hist() if hist else []) if r.get("kind")]
        if rows:
            return str(rows[-1]["kind"])
    return ""


_CANCEL = re.compile(
    r"^(?:stop|cancel(?: that| it)?|i'?ve got it|i got it|leave it to me|stop that)$"
)
_CARRY = re.compile(r"^(?:carry on|go on|keep going|you can carry on)$")
_LEAVE = re.compile(r"^leave it (?:there|where it is)$")


def route(text: str) -> tuple[str, str] | None:
    """Fast route incl. context-only phrases: a yes/no is taken only while an
    offer is pending, "stop / I've got it / carry on / leave it there" only
    while a takeover runs. None = not ours."""
    with contextlib.suppress(Exception):
        pend = _lazy("offers", "pending")
        parse_pending = _lazy("offers", "parse_pending")
        if pend and parse_pending and len(text or "") <= 80 and pend():
            ans = parse_pending(text)
            if ans is not None:
                return "answer", str(getattr(ans, "value", ans))
    t = _norm(text)
    current = _lazy("takeover", "current")
    running = False
    with contextlib.suppress(Exception):
        running = bool(current and current())
    if running:
        if _CANCEL.match(t):
            return "cancel", ""
        if _CARRY.match(t):
            return "carry_on", ""
        if _LEAVE.match(t):
            return "leave_there", ""
    return parse(text)


def parse(text: str) -> tuple[str, str] | None:
    """Fast route for the clear phrasings; None = let the model decide."""
    t = _norm(text)
    if not t or len(t) > 90:
        return None
    for pattern, action in _ROUTES:
        m = pattern.match(t)
        if m:
            arg = (m.groupdict().get("arg") or "").strip()
            if action == "mute" and not arg:
                return None
            return action, arg
    return None


# ------------------------------------------------------------------ time words


def _num(word: str) -> float | None:
    word = word.strip()
    try:
        return float(word)
    except ValueError:
        return WORDS.get(word)


def duration_s(text: str) -> float | None:
    """'for an hour' / 'for 30 minutes' / 'half an hour' -> seconds."""
    t = _norm(text).removeprefix("for ").strip()
    if t in ("a bit", "a while", "now", ""):
        return 3600.0 if t in ("a bit", "a while") else None
    m = re.match(r"^(?P<n>[\w ]+?) ?(?P<u>hours?|hrs?|minutes?|mins?|h|m)$", t)
    if not m:
        return None
    n = _num(m.group("n"))
    if n is None:
        return None
    unit = 3600 if m.group("u").startswith("h") else 60
    return float(n) * unit


def clock_ts(text: str, now: float) -> float | None:
    """'5', '5pm', '17:30', '3:15 pm' -> the next such moment (today or past
    noon-ambiguous). For pausing ('until'), a time already passed is tomorrow."""
    t = _norm(text).replace(".", ":")
    m = re.match(r"^(\d{1,2})(?::(\d\d))? ?(am|pm|a m|p m)?$", t)
    if not m:
        return None
    h, mins = int(m.group(1)), int(m.group(2) or 0)
    ampm = (m.group(3) or "").replace(" ", "")
    if h > 23 or mins > 59:
        return None
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    base = dt.datetime.fromtimestamp(now)
    cand = base.replace(hour=h, minute=mins, second=0, microsecond=0)
    if not ampm and h < 12 and cand.timestamp() <= now:
        cand = cand.replace(hour=h + 12)  # "until 5" at 14:00 means 17:00
    if cand.timestamp() <= now:
        cand += dt.timedelta(days=1)
    return cand.timestamp()


def past_ts(text: str, now: float) -> float | None:
    """'3pm' / '15:00' / 'half an hour ago' / 'this morning' -> a past moment."""
    t = _norm(text).removeprefix("at ").removeprefix("around ").strip()
    m = re.match(r"^(.+?) ago$", t)
    if m:
        secs = duration_s(m.group(1))
        return now - secs if secs else None
    named = {
        "this morning": 10,
        "lunchtime": 13,
        "lunch": 13,
        "this afternoon": 15,
        "this evening": 19,
        "earlier": None,
    }
    if t in named:
        hour = named[t]
        if hour is None:
            return now - 3600
        return dt.datetime.fromtimestamp(now).replace(hour=hour, minute=0).timestamp()
    nxt = clock_ts(t, now)
    if nxt is None:
        return None
    m2 = re.match(r"^(\d{1,2})", t)
    explicit = bool(re.search(r"(am|pm|:\d\d)", t)) or (m2 and int(m2.group(1)) > 12)
    ts = nxt - 86_400 if nxt > now else nxt
    if not explicit:
        # "at 3" -> the most recent 3 o'clock (am or pm) before now
        alt = ts + 12 * 3600
        if alt <= now:
            ts = alt
    return ts


# ------------------------------------------------------------------ actions


_UNITS = [
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
]
_TENS = [
    "_",
    "_",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
]


def _spoken_number(text: str) -> str:
    """'fifty pounds' / 'two hundred and fifty' -> '50' / '250'; '' if none."""
    total, current, seen = 0, 0, False
    for word in re.findall(r"[a-z]+", (text or "").lower()):
        if word in _UNITS:
            current += _UNITS.index(word)
        elif word in _TENS:
            current += 10 * _TENS.index(word)
        elif word == "hundred":
            current = max(1, current) * 100
        elif word == "thousand":
            total += max(1, current) * 1000
            current = 0
        elif word in ("a", "and") or not seen:
            continue
        else:
            break
        seen = True
    value = total + current
    return str(value) if seen and value > 0 else ""


def _hm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _status(now: float) -> str:
    s = st.load()
    word = st.status_word(s, now)
    try:
        from aware import ledger

        b = ledger.summary(now)
        spend = f"{b['calls']} calls and {round(b['tokens'] / 1000)}k tokens today"
    except Exception:
        spend = "no usage figures"
    if word == "off":
        why = " The kill switch is set." if st.killed() or not st.env_allows() else ""
        return f"Always Aware is off, Sir.{why}"
    head = (
        f"Always Aware is paused until {_hm(float(s['paused_until']))}"
        if word == "paused"
        else "Always Aware is on"
    )
    if s.get("shadow"):
        head += ", in shadow mode"
    last = s.get("last_reason") or ""
    tail = f" Last tick: {last.replace('_', ' ')}." if last else ""
    progress = ""
    try:
        from aware.coordinator import status as poll_status

        components = poll_status().get("components", {})
        wa = components.get("whatsapp", {})
        if wa.get("selected") is not None:
            chats = wa.get("chats", [])
            complete = sum(c.get("status") == "complete" for c in chats)
            partial = sum(c.get("status") == "partial" for c in chats)
            progress += f" WhatsApp: {complete} of {wa['selected']} chats covered, {partial} partial."
        elif wa.get("status") == "running":
            progress += " The previous WhatsApp scan is still running."
        cal = components.get("calendar", {})
        if cal.get("staged") is not None:
            progress += f" Calendar: {cal['staged']} new messages staged."
        failures = [
            name for name, item in components.items() if item.get("status") == "error"
        ]
        if failures:
            progress += " Retrying: " + ", ".join(failures) + "."
    except (ImportError, TypeError, ValueError):
        pass
    return f"{head}, Sir: {spend}.{tail}{progress} Exercise detection is observation-only. Opening WhatsApp chats marks messages as read."


def _period(arg: str, now: float) -> tuple[float, str]:
    a = _norm(arg)
    today = dt.datetime.fromtimestamp(now).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if "today" in a:
        return today.timestamp(), "today"
    if "month" in a:
        return now - 30 * 86_400, "this month"
    if "yesterday" in a:
        return (today - dt.timedelta(days=1)).timestamp(), "since yesterday"
    return now - 7 * 86_400, "this week"


def _lazy(mod: str, attr: str):
    try:
        import importlib

        return getattr(importlib.import_module(f"aware.{mod}"), attr)
    except (ImportError, AttributeError):
        return None


def handle(action: str, arg: str = "", now: float | None = None) -> str:
    now = time.time() if now is None else now
    action = (action or "").strip().lower()
    arg = (arg or "").strip()
    if action == "on":
        if not st.env_allows():
            return "Always Aware is disabled by JARVIS_AWARE=0 on this machine, Sir."
        if st.killed():
            return "The Always Aware kill file is set, Sir. Remove ~/.jarvis/aware/KILL first."
        st.update(enabled=True, paused_until=0.0)
        return (
            "Always Aware is on, Sir. I'll glance at your other monitors every few "
            "minutes, never the HUD screen, and black out anything sensitive. "
            "I'll also check computer health and the first 20 WhatsApp chats. "
            "Opening chats marks messages as read in WhatsApp. "
            "Exercise detection is observation-only until desktop safety verification."
        )
    if action == "off":
        st.update(enabled=False, paused_until=0.0, phase="idle", phase_until=0.0)
        return "Always Aware is off, Sir. Nothing more will be captured."
    if action == "pause":
        a = _norm(arg)
        until = None
        if a.startswith("until "):
            until = clock_ts(a[6:], now)
        else:
            secs = duration_s(a) if a else 3600.0
            until = now + secs if secs else None
        if until is None:
            return "For how long, Sir? Say for an hour, or until five."
        until = min(until, now + 24 * 3600)
        st.update(paused_until=until)
        return f"Paused until {_hm(until)}, Sir."
    if action == "resume":
        st.update(paused_until=0.0)
        return (
            "Awareness resumed, Sir."
            if st.load().get("enabled")
            else ("Always Aware is off, Sir. Say always aware on to start it.")
        )
    if action == "status":
        return _status(now)
    if action == "seen":
        from aware import log as alog

        return alog.spoken_recent(alog.recent(3, now=now))
    if action == "at":
        from aware import spans

        ts = past_ts(arg, now) if arg else now
        if ts is None:
            return ""
        return spans.spoken_at(ts)
    if action in ("shadow_on", "shadow_off"):
        on = action == "shadow_on"
        set_shadow = _lazy("offers", "set_shadow")
        if set_shadow:
            set_shadow(on)
        else:
            st.update(shadow=on)
        return (
            "Shadow mode on, Sir: I'll log offers without saying them."
            if on
            else "Shadow mode off, Sir: I'll speak up when I can take something over."
        )
    if action in ("mute", "unmute"):
        key = mute_key(arg)
        if not key:
            return "Which kind of offer, Sir?"
        words = key.replace("_", " ")
        was = key in (st.load().get("muted") or {})
        fn = _lazy("offers", "mute" if action == "mute" else "unmute")
        if fn is not None:
            fn(key)
        else:
            muted = dict(st.load().get("muted") or {})
            if action == "mute":
                muted[key] = 0
            else:
                muted.pop(key, None)
            st.update(muted=muted)
        if action == "mute":
            return f"Understood, Sir. No more {words} offers until you say otherwise."
        return (
            f"Very well, Sir. I may offer {words} again."
            if was
            else (f"I wasn't holding back on {words}, Sir.")
        )
    if action == "spend_limit":
        m = re.search(r"(\d+(?:\.\d{1,2})?)", arg.replace(",", ""))
        amount = m.group(1) if m else _spoken_number(arg)
        if not amount:
            return "What limit, Sir? For example, fifty pounds."
        cur = (
            "USD"
            if ("$" in arg or "dollar" in arg.lower())
            else ("EUR" if ("€" in arg or "euro" in arg.lower()) else "GBP")
        )
        set_cap = _lazy("payments", "set_spend_cap")
        if set_cap is not None:
            set_cap(amount, cur)  # audited by payments.py
        else:
            st.update(spend_cap=amount, spend_currency=cur)
        sym = {"GBP": "£", "USD": "$", "EUR": "€"}[cur]
        return f"Spend limit set, Sir: no single payment over {sym}{amount}."
    if action == "spend_limit_off":
        set_cap = _lazy("payments", "set_spend_cap")
        if set_cap is not None:
            set_cap(None, str(st.load().get("spend_currency") or "GBP"))
        else:
            st.update(spend_cap=None)
        return "Spend limit removed, Sir."
    if action in ("paid", "sent"):
        from aware import transactions as tx

        since, when = _period(arg, now)
        kinds = ("payment",) if action == "paid" else ("send", "submit")
        rows = tx.query(kinds=kinds, since=since, until=now)
        verb = "paid for" if action == "paid" else "sent"
        return tx.spoken(rows, verb, when)
    if action == "answer":
        fn = _lazy("offers", "answer")
        if fn is None:
            return "I'm not offering anything just now, Sir."
        res = fn("", arg or "yes") or {}
        if res.get("ok"):
            return res.get("say") or "Very well, Sir."
        return {
            "expired": "That offer has lapsed, Sir.",
            "already_answered": "You've already answered that one, Sir.",
        }.get(res.get("reason", ""), "I'm not offering anything just now, Sir.")
    if action in ("carry_on", "leave_there"):
        fn = _lazy("brave_live", action)
        line = ""
        with contextlib.suppress(Exception):
            line = fn() if fn else ""
        return line or "There's nothing of mine in progress, Sir."
    if action == "cancel":
        fn = _lazy("takeover", "request_cancel")
        if fn is None:
            return "Nothing of mine is running, Sir."
        fn()
        return "Stopping, Sir. I'll put things back as they were."
    if action == "undo":
        fn = _lazy("undo_journal", "undo_last")
        if fn is None:
            return "I have nothing to undo, Sir."
        try:
            n = (
                max(1, min(10, int(re.search(r"\d+", arg).group())))
                if re.search(r"\d+", arg)
                else 1
            )
        except (AttributeError, ValueError):
            n = 1
        reports = fn(n) or []
        restored = sum(len(getattr(r, "restored", []) or []) for r in reports)
        left = [p for r in reports for p in (getattr(r, "left_alone", []) or [])]
        line = f"Undone, Sir: {restored} file{'s' if restored != 1 else ''} restored."
        if left:
            names = ", ".join(p.rsplit("/", 1)[-1] for p in left[:3])
            line += f" I left {names} alone because you changed them since."
        return line if reports else "I have nothing to undo, Sir."
    if action == "show":
        fn = _lazy("takeover", "show_last")
        return (fn() if fn else "") or "There's nothing to show yet, Sir."
    if action == "tried":
        fn = _lazy("takeover", "what_did_you_try")
        return (fn() if fn else "") or "I haven't tried anything yet, Sir."
    return "I don't know that awareness command, Sir."
