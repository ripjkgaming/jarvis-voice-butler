"""WhatsApp via the WhatSie desktop client, driven over CDP.

WhatSie is a QtWebEngine wrapper around WhatsApp Web. When launched with
``--remote-debugging-port=9223`` it exposes Chromium's ``/json/list`` HTTP
endpoints; this module evaluates JavaScript in the WhatsApp page via the
page's websocket debugger URL (raw ``websockets``, because QtWebEngine's
CDP is too limited for Playwright's browser-level connect).

Read-only voice access: status / chat list / recent messages. Sending
stays manual on the phone — drafts queue via DailyTools.whatsapp_draft
and are never auto-sent.

Setup (one time, on the laptop)::

    flatpak run com.ktechpit.whatsie --remote-debugging-port=9223

or persist it with::

    flatpak override --user com.ktechpit.whatsie \\
        --env=QTWEBENGINE_CHROMIUM_FLAGS="--remote-debugging-port=9223"

The DOM queries below are ported from the proven laptop Jarvis
(``~/jarvis/src/new_jarvis/whatsapp.py``): same selectors, same
open-then-verify flow. Opening a chat marks its messages read — that is
WhatsApp behavior, and summaries say so.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import itertools
import json
import re
import time
import urllib.request

PORT = 9223
_BASE = f"http://127.0.0.1:{PORT}"

SETUP_HINT = (
    "WhatsApp is not reachable. Open WhatSie with remote debugging: "
    "run `flatpak run com.ktechpit.whatsie --remote-debugging-port=9223`, "
    "or say 'open whatsie' first and I will queue a draft instead."
)

_cdp_ids = itertools.count(1)


def alive() -> bool:
    """Is the WhatSie debug endpoint answering? Pure HTTP, never raises."""
    try:
        with urllib.request.urlopen(_BASE + "/json/version", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def _targets() -> list:
    try:
        with urllib.request.urlopen(_BASE + "/json/list", timeout=10) as r:
            data = json.loads(r.read().decode("utf8", "replace"))
            return data if isinstance(data, list) else []
    except Exception:
        return []


def page_present(targets: list | None = None) -> bool:
    """Is a WhatsApp Web page mounted in WhatSie?"""
    rows = targets if targets is not None else _targets()
    return any(
        "web.whatsapp.com" in (t.get("url") or "") for t in rows if isinstance(t, dict)
    )


def page_ws_url(targets: list | None = None) -> str:
    """Debugger websocket URL for the WhatsApp page, or ''."""
    for t in targets if targets is not None else _targets():
        try:
            if "web.whatsapp.com" in (t.get("url") or "") and t.get("type") == "page":
                return t.get("webSocketDebuggerUrl") or ""
        except Exception:
            continue
    return ""


async def cdp_evaluate(wsurl: str, expr: str, timeout: int = 25):
    """Evaluate JS in the WhatsApp page; returns the value (or None)."""
    import websockets

    req_id = next(_cdp_ids)
    async with websockets.connect(wsurl, max_size=10_000_000, timeout=10) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": req_id,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expr,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                }
            )
        )
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if msg.get("id") == req_id:
                result = msg["result"]["result"]
                return result.get("value", result.get("description"))


async def cdp(expr: str, timeout: int = 25):
    """Evaluate JS in the WhatsApp page with one retry. None when fatal."""
    for _ in range(2):
        wsurl = page_ws_url()
        if not wsurl:
            return None
        try:
            return await cdp_evaluate(wsurl, expr, timeout)
        except Exception:
            continue
    return None


def clean(text: str) -> str:
    """Strip icon class bleed + whitespace. Pure."""
    text = re.sub(r"wds-ic-[a-z-]*", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


_LIST_JS = """JSON.stringify([...document.querySelectorAll(
  '#pane-side [data-testid^="list-item-"]')].map(r => {
  const q = s => ((r.querySelector(s) || {}).textContent || '');
  let u = '';
  const badge = [...r.querySelectorAll('[aria-label]')].find(
    e => /unread/i.test(e.getAttribute('aria-label') || ''));
  if (badge) u = (badge.getAttribute('aria-label') || '').slice(0, 30);
  if (!u) {
    const m = (r.innerText || '').match(/(\\d+)\\s*unread/i);
    if (m) u = m[0].slice(0, 30);
  }
  return {t: q('[data-testid="cell-frame-title"]').trim().slice(0, 50),
          d: q('[data-testid="cell-frame-primary-detail"]').trim().slice(0, 30),
          s: q('[data-testid="cell-frame-secondary"]').trim().slice(0, 100),
          u: u};
}))"""

_MSG_JS = """JSON.stringify([...document.querySelectorAll(
  '#main [data-testid="msg-container"]')].slice(-%d).map(m => {
  const qi = m.querySelector('[data-testid="quoted-message"]');
  const tx = m.querySelector('[data-testid="msg-text"]')
    || m.querySelector('.copyable-text .selectable-text')
    || m.querySelector('.copyable-text')
    || m.querySelector('.selectable-text');
  const cp = m.querySelector('.copyable-text');
  const pre = m.getAttribute('data-pre-plain-text')
    || (cp && cp.getAttribute ? cp.getAttribute('data-pre-plain-text') : '')
    || '';
  const out = m.className.includes('message-out')
    || !!m.querySelector('[data-testid="tail-out"]')
    || !!m.querySelector('[data-testid="msg-dblcheck"],'
      + '[data-testid="msg-check"],[data-testid="msg-dblcheck-ack"],'
      + '[data-icon="msg-dblcheck"],[data-icon="msg-check"]');
  return {me: out,
    tailOut: !!m.querySelector('[data-testid="tail-out"]'),
    pre: (pre || '').slice(0, 80),
    text: ((tx || {}).innerText || '').slice(0, 500),
    meta: ((m.querySelector('[data-testid="msg-meta"]') || {}).innerText || '').slice(0, 30),
    quote: ((qi || {}).innerText || '').slice(0, 200),
    mention: !!m.querySelector('[data-testid="mention"]')};
}))"""


def parse_chat_rows(rows: list[dict]) -> list[dict]:
    """Raw CDP list rows -> [{name, time, snippet, unread}]. Pure."""
    out = []
    seen: set[str] = set()
    for r in rows:
        try:
            name = clean(str(r.get("t", "")))[:50]
            # Unread badge text bleeds into the title element.
            name = re.sub(r"^\d+\s*unread messages?\s*", "", name).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            um = re.search(r"(\d+)", str(r.get("u", "") or ""))
            out.append(
                {
                    "name": name,
                    "time": clean(str(r.get("d", "")))[:30],
                    "snippet": clean(str(r.get("s", "")))[:100],
                    "unread": int(um.group(1)) if um else 0,
                }
            )
        except Exception:
            continue
    return out


def parse_sender(pre: str) -> tuple[str, str]:
    """'[10:07 pm, 11/09/2026] Feen: ' -> ('Feen', '10:07 pm'). Pure."""
    m = re.match(r"\[(.*?)\]\s*(.*?):\s*$", (pre or "").strip())
    if m:
        return m.group(2).strip()[:40], m.group(1).strip()[:30]
    return "", ""


def parse_messages(rows: list[dict]) -> list[dict]:
    """Raw CDP message rows -> [{me, sender, text, meta}]. Pure.

    Ownership: tail-out / tick marks are strictly own bubbles, so the
    sender on those rows bootstraps the owner identity for mid-streak
    own messages that carry neither mark.
    """
    owners: dict[str, int] = {}
    prelim = []
    for m in rows:
        try:
            sender, when = parse_sender(str(m.get("pre", "")))
            tail = bool(m.get("tailOut"))
            tick = bool(m.get("me"))
            prelim.append((sender, when, tail, tick))
            if tail and sender:
                owners[sender] = owners.get(sender, 0) + 1
        except Exception:
            prelim.append(("", "", False, False))
    owner_set = set(owners)
    out = []
    for (sender, when, tail, tick), m in zip(prelim, rows):
        try:
            text = clean(str(m.get("text", "")))[:500]
            # copyable-text fallback includes the trailing timestamp.
            text = re.sub(r"\s*\d{1,2}:\d{2}\s*(am|pm)?\s*$", "", text).strip()
            if not text:
                continue  # sticker/photo/reaction-only row
            me = tick or tail or (bool(sender) and sender in owner_set)
            out.append(
                {
                    "me": me,
                    "sender": sender,
                    "when": when,
                    "text": text,
                    "meta": clean(str(m.get("meta", "")))[:30],
                }
            )
        except Exception:
            continue
    return out


def match_chat(name: str, chats: list[dict]) -> dict | None:
    """Exact, then substring, then fuzzy match on chat name. Pure."""
    low = (name or "").lower().strip()
    if not low or not chats:
        return None
    for c in chats:
        if c["name"].lower() == low:
            return c
    for c in chats:
        if low in c["name"].lower() or c["name"].lower() in low:
            return c
    best = difflib.get_close_matches(name, [c["name"] for c in chats], n=1, cutoff=0.6)
    if best:
        return next(c for c in chats if c["name"] == best[0])
    return None


async def list_chats(limit: int = 20) -> list[dict]:
    """Newest-first chat list. [] when unreachable."""
    raw = await cdp(_LIST_JS)
    try:
        rows = json.loads(raw) if isinstance(raw, str) else []
    except Exception:
        return []
    # The pane virtualizes (~20 rows): scroll to page older chats in,
    # merging by name, then restore the scroll position.
    seen = parse_chat_rows(rows if isinstance(rows, list) else [])
    try:
        top0 = (
            await cdp("(document.querySelector('#pane-side')||{scrollTop:0}).scrollTop")
            or 0
        )
    except Exception:
        top0 = 0
    for _ in range(6):
        before = len(seen)
        await cdp(
            "(()=>{const p=document.querySelector('#pane-side'); "
            "if(p) p.scrollTop=p.scrollHeight; return 1;})()"
        )
        await asyncio.sleep(1.2)
        raw = await cdp(_LIST_JS)
        try:
            rows = json.loads(raw) if isinstance(raw, str) else []
        except Exception:
            break
        merged = {c["name"]: c for c in seen}
        merged.update({c["name"]: c for c in parse_chat_rows(rows)})
        seen = list(merged.values())
        if len(seen) == before:
            break
    with contextlib.suppress(Exception):
        await cdp(
            f"(()=>{{const p=document.querySelector('#pane-side'); "
            f"if(p) p.scrollTop={int(top0 or 0)}; return 1;}})()"
        )
    return seen[: max(1, limit)]


async def open_chat(name: str, timeout_s: int = 12) -> dict:
    """Focus a chat by name (atomic page-side match+click).

    The pane virtualizes and re-renders constantly, so the click is
    retried: newest chats are scrolled into view and React is left to
    settle before each match+click. Up to 3 attempts before giving up.
    """
    chats = await list_chats(30)
    if not chats:
        return {"ok": False, "err": "chat list unreachable"}
    hit = match_chat(name, chats)
    if hit is None:
        return {"ok": False, "err": f"no chat matches '{name[:40]}'"}
    want = hit["name"]
    esc = want.lower().replace("\\", "\\\\").replace("'", "\\'")
    click_js = (
        """(()=>{const rows=[...document.querySelectorAll(
          '#pane-side [data-testid^="list-item-"]')];
          const want='"""
        + esc
        + """';
          let hit=null;
          for (const r of rows) {
            const t=((r.querySelector('[data-testid="cell-frame-title"]')||{})
              .textContent||'').trim().toLowerCase();
            if (t === want) { hit=r; break; }
          }
          if (!hit) for (const r of rows) {
            const t=((r.querySelector('[data-testid="cell-frame-title"]')||{})
              .textContent||'').trim().toLowerCase();
            if (t && (t.includes(want)||want.includes(t))) { hit=r; break; }
          }
          if (!hit) return 'norow:'+rows.length;
          const t=hit.querySelector('[data-testid="cell-frame-container"]')||hit;
          hit.scrollIntoView({block:'center'});
          for (const e of ['mousedown','mouseup','click'])
            t.dispatchEvent(new MouseEvent(e,{bubbles:true,cancelable:true,view:window}));
          return 'fired:'+((hit.querySelector('[data-testid="cell-frame-title"]')||{}).textContent||'').trim().slice(0,50);
        })()"""
    )
    fired = None
    for _ in range(3):
        # Newest chats mount at the top; scroll there and let React settle.
        with contextlib.suppress(Exception):
            await cdp(
                "(()=>{const p=document.querySelector('#pane-side'); "
                "if(p) p.scrollTop=0; return 1;})()"
            )
        await asyncio.sleep(1.0)
        fired = await cdp(click_js)
        if isinstance(fired, str) and fired.startswith("fired:"):
            break
    if not isinstance(fired, str) or not fired.startswith("fired:"):
        return {"ok": False, "err": f"row click failed ({fired})"}
    clicked = fired.split(":", 1)[1][:50] or want
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        hdr = (
            await cdp(
                "(document.querySelector('#main header')||{innerText:''})"
                ".innerText.slice(0,80)"
            )
            or ""
        )
        if clicked[:20].lower() in hdr.lower() or want[:20].lower() in hdr.lower():
            return {"ok": True, "name": clicked or want}
        await asyncio.sleep(0.7)
    hdr = (
        await cdp(
            "(document.querySelector('#main header')||{innerText:''})"
            ".innerText.slice(0,80)"
        )
        or ""
    )
    if hdr:
        return {"ok": True, "name": hdr.split("\n")[0][:50]}
    return {"ok": False, "err": "conversation did not open"}


async def read_chat(name: str, n: int = 30) -> dict:
    """Recent messages from a chat. Opening marks them read (WhatsApp does)."""
    opened = await open_chat(name)
    if not opened.get("ok"):
        return {"ok": False, **opened}
    raw = await cdp(_MSG_JS % max(1, min(60, n)))
    try:
        rows = json.loads(raw) if isinstance(raw, str) else []
    except Exception:
        return {"ok": False, "err": "could not read messages"}
    msgs = parse_messages(rows if isinstance(rows, list) else [])
    hdr = (
        await cdp(
            "(document.querySelector('#main header')||{innerText:''})"
            ".innerText.slice(0,80)"
        )
        or ""
    )
    return {
        "ok": True,
        "name": opened.get("name", name),
        "header": hdr,
        "is_group": "\n" in (hdr or ""),
        "messages": msgs,
    }
