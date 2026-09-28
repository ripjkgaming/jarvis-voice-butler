"""The research engine's PDF reader: download, pdftotext, cache, print."""

import json

import pytest

import pdf_fetch
from pdf_fetch import FetchError


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jh"))


# --- pure helpers ----------------------------------------------------------


def test_parse_args_defaults_and_forms():
    url, max_chars, pages = pdf_fetch.parse_args(["https://x.test/a.pdf"])
    assert (url, max_chars, pages) == ("https://x.test/a.pdf", 60000, None)
    assert pdf_fetch.parse_args(
        ["https://x.test/a.pdf", "--max-chars", "100", "--pages", "2-4"]
    ) == ("https://x.test/a.pdf", 100, "2-4")
    assert pdf_fetch.parse_args(
        ["--pages=3", "--max-chars=50", "https://x.test/a.pdf"]
    ) == ("https://x.test/a.pdf", 50, "3")


def test_parse_args_rejects():
    for argv in (
        [],
        ["a.pdf", "b.pdf"],
        ["https://x.test/a.pdf", "--max-chars", "0"],
        ["https://x.test/a.pdf", "--max-chars", "lots"],
        ["https://x.test/a.pdf", "--frobnicate"],
    ):
        with pytest.raises(ValueError):
            pdf_fetch.parse_args(argv)


def test_parse_page_range():
    assert pdf_fetch.parse_page_range("3-7") == (3, 7)
    assert pdf_fetch.parse_page_range("5") == (5, 5)
    for bad in ("", "0-3", "7-3", "a-b", "3-", "-4", "1-2-3"):
        with pytest.raises(ValueError):
            pdf_fetch.parse_page_range(bad)


def test_collapse_blanks_and_truncate_and_split():
    assert pdf_fetch.collapse_blanks("a\n\n\n\nb") == "a\n\nb"
    assert pdf_fetch.collapse_blanks("a\fb\n\n\n\nc") == "a\fb\n\nc"
    assert pdf_fetch.truncate("abcdef", 10) == ("abcdef", 0)
    assert pdf_fetch.truncate("abcdef", 4) == ("abcd", 2)
    # pdftotext ends output with a trailing FF: no phantom last page.
    assert pdf_fetch.split_pages("p1\fp2\f") == ["p1", "p2"]
    assert pdf_fetch.split_pages("") == []


def test_cache_paths_honour_jarvis_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jh"))
    text_path, meta_path = pdf_fetch.cache_paths("https://x.test/a.pdf")
    assert text_path.parent == tmp_path / "jh" / "cache" / "pdf"
    assert text_path.suffix == ".txt" and meta_path.suffix == ".json"
    assert text_path.stem == meta_path.stem  # same doc, side by side


# --- network (mocked) -------------------------------------------------------


class _Resp:
    def __init__(self, data=b"%PDF-1.4 fake", length=None, status=200):
        self._data, self.status = data, status
        self.headers = {
            "Content-Length": str(length if length is not None else len(data))
        }

    def read(self, n=-1):
        return self._data[:n] if n is not None and n >= 0 else self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen(data=b"%PDF-1.4 fake", **kw):
    return lambda req, **k: _Resp(data, **kw)


def test_fetch_pdf_ok_and_checks(monkeypatch):
    from urllib.error import HTTPError

    monkeypatch.setattr(pdf_fetch, "urlopen", _urlopen())
    assert pdf_fetch.fetch_pdf("https://x.test/a.pdf").startswith(b"%PDF")

    monkeypatch.setattr(pdf_fetch, "urlopen", _urlopen(b"<html>nope</html>"))
    with pytest.raises(FetchError, match="no %PDF magic"):
        pdf_fetch.fetch_pdf("https://x.test/a.pdf")

    monkeypatch.setattr(pdf_fetch, "urlopen", _urlopen(length=pdf_fetch.MAX_BYTES + 1))
    with pytest.raises(FetchError, match="too big"):
        pdf_fetch.fetch_pdf("https://x.test/a.pdf")

    def boom(req, **k):
        raise HTTPError(req.full_url, 404, "not found", {}, None)

    monkeypatch.setattr(pdf_fetch, "urlopen", boom)
    with pytest.raises(FetchError, match="HTTP error 404"):
        pdf_fetch.fetch_pdf("https://x.test/a.pdf")

    with pytest.raises(FetchError, match="non-http"):
        pdf_fetch.fetch_pdf("ftp://x.test/a.pdf")


# --- extraction (mocked subprocess) ------------------------------------------


class _Proc:
    def __init__(self, rc=0, out=b""):
        self.returncode, self.stdout = rc, out


def _fake_run(text=b"hello\f", title=b"", pages=b"2", rc=0):
    def run(argv, **kw):
        if argv[0] == "pdfinfo":
            return _Proc(0, b"Title: " + title + b"\nPages: " + pages + b"\n")
        assert argv[0] == "pdftotext"
        return _Proc(rc, text)

    return run


def test_extract_text_happy_path(monkeypatch):
    monkeypatch.setattr(pdf_fetch.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(pdf_fetch.subprocess, "run", _fake_run(title=b"Some Paper"))
    text, title, pages = pdf_fetch.extract_text(b"%PDF-1.4 fake")
    assert (text, title, pages) == ("hello\f", "Some Paper", 2)


def test_extract_text_failures(monkeypatch):
    monkeypatch.setattr(pdf_fetch.shutil, "which", lambda n: None)
    with pytest.raises(FetchError, match="pdftotext not installed"):
        pdf_fetch.extract_text(b"%PDF-1.4 fake")
    monkeypatch.setattr(pdf_fetch.shutil, "which", lambda n: f"/usr/bin/{n}")
    # Scanned PDF: poppler finds no text layer.
    monkeypatch.setattr(pdf_fetch.subprocess, "run", _fake_run(text=b"  \n\f "))
    with pytest.raises(FetchError, match="no text layer"):
        pdf_fetch.extract_text(b"%PDF-1.4 fake")
    # pdfinfo missing is fail-soft: title/pages just come back unknown.
    monkeypatch.setattr(
        pdf_fetch.shutil,
        "which",
        lambda n: None if n == "pdfinfo" else "/usr/bin/pdftotext",
    )
    monkeypatch.setattr(pdf_fetch.subprocess, "run", _fake_run())
    assert pdf_fetch.extract_text(b"%PDF-1.4 fake") == ("hello\f", None, None)


# --- main: cache, header, truncation, pages ----------------------------------


def test_main_caches_and_reuses(monkeypatch, capsys):
    monkeypatch.setattr(pdf_fetch, "fetch_pdf", lambda url: b"%PDF-1.4 fake")
    calls = []
    real_extract = pdf_fetch.extract_text

    def extract(pdf):
        calls.append(pdf)
        return "page one\fpage two\f", "Cached Paper", 2

    monkeypatch.setattr(pdf_fetch, "extract_text", extract)
    assert pdf_fetch.main(["https://x.test/a.pdf", "--max-chars", "1000"]) == 0
    first = capsys.readouterr().out
    assert first.startswith("PDF: Cached Paper — 2 pages\n")
    assert "page two" in first
    text_path, meta_path = pdf_fetch.cache_paths("https://x.test/a.pdf")
    assert text_path.exists() and json.loads(meta_path.read_text())["pages"] == 2

    monkeypatch.setattr(
        pdf_fetch,
        "fetch_pdf",
        lambda url: (_ for _ in ()).throw(AssertionError("network on cache hit")),
    )
    monkeypatch.setattr(pdf_fetch, "extract_text", real_extract)
    assert pdf_fetch.main(["https://x.test/a.pdf", "--max-chars", "1000"]) == 0
    assert capsys.readouterr().out == first
    assert calls == [b"%PDF-1.4 fake"]  # extraction ran only for the first read


def test_main_truncation_note_and_pages(monkeypatch, capsys):
    monkeypatch.setattr(
        pdf_fetch,
        "load_or_fetch",
        lambda url: ("p1 line\n" * 40 + "\fp2 line\n" * 40, None, 2),
    )
    assert pdf_fetch.main(["https://x.test/a.pdf", "--max-chars", "50"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("PDF: https://x.test/a.pdf — 2 pages\n")
    body = ("p1 line\n" * 40 + "\fp2 line\n" * 40).strip()
    assert out.rstrip().endswith(
        f"[truncated: {len(body) - 50} more chars; use --pages to read further]"
    )

    assert (
        pdf_fetch.main(
            ["https://x.test/a.pdf", "--pages", "2-2", "--max-chars", "10000"]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "p1 line" not in out and "p2 line" in out
    assert "truncated" not in out


def test_main_errors_are_one_line_exit_1(monkeypatch, capsys):
    assert pdf_fetch.main([]) == 1
    assert capsys.readouterr().err.startswith("pdf_fetch: ")
    monkeypatch.setattr(pdf_fetch, "load_or_fetch", lambda url: ("only\f", None, 1))
    assert pdf_fetch.main(["https://x.test/a.pdf", "--pages", "5-9"]) == 1
    assert "beyond this 1-page PDF" in capsys.readouterr().err
    monkeypatch.setattr(
        pdf_fetch,
        "load_or_fetch",
        lambda url: (_ for _ in ()).throw(FetchError("HTTP error 404 for x")),
    )
    assert pdf_fetch.main(["https://x.test/a.pdf"]) == 1
    assert "HTTP error 404" in capsys.readouterr().err
