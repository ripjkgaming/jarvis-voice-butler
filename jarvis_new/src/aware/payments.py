"""Always Aware sends, submits and payments (docs/always-aware/PLAN.md §4.4).

Sir chose full autonomy: a takeover may end by sending, submitting or paying
when that exact outcome was stated in the offer he said yes to. This module
holds the code-level safeguards, all pure apart from the audit and the cap:

  * ``consequential_words`` - the action-guard vocabulary (undeclared hit = stuck).
  * ``check_final`` - the re-read before the declared final click: merchant,
    host, exact amount + currency, items, saved payment method, recipient and
    content must all match the offer, and the optional spend cap must hold.
  * ``needs_user`` - 3-D Secure, bank-app approval and one-time codes are
    never automated; the task fails safe.
  * ``forbidden_url`` - account-security/password/recovery settings,
    browser/extension pages and the extension store are never driven.
  * ``record`` / ``intent`` - every attempt, including failed ones, is
    written through ``aware.transactions``.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from aware import state as st
from aware import transactions as tx

# ------------------------------------------------------------------ vocabulary

_VOCAB = re.compile(
    r"\b(send|pay|buy|order|submit|post|delete|confirm purchase|subscribe|"
    r"checkout|check out|place order|purchase|complete purchase|donate|"
    r"transfer|publish|tweet|reply)\b",
    re.I,
)
WEAK = frozenset({"checkout", "check out", "order"})


def consequential_words(name: str) -> set[str]:
    """Consequential words in a control's accessible name / nearby text."""
    return {m.group(1).lower() for m in _VOCAB.finditer(name or "")}


def only_weak(words: set[str]) -> bool:
    """True when the only hits are navigation-like ("Go to checkout")."""
    return bool(words) and words <= WEAK


# ------------------------------------------------------------------ never pages

_FORBIDDEN_SCHEMES = (
    "brave:",
    "chrome:",
    "chrome-extension:",
    "edge:",
    "about:settings",
)
_FORBIDDEN_HOSTS = (
    "chromewebstore.google.com",
    "chrome.google.com",  # /webstore
    "passwords.google.com",
    "myaccount.google.com",
    "account.live.com",
    "account.microsoft.com",
    "appleid.apple.com",
    "addons.mozilla.org",
)
_FORBIDDEN_PATH = re.compile(
    r"/(settings/)?(security|password|passwords|two[-_]?factor|2fa|mfa|"
    r"recovery|account[-_]?recovery|passkeys?|authenticator|signin[-_]?options|"
    r"auth[-_]?settings|credentials)(/|$|\?|#|\.)",
    re.I,
)


def forbidden_url(url: str) -> str | None:
    """Reason a page may never be driven by Jarvis, else None. Pure."""
    u = (url or "").strip()
    low = u.lower()
    if any(low.startswith(s) for s in _FORBIDDEN_SCHEMES):
        return "browser_or_extension_page"
    try:
        parts = urlsplit(u)
        host = (parts.hostname or "").lower()
        _ = parts.port
    except ValueError:
        return "malformed_url"
    if parts.scheme and parts.scheme.lower() not in ("http", "https"):
        return "unsupported_url_scheme"
    if parts.scheme and not host:
        return "malformed_url"
    if any(host == h or host.endswith("." + h) for h in _FORBIDDEN_HOSTS):
        return "account_or_store_page"
    if _FORBIDDEN_PATH.search(parts.path or "/"):
        return "security_settings_page"
    return None


# ------------------------------------------------------------------ snapshot


@dataclass
class PageSnapshot:
    """What the page shows right now (read through CDP, never typed into)."""

    url: str
    text: str = ""
    title: str = ""
    # visible form fields: {name,label,type,autocomplete,value,visible}
    fields: list[dict] = field(default_factory=list)
    frame_urls: list[str] = field(default_factory=list)

    @property
    def host(self) -> str:
        return (urlsplit(self.url).hostname or "").lower()


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    needs_user: bool = False
    observed: dict = field(default_factory=dict)


# ------------------------------------------------------------------ needs-user

_NEEDS_USER = re.compile(
    r"3-?d[ -]?secure|\b3ds\b|verified by visa|mastercard (identity check|securecode)|"
    r"securecode|american express safekey|safekey|"
    r"(banking|bank|mobile) app.{0,40}(approve|confirm|authori[sz]e)|"
    r"(approve|confirm|authori[sz]e).{0,40}(banking|bank|mobile) app|"
    r"one[- ]time (pass)?code|one[- ]time password|\botp\b|"
    r"verification code|security code (sent|we sent)|enter the code|"
    r"code (we )?sent to|authenticate (this|the|your) (payment|purchase)|"
    r"strong customer authentication|confirm it'?s you|verify it'?s you",
    re.I,
)
_ACS_FRAME = re.compile(r"3ds|acs\.|/acs|challenge|secure(code|key)|authenticat", re.I)
_CARD_FIELD = re.compile(r"cc-?number|card-?number|cardnumber|\bpan\b", re.I)
_CVC_FIELD = re.compile(r"\bcvc\b|\bcvv\b|\bcsc\b|cc-csc|security[- ]?code", re.I)


def _field_text(f: dict) -> str:
    return " ".join(
        str(f.get(k, "") or "")
        for k in ("name", "label", "autocomplete", "id", "placeholder")
    )


def needs_user(snap: PageSnapshot) -> str | None:
    """A step only Sir can do (3DS, bank app, OTP). Pure."""
    m = _NEEDS_USER.search(f"{snap.title}\n{snap.text}")
    if m:
        return f"needs_user:{m.group(0)[:40].lower()}"
    for f in snap.fields:
        if not f.get("visible", True):
            continue
        if str(f.get("autocomplete", "")).lower() == "one-time-code":
            return "needs_user:one_time_code_field"
    for u in snap.frame_urls:
        if _ACS_FRAME.search(u or ""):
            return "needs_user:3ds_challenge_frame"
    return None


# ------------------------------------------------------------------ money

_SYM = {"£": "GBP", "$": "USD", "€": "EUR"}
_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+(?:\.\d{2})?)"
_AMOUNT = re.compile(
    rf"(?:([£$€])\s?{_NUM})|(?:\b(GBP|USD|EUR)\s?{_NUM})|(?:{_NUM}\s?(GBP|USD|EUR)\b)"
)
_TOTAL_LABEL = re.compile(
    r"\b(grand total|order total|total to pay|total due|total payable|amount due|"
    r"amount to pay|you pay|to pay|total)\b",
    re.I,
)


def _dec(s: str) -> Decimal | None:
    try:
        value = Decimal(s.replace(",", "")).quantize(Decimal("0.01"))
        return value if value.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def parse_amounts(text: str) -> list[tuple[Decimal, str]]:
    out = []
    for m in _AMOUNT.finditer(text or ""):
        sym, n1, code1, n2, n3, code2 = m.groups()
        if sym:
            cur, num = _SYM[sym], n1
        elif code1:
            cur, num = code1, n2
        else:
            cur, num = code2, n3
        val = _dec(num)
        if val is not None:
            out.append((val, cur.upper()))
    return out


def page_totals(text: str) -> list[tuple[Decimal, str, str]]:
    """Every total-labelled amount (label line, or the line right after)."""
    # innerText puts blank lines between blocks: the label and its amount are
    # then two lines apart, so blank lines are dropped first.
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    out = []
    for i, ln in enumerate(lines):
        m = _TOTAL_LABEL.search(ln)
        if not m:
            continue
        tail = ln[m.end() :]
        amounts = parse_amounts(tail)
        if not amounts and i + 1 < len(lines):
            amounts = parse_amounts(lines[i + 1])[:1]
        for amt, cur in amounts[:1]:
            out.append((amt, cur, ln[:60]))
    return out


# ------------------------------------------------------------------ helpers


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").casefold()).strip()


def _alnum(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", (s or "").casefold())


def host_matches(host: str, site: str) -> bool:
    host, site = (host or "").lower().strip("."), (site or "").lower().strip(".")
    site = urlsplit(site).hostname or site if "://" in site else site
    if not host or not site:
        return False
    if site.startswith("www."):
        site = site[4:]
    return host == site or host.endswith("." + site)


def _visible(snap: PageSnapshot) -> list[dict]:
    return [f for f in snap.fields if f.get("visible", True)]


def _common(action, snap: PageSnapshot) -> Verdict | None:
    """Checks shared by every kind. Returns a failing verdict or None."""
    bad = forbidden_url(snap.url)
    if bad:
        return Verdict(False, f"forbidden page: {bad}")
    if any(str(f.get("type", "")).lower() == "password" for f in _visible(snap)):
        return Verdict(False, "password field on page")
    nu = needs_user(snap)
    if nu:
        return Verdict(False, nu, needs_user=True)
    if not action.site:
        return Verdict(False, "site not declared")
    if not host_matches(snap.host, action.site):
        return Verdict(False, f"host {snap.host} differs from declared {action.site}")
    return None


_LAST4 = re.compile(r"(\d{4})\D*$")


def _check_payment(a, snap: PageSnapshot, cap) -> Verdict:
    amount = _dec(str(a.amount or ""))
    cur = (a.currency or "").upper()
    if amount is None or not cur:
        return Verdict(False, "amount or currency not declared")
    if not a.merchant:
        return Verdict(False, "merchant not declared")
    if not a.items:
        return Verdict(False, "items not declared")
    obs = {"host": snap.host, "currency": cur}
    for f in _visible(snap):
        ft = _field_text(f)
        if _CARD_FIELD.search(ft) and not str(f.get("value", "")).strip():
            return Verdict(False, "needs_user:card_details_requested", True, obs)
        if _CVC_FIELD.search(ft) and not str(f.get("value", "")).strip():
            return Verdict(False, "needs_user:cvc_requested", True, obs)
    hay = _alnum(f"{snap.title} {snap.text} {snap.host}")
    if _alnum(a.merchant) not in hay:
        return Verdict(False, f"merchant {a.merchant!r} not shown", observed=obs)
    text = _norm(snap.text)
    for item in a.items:
        if _norm(item) not in text:
            return Verdict(False, f"item {item!r} not in basket", observed=obs)
    totals = page_totals(snap.text)
    if not totals:
        return Verdict(False, "no total shown on page", observed=obs)
    curs = {c for _, c, _ in totals}
    if curs != {cur}:
        return Verdict(
            False, f"currency on page {sorted(curs)} differs from {cur}", observed=obs
        )
    top = max(t for t, _, _ in totals)
    obs["amount"] = str(top)
    if top != amount:
        return Verdict(
            False, f"amount on page {top} differs from offered {amount}", observed=obs
        )
    m = _LAST4.search(a.payment_method or "")
    if not m:
        return Verdict(False, "saved payment method not identified", observed=obs)
    last4 = m.group(1)
    shown = re.search(
        rf"(ending( in)?|[•*·x]{{2,}}|\.\.\.\.?|-)\s*{last4}\b", snap.text, re.I
    )
    if not shown:
        return Verdict(
            False, f"saved payment method ending {last4} not selected", observed=obs
        )
    obs["payment_method_last4"] = last4
    if cap is not None and cap[0] is not None:
        cap_amt, cap_cur = cap
        if (cap_cur or "").upper() != cur:
            return Verdict(
                False, "spend cap currency differs; not comparing", observed=obs
            )
        if amount > cap_amt:
            return Verdict(False, f"over spend cap {cap_amt} {cap_cur}", observed=obs)
    return Verdict(True, "match", observed=obs)


_RECIP_FIELD = re.compile(r"^(to|cc|bcc|recipients?)$|recipient|\bto\b", re.I)
_BODY_FIELD = re.compile(r"body|message|content|compose|text", re.I)


def _split_addrs(v: str) -> list[str]:
    return [p.strip().casefold() for p in re.split(r"[,;\n]+", v or "") if p.strip()]


def _check_send(a, snap: PageSnapshot) -> Verdict:
    if not a.recipient or not a.content:
        return Verdict(False, "recipient or content not declared")
    obs = {"host": snap.host}
    want = _split_addrs(a.recipient)
    recips = [
        f
        for f in _visible(snap)
        if _RECIP_FIELD.search(f"{f.get('name', '')} {f.get('label', '')}".strip())
        and not _BODY_FIELD.search(str(f.get("name", "")))
    ]
    if recips:
        seen = [x for f in recips for x in _split_addrs(str(f.get("value", "")))]
        if sorted(set(seen)) != sorted(set(want)):
            return Verdict(
                False, f"recipient on page {seen} differs from {want}", observed=obs
            )
    elif not all(w in _norm(snap.text) for w in want):
        return Verdict(False, "recipient not shown", observed=obs)
    bodies = [
        f
        for f in _visible(snap)
        if str(f.get("type", "")).lower() in ("textarea", "contenteditable")
        or _BODY_FIELD.search(f"{f.get('name', '')} {f.get('label', '')}")
    ]
    target = _norm(a.content)
    if bodies:
        if not any(_norm(str(f.get("value", ""))) == target for f in bodies):
            return Verdict(False, "content changed from the offer", observed=obs)
    elif target not in _norm(snap.text):
        return Verdict(False, "content not shown", observed=obs)
    obs["recipient"] = ", ".join(want)
    return Verdict(True, "match", observed=obs)


def _check_submit(a, snap: PageSnapshot) -> Verdict:
    obs = {"host": snap.host}
    if a.content:
        hay = _norm(
            " ".join(str(f.get("value", "")) for f in snap.fields) + " " + snap.text
        )
        if _norm(a.content) not in hay:
            return Verdict(False, "content changed from the offer", observed=obs)
    return Verdict(True, "match", observed=obs)


def check_final(action, snap: PageSnapshot, cap=None) -> Verdict:
    """Re-read before the declared final click. ok=False means: do not click.

    ``cap`` is ``(Decimal|None, currency)`` from ``spend_cap()``.
    """
    kind = getattr(action, "kind", "")
    if kind not in tx.KINDS:
        return Verdict(False, f"unknown consequential kind {kind!r}")
    bad = _common(action, snap)
    if bad:
        return bad
    if kind == "payment":
        return _check_payment(action, snap, cap)
    if kind == "send":
        return _check_send(action, snap)
    return _check_submit(action, snap)


# ------------------------------------------------------------------ confirmation

_CONF = re.compile(
    r"(?:order|booking|confirmation|reference|ref|transaction|receipt)"
    r"(?:\s+(?:number|no\.?|id|code|ref(?:erence)?))?\s*(?:is|:|#)?\s*#?\s*"
    r"([A-Z0-9][A-Z0-9-]{3,30})\b",
    re.I,
)


def confirmation_ref(text: str) -> str:
    for m in _CONF.finditer(text or ""):
        ref = m.group(1)
        if any(c.isdigit() for c in ref):
            return ref
    return ""


# ------------------------------------------------------------------ saved methods
#
# Jarvis never reads card data from the browser profile. A payment method
# counts as "saved" only when (a) the merchant's own checkout page shows it
# selected by its last four digits with no card-number/CVC entry requested,
# or (b) a past verified payment on that host used it. Only host + network
# word + last four are kept (aware/saved_methods.json, 0600).


def _methods_path():
    return st.aware_dir() / "saved_methods.json"


def _last4(label: str) -> str | None:
    m = _LAST4.search(label or "")
    return m.group(1) if m else None


def _site(host: str) -> str:
    host = (host or "").lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def remember_method(host: str, label: str) -> None:
    last4 = _last4(label)
    if not last4 or not host:
        return
    data = {}
    with contextlib.suppress(OSError, ValueError):
        import json

        data = json.loads(_methods_path().read_text())
    if not isinstance(data, dict):
        data = {}
    known = set(data.get(_site(host), []))
    known.add(last4)
    data[_site(host)] = sorted(known)
    import json

    st.write_private(_methods_path(), json.dumps(data, sort_keys=True))


def shows_saved_method(
    label: str, page_text: str, fields: list[dict] | None = None
) -> bool:
    """The checkout page shows this saved method and asks for no card entry."""
    last4 = _last4(label)
    if not last4:
        return False
    for f in fields or []:
        if (
            f.get("visible", True)
            and (
                _CARD_FIELD.search(_field_text(f)) or _CVC_FIELD.search(_field_text(f))
            )
            and not str(f.get("value", "")).strip()
        ):
            return False
    return bool(
        re.search(
            rf"(ending( in)?|[•*·x]{{2,}}|\.\.\.\.?|-)\s*{last4}\b",
            page_text or "",
            re.I,
        )
    )


def saved_method(
    host: str, label: str, page_text: str = "", fields: list[dict] | None = None
) -> bool:
    """Gate check (P2): is ``label`` a payment method already saved for ``host``?

    Fails closed: no last four digits, or no evidence, returns False.
    """
    last4 = _last4(label)
    if not last4 or not host:
        return False
    if page_text and shows_saved_method(label, page_text, fields):
        return True
    with contextlib.suppress(OSError, ValueError):
        import json

        data = json.loads(_methods_path().read_text())
        if isinstance(data, dict):
            site = _site(host)
            for h, digits in data.items():
                if (
                    site == h or site.endswith("." + h) or h.endswith("." + site)
                ) and last4 in digits:
                    return True
    return False


# ------------------------------------------------------------------ spend cap


def spend_cap() -> tuple[Decimal | None, str]:
    """(cap, currency); None = unlimited (default). Corrupt value fails closed."""
    s = st.load()
    cur = str(s.get("spend_currency") or "GBP").upper()
    raw = s.get("spend_cap")
    if raw is None or raw == "":
        return None, cur
    val = _dec(str(raw))
    return (val if val is not None and val >= 0 else Decimal("0")), cur


def set_spend_cap(amount, currency: str = "GBP") -> tuple[Decimal | None, str]:
    """Voice: "set a spend limit of fifty pounds" / "remove the spend limit"."""
    cur = (currency or "GBP").upper()[:3]
    if amount is None:
        st.update(spend_cap=None, spend_currency=cur)
        tx.audit({"event": "spend_cap", "cap": None, "currency": cur})
        return None, cur
    val = _dec(str(amount))
    if val is None or val <= 0:
        raise ValueError(f"not a spend limit: {amount!r}")
    st.update(spend_cap=str(val), spend_currency=cur)
    tx.audit({"event": "spend_cap", "cap": str(val), "currency": cur})
    return val, cur


# ------------------------------------------------------------------ audit


def _counterparty(action) -> str:
    if action.kind == "payment":
        return action.merchant
    if action.kind == "send":
        return action.recipient
    return action.site


def audit_writable() -> bool:
    try:
        p = tx.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        import os

        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(fd)
        return True
    except OSError:
        return False


def intent(run_id: str, action, offer_text: str) -> None:
    """Durable pre-click transaction; an append failure must stop the action."""
    record(run_id, action, offer_text, "intent")
    tx.audit(
        {
            "event": "transaction_intent",
            "run": run_id,
            "kind": action.kind,
            "host": action.site,
            "amount": action.amount or None,
            "currency": action.currency or None,
            "offer": (offer_text or "")[:120],
        }
    )


def record(
    run_id: str,
    action,
    offer_text: str,
    result: str,
    *,
    observed: dict | None = None,
    confirmation_ref: str = "",
) -> dict:
    """Write one transactions.jsonl row (success or failure)."""
    obs = observed or {}
    if action.kind == "payment" and str(result).startswith("ok"):
        with contextlib.suppress(Exception):
            remember_method(obs.get("host") or action.site, action.payment_method)
    return tx.record(
        run=run_id,
        kind=action.kind,
        host=obs.get("host") or action.site,
        counterparty=_counterparty(action),
        amount=obs.get("amount", action.amount or None),
        currency=obs.get("currency") or action.currency,
        items=list(action.items or []),
        content=action.content or "",
        offer_text=offer_text,
        result=result,
        confirmation_ref=confirmation_ref,
    )
