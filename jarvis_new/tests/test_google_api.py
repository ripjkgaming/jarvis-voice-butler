"""Hermetic tests for src/google_api.py (fake opener, no network)."""

import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

import google_api
from google_api import (
    GoogleError,
    access_token,
    docs_create,
    docs_read,
    docs_text,
    drive_export,
    drive_search,
    drive_upload,
    ensure_folder,
    is_id,
    resolve_file,
    sheets_append,
    sheets_create,
    sheets_read,
)


class FakeResp:
    def __init__(self, payload: bytes | dict):
        self._raw = (
            json.dumps(payload).encode() if isinstance(payload, dict) else payload
        )

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Records (method, url, body) and replays queued payloads."""

    def __init__(self, *payloads):
        self.queue = list(payloads)
        self.calls: list[tuple] = []

    def __call__(self, req, timeout=None):
        data = req.data
        if not data:
            body = None
        elif data[:1] == b"{":
            try:
                body = json.loads(data.decode())
            except ValueError:
                body = data
        elif data[:2] == b"--":
            body = data  # multipart upload: tests assert on raw bytes
        else:
            body = dict(urllib.parse.parse_qsl(data.decode()))
        self.calls.append((req.get_method(), req.full_url, body))
        if not self.queue:
            raise AssertionError("fake opener called too often")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResp(item)


def _token_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "fake-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "fake-secret")
    (tmp_path / "google_token.json").write_text(json.dumps({"refresh_token": "rt"}))


def test_access_token_missing_raises_setup_hint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    with pytest.raises(GoogleError, match="connected yet"):
        access_token()


def test_access_token_refresh_shape(tmp_path, monkeypatch) -> None:
    _token_home(tmp_path, monkeypatch)
    opener = FakeOpener({"access_token": "fresh-123"})
    assert access_token(opener=opener) == "fresh-123"
    method, url, body = opener.calls[0]
    assert method == "POST" and "oauth2.googleapis.com/token" in url
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "rt"


def test_access_token_refresh_failure_speakable(tmp_path, monkeypatch) -> None:
    _token_home(tmp_path, monkeypatch)
    err = urllib.error.HTTPError(
        "https://oauth2.googleapis.com/token", 400, "bad", {}, None
    )
    with pytest.raises(GoogleError, match="expired"):
        access_token(opener=FakeOpener(err))


def test_drive_search_url_shape(tmp_path, monkeypatch) -> None:
    _token_home(tmp_path, monkeypatch)
    opener = FakeOpener(
        {"access_token": "t"},
        {"files": [{"id": "abc123xyz_", "name": "Budget", "mimeType": "sheet"}]},
    )
    hits = drive_search("budget", opener=opener)
    assert hits[0]["name"] == "Budget"
    method, url, _ = opener.calls[1]
    assert method == "GET" and "drive/v3/files" in url
    assert "trashed" in urllib.parse.unquote(url)


def test_resolve_file_id_passthrough(tmp_path, monkeypatch) -> None:
    _token_home(tmp_path, monkeypatch)
    opener = FakeOpener(
        {"access_token": "t"},
        {"id": "abc123xyz_", "name": "Notes", "mimeType": "doc"},
    )
    assert is_id("abc123xyz_") and not is_id("my notes")
    meta = resolve_file("abc123xyz_", opener=opener)
    assert meta["name"] == "Notes"


def test_resolve_file_name_searches(tmp_path, monkeypatch) -> None:
    _token_home(tmp_path, monkeypatch)
    opener = FakeOpener(
        {"access_token": "t"},
        {"access_token": "t"},  # nested refresh inside drive_search
        {"files": [{"id": "zzz999yyy_", "name": "Notes", "mimeType": "doc"}]},
    )
    assert resolve_file("meeting notes", opener=opener)["id"] == "zzz999yyy_"


def test_resolve_file_name_miss_speakable(tmp_path, monkeypatch) -> None:
    _token_home(tmp_path, monkeypatch)
    opener = FakeOpener({"access_token": "t"}, {"access_token": "t"}, {"files": []})
    with pytest.raises(GoogleError, match="could not find"):
        resolve_file("nope missing", opener=opener)


def test_drive_upload_multipart_shape(tmp_path, monkeypatch) -> None:
    _token_home(tmp_path, monkeypatch)
    opener = FakeOpener({"access_token": "t"}, {"id": "new1", "name": "a.html"})
    meta = drive_upload(
        "a.html", b"<p>hi</p>", "text/html", convert_to="doc", opener=opener
    )
    assert meta["id"] == "new1"
    method, url, body = opener.calls[1]
    assert method == "POST" and "uploadType=multipart" in url
    assert b"google-apps.document" in body  # conversion metadata present


def test_docs_create_requires_content(tmp_path, monkeypatch) -> None:
    _token_home(tmp_path, monkeypatch)
    with pytest.raises(GoogleError, match="title"):
        docs_create("", "<p>x</p>")
    with pytest.raises(GoogleError, match="no content"):
        docs_create("T", "  ")


def _doc_payload() -> dict:
    return {
        "title": "Notes",
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": "Hello "}},
                            {"textRun": {"content": "Sir"}},
                        ]
                    }
                },
                {
                    "table": {
                        "tableRows": [
                            {
                                "tableCells": [
                                    {
                                        "content": [
                                            {
                                                "paragraph": {
                                                    "elements": [
                                                        {"textRun": {"content": "cell"}}
                                                    ]
                                                }
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                },
            ]
        },
    }


def test_docs_text_flattens_paragraphs_and_tables() -> None:
    text = docs_text(_doc_payload())
    assert "Hello Sir" in text and "cell" in text


def test_docs_read_returns_text(tmp_path, monkeypatch) -> None:
    _token_home(tmp_path, monkeypatch)
    opener = FakeOpener({"access_token": "t"}, _doc_payload())
    assert "Hello Sir" in docs_read("docid1234567890", opener=opener)


def test_sheets_read_and_append_shapes(tmp_path, monkeypatch) -> None:
    _token_home(tmp_path, monkeypatch)
    opener = FakeOpener(
        {"access_token": "t"},
        {"values": [["a", "b"], ["c"]]},
    )
    assert sheets_read("sheetid1234567890", "A1:B2", opener=opener) == [
        ["a", "b"],
        ["c"],
    ]
    _, url, _ = opener.calls[1]
    assert "values" in url

    opener2 = FakeOpener({"access_token": "t"}, {"updates": {"updatedRows": 1}})
    out = sheets_append("sheetid1234567890", "A1", [["x", "y"]], opener=opener2)
    assert out["updates"]["updatedRows"] == 1
    _, url2, body2 = opener2.calls[1]
    assert ":append" in url2 and body2["values"] == [["x", "y"]]


def test_sheets_append_rejects_empty(tmp_path, monkeypatch) -> None:
    _token_home(tmp_path, monkeypatch)
    with pytest.raises(GoogleError, match="no rows"):
        sheets_append("sheetid1234567890", "A1", [])


def test_sheets_create_posts_title_then_values(tmp_path, monkeypatch) -> None:
    _token_home(tmp_path, monkeypatch)
    opener = FakeOpener(
        {"access_token": "t"},
        {"spreadsheetId": "sid1234567890"},
        {"updatedCells": 2},
    )
    got = sheets_create("Budget", [["a"]], opener=opener)
    assert got["spreadsheetId"] == "sid1234567890"
    _, _, body = opener.calls[1]
    assert body["properties"]["title"] == "Budget"


def test_drive_export_bytes_and_404_mapping(tmp_path, monkeypatch) -> None:
    _token_home(tmp_path, monkeypatch)
    opener = FakeOpener({"access_token": "t"}, b"plain bytes")
    assert drive_export("fileid1234567890", opener=opener) == b"plain bytes"

    err = urllib.error.HTTPError("https://x", 404, "nf", {}, None)
    with pytest.raises(GoogleError, match="could not read"):
        drive_export("fileid1234567890", opener=FakeOpener({"access_token": "t"}, err))


def test_ensure_folder_reuses_then_creates(tmp_path, monkeypatch) -> None:
    _token_home(tmp_path, monkeypatch)
    opener = FakeOpener({"access_token": "t"}, {"files": [{"id": "fold1"}]})
    assert ensure_folder("Jarvis Documents", opener=opener) == "fold1"

    opener2 = FakeOpener({"access_token": "t"}, {"files": []}, {"id": "fold2"})
    assert ensure_folder("Jarvis Documents", opener=opener2) == "fold2"
    _, _, body = opener2.calls[2]
    assert "vnd.google-apps.folder" in body["mimeType"]


def test_http_401_maps_to_reconnect(tmp_path, monkeypatch) -> None:
    _token_home(tmp_path, monkeypatch)
    err = urllib.error.HTTPError("https://x", 401, "denied", {}, None)
    with pytest.raises(GoogleError, match="refused access"):
        drive_search("budget", opener=FakeOpener({"access_token": "t"}, err))


def test_request_rejects_real_network_by_default(monkeypatch) -> None:
    # Guard: tests must always pass opener; patch urlopen to explode.
    def _boom(req, timeout=None):
        raise AssertionError("real network touched")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    assert google_api.DRIVE_BASE.startswith("https://")


def test_uploads_use_the_upload_endpoint(tmp_path, monkeypatch):
    """Multipart POSTs to /drive/v3/files are a 400; uploads live under /upload/."""
    import io
    import json as _json

    import google_api

    monkeypatch.setattr(google_api, "access_token", lambda **k: "tok")
    seen = {}

    def opener(req, timeout=None):
        seen["url"] = req.full_url
        return io.BytesIO(_json.dumps({"id": "abc", "name": "x"}).encode())

    google_api.drive_upload("x.html", b"<p>x</p>", "text/html", convert_to="doc", opener=opener)
    assert seen["url"].startswith("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart")


def test_share_with_school_shares_as_editor_without_email(tmp_path, monkeypatch):
    import json as _json

    import google_api

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(google_api, "access_token", lambda **k: "tok")
    calls = []
    monkeypatch.setattr(
        google_api, "_request_json", lambda m, u, t, p=None, opener=None: calls.append((m, u, p)) or {}
    )
    try:
        google_api.share_with_school("FILE")
        raise AssertionError("expected GoogleError without a school email")
    except google_api.GoogleError:
        pass
    (tmp_path / "google_accounts.json").write_text(_json.dumps({"school": "s@school.sg"}))
    assert google_api.share_with_school("FILE") == "s@school.sg"
    method, url, body = calls[-1]
    assert method == "POST" and "/files/FILE/permissions" in url and "sendNotificationEmail=false" in url
    assert body == {"type": "user", "role": "writer", "emailAddress": "s@school.sg"}
