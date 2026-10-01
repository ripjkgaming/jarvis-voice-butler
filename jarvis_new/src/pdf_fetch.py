"""Fetch a web PDF and print its text, for Jarvis's research engine.

Why a script, not a library: the headless research model may only run one
pre-approved Bash command, so PDF reading is a single CLI (`pdf_fetch.py
<url>`) rather than an import. WebFetch cannot read PDFs; this downloads
the file and prints its text layer via poppler instead.

Usage: pdf_fetch.py <url> [--max-chars N] [--pages A-B]
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

MAX_BYTES = 40 * 1024 * 1024  # refuse anything bigger than this
TIMEOUT_S = 30
DEFAULT_MAX_CHARS = 60000
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_PAGES_RE = re.compile(r"^\s*(?:(\d+)\s*-\s*(\d+)|(\d+))\s*$")
_URL_RE = re.compile(r"https?://[^\s)>\]]+")

USAGE = "usage: pdf_fetch.py <url> [--max-chars N] [--pages A-B]"


class FetchError(Exception):
    """One-line user-facing failure (goes to stderr, exit 1)."""


class _Help(Exception):  # noqa: N818 - control flow, not an error
    """`--help` was asked for: print USAGE on stdout, exit 0."""


def parse_args(argv: list[str]) -> tuple[str, int, str | None]:
    """(url, max_chars, pages_spec) from argv. Pure: raises, never exits.

    Hand-rolled because argparse exits 2 on misuse; we want exit 1
    with a one-line error like every other failure here.
    """
    url = ""
    max_chars = DEFAULT_MAX_CHARS
    pages: str | None = None
    positionals: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            raise _Help
        elif arg == "--max-chars":
            i += 1
            try:
                max_chars = int(argv[i])
            except (IndexError, ValueError):
                raise ValueError(
                    f"bad --max-chars {argv[i] if i < len(argv) else ''!r}; want a positive int"
                ) from None
            if max_chars < 1:
                raise ValueError(f"bad --max-chars {argv[i]!r}; want a positive int")
        elif arg.startswith("--max-chars="):
            try:
                max_chars = int(arg.split("=", 1)[1])
            except ValueError:
                raise ValueError(
                    f"bad --max-chars {arg!r}; want a positive int"
                ) from None
            if max_chars < 1:
                raise ValueError(f"bad --max-chars {arg!r}; want a positive int")
        elif arg == "--pages":
            i += 1
            if i >= len(argv):
                raise ValueError("bad --pages ''; want A-B like 3-7")
            pages = argv[i]
        elif arg.startswith("--pages="):
            pages = arg.split("=", 1)[1]
        elif arg.startswith("-"):
            raise ValueError(f"unknown option {arg!r}")
        else:
            positionals.append(arg)
        i += 1
    if len(positionals) != 1:
        raise ValueError("want exactly one URL")
    url = positionals[0]
    return url, max_chars, pages


def parse_page_range(spec: str) -> tuple[int, int]:
    """'A-B' (or a lone 'A') -> (first, last), 1-based inclusive. Pure."""
    m = _PAGES_RE.match(spec or "")
    if not m:
        raise ValueError(f"bad --pages {spec!r}; want A-B like 3-7")
    if m.group(3) is not None:
        page = int(m.group(3))
        if page < 1:
            raise ValueError(f"bad --pages {spec!r}; want A-B like 3-7")
        return (page, page)
    first, last = int(m.group(1)), int(m.group(2))
    if first < 1 or last < first:
        raise ValueError(f"bad --pages {spec!r}; want A-B like 3-7")
    return (first, last)


def split_pages(text: str) -> list[str]:
    """Full pdftotext output -> one entry per page. Pure.

    pdftotext separates pages with a form feed (plus one trailing FF at
    the very end), so split on \\f and drop the empty tail chunk(s).
    """
    chunks = (text or "").split("\f")
    while chunks and not chunks[-1].strip():
        chunks.pop()
    return chunks


def collapse_blanks(text: str) -> str:
    """3+ newlines in a row -> one blank line. Pure (leaves \\f alone)."""
    return re.sub(r"\n{3,}", "\n\n", text or "")


def truncate(text: str, max_chars: int) -> tuple[str, int]:
    """(printable text, hidden char count). Pure."""
    if len(text) <= max_chars:
        return text, 0
    return text[:max_chars], len(text) - max_chars


def jarvis_home() -> Path:
    return Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))


def cache_paths(url: str) -> tuple[Path, Path]:
    """(text cache, meta sidecar) for a URL: sha1 so filenames are safe."""
    digest = hashlib.sha1(url.encode()).hexdigest()
    folder = jarvis_home() / "cache" / "pdf"
    return folder / f"{digest}.txt", folder / f"{digest}.json"


def fetch_pdf(url: str) -> bytes:
    """Download, enforcing the size cap and %PDF magic. Raises FetchError."""
    if urlparse(url or "").scheme not in ("http", "https"):
        raise FetchError(f"refusing non-http(s) URL: {url}")
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=TIMEOUT_S) as resp:
            status = getattr(resp, "status", 200) or 200
            if status != 200:
                raise FetchError(f"HTTP error {status} for {url}")
            try:
                declared = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                declared = 0
            if declared > MAX_BYTES:
                raise FetchError(
                    f"PDF too big ({declared // 1024 // 1024} MB, limit 40 MB): {url}"
                )
            data = resp.read(MAX_BYTES + 1)
    except HTTPError as exc:
        raise FetchError(f"HTTP error {exc.code} for {url}") from None
    except (URLError, OSError, TimeoutError) as exc:
        raise FetchError(f"download failed for {url}: {exc}") from None
    if len(data) > MAX_BYTES:
        raise FetchError(f"PDF too big (over 40 MB): {url}")
    if not data.startswith(b"%PDF"):
        raise FetchError(f"URL did not return a PDF (no %PDF magic): {url}")
    return data


def pdf_meta(path: Path) -> tuple[str | None, int | None]:
    """(title, page count) from pdfinfo; (None, None) if unavailable. Fail-soft."""
    if shutil.which("pdfinfo") is None:
        return None, None
    try:
        proc = subprocess.run(["pdfinfo", str(path)], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if proc.returncode != 0:
        return None, None
    title: str | None = None
    pages: int | None = None
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        if line.startswith("Title:"):
            title = line.split(":", 1)[1].strip() or None
        elif line.startswith("Pages:"):
            with contextlib.suppress(ValueError):
                pages = int(line.split(":", 1)[1].strip())
    return title, pages


def extract_text(pdf: bytes) -> tuple[str, str | None, int | None]:
    """(full text with \\f page separators, title, page count). Raises FetchError."""
    if shutil.which("pdftotext") is None:
        raise FetchError("pdftotext not installed (poppler-utils); cannot read PDFs")
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.write(pdf)
            tmp = Path(fh.name)
        title, pages = pdf_meta(tmp)
        try:
            proc = subprocess.run(
                ["pdftotext", "-layout", str(tmp), "-"],
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FetchError(f"pdftotext failed: {exc}") from None
        if proc.returncode != 0:
            raise FetchError("pdftotext failed to read the PDF")
        text = proc.stdout.decode("utf-8", errors="replace")
        if not text.strip():
            raise FetchError("PDF has no text layer (scanned image?); nothing to read")
        return text, title, pages
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp)


def load_or_fetch(url: str) -> tuple[str, str | None, int | None]:
    """Cached (text, title, pages), else download + extract + cache. Raises FetchError."""
    text_path, meta_path = cache_paths(url)
    if text_path.exists():
        with contextlib.suppress(OSError):
            text = text_path.read_text(encoding="utf-8", errors="replace")
            if text.strip():
                title: str | None = None
                pages: int | None = None
                with contextlib.suppress(OSError, ValueError):
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    title = meta.get("title") or None
                    pages = meta.get("pages")
                    pages = int(pages) if pages is not None else None
                return text, title, pages
    pdf = fetch_pdf(url)
    text, title, pages = extract_text(pdf)
    # Cache writes are best-effort: a full disk must not lose the text.
    with contextlib.suppress(OSError):
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8")
        meta_path.write_text(
            json.dumps({"url": url, "title": title, "pages": pages}),
            encoding="utf-8",
        )
    return text, title, pages


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        url, max_chars, pages_spec = parse_args(args)
        wanted: tuple[int, int] | None = (
            parse_page_range(pages_spec) if pages_spec is not None else None
        )
        text, title, total = load_or_fetch(url)
    except _Help:
        print(USAGE)
        return 0
    except (ValueError, FetchError) as exc:
        print(f"pdf_fetch: {exc}", file=sys.stderr)
        return 1
    chunks = split_pages(text)
    total = total or len(chunks) or 1
    if wanted is not None:
        first, last = wanted
        if first > total:
            print(
                f"pdf_fetch: --pages {pages_spec} is beyond this {total}-page PDF",
                file=sys.stderr,
            )
            return 1
        body = collapse_blanks(
            "\n".join(chunks[first - 1 : min(last, len(chunks))])
        ).strip()
    else:
        body = collapse_blanks(text).strip()
    shown, hidden = truncate(body, max_chars)
    print(f"PDF: {title or url} — {total} pages")
    print(shown)
    if hidden:
        print(f"[truncated: {hidden} more chars; use --pages to read further]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
