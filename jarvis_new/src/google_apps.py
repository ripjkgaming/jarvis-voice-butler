"""Open Google Docs / Sheets / Slides / Drive the way Sir says it.

"open Google Docs"            -> the Docs homepage (never a guessed file URL)
"open the second document"    -> his 2nd most recently opened Doc
"open Physics notes in Docs"  -> the Doc whose name best matches
"... on my school account"    -> same, as the school Google account

Nth/name lookups use the Drive API for that account (src/google_api.py);
without it (school account not connected, Google down) names fall back to a
Drive search page and "nth" to the homepage, so a request always lands
somewhere sensible. `authuser=<email>` picks the right signed-in account in
the browser.
"""

from __future__ import annotations

import re
import urllib.parse

import google_api

#: app -> (homepage, Drive mimeType or None, spoken noun)
APPS: dict[str, tuple[str, str | None, str]] = {
    "docs": (
        "https://docs.google.com/document/",
        "application/vnd.google-apps.document",
        "document",
    ),
    "sheets": (
        "https://docs.google.com/spreadsheets/",
        "application/vnd.google-apps.spreadsheet",
        "spreadsheet",
    ),
    "slides": (
        "https://docs.google.com/presentation/",
        "application/vnd.google-apps.presentation",
        "presentation",
    ),
    "drive": ("https://drive.google.com/drive/my-drive", None, "file"),
}

_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "latest": 1, "last": 1, "recent": 1, "newest": 1,
}
_NTH = re.compile(r"^(?:the\s+)?(?:(\d{1,2})(?:st|nd|rd|th)?|(\w+))(?:\s+(?:most\s+)?recent)?"
                  r"(?:\s+(?:one|doc|docs|document|sheet|spreadsheet|slides?|deck|presentation|file))?$")
_HOME_WORDS = {"", "home", "homepage", "home page", "main page", "it", "google docs",
               "google sheets", "google slides", "google drive", "docs", "sheets",
               "slides", "drive"}


def normalize_app(app: str) -> str:
    """Spoken app -> docs/sheets/slides/drive. Pure."""
    text = (app or "").strip().lower()
    if any(w in text for w in ("sheet", "spreadsheet", "excel")):
        return "sheets"
    if any(w in text for w in ("slide", "presentation", "deck", "powerpoint")):
        return "slides"
    if "drive" in text:
        return "drive"
    return "docs"


def parse_which(which: str) -> tuple[str, int | str | None]:
    """"" -> ("home", None); "2nd"/"the second one" -> ("nth", 2);
    anything else -> ("name", text). Pure."""
    text = " ".join((which or "").strip().lower().split())
    if text in _HOME_WORDS:
        return "home", None
    m = _NTH.match(text)
    if m:
        if m.group(1):
            n = int(m.group(1))
            if n >= 1:
                return "nth", n
        elif m.group(2) in _ORDINALS:
            return "nth", _ORDINALS[m.group(2)]
    return "name", (which or "").strip()


def with_authuser(url: str, email: str) -> str:
    """Add authuser=<email> so the browser opens the right account. Pure."""
    if not email:
        return url
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if k != "authuser"] + [("authuser", email)]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def score_name(name: str, wanted: str) -> int:
    """Higher = better name match (0 = no match). Pure."""
    a, b = name.strip().lower(), wanted.strip().lower()
    if not b:
        return 0
    if a == b:
        return 100
    if a.startswith(b):
        return 80
    if b in a:
        return 60
    words = [w for w in re.split(r"\W+", b) if w]
    hits = sum(1 for w in words if w in a)
    return int(40 * hits / len(words)) if words and hits else 0


def _files(app: str, account: str, query: str, limit: int, opener=None) -> list[dict]:
    """Drive files of this app type, most recently opened by Sir first."""
    _home, mime, _noun = APPS[app]
    clauses = ["trashed = false"]
    if mime:
        clauses.append(f"mimeType = '{mime}'")
    if query:
        safe = query.replace("\\", "\\\\").replace("'", "\\'")
        clauses.append(f"name contains '{safe}'")
    params = urllib.parse.urlencode(
        {
            "q": " and ".join(clauses),
            "orderBy": "viewedByMeTime desc,modifiedTime desc",
            "fields": "files(id,name,webViewLink,viewedByMeTime)",
            "pageSize": max(1, min(50, limit)),
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
    )
    token = google_api.access_token(opener=opener, account=account)
    got = google_api._request_json(
        "GET", f"{google_api.DRIVE_BASE}/files?{params}", token, opener=opener
    )
    files = got.get("files", []) if isinstance(got, dict) else []
    return [f for f in files if isinstance(f, dict) and f.get("webViewLink")]


def resolve(app: str, which: str = "", account: str = "personal", opener=None) -> dict:
    """Where to go: {"url", "say", "title"}. Never raises."""
    app = normalize_app(app)
    account = google_api.normalize_account(account)
    home, _mime, noun = APPS[app]
    email = google_api.account_email(account)
    if not email and account == "school":
        email = google_api.remember_account_email(account, opener=opener)
    if not email and account == "school":
        # School account not connected to the API: Google's account index
        # ("second signed-in account") still opens it in the browser.
        import os

        email = os.environ.get("JARVIS_SCHOOL_AUTHUSER", "1").strip()
    label = {"docs": "Google Docs", "sheets": "Google Sheets",
             "slides": "Google Slides", "drive": "Google Drive"}[app]
    whose = " on your school account" if account == "school" else ""
    kind, arg = parse_which(which)
    if kind == "home":
        return {"url": with_authuser(home, email), "title": label,
                "say": f"Opening {label}{whose}, Sir."}
    try:
        if kind == "nth":
            n = int(arg)  # type: ignore[arg-type]
            files = _files(app, account, "", max(n, 10), opener)
            if n > len(files):
                return {"url": with_authuser(home, email), "title": label,
                        "say": f"You only have {len(files)} recent {noun}s, Sir; "
                               f"I've opened {label}{whose} instead."}
            f = files[n - 1]
            return {"url": with_authuser(f["webViewLink"], email), "title": f["name"],
                    "say": f"Opening {f['name']}, Sir."}
        wanted = str(arg)
        files = _files(app, account, wanted, 25, opener)
        if not files:
            # "name contains" is whole-string; retry on the longest word.
            longest = max(re.split(r"\W+", wanted), key=len, default="")
            if longest and longest.lower() != wanted.lower():
                files = _files(app, account, longest, 25, opener)
        ranked = sorted(files, key=lambda f: -score_name(f.get("name", ""), wanted))
        if ranked and score_name(ranked[0].get("name", ""), wanted) > 0:
            f = ranked[0]
            return {"url": with_authuser(f["webViewLink"], email), "title": f["name"],
                    "say": f"Opening {f['name']}, Sir."}
    except google_api.GoogleError as exc:
        if kind == "nth":
            return {"url": with_authuser(home, email), "title": label,
                    "say": f"I can't see your recent {noun}s just now ({exc}); "
                           f"I've opened {label}{whose}."}
    except Exception:
        pass
    if kind == "nth":
        return {"url": with_authuser(home, email), "title": label,
                "say": f"I've opened {label}{whose}, Sir."}
    search = "https://drive.google.com/drive/search?" + urllib.parse.urlencode({"q": str(arg)})
    return {"url": with_authuser(search, email), "title": f"Search: {arg}",
            "say": f"I couldn't pin down '{arg}', Sir, so I've opened a Drive search for it{whose}."}
