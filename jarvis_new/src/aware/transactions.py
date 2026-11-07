"""Mandatory audit of every send / submit / payment (PLAN.md §4.4).

``~/.jarvis/aware/transactions.jsonl`` is append-only and 0600; failed
attempts are recorded as well. Each entry is mirrored into the aware audit
and ``~/.jarvis/actions.log``. Amounts are Decimal, stored as strings.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from decimal import Decimal, InvalidOperation

from aware import state as st

KINDS = ("send", "submit", "payment")
SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}


def path():
    return st.aware_dir() / "transactions.jsonl"


def audit(event: dict, now: float | None = None) -> None:
    """Append to aware/audit.jsonl and mirror one 'aware ...' actions.log line."""
    row = {"ts": time.time() if now is None else now, **event}
    st.append_private(st.aware_dir() / "audit.jsonl", json.dumps(row, default=str))
    with contextlib.suppress(Exception):
        import phone_session

        if phone_session.private():
            return  # private-call content never enters shared feeds
    brief = " ".join(f"{k}={v}" for k, v in event.items() if k != "content_head")
    # Same line format as system.log_action, but under $JARVIS_HOME so tests
    # never touch the real ~/.jarvis/actions.log.
    with (
        contextlib.suppress(OSError),
        (st.jarvis_home() / "actions.log").open("a") as fh,
    ):
        fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} aware {brief[:300]}\n")


def _money(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def record(
    *,
    run: str,
    kind: str,
    host: str,
    counterparty: str,
    amount,
    currency: str,
    items: list,
    content: str,
    offer_text: str,
    result: str,
    confirmation_ref: str = "",
    now: float | None = None,
) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown transaction kind {kind!r}")
    amt = _money(amount)
    entry = {
        "ts": time.time() if now is None else now,
        "run": str(run)[:64],
        "kind": kind,
        "host": str(host)[:120],
        "counterparty": str(counterparty)[:120],
        "amount": None if amt is None else str(amt),
        "currency": (currency or "").upper()[:3],
        "items": [str(i)[:120] for i in (items or [])][:20],
        "content_sha256": hashlib.sha256((content or "").encode()).hexdigest(),
        "content_head": (content or "")[:200],
        "offer_text": str(offer_text)[:400],
        "result": str(result)[:120],
        "confirmation_ref": str(confirmation_ref)[:120],
    }
    if not st.append_private(path(), json.dumps(entry)):
        raise OSError("transaction log not writable")
    audit(
        {
            "event": "transaction",
            "run": entry["run"],
            "kind": kind,
            "host": entry["host"],
            "amount": entry["amount"],
            "currency": entry["currency"],
            "result": entry["result"],
        },
        now=entry["ts"],
    )
    return entry


def read_all() -> list[dict]:
    rows = []
    try:
        for line in path().read_text().splitlines():
            with contextlib.suppress(ValueError):
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def query(kinds=KINDS, since: float = 0.0, until: float | None = None) -> list[dict]:
    until = time.time() if until is None else until
    return [
        r
        for r in read_all()
        if r.get("kind") in kinds and since <= float(r.get("ts", 0)) <= until
    ]


def completed(row: dict) -> bool:
    return str(row.get("result", "")).startswith("ok")


def within_cap(amount, cap) -> bool:
    """Per-transaction spend cap; None cap = unlimited; unknown amount fails."""
    if cap is None:
        return True
    amt = _money(amount)
    if amt is None:
        return False
    return amt <= _money(cap)


_WORDS = [
    "no",
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
]


def _count(n: int, noun: str) -> str:
    word = _WORDS[n] if n < len(_WORDS) else str(n)
    return f"{word} {noun}{'' if n == 1 else 's'}"


def spoken(rows: list[dict], verb: str, when: str) -> str:
    """≤ 2 sentences for "what have you paid for / sent this week"."""
    if not rows:
        return f"Nothing {verb} {when}, Sir."
    done = [r for r in rows if completed(r)]
    stopped = len(rows) - len(done)
    kind = rows[0].get("kind", "payment")
    noun = {"payment": "payment", "send": "message", "submit": "form"}.get(kind, "item")
    if not done:
        return f"No {noun}s went through {when}, Sir; {_count(stopped, 'attempt')} stopped."
    names = ", ".join(
        dict.fromkeys(r.get("counterparty") or r.get("host", "") for r in done)
    )[:160]
    first = f"{_count(len(done), noun).capitalize()} {when}: {names}"
    if kind == "payment":
        totals: dict[str, Decimal] = {}
        for r in done:
            amt = _money(r.get("amount"))
            if amt is not None:
                cur = r.get("currency") or ""
                totals[cur] = totals.get(cur, Decimal("0")) + amt
        if totals:
            parts = [f"{SYMBOLS.get(c, c + ' ')}{v}" for c, v in totals.items()]
            first += f", {' and '.join(parts)} in total"
    out = first + "."
    if stopped:
        out += f" {'One more was' if stopped == 1 else _count(stopped, 'more').capitalize() + ' were'} stopped before paying."
    return out
