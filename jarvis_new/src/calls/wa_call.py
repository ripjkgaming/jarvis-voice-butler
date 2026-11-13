"""WhatsApp call control (accept / decline / hang up / dial) over CDP.

EVERYTHING in ``SELECTORS`` and the JS below is UNVERIFIED: written from
research without a real ringing call. Expect to fix selectors after the first
manual test call. A selector miss logs loudly (``SELECTOR MISS``) and the
action returns False / None; nothing raises into the call agent.

``WhatsAppCallDriver`` is the abstract interface; ``CdpCallDriver`` drives the
shared WhatSie page (uses system.whatsapp's page lock); ``FakeDriver`` is for
tests. Do not instantiate CdpCallDriver against the real page in tests.
"""

from __future__ import annotations

import abc
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.calls.wa")

# Each entry: ordered candidates. "text:<s>" = clickable whose aria-label /
# title / visible text contains <s> (case-insensitive); anything else = CSS.
# ALL UNVERIFIED.
SELECTORS: dict[str, list[str]] = {
    "incoming_overlay": [
        "[data-testid='incoming-call']",
        "[data-testid='call-notification']",
        "div[role='dialog']:has([data-icon='call-accept'])",
        "text:Incoming voice call",
        "text:Incoming call",
        "text:WhatsApp voice call",
    ],
    "incoming_caller": [
        "[data-testid='incoming-call'] [data-testid='cell-frame-title']",
        "div[role='dialog'] span[dir='auto']",
        "div[role='dialog'] h1",
        "div[role='dialog'] span[title]",
    ],
    "accept": [
        "[data-testid='call-accept']",
        "[data-icon='call-accept']",
        "button[aria-label='Accept']",
        "text:Accept",
        "text:Answer",
    ],
    "decline": [
        "[data-testid='call-decline']",
        "[data-icon='call-decline']",
        "button[aria-label='Decline']",
        "text:Decline",
    ],
    "hangup": [
        "[data-testid='call-end']",
        "[data-icon='call-end']",
        "[data-icon='end-call']",
        "button[aria-label='End call']",
        "button[aria-label='Leave']",
        "text:End call",
        "text:Leave",
    ],
    "in_call": [
        "[data-testid='call-end']",
        "[data-icon='call-end']",
        "[data-testid='call-controls']",
        "button[aria-label='Mute']",
        "text:Mute",
    ],
    "chat_voice_call": [
        "[data-testid='voice-call']",
        "[data-icon='voice-call']",
        "button[aria-label='Voice call']",
        "text:Voice call",
    ],
    "search_box": [
        "[data-testid='chat-list-search']",
        "div[contenteditable='true'][data-tab='3']",
        "input[type='text'][role='textbox']",
    ],
    "first_result": [
        "[data-testid='cell-frame-container']",
        "#pane-side [role='listitem']",
        "#pane-side [role='row']",
    ],
}

# JS prelude shared by every snippet: resolve a selector-table key.
_PRELUDE = """
const SEL = %s;
const __vis = e => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
function __find(key) {
  for (const s of SEL[key] || []) {
    try {
      if (s.startsWith('text:')) {
        const t = s.slice(5).toLowerCase();
        const els = document.querySelectorAll("button,[role='button'],div[role='dialog'] *,span,div");
        for (const e of els) {
          const lab = ((e.getAttribute('aria-label')||'') + ' ' + (e.getAttribute('title')||'') +
                       ' ' + (e.children.length ? '' : (e.innerText||''))).toLowerCase();
          if (lab.includes(t) && __vis(e)) return e;
        }
      } else {
        const e = document.querySelector(s);
        if (e && __vis(e)) return e;
      }
    } catch (_) {}
  }
  return null;
}
function __click(e) { (e.closest("button,[role='button']") || e).click(); }
"""


def _js(body: str) -> str:
    return "(async()=>{" + (_PRELUDE % json.dumps(SELECTORS)) + body + "})()"


JS_DETECT = _js(
    """
const o = __find('incoming_overlay');
if (!o) return JSON.stringify(null);
const c = __find('incoming_caller');
return JSON.stringify({caller: c ? (c.getAttribute('title') || c.innerText || '').trim() : '',
                       text: (o.innerText || '').slice(0, 200)});
"""
)
JS_IS_IN_CALL = _js("return JSON.stringify(!!__find('in_call'));")


def js_click(key: str) -> str:
    return _js(
        f"const e = __find({json.dumps(key)}); if (!e) return JSON.stringify(false);"
        " __click(e); return JSON.stringify(true);"
    )


def js_dial(query: str) -> str:
    q = json.dumps(query)
    return _js(
        f"""
const sb = __find('search_box'); if (!sb) return JSON.stringify('search_box');
sb.focus(); document.execCommand('selectAll'); document.execCommand('insertText', false, {q});
await new Promise(r => setTimeout(r, 900));
const r0 = __find('first_result'); if (!r0) return JSON.stringify('first_result');
__click(r0);
await new Promise(r => setTimeout(r, 900));
const b = __find('chat_voice_call'); if (!b) return JSON.stringify('chat_voice_call');
__click(b);
return JSON.stringify('ok');
"""
    )


@dataclass
class IncomingCall:
    caller: str  # display name or number as shown by WhatsApp ('' if unknown)
    raw: dict = field(default_factory=dict)


class WhatsAppCallDriver(abc.ABC):
    @abc.abstractmethod
    async def detect_incoming(self) -> IncomingCall | None: ...

    @abc.abstractmethod
    async def accept(self) -> bool: ...

    @abc.abstractmethod
    async def decline(self) -> bool: ...

    @abc.abstractmethod
    async def hangup(self) -> bool: ...

    @abc.abstractmethod
    async def dial(self, number_or_name: str) -> bool: ...

    @abc.abstractmethod
    async def is_in_call(self) -> bool: ...


Evaluate = Callable[[str], Awaitable[object]]


async def _default_evaluate(expr: str):
    """Run JS on the shared WhatSie page under its page lock."""
    from system import whatsapp as wa

    async with wa._page_session():
        return await wa.cdp_once(expr)


class CdpCallDriver(WhatsAppCallDriver):
    """UNVERIFIED against a real call. ``evaluate`` is injectable for tests."""

    def __init__(self, evaluate: Evaluate | None = None) -> None:
        self._eval = evaluate or _default_evaluate

    async def _run(self, expr: str, what: str):
        try:
            raw = await self._eval(expr)
        except Exception:
            log.exception("WA call CDP %s failed", what)
            return None
        if raw is None:
            log.warning("WA call CDP %s: no page / no result", what)
            return None
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            log.error("WA call CDP %s: bad result %r", what, raw)
            return None

    async def _click(self, key: str) -> bool:
        ok = await self._run(js_click(key), key)
        if ok is not True:
            log.error(
                "SELECTOR MISS: WA call '%s' not found/clickable; "
                "update SELECTORS in calls/wa_call.py (all UNVERIFIED)",
                key,
            )
            return False
        return True

    async def detect_incoming(self) -> IncomingCall | None:
        d = await self._run(JS_DETECT, "detect_incoming")
        if not isinstance(d, dict):
            return None
        return IncomingCall(caller=str(d.get("caller") or ""), raw=d)

    async def accept(self) -> bool:
        return await self._click("accept")

    async def decline(self) -> bool:
        return await self._click("decline")

    async def hangup(self) -> bool:
        return await self._click("hangup")

    async def is_in_call(self) -> bool:
        return (await self._run(JS_IS_IN_CALL, "is_in_call")) is True

    async def dial(self, number_or_name: str) -> bool:
        if not number_or_name.strip():
            return False
        r = await self._run(js_dial(number_or_name.strip()), "dial")
        if r == "ok":
            return True
        log.error(
            "SELECTOR MISS: WA dial stopped at '%s'; update SELECTORS in "
            "calls/wa_call.py (all UNVERIFIED)",
            r,
        )
        return False


class FakeDriver(WhatsAppCallDriver):
    """Scriptable in-memory driver for tests."""

    def __init__(self) -> None:
        self.incoming: IncomingCall | None = None
        self.in_call = False
        self.calls: list[tuple[str, str]] = []
        self.fail: set[str] = set()

    def ring(self, caller: str = "Unknown") -> None:
        self.incoming = IncomingCall(caller=caller)

    async def detect_incoming(self) -> IncomingCall | None:
        return self.incoming

    async def accept(self) -> bool:
        self.calls.append(("accept", ""))
        if "accept" in self.fail or self.incoming is None:
            return False
        self.incoming, self.in_call = None, True
        return True

    async def decline(self) -> bool:
        self.calls.append(("decline", ""))
        if "decline" in self.fail or self.incoming is None:
            return False
        self.incoming = None
        return True

    async def hangup(self) -> bool:
        self.calls.append(("hangup", ""))
        if "hangup" in self.fail or not self.in_call:
            return False
        self.in_call = False
        return True

    async def dial(self, number_or_name: str) -> bool:
        self.calls.append(("dial", number_or_name))
        if "dial" in self.fail or self.in_call:
            return False
        self.in_call = True
        return True

    async def is_in_call(self) -> bool:
        return self.in_call
