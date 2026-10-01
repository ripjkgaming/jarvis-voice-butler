"""Close a website or app by name ("close YouTube", "close Dolphin").

Websites first: Sir's system Brave runs with a CDP port, so a site closes
as exactly its tab(s) via /json/close, never the whole Brave window with
his other tabs in it. Anything else is a desktop window, closed through
KWin (title or app match) on Plasma Wayland, or wmctrl on X11.
"""

from __future__ import annotations

import os
import re
import subprocess
import urllib.parse
import urllib.request

from system import kwin_windows

# Words Sir wraps the name in: "close the YouTube tab", "quit my Steam app".
_FILLER = re.compile(
    r"^(?:the|my|this|that)\s+|\s+(?:tabs?|windows?|apps?|application|program|website|site|page)$"
)
# Never close Jarvis itself ("close jarvis" would kill the assistant mid-call).
_PROTECTED = ("jarvis",)


def clean_name(name: str) -> str:
    """Lowercased target with filler stripped. Pure."""
    text = re.sub(r"[.!?,]+$", "", (name or "").strip().lower()).strip()
    prev = None
    while prev != text:
        prev = text
        text = _FILLER.sub("", text).strip()
    return text


def _host(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _site_host(name: str) -> str:
    """Host Sir means by a site name ("youtube" -> youtube.com). Pure."""
    from tools import KNOWN_SITES

    key = name.replace(" ", "").replace("-", "")
    if key in KNOWN_SITES:
        return _host(KNOWN_SITES[key])
    if "." in name and " " not in name:
        return _host(name if "://" in name else f"https://{name}")
    return ""


def match_tabs(targets: list, name: str) -> list[dict]:
    """CDP /json/list rows that are the named site. Pure.

    A known site or domain matches by host (so "close google" does not
    take out Gmail or Docs); a free-form name matches the host label or
    the tab title ("close the physics notebook").
    """
    name = clean_name(name)
    if not name:
        return []
    site = _site_host(name)
    key = name.replace(" ", "")
    hits = []
    for t in targets if isinstance(targets, list) else []:
        if not isinstance(t, dict) or t.get("type") != "page":
            continue
        url = str(t.get("url", ""))
        if not url.startswith(("http://", "https://")):
            continue
        host = _host(url)
        if site:
            # Exact host (plus mobile m.): "google" must not take out
            # notebook.google.com or mail.google.com.
            ok = host in (site, "m." + site, "mobile." + site)
        else:
            labels = host.split(".")
            ok = key in labels or name in str(t.get("title", "")).lower()
        if ok:
            hits.append(t)
    return hits


def _cdp_port() -> int:
    try:
        return int(os.environ.get("JARVIS_BRAVE_CDP_PORT", "9222") or 9222)
    except ValueError:
        return 9222


def _cdp(path: str, opener=None, timeout: float = 3.0) -> bytes:
    open_fn = opener or urllib.request.urlopen
    with open_fn(f"http://127.0.0.1:{_cdp_port()}{path}", timeout=timeout) as resp:
        return resp.read()


def close_tabs(name: str, opener=None) -> list[str]:
    """Close Brave tabs showing the named site. Returns their titles."""
    import json

    try:
        targets = json.loads(_cdp("/json/list", opener).decode() or "[]")
    except Exception:
        return []  # Brave not running / no CDP: fall through to windows.
    closed = []
    for t in match_tabs(targets, name):
        try:
            _cdp(f"/json/close/{t['id']}", opener)
            closed.append(str(t.get("title") or _host(str(t.get("url", "")))))
        except Exception:
            continue
    return closed


def close_window(name: str, run=subprocess.run) -> tuple[str, int] | None:
    """Close the best-matching desktop window. (title, matches) or None.

    A site name ("google", "discord") whose tab was not open never closes
    a Brave window: its title merely mentions the site, and closing it
    would take every other tab with it.
    """
    site = bool(_site_host(name))
    if kwin_windows.available():
        wins = kwin_windows.find(name, run=run)
        if site:
            wins = [w for w in wins if not re.search(r"\bbrave\b", w[1], re.I)]
        if not wins:
            return None
        uuid, title = wins[0]
        kwin_windows.act_on("close", uuid, run=run)
        return title, len(wins)
    if site:
        return None
    proc = run(["wmctrl", "-c", name], capture_output=True, text=True, timeout=5)
    return (name, 1) if proc.returncode == 0 else None


def close_target(name: str, opener=None, run=subprocess.run) -> dict:
    """Close a site's tabs, else an app window. {"ok", "say", "closed"}."""
    target = clean_name(name)
    if not target:
        return {"ok": False, "say": "Close what, Sir?"}
    if any(p in target for p in _PROTECTED):
        return {"ok": False, "say": "I'd rather not close myself, Sir."}
    tabs = close_tabs(target, opener=opener)
    if tabs:
        n = len(tabs)
        what = tabs[0][:60] if n == 1 else f"{n} {target} tabs"
        return {"ok": True, "say": f"Closed {what}.", "closed": "tab"}
    try:
        hit = close_window(target, run=run)
    except (OSError, subprocess.SubprocessError):
        hit = None
    if hit:
        title, n = hit
        more = f", {n - 1} more still open" if n > 1 else ""
        return {"ok": True, "say": f"Closed {title[:60]}{more}.", "closed": "window"}
    return {"ok": False, "say": f"Nothing called {target[:60]} is open, Sir."}
