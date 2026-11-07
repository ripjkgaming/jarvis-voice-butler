"""Approved-source read-only adapters. No scans occur at import/initialization."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
MAX_CHARS = 200_000
MAX_CHUNKS = 512
CHUNK_CHARS = 1024
DOC_MIME = "application/vnd.google-apps.document"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
ALLOWED = frozenset(
    {"text/plain", "text/markdown", "application/pdf", DOC_MIME, SHEET_MIME}
)
FILE_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
A1 = re.compile(
    r"^(?:'[^']+'|[A-Za-z0-9_ ]+)!([A-Z]{1,3})([1-9][0-9]*)(?::([A-Z]{1,3})([1-9][0-9]*))?$"
)
META_FIELDS = "id,name,mimeType,parents,trashed,modifiedTime,version,size,webViewLink,capabilities(canDownload)"


class SourceError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _bounded(chunks):
    total = 0
    if len(chunks) > MAX_CHUNKS:
        raise SourceError(
            "too_large", "Too many source chunks; use a smaller document."
        )
    for c in chunks:
        total += len(c["text"])
    if total > MAX_CHARS:
        raise SourceError(
            "too_large", "Extracted content exceeds the approved parser bound."
        )
    return chunks


def text_chunks(text):
    if len(text) > MAX_CHARS:
        raise SourceError("too_large", "Text exceeds the parser limit.")
    chunks = []
    offset = 0
    for n, line in enumerate(text.splitlines(keepends=True), 1):
        value = line.rstrip("\r\n")
        for start in range(0, len(value), CHUNK_CHARS):
            chunks.append(
                {
                    "text": value[start : start + CHUNK_CHARS],
                    "locator": {"line_start": n, "line_end": n},
                    "offset": offset + start,
                }
            )
        offset += len(line)
    return _bounded(chunks)


def parse_doc(doc):
    """Retain tab identity and structural indices; never infer physical pages."""
    chunks = []
    visited = 0

    def elements(items, tab, path="", depth=0):
        nonlocal visited
        if depth > 12:
            raise SourceError("too_large", "Document nesting exceeds the parser limit.")
        for n, item in enumerate(items):
            visited += 1
            if visited > 4000:
                raise SourceError(
                    "too_large", "Document structure exceeds the parser limit."
                )
            if not isinstance(item, dict):
                continue
            location = f"{path}/{n}"
            if "paragraph" in item:
                text = "".join(
                    str(e.get("textRun", {}).get("content", ""))
                    for e in item["paragraph"].get("elements", [])
                    if isinstance(e, dict)
                ).rstrip("\r\n")
                for start in range(0, len(text), CHUNK_CHARS):
                    chunks.append(
                        {
                            "text": text[start : start + CHUNK_CHARS],
                            "offset": start,
                            "locator": {
                                "tab": tab,
                                "structural_path": location,
                                "start_index": item.get("startIndex"),
                                "end_index": item.get("endIndex"),
                            },
                        }
                    )
            for r, row in enumerate(item.get("table", {}).get("tableRows", [])):
                for c, cell in enumerate(row.get("tableCells", [])):
                    elements(
                        cell.get("content", []),
                        tab,
                        f"{location}/table/{r}/{c}",
                        depth + 1,
                    )
            if "sectionBreak" in item:
                continue
            _bounded(chunks)

    def tabs(items, depth=0):
        if depth > 12:
            raise SourceError("too_large", "Too many nested document tabs.")
        for tab in items:
            elements(
                tab.get("documentTab", {}).get("body", {}).get("content", []),
                str(tab.get("tabProperties", {}).get("tabId", "")),
            )
            tabs(tab.get("childTabs", []), depth + 1)

    if doc.get("tabs"):
        tabs(doc["tabs"])
    else:
        elements(doc.get("body", {}).get("content", []), "body")
    return _bounded(chunks)


def _col_number(value):
    result = 0
    for char in value:
        result = result * 26 + ord(char) - 64
    return result


def _col_name(number):
    result = ""
    while number:
        number, rem = divmod(number - 1, 26)
        result = chr(65 + rem) + result
    return result


def check_range(value):
    match = A1.fullmatch(value or "")
    if not match:
        raise SourceError("unsupported", "Use an explicit tab and bounded A1 range.")
    col, row = _col_number(match[1]), int(match[2])
    endcol, endrow = _col_number(match[3] or match[1]), int(match[4] or match[2])
    if (
        endcol < col
        or endrow < row
        or (endcol - col + 1) * (endrow - row + 1) > 500
        or endrow > 10000
        or endcol > 702
    ):
        raise SourceError(
            "too_large", "Sheet range is limited to 500 cells within A1:ZZ10000."
        )
    return value.rsplit("!", 1)[0], col, row, endcol, endrow


def parse_sheet(payload):
    scope, col, row, endcol, endrow = check_range(payload.get("range", ""))
    chunks = []
    values = payload.get("values", [])
    if len(values) > endrow - row + 1:
        raise SourceError("unsupported", "Sheet response exceeds the requested range.")
    for rn, values_row in enumerate(values, row):
        if len(values_row) > endcol - col + 1:
            raise SourceError(
                "unsupported", "Sheet response exceeds the requested range."
            )
        for cn, value in enumerate(values_row, col):
            text = str(value)
            if len(text) > CHUNK_CHARS:
                raise SourceError("too_large", "Sheet cell exceeds the parser limit.")
            if text:
                chunks.append(
                    {
                        "text": text,
                        "locator": {"range": f"{scope}!{_col_name(cn)}{rn}"},
                        "offset": 0,
                    }
                )
    return _bounded(chunks)


def _mime(key):
    return {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".pdf": "application/pdf",
    }.get(Path(key).suffix.lower(), "unsupported")


def page_chunks(pages):
    if not isinstance(pages, list) or not pages or len(pages) > 200:
        raise SourceError(
            "unsupported", "PDF parser must return 1–200 physical-page texts."
        )
    chunks = []
    for page, text in enumerate(pages, 1):
        if not isinstance(text, str):
            raise SourceError("unsupported", "PDF page parser returned invalid text.")
        if len(text) > MAX_CHARS:
            raise SourceError("too_large", "PDF page exceeds the extraction limit.")
        for offset in range(0, len(text), CHUNK_CHARS):
            value = text[offset : offset + CHUNK_CHARS]
            if value.strip():
                chunks.append(
                    {"text": value, "locator": {"page": page}, "offset": offset}
                )
        _bounded(chunks)
    if not chunks:
        raise SourceError(
            "unsupported", "No PDF text; OCR/encrypted/scanned files are unsupported."
        )
    return _bounded(chunks)


def default_pdf_parser(raw):
    from librarian_pdf import parse_pdf

    return parse_pdf(raw)


class LocalSource:
    """No symlinks or hidden paths; open every component relative to root fd."""

    def __init__(self, pdf_parser=None):
        self.pdf_parser = pdf_parser or default_pdf_parser

    def _root_fd(self, grant):
        try:
            fd = os.open(grant.scope, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            value = os.fstat(fd)
            if (
                grant.root_identity is None
                or (value.st_dev, value.st_ino) != grant.root_identity
            ):
                os.close(fd)
                raise SourceError(
                    "denied",
                    "Approved root identity changed; fresh approval is required.",
                )
            return fd
        except OSError as exc:
            raise SourceError("denied", "Approved root cannot be opened.") from exc

    def _open(self, grant, key):
        parts = Path(key).parts
        if (
            not parts
            or Path(key).is_absolute()
            or any(p in (".", "..") or p.startswith(".") for p in parts)
        ):
            raise SourceError("denied", "Path is outside the approved source.")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = None
        try:
            fd = self._root_fd(grant)
            for part in parts[:-1]:
                child = os.open(part, flags | os.O_DIRECTORY, dir_fd=fd)
                os.close(fd)
                fd = child
            result = os.open(parts[-1], flags, dir_fd=fd)
            if not stat.S_ISREG(os.fstat(result).st_mode):
                os.close(result)
                raise SourceError("denied", "Only regular files are permitted.")
            return result
        except FileNotFoundError as exc:
            raise SourceError("not_found", "Approved source no longer exists.") from exc
        except OSError as exc:
            raise SourceError("denied", "Approved source is not readable.") from exc
        finally:
            if fd is not None:
                os.close(fd)

    def metadata(self, grant, key):
        fd = self._open(grant, key)
        try:
            s = os.fstat(fd)
            return {
                "key": key,
                "title": Path(key).name,
                "mime": _mime(key),
                "version": f"{s.st_dev}:{s.st_ino}:{s.st_size}:{s.st_mtime_ns}:{s.st_ctime_ns}",
                "size": s.st_size,
                "modified_at": s.st_mtime,
                "path_or_url": str(Path(grant.scope) / key),
            }
        finally:
            os.close(fd)

    def discover(self, grant, query, limit, check=lambda: None):
        # Directory descriptors prevent a symlink replacement from widening scans.
        hits = []
        count = 0

        def walk(folder_fd, relative, depth):
            nonlocal count
            check()
            if depth > 4:
                return True
            try:
                with os.scandir(folder_fd) as entries:
                    for entry in entries:
                        check()
                        count += 1
                        if count > 1000 or len(hits) >= limit:
                            return True
                        if entry.name.startswith(".") or entry.is_symlink():
                            continue
                        key = str(Path(relative) / entry.name)
                        if entry.is_dir(follow_symlinks=False):
                            try:
                                child = os.open(
                                    entry.name,
                                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=folder_fd,
                                )
                            except OSError:
                                continue
                            try:
                                if walk(child, key, depth + 1):
                                    return True
                            finally:
                                os.close(child)
                        elif (
                            entry.is_file(follow_symlinks=False)
                            and _mime(entry.name) in grant.mimes
                        ):
                            if not query or query.casefold() in entry.name.casefold():
                                try:
                                    hits.append(self.metadata(grant, key))
                                except SourceError:
                                    continue
            except OSError as exc:
                raise SourceError(
                    "denied", "Approved directory is not readable."
                ) from exc
            return False

        root_fd = self._root_fd(grant)
        try:
            partial = walk(root_fd, "", 0)
        finally:
            os.close(root_fd)
        return hits, partial

    def read(self, grant, meta):
        if meta["mime"] == "application/pdf" and self.pdf_parser is None:
            raise SourceError(
                "unsupported", "PDF parser unavailable; no dependency was installed."
            )
        if meta["mime"] not in ("text/plain", "text/markdown", "application/pdf"):
            raise SourceError("unsupported", "This file type has no approved parser.")
        fd = self._open(grant, meta["key"])
        try:
            with os.fdopen(fd, "rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
        except OSError as exc:
            raise SourceError("denied", "Approved source cannot be read.") from exc
        if len(raw) > MAX_BYTES:
            raise SourceError("too_large", "File exceeds the 10 MiB read limit.")
        if meta["mime"] == "application/pdf":
            return (
                page_chunks(self.pdf_parser(raw)),
                hashlib.sha256(raw).hexdigest(),
                "identical_bytes",
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SourceError("unsupported", "Only UTF-8 text is supported.") from exc
        return text_chunks(text), hashlib.sha256(raw).hexdigest(), "identical_bytes"


class GoogleReadTransport:
    """GET-only, bounded transport. Tokens are accessed only during approved use."""

    def __call__(self, account, resource, params, media=False):
        import google_api

        if resource == "files" or resource.startswith("files/"):
            base = google_api.DRIVE_BASE
        elif resource.startswith("documents/"):
            base = "https://docs.googleapis.com/v1"
        elif resource.startswith("spreadsheets/"):
            base = "https://sheets.googleapis.com/v4"
        else:
            raise SourceError("denied", "Unsupported read endpoint.")
        try:
            token = google_api.access_token(account=account)
            req = urllib.request.Request(
                f"{base}/{resource}?{urllib.parse.urlencode(params)}",
                headers={"Authorization": f"Bearer {token}"},
                method="GET",
            )

            # Default urllib redirect handlers could leak credentials; refuse redirects.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None

            with urllib.request.build_opener(NoRedirect).open(
                req, timeout=15
            ) as response:
                raw = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = {
                401: "unauthenticated",
                403: "denied",
                404: "not_found",
                429: "unavailable",
            }.get(exc.code, "unavailable")
            raise SourceError(
                status,
                "Google read could not be verified; reconnect or review access when authorized.",
            ) from exc
        except google_api.GoogleError as exc:
            raise SourceError(
                "unauthenticated", "Existing Google connection could not be verified."
            ) from exc
        except OSError as exc:
            raise SourceError(
                "unavailable", "Google read timed out or is unavailable."
            ) from exc
        if len(raw) > MAX_BYTES:
            raise SourceError("too_large", "Google read exceeds the 10 MiB limit.")
        if media:
            return raw
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise SourceError(
                "unavailable", "Google returned an invalid read response."
            ) from exc
        if not isinstance(value, dict):
            raise SourceError(
                "unavailable", "Google returned an invalid read response."
            )
        return value


class DriveSource:
    def __init__(self, transport=None, pdf_parser=None):
        self.transport = transport or GoogleReadTransport()
        self.pdf_parser = pdf_parser or default_pdf_parser

    def _meta(self, grant, value):
        if (
            not FILE_ID.fullmatch(str(value.get("id", "")))
            or grant.scope not in value.get("parents", [])
            or value.get("trashed")
        ):
            raise SourceError(
                "denied", "File is outside the approved direct-child Drive folder."
            )
        if not value.get("modifiedTime") and not value.get("version"):
            raise SourceError(
                "unavailable", "Drive did not return a source version marker."
            )
        mime = value.get("mimeType", "")
        if mime not in grant.mimes:
            raise SourceError(
                "unsupported", "File type is outside the approved source scope."
            )
        url = str(value.get("webViewLink", ""))
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"drive.google.com", "docs.google.com"}
            or parsed.username
            or parsed.password
        ):
            url = ""
        try:
            size = int(value.get("size", 0))
        except (TypeError, ValueError) as exc:
            raise SourceError(
                "unavailable", "Drive returned invalid metadata."
            ) from exc
        return {
            "key": value["id"],
            "title": str(value.get("name", ""))[:200],
            "mime": mime,
            "version": str(value.get("version", ""))
            + ":"
            + str(value.get("modifiedTime", "")),
            "modified_at": value.get("modifiedTime"),
            "size": size,
            "path_or_url": url,
            "can_download": value.get("capabilities", {}).get("canDownload", True),
        }

    def discover(self, grant, query, limit, check=lambda: None):
        check()
        safe = query.replace("\\", "\\\\").replace("'", "\\'")
        clauses = f"trashed = false and '{grant.scope}' in parents"
        if query:
            predicate = f"name contains '{safe}'"
            if grant.content:
                predicate = f"({predicate} or fullText contains '{safe}')"
            clauses += " and " + predicate
        got = self.transport(
            grant.account,
            "files",
            {
                "q": clauses,
                "fields": f"nextPageToken,incompleteSearch,files({META_FIELDS})",
                "pageSize": min(100, limit),
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            },
        )
        check()
        hits = []
        for raw in got.get("files", [])[:limit]:
            try:
                hits.append(self._meta(grant, raw))
            except SourceError:
                continue
        return hits, bool(got.get("nextPageToken") or got.get("incompleteSearch"))

    def metadata(self, grant, key):
        if not FILE_ID.fullmatch(key):
            raise SourceError("denied", "Invalid approved file ID.")
        got = self.transport(
            grant.account,
            f"files/{key}",
            {"fields": META_FIELDS, "supportsAllDrives": "true"},
        )
        if got.get("id") != key:
            raise SourceError(
                "denied", "Returned Drive identity differs from the selected file."
            )
        return self._meta(grant, got)

    def read(self, grant, meta):
        if meta.get("size", 0) > MAX_BYTES:
            raise SourceError("too_large", "File exceeds the 10 MiB read limit.")
        key = meta["key"]
        if not meta.get("can_download", True):
            raise SourceError("denied", "Drive content read is restricted.")
        if meta["mime"] == DOC_MIME:
            got = self.transport(
                grant.account, f"documents/{key}", {"includeTabsContent": "true"}
            )
            chunks = parse_doc(got)
            digest_kind = "same_extracted_text"
            raw = json.dumps([c["text"] for c in chunks], ensure_ascii=False).encode()
        elif meta["mime"] == SHEET_MIME:
            check_range(grant.sheet_range)
            got = self.transport(
                grant.account,
                f"spreadsheets/{key}/values/{urllib.parse.quote(grant.sheet_range, safe='')}",
                {"valueRenderOption": "FORMATTED_VALUE"},
            )
            # Returned range may use canonical quoting but must cover the approved cells only.
            expected = check_range(grant.sheet_range)
            actual = check_range(got.get("range", ""))
            if (
                expected[0].strip("'") != actual[0].strip("'")
                or expected[1:] != actual[1:]
            ):
                raise SourceError(
                    "denied", "Returned sheet range is outside the approved range."
                )
            chunks = parse_sheet(got)
            digest_kind = "same_extracted_text"
            raw = json.dumps([c["text"] for c in chunks], ensure_ascii=False).encode()
        elif meta["mime"] in ("text/plain", "text/markdown") or (
            meta["mime"] == "application/pdf" and self.pdf_parser is not None
        ):
            raw = self.transport(
                grant.account, f"files/{key}", {"alt": "media"}, media=True
            )
            if len(raw) > MAX_BYTES:
                raise SourceError("too_large", "File exceeds the 10 MiB read limit.")
            try:
                chunks = (
                    page_chunks(self.pdf_parser(raw))
                    if meta["mime"] == "application/pdf"
                    else text_chunks(raw.decode("utf-8"))
                )
            except UnicodeDecodeError as exc:
                raise SourceError(
                    "unsupported", "Only UTF-8 text is supported."
                ) from exc
            digest_kind = "identical_bytes"
        else:
            raise SourceError(
                "unsupported", "PDF parser unavailable or file type unsupported."
            )
        return chunks, hashlib.sha256(raw).hexdigest(), digest_kind
