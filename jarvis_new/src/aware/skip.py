"""Always Aware skip rules and the sensitive-window matcher (PLAN.md §1.2).

``evaluate(ctx)`` is pure and ordered: the first hit wins and a skip means
no capture is taken at all. ``is_sensitive`` is shared with capture's
window blackout (§1.3). ``gather()`` reads the live machine; every probe
is fail-soft and the unsafe direction (guest/locked unknown) fails closed.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field

from aware import state as st

APPS = (
    "keepassxc",
    "keepass",
    "bitwarden",
    "1password",
    "kwalletmanager",
    "kwallet",
    "polkit",
    "ksshaskpass",
    "pinentry",
    "seahorse",
    "authenticator",
)
TITLES = (
    r"password",
    r"passphrase",
    r"sign[ -]?in",
    r"log[ -]?in\b",
    r"\b2fa\b",
    r"two[- ]factor",
    r"verification code",
    r"one[- ]time (?:code|password)",
    r"authentication required",
    r"\bprivate tab\b",
    r"private window",
    r"with tor\b",
    r"\(private\)",
)
HOSTS = (
    "accounts.google.com",
    "myaccount.google.com",
    "passwords.google.com",
    "login.microsoftonline.com",
    "login.live.com",
    "appleid.apple.com",
    "paypal.com",
    "barclays.co.uk",
    "hsbc.co.uk",
    "natwest.com",
    "lloydsbank.com",
    "halifax.co.uk",
    "santander.co.uk",
    "nationwide.co.uk",
    "monzo.com",
    "starlingbank.com",
    "revolut.com",
    "tsb.co.uk",
    "firstdirect.com",
    "metrobankonline.co.uk",
    "coop.co.uk",
    "chase.com",
    "nhs.uk",
    "nhsapp.service.nhs.uk",
    "patientaccess.com",
    "hmrc.gov.uk",
    "bitwarden.com",
    "1password.com",
    "lastpass.com",
)
PATHS = (
    "/login",
    "/signin",
    "/sign-in",
    "/log-in",
    "/checkout",
    "/payment",
    "/payments/",
    "/auth",
    "/oauth",
    "/2fa",
    "/mfa",
    "/password",
    "/account/security",
)
SCHEMES_DENIED = ("brave:", "chrome:", "chrome-extension:", "devtools:", "about:")


@dataclass
class Rules:
    apps: tuple = APPS
    titles: tuple = TITLES
    hosts: tuple = HOSTS
    paths: tuple = PATHS


def deny_path():
    return st.aware_dir() / "deny.json"


def load_rules() -> Rules:
    """Defaults plus ~/.jarvis/aware/deny.json additions. Never raises."""
    rules = Rules()
    try:
        data = json.loads(deny_path().read_text())
    except (OSError, ValueError):
        return rules
    if not isinstance(data, dict):
        return rules

    def more(key: str, base: tuple, regex: bool = False) -> tuple:
        extra = data.get(key)
        if not isinstance(extra, list):
            return base
        items = [str(x).strip().lower() for x in extra if str(x).strip()]
        if regex:
            items = [re.escape(x) for x in items]
        return tuple(base) + tuple(items)

    return Rules(
        apps=more("apps", APPS),
        titles=more("titles", TITLES, regex=True),
        hosts=more("hosts", HOSTS),
        paths=more("paths", PATHS),
    )


def host_path(url: str) -> str:
    """host + path of a URL, query and fragment stripped; '' if not http(s)."""
    try:
        parts = urllib.parse.urlsplit(url or "")
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return (parts.netloc.lower() + parts.path)[:200]


def url_denied(url: str, rules: Rules | None = None) -> bool:
    rules = rules or Rules()
    raw = (url or "").strip().lower()
    if not raw:
        return False
    if raw.startswith(SCHEMES_DENIED) and not raw.startswith("about:blank"):
        return True
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        return True
    host = parts.hostname or ""
    for denied in rules.hosts:
        if host == denied or host.endswith("." + denied):
            return True
    if "bank" in host.split(".")[0] or host.startswith(("banking.", "online.")):
        return True
    path = parts.path.rstrip("/") + "/"
    for prefix in rules.paths:
        p = prefix.rstrip("/")
        if re.search(re.escape(p) + r"(?:/|$)", path):
            return True
    return False


def is_sensitive(
    app: str, title: str, url: str = "", rules: Rules | None = None
) -> bool:
    """True for password managers, auth prompts, private windows, denied URLs."""
    rules = rules or Rules()
    app_l = (app or "").lower()
    title_l = (title or "").lower()
    if any(a in app_l for a in rules.apps):
        return True
    if any(re.search(p, title_l) for p in rules.titles):
        return True
    return url_denied(url, rules)


def password_focused(tab: dict | None, evaluate=None) -> bool | None:
    """One read-only CDP probe of document.activeElement.type (Brave only).

    None when the tab or probe is unavailable."""
    if not tab or not tab.get("webSocketDebuggerUrl"):
        return None
    try:
        from aware import cdp

        fn = evaluate or cdp.evaluate
        value = fn(
            tab["webSocketDebuggerUrl"],
            "(document.activeElement && document.activeElement.type) || ''",
        )
    except Exception:
        return None
    return str(value or "").lower() == "password"


@dataclass
class Context:
    env_ok: bool = True
    killed: bool = False
    state: dict = field(default_factory=dict)
    now: float = 0.0
    guest: bool = False
    private_call: bool = False
    locked: bool = False
    watch_running: bool = False
    visual_watch: bool = False
    focus_active: bool = False
    idle: str = "active"
    foreground: dict | None = None
    foreground_url: str = ""
    password_focused: bool | None = False
    outputs: list = field(default_factory=list)
    hud: str | None = None
    school: bool = False
    rules: Rules = field(default_factory=Rules)


@dataclass
class Verdict:
    skip: bool
    reason: str = ""
    log_only: bool = False


def evaluate(ctx: Context) -> Verdict:
    """Ordered skip rules (PLAN.md §1.2). Pure."""
    s = ctx.state or {}
    if not ctx.env_ok or not s.get("enabled"):
        return Verdict(True, "disabled")
    if ctx.killed:
        return Verdict(True, "killed")
    if st.paused(s, ctx.now):
        return Verdict(True, "paused")
    if ctx.guest:
        return Verdict(True, "guest")
    if ctx.private_call:
        return Verdict(True, "private_call")
    if ctx.locked:
        return Verdict(True, "locked")
    if ctx.watch_running:
        return Verdict(True, "watch_mode")
    if ctx.visual_watch:
        return Verdict(True, "visual_watch")
    if ctx.idle != "active":
        return Verdict(True, "idle")
    fg = ctx.foreground or {}
    if is_sensitive(
        fg.get("app", ""), fg.get("title", ""), ctx.foreground_url, ctx.rules
    ):
        return Verdict(True, "sensitive_foreground")
    if ctx.password_focused:
        return Verdict(True, "password_focused")
    if not ctx.outputs:
        return Verdict(True, "no_monitors")
    if [o for o in ctx.outputs if o != ctx.hud] == []:
        return Verdict(True, "only_hud_monitor")
    if ctx.focus_active:
        return Verdict(False, "focus_mode", log_only=True)
    if ctx.school:
        return Verdict(False, "school_mode", log_only=True)
    return Verdict(False)


# --------------------------------------------------------------- live probes


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def school_mode() -> bool:
    def read():
        data = json.loads((st.jarvis_home() / "mode.json").read_text())
        return isinstance(data, dict) and data.get("mode") == "school"

    return _safe(read, False)


_MEDIA_CACHE: dict = {"ts": -1e9, "playing": False}
MEDIA_CACHE_S = 45.0


def media_playing(now: float | None = None, players=None) -> bool:
    """Any MPRIS player reporting Playing. Cached; never raises."""
    now = time.time() if now is None else now
    if players is None and now - _MEDIA_CACHE["ts"] < MEDIA_CACHE_S:
        return _MEDIA_CACHE["playing"]

    def read():
        from system import audio_ctl

        found = players() if players is not None else audio_ctl.players()
        return any(status == "Playing" for _, status in found)

    playing = bool(_safe(read, False))
    _MEDIA_CACHE.update(ts=now, playing=playing)
    return playing


def presence(now: float | None = None) -> str:
    """Input-idle state, except media playing counts as "active". Fail-soft."""
    import input_idle

    idle = _safe(input_idle.read_state, "unknown")
    if idle != "active" and media_playing(now):
        return "active"
    return idle


def visual_watch_active() -> bool:
    """Visual watch lives in this (bridge) process; not imported = not active."""
    import sys

    def read():
        mod = sys.modules.get("computer_watch")
        return bool(mod and mod.status().get("active"))

    return _safe(read, False)


def gather(outputs: list, hud: str | None, now: float, brave_tab=None) -> Context:
    """Live Context. Guest/private/locked probes fail closed (True)."""
    import phone_session
    import watch_mode

    state = st.load()
    fg = _safe(lambda: __import__("active_window").active(), None)
    tab = brave_tab
    url = (tab or {}).get("url", "") if tab else ""
    is_brave = "brave" in str((fg or {}).get("app", "")).lower()
    return Context(
        env_ok=st.env_allows(),
        killed=st.killed(),
        state=state,
        now=now,
        guest=_safe(phone_session.guest_required, True)
        or _safe(watch_mode.guest_required, True),
        private_call=_safe(phone_session.has_private_calls, True),
        locked=_safe(watch_mode.session_locked, True),
        watch_running=_safe(watch_mode.is_running, False),
        visual_watch=visual_watch_active(),
        focus_active=_safe(lambda: __import__("focus").is_active(), False),
        idle=presence(now),
        foreground=fg,
        foreground_url=url if is_brave else "",
        password_focused=password_focused(tab) if is_brave else False,
        outputs=outputs,
        hud=hud,
        school=school_mode(),
        rules=load_rules(),
    )
