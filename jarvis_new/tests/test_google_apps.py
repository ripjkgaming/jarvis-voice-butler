import json

import google_apps


def test_normalize_app_and_which():
    assert google_apps.normalize_app("Google Docs") == "docs"
    assert google_apps.normalize_app("my spreadsheet") == "sheets"
    assert google_apps.normalize_app("slides deck") == "slides"
    assert google_apps.normalize_app("google drive") == "drive"
    assert google_apps.parse_which("") == ("home", None)
    assert google_apps.parse_which("Google Docs") == ("home", None)
    assert google_apps.parse_which("2nd") == ("nth", 2)
    assert google_apps.parse_which("the second one") == ("nth", 2)
    assert google_apps.parse_which("latest") == ("nth", 1)
    assert google_apps.parse_which("third document") == ("nth", 3)
    assert google_apps.parse_which("Physics notes") == ("name", "Physics notes")


def test_authuser_and_scoring():
    url = google_apps.with_authuser("https://docs.google.com/document/", "a@b.com")
    assert url == "https://docs.google.com/document/?authuser=a%40b.com"
    assert google_apps.with_authuser("https://x/?authuser=0", "1").endswith("authuser=1")
    assert google_apps.score_name("Physics Notes", "physics notes") == 100
    assert google_apps.score_name("Physics Notes 2", "physics") == 80
    assert google_apps.score_name("Maths", "physics") == 0


def _fake_drive(files):
    import io

    def opener(req, timeout=None):
        return io.BytesIO(json.dumps({"files": files}).encode())

    return opener


def test_resolve_home_nth_name_and_school_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "google_accounts.json").write_text(json.dumps({"personal": "me@x.com"}))
    monkeypatch.setattr(google_apps.google_api, "access_token", lambda **k: "tok")
    files = [
        {"id": "1", "name": "Essay draft", "webViewLink": "https://docs/1"},
        {"id": "2", "name": "Physics Notes", "webViewLink": "https://docs/2"},
    ]
    home = google_apps.resolve("docs", "", "personal")
    assert home["url"] == "https://docs.google.com/document/?authuser=me%40x.com"
    nth = google_apps.resolve("docs", "2nd", "personal", opener=_fake_drive(files))
    assert nth["url"].startswith("https://docs/2") and "Physics Notes" in nth["say"]
    too_far = google_apps.resolve("docs", "5th", "personal", opener=_fake_drive(files))
    assert too_far["url"].startswith("https://docs.google.com/document/")
    named = google_apps.resolve("docs", "physics", "personal", opener=_fake_drive(files))
    assert named["url"].startswith("https://docs/2")
    missing = google_apps.resolve("docs", "zz", "personal", opener=_fake_drive([]))
    assert missing["url"].startswith("https://drive.google.com/drive/search?q=zz")

    def no_school(**k):
        raise google_apps.google_api.GoogleError("not connected")

    monkeypatch.setattr(google_apps.google_api, "access_token", no_school)
    monkeypatch.delenv("JARVIS_SCHOOL_AUTHUSER", raising=False)
    school = google_apps.resolve("sheets", "", "school account")
    assert school["url"] == "https://docs.google.com/spreadsheets/?authuser=1"
    assert "school" in school["say"]


def test_school_account_opens_with_its_email(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "google_accounts.json").write_text(
        json.dumps({"personal": "me@x.com", "school": "hs1@school.sg"})
    )

    def no_school(**k):
        raise google_apps.google_api.GoogleError("third-party apps blocked")

    monkeypatch.setattr(google_apps.google_api, "access_token", no_school)
    home = google_apps.resolve("slides", "", "secondary school account")
    assert home["url"] == "https://docs.google.com/presentation/?authuser=hs1%40school.sg"
    named = google_apps.resolve("docs", "Chemistry essay", "school")
    assert named["url"].startswith("https://drive.google.com/drive/search?q=Chemistry+essay")
    assert named["url"].endswith("authuser=hs1%40school.sg")
