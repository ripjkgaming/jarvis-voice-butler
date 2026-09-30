"""Google Docs/Sheets/Drive REST clients (urllib only, fail-soft).

OAuth token lives at $JARVIS_HOME/google_token.json — separate from the
Gmail token (src/system/inbox.py) so Gmail keeps working untouched. The
client_id/client_secret are reused from that Gmail token file when present,
else read from GOOGLE_OAUTH_CLIENT_ID/SECRET in $JARVIS_HOME/keys.env.

Every public function raises GoogleError with a speakable message on
failure, never a traceback. Pass `opener=` in tests to fake the network.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
    # Calendar: added 2026-09-29. Tokens minted before then lack it, so
    # calendar calls 403 until Sir re-runs scripts/google_auth.py.
    "https://www.googleapis.com/auth/calendar.events",
    # Gmail: added 2026-09-29. Tokens minted before then lack it, so
    # Gmail calls 403 until Sir re-runs scripts/google_auth.py.
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

TOKEN_URI = "https://oauth2.googleapis.com/token"
DRIVE_BASE = "https://www.googleapis.com/drive/v3"
# Media uploads live on a separate host path; POSTing multipart to
# DRIVE_BASE/files is a 400 (create_google_doc and invoices never worked).
UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
DOCS_BASE = "https://docs.googleapis.com/v1/documents"
SHEETS_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3/calendars"
CALENDAR_SCOPE_HINT = (
    "Your Google connection predates calendar access, Sir. Re-run "
    "scripts/google_auth.py once and tick the calendar box."
)
GMAIL_SCOPE_HINT = (
    "Your Google connection predates Gmail access, Sir. Re-run "
    "scripts/google_auth.py once and tick the Gmail boxes."
)

SETUP_HINT = (
    "Google isn't connected yet, Sir — run scripts/google_auth.py once to connect it."
)

_ID_OK = re.compile(r"^[A-Za-z0-9_-]{10,}$")


class GoogleError(Exception):
    """User-facing Google failure (speakable message)."""


def jarvis_home() -> Path:
    """Base dir honoring $JARVIS_HOME (default ~/.jarvis). Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


#: Google accounts Jarvis can act for. "personal" keeps the original token
#: file; others (Sir's school account) get google_token_<name>.json.
ACCOUNTS = ("personal", "school")


def normalize_account(account: str | None) -> str:
    """Spoken account name -> "personal"/"school". Pure."""
    text = (account or "").strip().lower()
    if any(w in text for w in ("school", "secondary", "student", "edu")):
        return "school"
    return "personal"


def token_path(account: str = "personal") -> Path:
    """OAuth token file (separate from the Gmail token). Pure (env)."""
    name = normalize_account(account)
    suffix = "" if name == "personal" else f"_{name}"
    return jarvis_home() / f"google_token{suffix}.json"


def _accounts_path() -> Path:
    return jarvis_home() / "google_accounts.json"


def account_email(account: str = "personal") -> str:
    """Remembered email for an account ("" if unknown). Never raises."""
    try:
        saved = json.loads(_accounts_path().read_text())
        return str(saved.get(normalize_account(account), "") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def remember_account_email(account: str = "personal", opener=None) -> str:
    """Ask Drive who this token belongs to and remember it. "" on failure."""
    try:
        token = access_token(opener=opener, account=account)
        about = _request_json(
            "GET", f"{DRIVE_BASE}/about?fields=user(emailAddress)", token, opener=opener
        )
        email = str(((about or {}).get("user") or {}).get("emailAddress") or "")
    except Exception:
        return ""
    if email:
        try:
            path = _accounts_path()
            try:
                saved = json.loads(path.read_text())
            except (OSError, ValueError):
                saved = {}
            saved[normalize_account(account)] = email
            path.write_text(json.dumps(saved, indent=1))
        except OSError:
            pass
    return email


#: Said when a file is made "for school". Google refuses both a direct and a
#: pending ownership transfer from a personal Gmail to another organization
#: ("Ownership can only be transferred to another user in the same
#: organization"), verified live 2026-09-28 — so the school account edits.
SCHOOL_SHARE_NOTE = (
    "Your school account can edit it; Google won't let a personal account "
    "hand ownership to a school one, so use Make a copy there if it must own it."
)


def share_with_school(file_id: str, opener=None) -> str:
    """Share a file Sir's main account made with his school account as an
    editor (no notification email). Returns the school email. Raises
    GoogleError when the school email is unknown or Google refuses."""
    email = account_email("school")
    if not email:
        raise GoogleError("I don't know your school Google address yet, Sir.")
    token = access_token(opener=opener)
    _request_json(
        "POST",
        f"{DRIVE_BASE}/files/{file_id}/permissions?sendNotificationEmail=false",
        token,
        {"type": "user", "role": "writer", "emailAddress": email},
        opener=opener,
    )
    return email


def _read_keys_env() -> dict[str, str]:
    """KEY=VALUE lines from $JARVIS_HOME/keys.env. Never raises."""
    out: dict[str, str] = {}
    try:
        for line in (jarvis_home() / "keys.env").read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip("'\"")
    except OSError:
        pass
    return out


def client_creds() -> tuple[str, str]:
    """(client_id, client_secret) for the installed-app OAuth client.

    Prefers explicit env, then the existing Gmail token file (same Google
    Cloud project), then keys.env. Empty strings when nothing is found.
    """
    env_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    env_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if env_id and env_secret:
        return env_id, env_secret
    # Same Cloud project as Gmail: reuse its installed-app credentials.
    try:
        saved = json.loads(
            (Path.home() / "jarvis" / "data" / "gmail_token.json").read_text()
        )
        if (
            isinstance(saved, dict)
            and saved.get("client_id")
            and saved.get("client_secret")
        ):
            return str(saved["client_id"]), str(saved["client_secret"])
    except (OSError, ValueError):
        pass
    keys = _read_keys_env()
    return keys.get("GOOGLE_OAUTH_CLIENT_ID", ""), keys.get(
        "GOOGLE_OAUTH_CLIENT_SECRET", ""
    )


def _load_token(account: str = "personal") -> dict | None:
    try:
        saved = json.loads(token_path(account).read_text())
    except (OSError, ValueError):
        return None
    return saved if isinstance(saved, dict) else None


def save_token(saved: dict, account: str = "personal") -> None:
    """Persist the token file with 0600 perms. Never raises."""
    try:
        path = token_path(account)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(saved))
        path.chmod(0o600)
    except OSError:
        pass


def access_token(opener=None, account: str = "personal") -> str:
    """Fresh access token via the refresh-token grant. Raises GoogleError."""
    saved = _load_token(account)
    if not isinstance(saved, dict) or not saved.get("refresh_token"):
        if normalize_account(account) == "school":
            raise GoogleError(
                "Your school Google account isn't connected yet, Sir — run "
                "scripts/google_auth.py --account school."
            )
        raise GoogleError(SETUP_HINT)
    client_id, client_secret = client_creds()
    payload = urllib.parse.urlencode(
        {
            "client_id": client_id or saved.get("client_id", ""),
            "client_secret": client_secret or saved.get("client_secret", ""),
            "refresh_token": saved["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode()
    req = urllib.request.Request(
        saved.get("token_uri") or TOKEN_URI,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    call = opener or urllib.request.urlopen
    try:
        with call(req, timeout=15) as resp:
            fresh = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise GoogleError(
            "Google sign-in expired, Sir — please re-run scripts/google_auth.py."
        ) from exc
    except Exception as exc:
        raise GoogleError(f"Google did not respond ({exc}).") from exc
    token = fresh.get("access_token", "")
    if not token:
        raise GoogleError("Google sign-in expired, Sir — please reconnect it.")
    saved["token"] = token  # best-effort cache for next time
    save_token(saved, account)
    return token


def _request_json(
    method: str,
    url: str,
    token: str,
    payload: dict | list | None = None,
    opener=None,
) -> dict | list:
    """One authenticated JSON call. Raises GoogleError."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}"},
    )
    if data is not None:
        req.add_header("Content-Type", "application/json")
    call = opener or urllib.request.urlopen
    try:
        with call(req, timeout=20) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise GoogleError(
                "Google refused access, Sir — the connection may need reconnecting."
            ) from exc
        if exc.code == 404:
            raise GoogleError("I could not find that file, Sir.") from exc
        raise GoogleError(f"Google returned an error ({exc.code}).") from exc
    except Exception as exc:
        raise GoogleError(f"Google did not respond ({exc}).") from exc
    try:
        return json.loads(raw or "{}")
    except ValueError as exc:
        raise GoogleError("Google answered with garbage.") from exc


def is_id(ref: str) -> bool:
    """Does this look like a Drive/Docs id rather than a name? Pure."""
    return bool(_ID_OK.match((ref or "").strip()))


def drive_search(query: str, limit: int = 10, opener=None) -> list[dict]:
    """Search Sir's Drive by name/content. Returns [{id, name, mimeType}]."""
    token = access_token(opener=opener)
    query = (query or "").strip()[:200]
    if not query:
        raise GoogleError("What should I search Drive for, Sir?")
    safe = query.replace("'", "\\'")
    params = urllib.parse.urlencode(
        {
            "q": f"trashed = false and (name contains '{safe}' or fullText contains '{safe}')",
            "fields": "files(id, name, mimeType, modifiedTime)",
            "pageSize": max(1, min(50, int(limit or 10))),
            "orderBy": "modifiedTime desc",
        }
    )
    got = _request_json("GET", f"{DRIVE_BASE}/files?{params}", token, opener=opener)
    files = got.get("files", []) if isinstance(got, dict) else []
    return [f for f in files if isinstance(f, dict)][:limit]


def resolve_file(name_or_id: str, opener=None) -> dict:
    """Name -> first Drive search hit; ids pass through via metadata read."""
    ref = (name_or_id or "").strip()
    if not ref:
        raise GoogleError("Which file, Sir?")
    token = access_token(opener=opener)
    if is_id(ref):
        meta = _request_json(
            "GET",
            f"{DRIVE_BASE}/files/{ref}?fields=id,name,mimeType",
            token,
            opener=opener,
        )
        if isinstance(meta, dict) and meta.get("id"):
            return meta
        raise GoogleError("I could not find that file, Sir.")
    hits = drive_search(ref, opener=opener)
    if not hits:
        raise GoogleError(f"I could not find anything called {ref[:60]}, Sir.")
    return hits[0]


def drive_upload(
    name: str,
    data: bytes,
    mime: str,
    convert_to: str | None = None,
    folder_id: str = "",
    opener=None,
) -> dict:
    """Upload bytes to Drive; convert_to="doc" makes a Google Doc. Returns meta."""
    token = access_token(opener=opener)
    if not (name or "").strip():
        raise GoogleError("The upload needs a file name, Sir.")
    metadata: dict = {"name": name.strip()[:200]}
    if convert_to == "doc":
        metadata["mimeType"] = "application/vnd.google-apps.document"
    if folder_id.strip():
        metadata["parents"] = [folder_id.strip()]
    # Multipart/related upload (metadata + media in one call).
    boundary = "jarvis-upload-boundary"
    body = (
        f"--{boundary}\r\nContent-Type: application/json\r\n\r\n".encode()
        + json.dumps(metadata).encode()
        + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode()
        + bytes(data or b"")
        + f"\r\n--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(
        f"{UPLOAD_BASE}/files?uploadType=multipart&fields=id,name,mimeType,webViewLink",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f'multipart/related; boundary="{boundary}"',
        },
    )
    call = opener or urllib.request.urlopen
    try:
        with call(req, timeout=60) as resp:
            meta = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            detail = json.loads(exc.read().decode())["error"]["message"][:160]
        raise GoogleError(
            f"Google refused the upload ({exc.code}) {detail}".strip()
        ) from exc
    except Exception as exc:
        raise GoogleError(f"Google did not respond ({exc}).") from exc
    if not isinstance(meta, dict) or not meta.get("id"):
        raise GoogleError("The upload did not stick, Sir.")
    return meta


def drive_export(file_id: str, mime: str = "text/plain", opener=None) -> bytes:
    """Export a Google Doc/Sheet as bytes (default plain text)."""
    token = access_token(opener=opener)
    req = urllib.request.Request(
        f"{DRIVE_BASE}/files/{file_id.strip()}/export?mimeType={urllib.parse.quote(mime)}",
        headers={"Authorization": f"Bearer {token}"},
    )
    call = opener or urllib.request.urlopen
    try:
        with call(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise GoogleError("I could not read that file, Sir.") from exc
    except Exception as exc:
        raise GoogleError(f"Google did not respond ({exc}).") from exc


def _para_text(element: dict) -> str:
    """One structuralElement -> its text content. Pure."""
    if not isinstance(element, dict):
        return ""
    out: list[str] = []
    for child in (element.get("paragraph") or {}).get("elements", []) or []:
        run = (child or {}).get("textRun") or {}
        if run.get("content"):
            out.append(str(run["content"]))
    return "".join(out)


def docs_text(doc: dict) -> str:
    """Docs.get payload -> plain text (tables flattened). Pure."""
    try:
        content = (doc.get("body") or {}).get("content") or []
    except AttributeError:
        return ""
    parts: list[str] = []
    for element in content:
        if not isinstance(element, dict):
            continue
        if "paragraph" in element:
            parts.append(_para_text(element))
        elif "table" in element:
            for row in (element.get("table") or {}).get("tableRows", []) or []:
                for cell in (row or {}).get("tableCells", []) or []:
                    for child in (cell or {}).get("content", []) or []:
                        parts.append(_para_text(child))
    return "".join(parts).strip()[:8000]


def docs_read(doc_id: str, opener=None) -> str:
    """Google Doc -> plain text."""
    token = access_token(opener=opener)
    doc = _request_json("GET", f"{DOCS_BASE}/{doc_id.strip()}", token, opener=opener)
    if not isinstance(doc, dict):
        raise GoogleError("I could not read that document, Sir.")
    return docs_text(doc)


def docs_create(title: str, html: str, folder_id: str = "", opener=None) -> dict:
    """New Google Doc from HTML (formatting preserved via conversion)."""
    title = (title or "").strip()[:200]
    if not title:
        raise GoogleError("The document needs a title, Sir.")
    if not (html or "").strip():
        raise GoogleError("There is no content for that document, Sir.")
    return drive_upload(
        title + ".html",
        html.encode(),
        "text/html",
        convert_to="doc",
        folder_id=folder_id,
        opener=opener,
    )


def sheets_read(
    spreadsheet_id: str, sheet_range: str = "A1:Z50", opener=None
) -> list[list]:
    """Sheet values -> rows of cell strings."""
    token = access_token(opener=opener)
    got = _request_json(
        "GET",
        f"{SHEETS_BASE}/{spreadsheet_id.strip()}/values/{urllib.parse.quote(sheet_range, safe='')}",
        token,
        opener=opener,
    )
    values = got.get("values", []) if isinstance(got, dict) else []
    return [r for r in values if isinstance(r, list)][:500]


def sheets_append(
    spreadsheet_id: str, sheet_range: str, rows: list[list], opener=None
) -> dict:
    """Append rows to a sheet (USER_ENTERED so formulas/dates parse)."""
    rows = [r for r in (rows or []) if isinstance(r, list)]
    if not rows:
        raise GoogleError("There are no rows to add, Sir.")
    token = access_token(opener=opener)
    params = urllib.parse.urlencode(
        {"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"}
    )
    got = _request_json(
        "POST",
        f"{SHEETS_BASE}/{spreadsheet_id.strip()}/values/{urllib.parse.quote(sheet_range, safe='')}:append?{params}",
        token,
        {"values": rows[:1000]},
        opener=opener,
    )
    return got if isinstance(got, dict) else {}


def sheets_create(title: str, rows: list[list] | None = None, opener=None) -> dict:
    """New spreadsheet, optionally pre-filled from A1. Returns {spreadsheetId}."""
    token = access_token(opener=opener)
    title = (title or "").strip()[:200]
    if not title:
        raise GoogleError("The spreadsheet needs a title, Sir.")
    got = _request_json(
        "POST", SHEETS_BASE, token, {"properties": {"title": title}}, opener=opener
    )
    if not isinstance(got, dict) or not got.get("spreadsheetId"):
        raise GoogleError("The spreadsheet did not stick, Sir.")
    if rows:
        _request_json(
            "PUT",
            f"{SHEETS_BASE}/{got['spreadsheetId']}/values/A1?valueInputOption=USER_ENTERED",
            token,
            {"values": [r for r in rows if isinstance(r, list)][:1000]},
            opener=opener,
        )
    return got


def ensure_folder(name: str, opener=None) -> str:
    """Drive folder id for `name`, creating it when missing."""
    token = access_token(opener=opener)
    params = urllib.parse.urlencode(
        {
            "q": f"trashed = false and mimeType = 'application/vnd.google-apps.folder' and name = '{name.replace(chr(39), chr(92) + chr(39))}'",
            "fields": "files(id, name)",
            "pageSize": 1,
        }
    )
    got = _request_json("GET", f"{DRIVE_BASE}/files?{params}", token, opener=opener)
    files = got.get("files", []) if isinstance(got, dict) else []
    if files:
        return str(files[0].get("id", ""))
    created = _request_json(
        "POST",
        f"{DRIVE_BASE}/files?fields=id",
        token,
        {"name": name, "mimeType": "application/vnd.google-apps.folder"},
        opener=opener,
    )
    folder_id = created.get("id", "") if isinstance(created, dict) else ""
    if not folder_id:
        raise GoogleError("I could not set up the Drive folder, Sir.")
    return str(folder_id)


def _has_calendar_scope(account: str = "personal") -> bool:
    """Was the saved token granted calendar access? Unknown counts as yes."""
    saved = _load_token(account) or {}
    scopes = saved.get("scopes") or saved.get("scope")
    if not scopes:
        return True
    if isinstance(scopes, str):
        scopes = scopes.split()
    return any("auth/calendar" in str(s) for s in scopes)


def _calendar_token(opener=None, account: str = "personal") -> str:
    if not _has_calendar_scope(account):
        raise GoogleError(CALENDAR_SCOPE_HINT)
    return access_token(opener=opener, account=account)


def _has_gmail_scope(account: str = "personal") -> bool:
    """Was the saved token granted Gmail read+send? Unknown counts as yes."""
    saved = _load_token(account) or {}
    scopes = saved.get("scopes") or saved.get("scope")
    if not scopes:
        return True
    if isinstance(scopes, str):
        scopes = scopes.split()
    texts = [str(s) for s in scopes]
    return any("gmail.readonly" in s for s in texts) and any(
        "gmail.send" in s for s in texts
    )


def event_body(event: dict, timezone: str = "") -> dict:
    """Normalized event dict -> Calendar API body. Pure. Raises GoogleError.

    Input keys: title, date (YYYY-MM-DD), start/end ("HH:MM", optional),
    end_date (YYYY-MM-DD, all-day ranges), location, notes. No start ->
    all-day event; start without end -> 1 hour.
    """
    title = str(event.get("title") or "").strip()[:200]
    date = str(event.get("date") or "").strip()
    if not title or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise GoogleError("That event needs a title and a YYYY-MM-DD date, Sir.")
    start = str(event.get("start") or "").strip()
    end = str(event.get("end") or "").strip()
    body: dict = {"summary": title}
    for key, field in (("location", "location"), ("notes", "description")):
        if str(event.get(key) or "").strip():
            body[field] = str(event[key]).strip()[:1000]
    hhmm = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
    if not hhmm.match(start):
        import datetime as _dt

        last = date
        end_date = str(event.get("end_date") or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_date) and end_date > date:
            last = end_date  # multi-day all-day event (end is exclusive)
        nxt = (_dt.date.fromisoformat(last) + _dt.timedelta(days=1)).isoformat()
        body["start"] = {"date": date}
        body["end"] = {"date": nxt}
        return body
    if not hhmm.match(end):
        h, m = (int(x) for x in start.split(":"))
        end = f"{min(h + 1, 23):02d}:{m:02d}" if h < 23 else "23:59"
    tz = timezone or local_timezone()
    body["start"] = {
        "dateTime": f"{date}T{int(start.split(':')[0]):02d}:{start.split(':')[1]}:00",
        "timeZone": tz,
    }
    body["end"] = {
        "dateTime": f"{date}T{int(end.split(':')[0]):02d}:{end.split(':')[1]}:00",
        "timeZone": tz,
    }
    return body


def local_timezone() -> str:
    """IANA zone for new events: $JARVIS_TZ, else the system zone, else UTC."""
    tz = os.environ.get("JARVIS_TZ", "").strip()
    if tz:
        return tz
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


def calendar_list(
    time_min: str,
    time_max: str,
    calendar_id: str = "primary",
    account: str = "personal",
    opener=None,
) -> list[dict]:
    """Events between two RFC3339 instants, soonest first."""
    token = _calendar_token(opener=opener, account=account)
    params = urllib.parse.urlencode(
        {
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 250,
        }
    )
    cal = urllib.parse.quote(calendar_id, safe="")
    data = _request_json(
        "GET", f"{CALENDAR_BASE}/{cal}/events?{params}", token, opener=opener
    )
    return list(data.get("items") or []) if isinstance(data, dict) else []


def calendar_create(
    event: dict,
    calendar_id: str = "primary",
    account: str = "personal",
    opener=None,
) -> dict:
    """Create one event from a normalized dict (see event_body)."""
    body = event_body(event)
    token = _calendar_token(opener=opener, account=account)
    cal = urllib.parse.quote(calendar_id, safe="")
    data = _request_json(
        "POST", f"{CALENDAR_BASE}/{cal}/events", token, body, opener=opener
    )
    return data if isinstance(data, dict) else {}
