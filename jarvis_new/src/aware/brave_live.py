"""Always Aware live Brave takeovers (docs/always-aware/PLAN.md §4).

Attaches to Sir's running Brave over CDP (127.0.0.1:9222) with Playwright,
so his profile, sign-ins and extensions stay exactly as they are. Nothing is
launched, copied or restarted.

Hard rules, enforced in code here:
  * Never ``browser.close()`` / ``context.close()`` (they can close Sir's
    windows). The connection ends with ``playwright.stop()`` only. The
    Playwright objects are wrapped so those methods are unreachable.
  * Connect with ``no_defaults=True``: Playwright otherwise rewires the
    browser's download behaviour and focus emulation for every tab.
  * Only tabs Jarvis took over (moved into his window) or opened himself are
    driven. Sir's other tabs are never read beyond /json/list, and are never
    reloaded, navigated or closed. ``goto`` works only in Jarvis's own tabs.
  * The action guard blocks any undeclared send/pay/submit control; the
    declared final step re-reads the page and refuses to click on any drift
    from the offer (``aware.payments.check_final``). 3-D Secure, bank-app and
    one-time-code steps are never automated. Every attempt is audited.
  * Sir's own input in Jarvis's tab pauses the run.

Public API (COORD.md): ``run(plan, *, progress)``, ``verify(plan, result)``,
``handback(run_id)``, ``cancel``, ``carry_on``, ``leave_there``,
``recover()``, ``available()``, ``probe()``.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from aware import cdp
from aware import payments as pay
from aware import state as st
from aware import tabsplit as ts
from aware.types import TakeoverPlan, TakeoverResult

LOCKE_EXT_DIR = Path(__file__).resolve().parents[2] / "desktop" / "brave" / "locke-ext"
WORKER_SCRIPT = "jarvis-tabs.js"
HELPER_PAGE = "jarvis-tabs.html"

OPS = ("click", "type", "press", "select", "check", "wait_for", "goto")
MAX_ACTIONS = 40
NO_PROGRESS_S = 120.0
REPEAT_LIMIT = 3
PAUSE_MAX_S = 600.0
STEP_TIMEOUT_S = 20.0
SETTLE_S = 0.6
PAUSE_LINE = "You've got it, Sir. Say 'carry on' if you want me back."


class PlanError(ValueError):
    """The plan is not executable as given; nothing was done."""


class StopError(Exception):
    """Ends a run: status + reason (+ optional spoken line)."""

    def __init__(self, status: str, reason: str, spoken: str = ""):
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.spoken = spoken


# ------------------------------------------------------------------ extension ids


def extension_id(path: str | Path) -> str:
    """Chromium's id for an unpacked extension loaded from ``path``."""
    digest = hashlib.sha256(str(Path(path)).encode()).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def known_ext_ids() -> set[str]:
    ids = {extension_id(LOCKE_EXT_DIR)}
    for part in os.environ.get("JARVIS_LOCKE_EXT_ID", "").split(","):
        if re.fullmatch(r"[a-p]{32}", part.strip()):
            ids.add(part.strip())
    return ids


def _ext_of(url: str) -> str:
    parts = urlsplit(url or "")
    return parts.hostname or "" if parts.scheme == "chrome-extension" else ""


def find_worker(targets: list[dict], ids: set[str]) -> dict | None:
    for t in targets:
        if t.get("type") != "service_worker":
            continue
        url = str(t.get("url", ""))
        if _ext_of(url) in ids and urlsplit(url).path == "/" + WORKER_SCRIPT:
            return t
    return None


async def probe(cdp_port: int | None = None, ext_ids: set[str] | None = None) -> dict:
    """Read-only: /json/version + /json/list. Never attaches to a tab."""
    ids = ext_ids or known_ext_ids()
    ver = await asyncio.to_thread(cdp.version, cdp_port)
    out = {
        "cdp": bool(ver),
        "browser": (ver or {}).get("Browser", ""),
        "worker": None,
        "ext_ids": sorted(ids),
    }
    if ver:
        with contextlib.suppress(Exception):
            targets = await asyncio.to_thread(cdp.targets, cdp_port)
            w = find_worker(targets, ids)
            out["worker"] = w.get("url") if w else None
    return out


def available(cdp_port: int | None = None) -> tuple[bool, str]:
    """Stage-2 executor check for web kinds (P2 gate). Read-only."""
    if cdp.version(cdp_port) is None:
        return False, "cdp_unavailable"
    try:
        targets = cdp.targets(cdp_port)
    except Exception:
        return False, "cdp_unavailable"
    if find_worker(targets, known_ext_ids()) is None:
        return False, "no_tab_move: locke-ext worker not reachable"
    return True, "ok"


def worker_ok(cdp_port: int | None = None) -> bool:
    """P2 gate: the locke-ext tab worker is reachable (read-only check)."""
    return available(cdp_port)[0]


# ------------------------------------------------------------------ plan parsing


@dataclass
class WebStep:
    op: str
    target: str = ""
    text: str = ""
    action: int | None = None
    final: bool = True

    def sig(self) -> tuple:
        return (self.op, self.target, self.text)


def _norm_op(tool: str) -> str:
    tool = (tool or "").strip().lower()
    for prefix in ("web.", "browser.", "brave."):
        if tool.startswith(prefix):
            tool = tool[len(prefix) :]
    return {
        "fill": "type",
        "type_text": "type",
        "press_key": "press",
        "wait": "wait_for",
    }.get(tool, tool)


def parse_steps(plan: TakeoverPlan) -> list[WebStep]:
    raw = (plan.targets or {}).get("web_steps")
    steps: list[WebStep] = []
    if raw is not None and not isinstance(raw, list):
        raise PlanError("web_steps must be a list")
    if raw:
        for r in raw:
            if not isinstance(r, dict):
                raise PlanError("web step is not an object")
            act = r.get("action")
            if act is not None and (
                isinstance(act, bool)
                or not (
                    isinstance(act, int) or (isinstance(act, str) and act.isdecimal())
                )
            ):
                raise PlanError("action index must be an integer")
            if "final" in r and not isinstance(r["final"], bool):
                raise PlanError("final must be a boolean")
            steps.append(
                WebStep(
                    op=_norm_op(str(r.get("op", ""))),
                    target=str(r.get("target", "")),
                    text=str(r.get("text", "")),
                    action=None if act is None else int(act),
                    final=bool(r.get("final", True)),
                )
            )
    else:
        for s in plan.steps or []:
            op = _norm_op(s.tool)
            if op == "reason":
                continue
            if op not in OPS:
                raise PlanError(f"unknown web op {op!r}")
            target, text = s.step, ""
            if op in ("type", "select") and "=>" in s.step:
                target, text = (x.strip() for x in s.step.split("=>", 1))
            elif op == "press":
                target, text = "", s.step.strip()
            steps.append(WebStep(op=op, target=target.strip(), text=text))
    if not steps:
        raise PlanError("plan has no web steps")
    n_actions = len(plan.actions or [])
    for s in steps:
        if s.op not in OPS:
            raise PlanError(f"unknown web op {s.op!r}")
        if s.action is not None and not 0 <= s.action < n_actions:
            raise PlanError("web step points at an undeclared action")
        if s.action is not None and s.op not in ("click", "press"):
            raise PlanError("only a click or key press can complete an action")
    return steps


# ------------------------------------------------------------------ guards (pure)

DESCRIBE_JS = """(e) => {
  const f = e.form || (e.closest && e.closest('form'));
  const txt = (x) => (x || '').toString().replace(/\\s+/g, ' ').trim();
  const subs = f ? [...f.querySelectorAll('button,input[type=submit]')]
      .map(b => txt(b.innerText || b.value)).join(' | ') : '';
  return {
    name: txt(e.getAttribute('aria-label') || e.innerText || e.value ||
              e.title || e.getAttribute('alt')).slice(0, 200),
    tag: e.tagName, type: (e.type || '').toLowerCase(),
    autocomplete: (e.autocomplete || e.getAttribute('autocomplete') || '').toLowerCase(),
    field: txt([e.name, e.id, e.getAttribute('aria-label'), e.placeholder].join(' ')),
    in_form: !!f, form_action: f ? (f.getAttribute('action') || '') : '',
    form_method: f ? (f.method || 'get').toLowerCase() : '',
    form_submit_text: subs.slice(0, 200),
    href: e.href || ''
  };
}"""


def _action_words(desc: dict) -> set[str]:
    words = pay.consequential_words(desc.get("name", ""))
    is_submit = desc.get("type") == "submit" or (
        desc.get("tag") == "BUTTON"
        and desc.get("type") in ("", "submit")
        and desc.get("in_form")
    )
    if is_submit and desc.get("form_method", "").lower() == "post":
        words.add("submit")
    if is_submit:
        words |= pay.consequential_words(
            urlsplit(desc.get("form_action", "")).path.replace("/", " ")
        )
    return words


def guard_click(desc: dict, step: WebStep) -> str | None:
    """Reason to refuse a click, else None. Pure."""
    bad = pay.forbidden_url(desc.get("href", "")) if desc.get("href") else None
    if bad:
        return f"forbidden_link:{bad}"
    words = _action_words(desc)
    if step.action is None:
        if words:
            return "undeclared_consequential:" + ",".join(sorted(words))
        return None
    if not step.final and words and not pay.only_weak(words):
        return "pre_step_looks_final:" + ",".join(sorted(words))
    return None


def guard_type(desc: dict, step: WebStep) -> str | None:
    if desc.get("type") == "password":
        return "refused:password_field"
    ac = desc.get("autocomplete", "")
    field = desc.get("field", "")
    if ac == "one-time-code" or re.search(
        r"\botp\b|one.?time|verification.?code", field, re.I
    ):
        return "needs_user:one_time_code"
    if ac.startswith("cc-") or re.search(
        r"card.?number|cardnumber|\bcvc\b|\bcvv\b", field, re.I
    ):
        return "refused:card_details"
    return None


def guard_press(key: str, desc: dict, step: WebStep) -> str | None:
    """Guard sensitive fields, then treat activation chords like clicks."""
    why = guard_type(desc, step)
    if why:
        return why
    base = key.rsplit("+", 1)[-1].lower()
    if base not in ("enter", "return", "numpadenter", "space", " "):
        return None
    # Space activates buttons/links, but remains ordinary text in inputs.
    if base in ("space", " "):
        if desc.get("tag") not in ("BUTTON", "A", "INPUT"):
            return None
        if desc.get("tag") == "INPUT" and desc.get("type") not in (
            "submit",
            "button",
            "reset",
            "checkbox",
            "radio",
        ):
            return None
        return guard_click(desc, step)
    why = guard_click(desc, step)
    if why:
        return why
    if not desc.get("in_form"):
        return None
    words = pay.consequential_words(
        desc.get("form_submit_text", "")
    ) | pay.consequential_words(
        urlsplit(desc.get("form_action", "")).path.replace("/", " ")
    )
    submits = desc.get("form_method", "").lower() == "post" or bool(words)
    if submits and step.action is None:
        return "undeclared_consequential:form_submit"
    if submits and not step.final:
        return "pre_step_looks_final:form_submit"
    return None


def predicate_holds(
    pred: dict, *, url: str, title: str, text: str, selector_found: bool | None
) -> tuple[bool, str]:
    """All given clauses must hold. Empty predicate cannot verify. Pure."""
    pred = {k: v for k, v in (pred or {}).items() if v not in (None, "")}
    if not pred:
        return False, "no success predicate"
    seen = []
    low_text = (text or "").casefold()
    for key, want in pred.items():
        if key == "url_contains":
            ok = str(want) in (url or "")
        elif key == "url_regex":
            ok = re.search(str(want), url or "") is not None
        elif key == "title_contains":
            ok = str(want).casefold() in (title or "").casefold()
        elif key == "text":
            ok = str(want).casefold() in low_text
        elif key == "text_absent":
            ok = str(want).casefold() not in low_text
        elif key == "selector":
            ok = bool(selector_found)
        else:
            return False, f"unknown predicate clause {key!r}"
        if not ok:
            return False, f"{key} not satisfied"
        seen.append(f"{key}={str(want)[:60]}")
    return True, "; ".join(seen)


# ------------------------------------------------------------------ page reads

SNAPSHOT_JS = """() => {
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const lab = (e) => (e.labels && e.labels.length) ? [...e.labels].map(l => l.innerText).join(' ')
    : (e.getAttribute('aria-label') || '');
  const secret = (e) => /^cc-/.test((e.autocomplete || '').toLowerCase()) ||
    /card.?number|cardnumber|cvc|cvv/i.test((e.name || '') + ' ' + (e.id || ''));
  const fields = [...document.querySelectorAll(
      'input:not([type=hidden]),textarea,select,[contenteditable=""],[contenteditable="true"]')]
    .slice(0, 200).map(e => {
      const type = e.isContentEditable ? 'contenteditable'
        : (e.tagName === 'TEXTAREA' ? 'textarea' : (e.type || '').toLowerCase());
      let value = e.isContentEditable ? e.innerText : (e.value || '');
      if (type === 'password') value = '';
      else if (secret(e)) value = value ? '<filled>' : '';
      return {name: e.name || e.id || '', id: e.id || '', label: lab(e), type,
              autocomplete: (e.autocomplete || e.getAttribute('autocomplete') || '').toLowerCase(),
              placeholder: e.placeholder || '', value: value.slice(0, 2000), visible: vis(e)};
    });
  return {url: location.href, title: document.title,
          text: (document.body ? document.body.innerText : '').slice(0, 30000), fields};
}"""


async def read_snapshot(page) -> pay.PageSnapshot:
    data = await page.evaluate(SNAPSHOT_JS)
    snap = pay.PageSnapshot(
        url=data["url"], title=data["title"], text=data["text"], fields=data["fields"]
    )
    extra = []
    for fr in page.frames[1:20]:
        snap.frame_urls.append(fr.url)
        with contextlib.suppress(Exception):
            sub = await asyncio.wait_for(fr.evaluate(SNAPSHOT_JS), 3)
            snap.fields.extend(sub["fields"])
            extra.append(sub["text"][:3000])
    if extra:
        # frame text helps needs_user() spot a 3DS/OTP challenge; it is never
        # used to satisfy the payment match (that reads the main document).
        snap.frame_text = "\n".join(extra)  # type: ignore[attr-defined]
    return snap


def _needs_user_any(snap: pay.PageSnapshot) -> str | None:
    hit = pay.needs_user(snap)
    if hit:
        return hit
    ft = getattr(snap, "frame_text", "")
    return pay.needs_user(pay.PageSnapshot(url=snap.url, text=ft)) if ft else None


# ------------------------------------------------------------------ guarded wrappers


_FORBIDDEN_BROWSER = frozenset({"close", "new_context", "new_page", "start_tracing"})
_FORBIDDEN_CONTEXT = frozenset(
    {
        "close",
        "clear_cookies",
        "clear_permissions",
        "grant_permissions",
        "set_offline",
        "route",
        "route_from_har",
        "unroute",
        "add_init_script",
        "expose_binding",
        "expose_function",
        "set_extra_http_headers",
        "set_geolocation",
        "add_cookies",
        "set_default_navigation_timeout",
        "new_page",
    }
)


class _NoClose:
    """Proxy that makes destructive Playwright methods unreachable."""

    def __init__(self, inner, forbidden: frozenset):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_forbidden", forbidden)

    def __getattr__(self, name):
        if name in self._forbidden:
            raise PermissionError(f"{name}() is never called on Sir's browser")
        return getattr(self._inner, name)


class OwnedPage:
    """A tab Jarvis drives: one he took over, or one he opened himself."""

    def __init__(self, page, *, created: bool, target_id: str = ""):
        self._page = page
        self.created = created
        self.target_id = target_id

    @property
    def page(self):
        return self._page

    @property
    def url(self) -> str:
        return self._page.url

    async def goto(self, url: str):
        if not self.created:
            raise PermissionError("Jarvis never navigates a tab that was Sir's")
        bad = pay.forbidden_url(url)
        if bad:
            raise PermissionError(f"forbidden page: {bad}")
        return await self._page.goto(url)

    async def reload(self, *a, **k):
        raise PermissionError("Jarvis never reloads a tab")

    async def close(self):
        if not self.created:
            raise PermissionError("Jarvis never closes a tab that was Sir's")
        await self._page.close()


# ------------------------------------------------------------------ control files


def _ctl(run_id: str, name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(run_id))[:80]
    return st.sub("brave") / f"{safe}.{name}"


def _current_path() -> Path:
    return st.sub("brave") / "current"


def current() -> str | None:
    with contextlib.suppress(OSError):
        rid = _current_path().read_text().strip()
        return rid or None
    return None


def _flag(run_id: str | None, name: str) -> str | None:
    rid = run_id or current()
    if not rid:
        return None
    st.write_private(_ctl(rid, name), str(time.time()))
    return rid


def cancel(run_id: str | None = None) -> str:
    return "Stopping, Sir." if _flag(run_id, "CANCEL") else "Nothing to stop, Sir."


def carry_on(run_id: str | None = None) -> str:
    rid = _flag(run_id, "CARRY")
    return "Carrying on, Sir." if rid else "There's nothing paused, Sir."


def leave_there(run_id: str | None = None) -> str:
    rid = run_id or current()
    if rid:
        _flag(rid, "LEAVE")
        return "I'll leave that window where it is, Sir."
    return "It's already back in your tab bar, Sir."


def _flagged(run_id: str, name: str) -> bool:
    return _ctl(run_id, name).exists()


def _clear(run_id: str, name: str) -> None:
    with contextlib.suppress(OSError):
        _ctl(run_id, name).unlink()


def _cancel_requested(run_id: str) -> bool:
    if _flagged(run_id, "CANCEL"):
        return True
    with contextlib.suppress(OSError):
        if (st.aware_dir() / "journal" / run_id / "CANCEL").exists():  # P3's flag
            return True
    return False


def _run_record_path(run_id: str) -> Path:
    return _ctl(run_id, "run.json")


def _save_record(rec: dict) -> None:
    st.write_private(
        _run_record_path(rec["run_id"]), json.dumps(rec, sort_keys=True, default=str)
    )


def _load_record(run_id: str) -> dict | None:
    with contextlib.suppress(OSError, ValueError):
        return json.loads(_run_record_path(run_id).read_text())
    return None


def _audit(event: dict) -> None:
    with contextlib.suppress(Exception):
        from aware import transactions as tx

        tx.audit({"event": "web", **event})


# ------------------------------------------------------------------ the session


LISTENER_JS = """(() => {
  if (window.__jarvisWatch) return; window.__jarvisWatch = 1;
  const f = (e) => { if (e.isTrusted && window.__jarvisUserInput) {
    try { window.__jarvisUserInput(e.type); } catch (x) {} } };
  for (const t of ['pointerdown', 'keydown', 'wheel'])
    addEventListener(t, f, {capture: true, passive: true});
})()"""


class LiveBrave:
    """One CDP attachment for one takeover. Use as ``async with``."""

    def __init__(
        self,
        cdp_url: str | None = None,
        *,
        ext_ids: set[str] | None = None,
        kwin=None,
        screens=None,
        main=None,
        hud=None,
        active=None,
    ):
        self.cdp_url = cdp_url or f"http://127.0.0.1:{cdp.port()}"
        self.ext_ids = ext_ids or known_ext_ids()
        self._kwin = kwin
        self._split_kw = {
            "screens": screens,
            "main": main,
            "hud": hud,
            "active": active,
        }
        self._pw = None
        self.browser = None
        self.context = None
        self._helper = None
        self._eval = None
        self.browser_id = ""
        self.acting_until = 0.0
        self.user_input_at = 0.0
        self.events: list[str] = []
        self._sessions = []

    async def __aenter__(self):
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        try:
            raw = await self._pw.chromium.connect_over_cdp(
                self.cdp_url, no_defaults=True, timeout=15000
            )
        except Exception:
            await self._pw.stop()
            raise
        self.browser = _NoClose(raw, _FORBIDDEN_BROWSER)
        self.context = _NoClose(raw.contexts[0], _FORBIDDEN_CONTEXT)
        with contextlib.suppress(Exception):
            ver = await asyncio.to_thread(
                cdp._get, "/json/version", urlsplit(self.cdp_url).port
            )
            self.browser_id = str(ver.get("webSocketDebuggerUrl", "")).rsplit("/", 1)[
                -1
            ]
        return self

    async def __aexit__(self, *exc):
        for s in self._sessions:
            with contextlib.suppress(Exception):
                await s.detach()
        if self._helper is not None:
            with contextlib.suppress(Exception):
                await self._helper.close()  # Jarvis's own helper tab
        # Disconnect only. Never browser.close() / context.close().
        with contextlib.suppress(Exception):
            await self._pw.stop()

    # -- extension context

    async def _acquire_eval(self):
        for w in self.context.service_workers:
            url = w.url
            if (
                _ext_of(url) in self.ext_ids
                and urlsplit(url).path == "/" + WORKER_SCRIPT
            ):
                return lambda expr, arg, _w=w: _w.evaluate(expr, arg)
        if self._helper is None:
            eid = sorted(self.ext_ids)[0]
            url = f"chrome-extension://{eid}/{HELPER_PAGE}"
            page = await self._create_target(url, minimized=True)
            if page is None:
                raise StopError("failed", "no_tab_move: locke-ext not reachable")
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            ok = await page.evaluate(
                "typeof chrome !== 'undefined' && !!(chrome.tabs && chrome.windows)"
            )
            if not ok:
                await page.close()
                raise StopError("failed", "no_tab_move: locke-ext not reachable")
            self._helper = page
        return lambda expr, arg: self._helper.evaluate(expr, arg)

    async def ext_eval(self, expr: str, arg=None):
        if self._eval is None:
            self._eval = await self._acquire_eval()
        try:
            return await self._eval(expr, arg)
        except StopError:
            raise
        except Exception:
            # worker may have been stopped; re-acquire once
            self._eval = await self._acquire_eval()
            return await self._eval(expr, arg)

    def tab_api(self) -> ts.TabApi:
        return ts.TabApi(self.ext_eval)

    def tabsplit(self) -> ts.TabSplit:
        kwin = self._kwin if self._kwin is not None else ts.KwinPlacer()
        return ts.TabSplit(
            self.tab_api(),
            kwin,
            browser_id=lambda: self.browser_id,
            unmark=self.unmark_tab,
            **{k: v for k, v in self._split_kw.items() if v},
        )

    # -- targets

    async def _target_id(self, page) -> str:
        s = await self.context.new_cdp_session(page)
        try:
            info = await s.send("Target.getTargetInfo")
            return info["targetInfo"]["targetId"]
        finally:
            with contextlib.suppress(Exception):
                await s.detach()

    async def page_for_target(self, target_id: str, url_hint: str = ""):
        pages = list(self.context.pages)
        if url_hint:
            pages.sort(key=lambda p: p.url != url_hint)
        for p in pages:
            if p.url.startswith(("chrome-extension:", "devtools:")):
                continue
            with contextlib.suppress(Exception):
                if await self._target_id(p) == target_id:
                    return p
        return None

    async def _create_target(self, url: str, *, minimized: bool = False):
        bs = await self.browser.new_browser_cdp_session()
        try:
            params = {"url": url, "newWindow": True, "background": True}
            if minimized:
                params["windowState"] = "minimized"
            try:
                res = await bs.send("Target.createTarget", params)
            except Exception:
                params.pop("windowState", None)
                res = await bs.send("Target.createTarget", params)
        finally:
            with contextlib.suppress(Exception):
                await bs.detach()
        tid = res.get("targetId")
        for _ in range(50):
            for p in self.context.pages:
                with contextlib.suppress(Exception):
                    if p.url and await self._target_id(p) == tid:
                        return p
            await asyncio.sleep(0.1)
        return None

    async def open_own_tab(self, url: str, *, minimized: bool) -> OwnedPage:
        bad = pay.forbidden_url(url)
        if bad:
            raise StopError("failed", f"forbidden page: {bad}")
        page = await self._create_target(url, minimized=minimized)
        if page is None:
            raise StopError("stuck", "could not open Jarvis's tab")
        return OwnedPage(page, created=True, target_id=await self._target_id(page))

    # -- markers

    async def mark(self, page) -> None:
        await page.evaluate(
            "(m)=>{if(!document.title.startsWith(m))document.title=m+document.title}",
            ts.MARK,
        )

    async def unmark_tab(self, tab_id: int) -> None:
        tabs = {int(t["id"]): t for t in await self.tab_api().query()}
        t = tabs.get(int(tab_id))
        if not t or not str(t.get("title", "")).startswith(ts.MARK):
            return
        matches = []
        for p in self.context.pages:
            if p.url == t.get("url"):
                with contextlib.suppress(Exception):
                    if await p.title() == t.get("title"):
                        matches.append(p)
        # A duplicate URL is common (compose windows, checkout tabs). Leave the
        # marker alone if URL + title cannot identify a single page safely.
        if len(matches) == 1:
            with contextlib.suppress(Exception):
                await matches[0].evaluate(
                    "(m)=>{if(document.title.startsWith(m))document.title=document.title.slice(m.length)}",
                    ts.MARK,
                )

    # -- watching an owned page

    async def watch(self, owned: OwnedPage, *, keep_awake: bool) -> None:
        page = owned.page

        def on_input(_source, kind):
            if time.time() < self.acting_until:
                return
            self.user_input_at = time.time()
            self.events.append(f"user_input:{kind}")

        await page.expose_binding("__jarvisUserInput", on_input)
        await page.add_init_script(LISTENER_JS)
        await page.evaluate(LISTENER_JS)
        page.on("popup", lambda p: self.events.append(f"popup:{p.url}"))
        page.on(
            "framenavigated",
            lambda f: (
                self.events.append(f"nav:{f.url}") if f == page.main_frame else None
            ),
        )
        s = await self.context.new_cdp_session(page)
        self._sessions.append(s)
        with contextlib.suppress(Exception):
            await s.send("Page.enable")
        s.on(
            "Page.downloadWillBegin",
            lambda e: self.events.append(f"download:{e.get('guid', '')}"),
        )
        if keep_awake:
            with contextlib.suppress(Exception):
                await s.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
            with contextlib.suppress(Exception):
                await s.send("Page.setWebLifecycleState", {"state": "active"})

    async def cancel_download(self, guid: str) -> None:
        bs = await self.browser.new_browser_cdp_session()
        try:
            with contextlib.suppress(Exception):
                await bs.send("Browser.cancelDownload", {"guid": guid})
        finally:
            with contextlib.suppress(Exception):
                await bs.detach()


# ------------------------------------------------------------------ the runner


class _Runner:
    def __init__(self, plan: TakeoverPlan, progress, run_id: str, lb: LiveBrave):
        self.plan = plan
        self.progress = progress or (lambda f, t: None)
        self.run_id = run_id
        self.lb = lb
        self.owned: OwnedPage | None = None
        self.started = time.time()
        self.last_progress = time.time()
        self.actions_done = 0
        self.repeat = 0
        self.last_sig = None
        self.evidence = ""
        self.tx_note = ""
        budget = float(plan.time_budget_s or 0) or max(
            300.0, 1.5 * 60 * float(plan.est_minutes or 0)
        )
        self.deadline = self.started + max(60.0, budget)
        self.max_actions = int(plan.max_steps or 0) or MAX_ACTIONS
        self.seen_events = 0
        self.allow_popups = bool((plan.targets or {}).get("allow_popups"))
        self.split_info: dict = {}

    def tick(self, frac, text):
        self.last_progress = time.time()
        with contextlib.suppress(Exception):
            self.progress(frac, text)

    # -- checks between and around steps

    async def checkpoint(self):
        if st.killed():
            raise StopError("cancelled", "kill switch")
        if not st.env_allows() or not st.load().get("enabled"):
            raise StopError("cancelled", "always aware is off")
        if _cancel_requested(self.run_id):
            raise StopError("cancelled", "cancelled by Sir")
        now = time.time()
        if now > self.deadline:
            raise StopError("stuck", "time budget exceeded")
        if now - self.last_progress > NO_PROGRESS_S:
            raise StopError("stuck", "no progress")
        await self._events()
        if self.lb.user_input_at:
            await self._paused()

    async def _events(self):
        new = self.lb.events[self.seen_events :]
        self.seen_events = len(self.lb.events)
        for ev in new:
            kind, _, val = ev.partition(":")
            if kind == "popup" and not self.allow_popups:
                raise StopError("stuck", "an unexpected tab or window opened")
            if kind == "nav":
                bad = pay.forbidden_url(val)
                if bad:
                    raise StopError(
                        "stuck", f"page went somewhere Jarvis never drives ({bad})"
                    )
            if kind == "download":
                await self.lb.cancel_download(val)
                raise StopError("stuck", "the page started a download")

    async def _paused(self):
        self.tick(None, "paused: " + PAUSE_LINE)
        _audit({"run": self.run_id, "step": "pause", "result": "user_input"})
        _clear(self.run_id, "CARRY")
        waited = time.time()
        while True:
            if not st.env_allows() or not st.load().get("enabled"):
                raise StopError("cancelled", "always aware is off")
            if st.killed() or _cancel_requested(self.run_id):
                raise StopError("cancelled", "Sir took over")
            if _flagged(self.run_id, "CARRY"):
                _clear(self.run_id, "CARRY")
                self.lb.user_input_at = 0.0
                # Anything Sir did may have changed the page: time and
                # progress restart, and the next step re-checks everything.
                self.deadline += time.time() - waited
                self.tick(None, "resumed")
                return
            if time.time() - waited > PAUSE_MAX_S:
                raise StopError(
                    "cancelled", "Sir took over", "You kept it, Sir; I've stepped back."
                )
            await asyncio.sleep(0.5)

    async def page_state(self):
        page = self.owned.page
        snap = await read_snapshot(page)
        from browser import detect_page_wall

        wall = detect_page_wall(snap.title, snap.text)
        if wall in ("bot-check", "login"):
            raise StopError("stuck", f"the site put up a {wall.replace('-', ' ')} wall")
        nu = _needs_user_any(snap)
        if nu:
            raise StopError("failed", nu)
        if any(f.get("type") == "password" and f.get("visible") for f in snap.fields):
            raise StopError("stuck", "the site wants a password")
        return snap

    # -- resolving and acting

    async def locate(self, step: WebStep):
        from browser import BrowserError, BrowserManager

        page = self.owned.page
        if step.target.startswith("css="):
            loc = page.locator(step.target[4:])
            if not await loc.count():
                raise StopError("stuck", f"could not find {step.target!r}")
            return loc.first
        try:
            if step.op in ("type", "select"):
                return await BrowserManager._resolve_textbox(None, page, step.target)
            return await BrowserManager._resolve_target(None, page, step.target)
        except BrowserError as exc:
            if step.op == "select":
                loc = page.get_by_label(step.target)
                if await loc.count():
                    return loc.first
            raise StopError("stuck", str(exc)) from exc

    async def _act(self, coro_fn):
        self.lb.acting_until = time.time() + STEP_TIMEOUT_S
        try:
            return await asyncio.wait_for(coro_fn(), STEP_TIMEOUT_S)
        except asyncio.TimeoutError as exc:
            raise StopError("stuck", "a step timed out") from exc
        finally:
            await asyncio.sleep(SETTLE_S)
            self.lb.acting_until = time.time() + 0.3

    async def _click(self, loc):
        try:
            await loc.click(timeout=8000)
        except Exception:
            hidden = (
                await self.owned.page.evaluate("document.visibilityState") == "hidden"
            )
            if not hidden:
                raise
            # minimised window: rAF is throttled, so skip only the stability
            # wait; the guard already checked the element itself.
            await loc.click(timeout=8000, force=True)

    async def do_step(self, i: int, step: WebStep):
        await self.checkpoint()
        if step.sig() == self.last_sig:
            self.repeat += 1
        else:
            self.repeat, self.last_sig = 1, step.sig()
        if self.repeat >= REPEAT_LIMIT:
            raise StopError("stuck", "the same action repeated three times")
        self.actions_done += 1
        if self.actions_done > self.max_actions:
            raise StopError("stuck", "too many browser actions")
        page = self.owned.page
        _audit(
            {"run": self.run_id, "step": i, "tool": step.op, "target": step.target[:80]}
        )

        if step.op == "goto":
            if not self.owned.created:
                raise StopError("failed", "plan tried to navigate Sir's tab")
            try:
                await self._act(lambda: self.owned.goto(step.target))
            except PermissionError as exc:
                raise StopError("failed", str(exc)) from exc
        elif step.op == "wait_for":
            ok = False
            for _ in range(int(STEP_TIMEOUT_S / 0.5)):
                body = await page.evaluate(
                    "document.body ? document.body.innerText : ''"
                )
                if step.target.startswith("css="):
                    ok = await page.locator(step.target[4:]).count() > 0
                else:
                    ok = step.target.casefold() in body.casefold()
                if ok:
                    break
                await self.checkpoint()
                await asyncio.sleep(0.5)
            if not ok:
                raise StopError("stuck", f"{step.target!r} never appeared")
        else:
            await self.page_state()
            loc = await self.locate(step)
            desc = await loc.evaluate(DESCRIBE_JS)
            if step.op == "click" or step.op == "check":
                why = guard_click(desc, step)
            elif step.op == "press":
                why = guard_press(step.text, desc, step)
            else:
                why = guard_type(desc, step)
            if why:
                status = "failed" if why.startswith("needs_user") else "stuck"
                _audit({"run": self.run_id, "step": i, "result": "guard:" + why})
                raise StopError(status, why)
            if step.action is not None and step.final:
                await self.final_action(step, loc)
            elif step.op == "click":
                await self._act(lambda: self._click(loc))
            elif step.op == "check":
                await self._act(lambda: loc.check(timeout=8000))
            elif step.op == "type":
                await self._act(lambda: loc.fill(step.text, timeout=8000))
            elif step.op == "select":
                await self._act(
                    lambda: loc.select_option(label=step.text, timeout=8000)
                )
            elif step.op == "press":
                await self._act(lambda: loc.press(step.text, timeout=8000))
        await self._events()
        self.tick(
            (i + 1) / max(1, self.n_steps),
            f"step {i + 1}: {step.op} {step.target[:40]}",
        )

    # -- the declared final click

    async def final_action(self, step: WebStep, loc):
        action = self.plan.actions[step.action]
        offer = self.plan.offer_text
        if not pay.audit_writable():
            raise StopError(
                "stuck", "transaction log is not writable, so I did not proceed"
            )
        try:
            snap = await self.page_state()
        except StopError as stop:
            pay.record(self.run_id, action, offer, f"blocked:{stop.reason}")
            raise
        verdict = pay.check_final(
            action, snap, cap=pay.spend_cap() if action.kind == "payment" else None
        )
        if not verdict.ok:
            prefix = "failed:needs_user" if verdict.needs_user else "blocked"
            pay.record(
                self.run_id,
                action,
                offer,
                f"{prefix}:{verdict.reason}"[:120],
                observed=verdict.observed,
            )
            raise StopError("failed" if verdict.needs_user else "stuck", verdict.reason)
        pay.intent(self.run_id, action, offer)
        try:
            if step.op == "press":
                await self._act(lambda: loc.press(step.text, timeout=8000))
            else:
                await self._act(lambda: self._click(loc))
        except StopError as stop:
            pay.record(
                self.run_id,
                action,
                offer,
                f"unverified:{stop.reason}",
                observed=verdict.observed,
            )
            raise
        with contextlib.suppress(Exception):
            await self.owned.page.wait_for_load_state("load", timeout=10000)
        await asyncio.sleep(SETTLE_S)
        after = await read_snapshot(self.owned.page)
        nu = _needs_user_any(after)
        if nu:
            pay.record(
                self.run_id,
                action,
                offer,
                f"failed:needs_user_after_click:{nu}"[:120],
                observed=verdict.observed,
            )
            raise StopError("failed", nu, self._spoken_needs_user_after(action))
        ok, ev = await self.predicate()
        observed = dict(verdict.observed)
        if action.kind == "payment":
            seen = pay.page_totals(after.text)
            if seen:
                observed["amount"] = str(max(a for a, _, _ in seen))
        ref = pay.confirmation_ref(after.text)
        pay.record(
            self.run_id,
            action,
            offer,
            "ok:verified" if ok else f"unverified:{ev}"[:120],
            observed=observed,
            confirmation_ref=ref,
        )
        self.tx_note = self._tx_line(action, ok, ref)
        if not ok:
            raise StopError("failed", f"after the final step, {ev}", self.tx_note)

    def _tx_line(self, action, ok: bool, ref: str) -> str:
        sym = {"GBP": "£", "USD": "$", "EUR": "€"}.get(
            (action.currency or "").upper(), ""
        )
        if not ok:
            what = {
                "payment": "pressed pay",
                "send": "pressed send",
                "submit": "submitted",
            }[action.kind]
            return (
                f"I {what} but could not confirm it went through, Sir. "
                "Please check it; it's logged."
            )
        if action.kind == "payment":
            line = f"Paid {sym}{action.amount} to {action.merchant}"
        elif action.kind == "send":
            line = f"Sent to {action.recipient}"
        else:
            line = f"Submitted on {action.site}"
        return line + (f", reference {ref}." if ref else ".")

    @staticmethod
    def _spoken_needs_user_after(action) -> str:
        if action.kind == "payment":
            return (
                "Your bank asked for approval after I pressed pay, Sir. I can't do that "
                "step; the tab is in your tab bar waiting for you."
            )
        return "The site asked for a code only you can give, Sir; the tab is in your tab bar."

    # -- verification

    async def predicate(self) -> tuple[bool, str]:
        pred = (self.plan.targets or {}).get("success_predicate") or {}
        return await eval_predicate(self.owned.page, pred)

    @property
    def n_steps(self) -> int:
        return getattr(self, "_n", 1)


async def eval_predicate(page, pred: dict) -> tuple[bool, str]:
    sel = (pred or {}).get("selector")
    found = None
    if sel:
        with contextlib.suppress(Exception):
            found = await page.locator(sel).count() > 0
    data = await page.evaluate(
        "()=>({u:location.href,t:document.title,b:document.body?document.body.innerText:''})"
    )
    return predicate_holds(
        pred,
        url=data["u"],
        title=ts.unmarked(data["t"]),
        text=data["b"],
        selector_found=found,
    )


# ------------------------------------------------------------------ public API

_LOCK_FD = None


def _take_lock() -> int | None:
    path = st.sub("brave") / "run.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def _drop_lock(fd: int | None) -> None:
    if fd is not None:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _new_run_id(plan: TakeoverPlan) -> str:
    return f"web-{re.sub(r'[^A-Za-z0-9]', '', plan.id)[:16] or 'plan'}-{int(time.time() * 1000)}"


_HUMAN = {
    "no progress": "it stopped making progress",
    "time budget exceeded": "it was taking too long",
}


def _spoken(status: str, reason: str, place: str) -> str:
    why = _HUMAN.get(reason, reason.replace("_", " "))
    if status == "cancelled":
        return f"Stopped, Sir. {place}".strip()
    if reason.startswith("needs_user"):
        return (
            f"I stopped: that step needs you ({why.split(':', 1)[-1].strip()}). "
            f"Nothing was paid or sent. {place}"
        ).strip()
    return f"I couldn't finish: {why}. {place}".strip()


async def run(
    plan: TakeoverPlan,
    *,
    progress=None,
    run_id: str | None = None,
    session_factory=None,
) -> TakeoverResult:
    """Execute a web TakeoverPlan in Jarvis's own Brave window, then hand back.

    ``session_factory()`` returns an async context manager yielding a
    LiveBrave (tests inject a throwaway browser and fake KWin).
    """
    run_id = run_id or _new_run_id(plan)
    t0 = time.time()
    result = TakeoverResult(
        run_id=run_id,
        plan_id=plan.id,
        status="failed",
        journal=str(ts.journal_path(run_id)),
    )
    if not st.env_allows() or st.killed() or not st.load().get("enabled"):
        result.reason = "always aware is off"
        result.spoken = "I can't, Sir: Always Aware is switched off."
        return result
    try:
        steps = parse_steps(plan)
    except PlanError as exc:
        result.reason = f"plan: {exc}"
        result.spoken = (
            "I couldn't start that, Sir: the plan wasn't complete. Nothing was touched."
        )
        return result
    lock = _take_lock()
    if lock is None:
        result.reason = "another takeover is running"
        result.spoken = "Another web task is already running, Sir."
        return result
    st.write_private(_current_path(), run_id)
    rec = {
        "run_id": run_id,
        "plan_id": plan.id,
        "started": t0,
        "status": "running",
        "target_ids": [],
        "predicate": (plan.targets or {}).get("success_predicate") or {},
    }
    _save_record(rec)
    factory = session_factory or (lambda: LiveBrave())
    place = ""
    try:
        async with factory() as lb:
            r = _Runner(plan, progress, run_id, lb)
            r._n = len(steps)
            split = lb.tabsplit()
            status, reason, extra = "failed", "", ""
            try:
                r.tick(0.0, "taking the tab")
                await _take_tab(r, lb, split, plan, rec)
                for i, step in enumerate(steps):
                    await r.do_step(i, step)
                await r.checkpoint()
                ok, ev = await r.predicate()
                if ok:
                    status, reason = "verified", ""
                    r.evidence = ev
                else:
                    status, reason = "failed", f"the result check failed ({ev})"
            except StopError as stop:
                status, reason, extra = stop.status, stop.reason, stop.spoken
            except Exception as exc:  # unexpected: honest stop, still hand back
                msg = str(exc).splitlines()[0][:160] if str(exc) else type(exc).__name__
                status, reason = "stuck", f"error: {msg}"
                if (
                    "Target page, context or browser has been closed" in msg
                    or "disconnected" in msg.lower()
                ):
                    reason = "browser_gone"
            # hand back (also on failure); "leave it there" keeps the window
            leave = _flagged(run_id, "LEAVE")
            with contextlib.suppress(Exception):
                back = await split.handback(
                    run_id, verified=status == "verified", leave_there=leave
                )
                nb = back.get("neighbour_title", "")
                if leave:
                    place = "I've left the window on the side monitor for you."
                elif back.get("returned"):
                    place = (
                        f"It's back in your tab bar, next to {nb}."
                        if nb
                        else "It's back in your tab bar."
                    )
                elif status == "verified":
                    place = "The result is in your tab bar."
            result.status, result.reason = status, reason
            result.evidence = r.evidence
            if status == "verified":
                lead = r.tx_note or "Done, Sir."
                result.spoken = f"{lead} {place}".strip()
            else:
                result.spoken = extra or _spoken(
                    status, reason, place or "Nothing else was touched."
                )
                if extra and place:
                    result.spoken = (
                        f"{extra} {place}" if "tab bar" not in extra else extra
                    )
    except StopError as stop:
        result.status, result.reason = stop.status, stop.reason
        result.spoken = _spoken(stop.status, stop.reason, "")
    except Exception as exc:
        result.status = "stuck"
        result.reason = (
            "browser_gone"
            if "connect" in str(exc).lower()
            else f"error: {str(exc)[:120]}"
        )
        result.spoken = "I couldn't reach your browser, Sir. Nothing was changed."
    finally:
        rec.update(
            status=result.status,
            reason=result.reason,
            ended=time.time(),
            evidence=result.evidence,
        )
        _save_record(rec)
        for name in ("CANCEL", "CARRY", "LEAVE"):
            _clear(run_id, name)
        with contextlib.suppress(OSError):
            if current() == run_id:
                _current_path().unlink()
        _drop_lock(lock)
    result.duration_s = round(time.time() - t0, 2)
    _audit(
        {
            "run": run_id,
            "step": "end",
            "result": result.status,
            "reason": result.reason[:80],
        }
    )
    return result


async def _take_tab(
    r: _Runner, lb: LiveBrave, split: ts.TabSplit, plan: TakeoverPlan, rec: dict
):
    t = plan.targets or {}
    tab_target = str(t.get("tab_id") or "")
    if t.get("new_tab") or not tab_target:
        url = str(t.get("url") or "")
        if not url:
            raise StopError("failed", "plan names no tab and no url")
        screens = split.screens()
        hud, rect = split.hud(screens)
        placement = ts.choose_placement(screens, split.main(screens), hud, rect)
        owned = await lb.open_own_tab(url, minimized=placement.mode == "minimise")
        await lb.mark(owned.page)
        tab_id = ts.resolve_tab(await lb.tab_api().query(), owned.page.url)
        await split.adopt(r.run_id, tab_id, placement)
        split.note_created(r.run_id, tab_id, role="result")
    else:
        page = await lb.page_for_target(tab_target, str(t.get("url") or ""))
        if page is None:
            raise StopError("failed", "the task tab is gone")
        bad = pay.forbidden_url(page.url)
        if bad:
            raise StopError("failed", f"forbidden page: {bad}")
        if t.get("host") and not pay.host_matches(
            urlsplit(page.url).hostname or "", t["host"]
        ):
            raise StopError("failed", "the task tab is on a different site now")
        owned = OwnedPage(page, created=False, target_id=tab_target)
        await lb.mark(page)
        try:
            tab_id = ts.resolve_tab(await lb.tab_api().query(), page.url)
            j = await split.split(r.run_id, [tab_id])
        except ts.SplitError as exc:
            with contextlib.suppress(Exception):
                await page.evaluate(
                    "(m)=>{if(document.title.startsWith(m))document.title=document.title.slice(m.length)}",
                    ts.MARK,
                )
            raise StopError("stuck", f"window split: {exc}") from exc
        r.split_info = j
        _audit(
            {
                "run": r.run_id,
                "event": "tab_move",
                "tab": tab_id,
                "from_window": j["user_window"],
                "to_window": j["jarvis_window"],
            }
        )
    r.owned = owned
    rec["target_ids"] = [owned.target_id]
    rec["url"] = owned.url
    _save_record(rec)
    keep_awake = (ts.load_journal(r.run_id) or {}).get("placement", {}).get(
        "mode"
    ) == "minimise"
    await lb.watch(owned, keep_awake=keep_awake)


async def handback(run_id: str, *, session_factory=None) -> None:
    """Return a run's tabs to Sir's window (idempotent). Never raises."""
    j = ts.load_journal(run_id)
    if j is None or j.get("state") in ("handed_back", "left_there"):
        return
    factory = session_factory or (lambda: LiveBrave())
    with contextlib.suppress(Exception):
        async with factory() as lb:
            await lb.tabsplit().handback(run_id)


async def verify(
    plan: TakeoverPlan, result: TakeoverResult, *, session_factory=None
) -> tuple[bool, str]:
    """Independent re-check of the success predicate over a fresh connection."""
    rec = _load_record(result.run_id) or {}
    pred = (plan.targets or {}).get("success_predicate") or rec.get("predicate") or {}
    tids = rec.get("target_ids") or []
    if not pred or not tids:
        return False, "nothing to verify"
    factory = session_factory or (lambda: LiveBrave())
    try:
        async with factory() as lb:
            page = await lb.page_for_target(tids[0], rec.get("url", ""))
            if page is None:
                return False, "the result tab is gone"
            return await eval_predicate(page, pred)
    except Exception as exc:
        return False, f"could not re-check: {str(exc)[:80]}"


async def recover(*, session_factory=None) -> list[str]:
    """Sidecar start: hand back tabs from any run cut short. Never raises."""
    if not ts.open_journals():
        return []
    factory = session_factory or (lambda: LiveBrave())
    done: list[str] = []
    with contextlib.suppress(Exception):
        async with factory() as lb:
            done = await lb.tabsplit().recover()
            # stray helper pages are always Jarvis's own
            for p in list(lb.context.pages):
                if (
                    _ext_of(p.url) in lb.ext_ids
                    and urlsplit(p.url).path == "/" + HELPER_PAGE
                ):
                    with contextlib.suppress(Exception):
                        await p.close()
    for rid in done:
        rec = _load_record(rid)
        if rec and rec.get("status") == "running":
            rec.update(status="failed", reason="interrupted", ended=time.time())
            _save_record(rec)
    return done
