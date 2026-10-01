"""Universal launcher: "open <anything>" -> the right app, site, or search.

The old path forced every launch through an 8-item Literal / worked
example, so "open dolphin" opened Spotify and "open obs" opened nothing.
This resolves ANY spoken target against what Sir actually has, in this
order (each step only runs when the previous abstains):

  1. Installed apps    -- .desktop Name/GenericName/Keywords/Exec, PATH,
                          flatpak exports; fuzzy-scored, cached.
  2. Browser history   -- sites Sir has actually visited whose title/host
                          match, ranked by visit_count + recency.
  3. Closest app       -- a weaker fuzzy app match (typos, partial names).
  4. Google search     -- "I'm Feeling Lucky" redirect to the first result;
                          if that is blocked, the plain results page.

A local 1.9B model (Ollama ``jarvis-router``) is consulted ONLY when the
top candidates are close enough to be ambiguous -- exact/dominant hits
skip the LLM entirely so the common case stays at PATH-lookup latency.
Everything is pure and injectable (index, history, and the LLM are
parameters) so the whole chain is unit-testable without a desktop.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

# --- data types -------------------------------------------------------


@dataclass(frozen=True)
class AppEntry:
    """One launchable desktop program."""

    name: str  # display name, e.g. "OBS Studio"
    argv: list[str]  # exec, e.g. ["obs"] or ["flatpak","run","<id>"]
    keywords: str = ""  # extra searchable text (GenericName+Keywords)


@dataclass(frozen=True)
class Decision:
    """What to do for a launch request."""

    kind: str  # "app" | "url"
    target: str  # app display name, or a full URL
    argv: list[str] = field(default_factory=list)
    say: str = ""
    reason: str = ""  # which layer decided (for logs/tests)
    confidence: float = 0.0


# --- scoring ----------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]+")


def _norm(text: str) -> str:
    return " ".join(_WORD.findall((text or "").lower()))


# Spoken filler after the app name ("sober on the laptop please") that would
# otherwise drag the fuzzy match to the wrong app or a web search.
_TRAILING_FILLER = re.compile(
    r"(?:\s+(?:on|in)\s+(?:the\s+|my\s+)?(?:laptop|pc|computer|desktop|screen|monitor)"
    r"|\s+for\s+me|\s+please|\s+now|\s+app)+[.!?]*$",
    re.IGNORECASE,
)


# Leading speech cruft ("hey jarvis, could you open the ...") that reaches
# the launcher whenever the caller passes the raw phrase instead of a name.
_LEADING_FILLER = re.compile(
    r"^(?:(?:hey\s+)?(?:jarvis|jeeves|jarves|jervis)\b[\s,.:;!-]*)?"
    r"(?:(?:please|can\s+you|could\s+you|would\s+you)\b[\s,]*)*"
    r"(?:(?:open|launch|start|run|load|fire\s+up|bring\s+up|pull\s+up)\b\s*)?"
    r"(?:(?:up|the|my|a)\b\s*)*",
    re.IGNORECASE,
)


def strip_filler(query: str) -> str:
    """Drop leading wakeword/verb and trailing location/politeness words.

    Pure; never strips the whole query away.
    """
    q = (query or "").strip().rstrip(".!?")
    stripped = _TRAILING_FILLER.sub("", _LEADING_FILLER.sub("", q)).strip()
    return stripped or q


_FUZZY_FULL = 0.75


def _score(query: str, text: str) -> float:
    """0..1 match of query against a candidate's searchable text. Pure.

    Blends exact/substring/token-overlap with edit-distance so both
    "obs" -> "OBS Studio" (substring) and "libra office" ->
    "LibreOffice" (fuzzy) score high, while unrelated pairs stay low.
    """
    q, t = _norm(query), _norm(text)
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0
    qt, tt = set(q.split()), set(t.split())
    # Whole query is a token of the candidate ("obs" in "obs studio").
    if q in tt or any(q == w for w in tt):
        return 0.95
    if q in t:  # contiguous substring ("libre" in "libreoffice")
        return 0.9
    # Spacing-insensitive: "vs code" -> "vscode", "libre office" -> "libreoffice".
    qc = q.replace(" ", "")
    if len(qc) >= 4 and qc != q:
        if qc in tt:
            return 0.95
        if qc in t.replace(" ", ""):
            return 0.9
    overlap = len(qt & tt) / len(qt) if qt else 0.0
    ratio = SequenceMatcher(None, q, t).ratio()
    # Letters-only resemblance ("telegram"~"steam", "slack"~"lact") is weak
    # evidence: damp it so an uninstalled app searches instead of launching
    # a lookalike. Close misspellings (>= 0.75) keep full weight.
    if ratio < _FUZZY_FULL:
        ratio *= 0.8
    return max(overlap * 0.85, ratio)


# --- layer 1: installed apps -----------------------------------------

_DESKTOP_DIRS = (
    Path.home() / ".local" / "share" / "applications",
    Path("/usr/share/applications"),
    Path.home() / ".local" / "share" / "flatpak" / "exports" / "share" / "applications",
    Path("/var/lib/flatpak") / "exports" / "share" / "applications",
)

_FLATPAK_RUN = re.compile(r"flatpak run (?:--\S+(?:=\S+)? )*([A-Za-z][A-Za-z0-9_.-]*)")


def _parse_desktop(text: str, which=shutil.which) -> AppEntry | None:
    """One .desktop file's text -> AppEntry, or None if not launchable."""
    fields: dict[str, str] = {}
    no_display = False
    for line in text.splitlines():
        if line.strip() == "[Desktop Entry]":
            continue
        if line.startswith("[") and fields:
            break  # stop at the first action group
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if (
            key in ("Name", "GenericName", "Keywords", "Exec", "Type", "NoDisplay")
            and key not in fields
        ):
            fields[key] = val.strip()
        if key == "NoDisplay" and val.strip().lower() == "true":
            no_display = True
    if no_display or fields.get("Type", "Application") != "Application":
        return None
    name = fields.get("Name", "").strip()
    exec_line = fields.get("Exec", "").strip()
    if not name or not exec_line:
        return None
    flat = _FLATPAK_RUN.search(exec_line)
    if flat and which("flatpak"):
        argv = ["flatpak", "run", flat.group(1)]
    else:
        # Drop field codes (%U %f ...) and resolve the binary.
        parts = [p for p in exec_line.split() if not p.startswith("%")]
        if not parts:
            return None
        binary = parts[0].strip("\"'")
        resolved = (
            binary
            if (binary.startswith("/") and os.access(binary, os.X_OK))
            else which(binary)
        )
        if not resolved:
            return None
        argv = [resolved, *parts[1:]]
    keywords = " ".join(fields.get(k, "") for k in ("GenericName", "Keywords")).replace(
        ";", " "
    )
    return AppEntry(name=name, argv=argv, keywords=keywords)


def index_apps(
    dirs: tuple[Path, ...] | None = None, which=shutil.which
) -> list[AppEntry]:
    """Scan .desktop dirs into a de-duplicated app index. Reads files only."""
    seen: dict[str, AppEntry] = {}
    for directory in dirs if dirs is not None else _DESKTOP_DIRS:
        try:
            files = sorted(directory.glob("*.desktop"))
        except OSError:
            continue
        for path in files:
            try:
                entry = _parse_desktop(path.read_text(errors="replace"), which=which)
            except OSError:
                continue
            if entry and entry.name.lower() not in seen:
                seen[entry.name.lower()] = entry
    return list(seen.values())


def best_app(query: str, apps: list[AppEntry]) -> tuple[AppEntry | None, float]:
    """Highest-scoring app for the query, with its score. Pure."""
    best: AppEntry | None = None
    best_score = 0.0
    for app in apps:
        s = max(_score(query, app.name), _score(query, app.keywords) * 0.9)
        if s > best_score:
            best, best_score = app, s
    return best, round(best_score, 3)


# --- priority apps -----------------------------------------------------

# Sir's everyday apps: exact spoken aliases that always win, on every launch
# path (desktop voice, phone, agent open_app), before any fuzzy/LLM layer.
# Each maps to .desktop entry names tried in order (argv comes from the
# entry, so e.g. Resolve keeps its env/GPU prefix), or a special builder.
PRIORITY_APPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("sober", "roblox", "sober roblox", "sobre", "soba"), ("Sober",)),
    (
        ("davinci resolve", "davinci", "resolve", "da vinci resolve", "da vinci"),
        ("DaVinci Resolve",),
    ),
    (("files", "file manager", "dolphin", "my files", "file explorer"), ("Dolphin",)),
    (("konsole", "terminal", "console", "the terminal", "shell"), ("Konsole",)),
    (
        ("brave", "brave browser", "browser", "web browser"),
        ("Brave Web Browser", "Brave"),
    ),
)
_CLAUDE_ALIASES = frozenset({"claude", "claude code", "claud", "clod"})
# Apps that refuse a second instance (Sober pops a "Crash: already running"
# dialog): if one is up, say so instead of launching a duplicate.
_SINGLE_INSTANCE = frozenset({"Sober", "DaVinci Resolve"})
# Apps Sir never wants opened (a misheard "Sober" once launched Spotify).
# Checked at every launch site against the name, target, argv and URL.
# JARVIS_BLOCKED_APPS (comma list) adds more.
_BLOCKED_APPS = frozenset({"spotify"})


def blocked_apps() -> frozenset[str]:
    extra = os.environ.get("JARVIS_BLOCKED_APPS", "")
    return _BLOCKED_APPS | {w.strip().lower() for w in extra.split(",") if w.strip()}


def is_blocked(*parts: object) -> str | None:
    """The blocked app any launch part names (app, target, argv, URL), or None."""
    text = " ".join(str(p) for p in parts if p).lower()
    return next((b for b in sorted(blocked_apps()) if b in text), None)


def blocked_say(name: str) -> str:
    return f"{name.title()} is disabled on this machine, Sir. I won't open it."


def _run_quiet(argv: list[str]) -> str:
    """stdout of a short read-only command, "" on any failure."""
    try:
        import subprocess

        return subprocess.run(argv, capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return ""


def is_running(argv: list[str]) -> bool:
    """True when the program an argv launches is already running."""
    if len(argv) >= 3 and Path(argv[0]).name == "flatpak" and argv[1] == "run":
        running = _run_quiet(["flatpak", "ps", "--columns=application"]).split()
        return argv[2] in running
    # Skip env-style prefixes ("env VAR=1 /opt/app/bin/app") to the binary.
    binary = next((a for a in reversed(argv) if a.startswith("/") and "=" not in a), "")
    return bool(binary) and bool(
        _run_quiet(["pgrep", "-f", "-x", f"{binary}.*"]).strip()
    )


def _priority_key(query: str) -> str:
    return _norm(strip_filler(query))


def priority_app(
    query: str,
    apps: list[AppEntry] | None = None,
    which=shutil.which,
    running=None,
) -> Decision | None:
    """Decision for one of Sir's priority apps, else None. Reads no network."""
    key = _priority_key(query)
    if not key:
        return None
    if key in _CLAUDE_ALIASES:
        # Claude Code is a terminal program: run it inside Konsole.
        konsole, claude = which("konsole"), which("claude")
        if konsole and claude:
            return Decision(
                "app",
                "Claude",
                argv=[konsole, "--workdir", str(Path.home()), "-e", claude],
                say="Opening Claude in Konsole, Sir.",
                reason="priority",
                confidence=1.0,
            )
        return None
    for aliases, entry_names in PRIORITY_APPS:
        if key not in aliases:
            continue
        index = index_apps() if apps is None else apps
        by_name = {a.name.lower(): a for a in index}
        for name in entry_names:
            app = by_name.get(name.lower())
            if app is not None and app.name in _SINGLE_INSTANCE:
                check = running or (lambda d: is_running(d.argv))
                if check(app):
                    return Decision(
                        "app",
                        app.name,
                        argv=[],
                        say=f"{app.name} is already running, Sir.",
                        reason="priority-running",
                        confidence=1.0,
                    )
            if app is not None:
                return Decision(
                    "app",
                    app.name,
                    argv=list(app.argv),
                    say=f"Opening {app.name}, Sir.",
                    reason="priority",
                    confidence=1.0,
                )
        return None
    return None


# --- layer 2: browser history ----------------------------------------

_HISTORY_PATHS = (
    Path.home() / ".config" / "BraveSoftware" / "Brave-Browser" / "Default" / "History",
    Path.home() / ".config" / "google-chrome" / "Default" / "History",
    Path.home() / ".config" / "chromium" / "Default" / "History",
)


def _host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return ""


def history_sites(
    query: str, paths: tuple[Path, ...] | None = None, limit: int = 5
) -> list[tuple[str, str, float]]:
    """Sites matching query from browser history: (title, url, score).

    Chromium locks History while running, so it is copied to a temp file
    and opened read-only. Ranked by title/host match blended with a
    log-ish visit_count boost. Never raises; returns [] on any failure.
    """
    out: list[tuple[str, str, float]] = []
    qtokens = set(_norm(query).split())
    for src in paths if paths is not None else _HISTORY_PATHS:
        if not src.exists():
            continue
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            shutil.copy2(src, tmp)
            con = sqlite3.connect(f"file:{tmp}?mode=ro&immutable=1", uri=True)
            try:
                rows = con.execute(
                    "SELECT url, title, visit_count FROM urls "
                    "WHERE visit_count > 0 ORDER BY visit_count DESC LIMIT 4000"
                ).fetchall()
            finally:
                con.close()
        except (OSError, sqlite3.Error):
            continue
        finally:
            if tmp:
                with __import__("contextlib").suppress(OSError):
                    os.unlink(tmp)
        for url, title, visits in rows:
            host = _host(url)
            # Match against the registrable-ish name (host minus tld) and title.
            host_name = re.sub(r"^www\.", "", host).split(".")[0]
            # Match the DOMAIN, not the page title: "open gimp" means the
            # site gimp.org, never a YouTube video whose title says "gimp".
            # Title matches count only weakly so they can't clear the floor
            # alone -- they merely reinforce a domain that already matches.
            s = max(_score(query, host_name), _score(query, title or "") * 0.6)
            # The site's name is a whole word Sir said ("google classroom"
            # -> classroom.google.com): solid evidence, not mere lookalike.
            if len(host_name) >= 4 and host_name in qtokens:
                s = max(s, 0.8)
            if s < 0.72:
                continue
            boost = min(0.1, (int(visits or 0)) / 2000)
            out.append((title or host, url, round(min(1.0, s + boost), 3)))
        if out:
            break  # first browser with history wins
    out.sort(key=lambda r: r[2], reverse=True)
    # De-dup by host, keep the strongest.
    seen: set[str] = set()
    deduped: list[tuple[str, str, float]] = []
    for title, url, s in out:
        h = _host(url)
        if h in seen:
            continue
        seen.add(h)
        deduped.append((title, url, s))
    return deduped[:limit]


# --- layer 4: google fallback ----------------------------------------


def lucky_url(query: str, timeout: float = 5.0, opener=None) -> str:
    """First organic result via Google's "I'm Feeling Lucky" redirect.

    Returns the resolved destination URL, or the plain results page when
    the redirect is blocked/unavailable. Never raises.
    """
    q = urllib.parse.quote_plus(query.strip())
    results_page = f"https://www.google.com/search?q={q}"
    lucky = f"https://www.google.com/search?q={q}&btnI=1"
    try:
        req = urllib.request.Request(
            lucky,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
                )
            },
        )
        _open = opener or urllib.request.urlopen
        with _open(req, timeout=timeout) as resp:
            final = resp.geturl()
            location = resp.headers.get("Location", "")
        # Google wraps the target as /url?q=<dest>; unwrap it.
        for candidate in (location, final):
            m = re.search(r"[?&]q=(https?[^&]+)", candidate or "")
            if m:
                return urllib.parse.unquote(m.group(1))
            if candidate and "google.com" not in _host(candidate):
                return candidate
    except Exception:
        pass
    return results_page


# --- arbiter (local LLM, only when ambiguous) ------------------------

OLLAMA_URL = os.environ.get("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434")
ROUTER_MODEL = os.environ.get("JARVIS_ROUTER_MODEL", "jarvis-router")


def _llm_pick(
    query: str, options: list[str], timeout: float = 3.0, poster=None
) -> int | None:
    """Ask the local router to choose one option index. None on any failure.

    Pure I/O against Ollama's /api/generate; deterministic (temp 0), tiny
    num_predict. Callers pass `poster` in tests to avoid the network.
    """
    listing = "\n".join(f"{i}) {opt}" for i, opt in enumerate(options))
    prompt = (
        f'The user said: "open {query}".\n'
        f"Which of these best matches what they want to open?\n{listing}\n"
        "Reply with only the single digit index. If none fit, reply -1."
    )
    body = json.dumps(
        {
            "model": ROUTER_MODEL,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_predict": 4},
        }
    ).encode()
    try:
        if poster is not None:
            text = poster(body)
        else:
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/generate",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = json.loads(resp.read()).get("response", "")
        m = re.search(r"-?\d+", text or "")
        if not m:
            return None
        idx = int(m.group(0))
        return idx if 0 <= idx < len(options) else None
    except Exception:
        return None


# --- top-level resolve ------------------------------------------------

# Thresholds: a dominant app hit acts outright; a mid hit competes with
# history and, if still close, goes to the arbiter; below the floor we
# search the web.
_APP_STRONG = 0.9
_APP_FLOOR = 0.55
_HIST_STRONG = 0.85


def resolve_launch(
    query: str,
    *,
    apps: list[AppEntry] | None = None,
    history: list[tuple[str, str, float]] | None = None,
    known_sites: dict[str, str] | None = None,
    llm=_llm_pick,
    lucky=lucky_url,
) -> Decision:
    """Resolve "open <query>" to an app launch, a URL, or a web search.

    All external inputs are injectable so this is fully unit-testable.
    """
    q = strip_filler(query)
    if not q:
        return Decision(
            "url", "https://www.google.com", say="Nothing to open, Sir.", reason="empty"
        )

    apps = index_apps() if apps is None else apps
    pri = priority_app(q, apps=apps)
    if pri is not None:
        return pri
    app, app_score = best_app(q, apps)

    # Layer 0: a well-known site named exactly ("google") beats a partial
    # app hit ("Google Play Store") and stray history rows ("... - Google
    # Search"); an app named exactly the same ("ChatGPT") still wins.
    site_key = _norm(q).replace(" ", "")
    if known_sites and site_key in known_sites and app_score < 1.0:
        return Decision(
            "url",
            known_sites[site_key],
            say=f"Opening {q}, Sir.",
            reason="known-site",
            confidence=0.9,
        )

    # Layer 1: a dominant installed-app match wins immediately.
    if app is not None and app_score >= _APP_STRONG:
        return Decision(
            "app",
            app.name,
            argv=list(app.argv),
            say=f"Opening {app.name}, Sir.",
            reason="app-strong",
            confidence=app_score,
        )

    # Layer 2: sites Sir has actually visited.
    hist = history_sites(q) if history is None else history
    if hist and hist[0][2] >= _HIST_STRONG:
        title, url, s = hist[0]
        return Decision(
            "url",
            url,
            say=f"Opening {title}, Sir.",
            reason="history-strong",
            confidence=s,
        )

    # Layer 3: ambiguous zone -- gather the near-matches and let the tiny
    # local model arbitrate app vs. history vs. search.
    candidates: list[tuple[str, Decision]] = []
    if app is not None and app_score >= _APP_FLOOR:
        candidates.append(
            (
                f"app: {app.name}",
                Decision(
                    "app",
                    app.name,
                    argv=list(app.argv),
                    say=f"Opening {app.name}, Sir.",
                    reason="app-arbiter",
                    confidence=app_score,
                ),
            )
        )
    for title, url, s in hist[:3]:
        if s >= 0.72:
            candidates.append(
                (
                    f"website you visited: {title} ({_host(url)})",
                    Decision(
                        "url",
                        url,
                        say=f"Opening {title}, Sir.",
                        reason="history-arbiter",
                        confidence=s,
                    ),
                )
            )

    if candidates:
        if len(candidates) == 1:
            return candidates[0][1]
        idx = llm(q, [c[0] for c in candidates])
        if idx is not None:
            return candidates[idx][1]
        # No arbiter available: take the highest-confidence candidate.
        return max(candidates, key=lambda c: c[1].confidence)[1]

    # Layer 4: nothing installed or visited matches -- search the web and
    # go straight to the first result.
    url = lucky(q)
    return Decision(
        "url",
        url,
        say=f"Nothing of yours matched, Sir — opening the top result for {q}.",
        reason="web-search",
        confidence=0.4,
    )


def warm_router(timeout: float = 20.0) -> bool:
    """Load the router model into VRAM ahead of the first ambiguous launch.

    Cold load is ~6s, warm ~0.5s: call once at session start (background
    thread). Returns True when Ollama answered. Never raises.
    """
    body = json.dumps(
        {"model": ROUTER_MODEL, "prompt": "", "keep_alive": "30m", "stream": False}
    ).encode()
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False
