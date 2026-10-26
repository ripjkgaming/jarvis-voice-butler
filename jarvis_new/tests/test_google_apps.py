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
    assert google_apps.with_authuser("https://x/?authuser=0", "1").endswith(
        "authuser=1"
    )
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
    named = google_apps.resolve(
        "docs", "Physics Notes", "personal", opener=_fake_drive(files)
    )
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
    assert (
        home["url"] == "https://docs.google.com/presentation/?authuser=hs1%40school.sg"
    )
    named = google_apps.resolve("docs", "Chemistry essay", "school")
    assert named["url"].startswith(
        "https://drive.google.com/drive/search?q=Chemistry+essay"
    )
    assert named["url"].endswith("authuser=hs1%40school.sg")


def test_partial_and_duplicate_names_require_choice(monkeypatch):
    monkeypatch.setattr(google_apps.google_api, "account_email", lambda _: "")
    monkeypatch.setattr(google_apps.google_api, "access_token", lambda **_: "fake")
    files = [
        {
            "id": "one",
            "name": "Physics Notes",
            "webViewLink": "https://docs.google.com/document/d/one",
        },
        {
            "id": "two",
            "name": "Physics Notes",
            "webViewLink": "https://docs.google.com/document/d/two",
        },
    ]
    for query, matches in [
        ("physics", files),
        ("Physics Notes", files),
        ("physics", files[:1]),
    ]:
        result = google_apps.resolve("docs", query, opener=_fake_drive(matches))
        assert result["status"] == "needs_choice"
        assert "url" not in result
        assert len(result["candidates"]) == len(matches)
        assert "Which" in result["say"]
        assert "opened" not in result["say"]


def test_unique_exact_name_is_preferred_over_partial_match(monkeypatch):
    monkeypatch.setattr(google_apps.google_api, "account_email", lambda _: "")
    monkeypatch.setattr(google_apps.google_api, "access_token", lambda **_: "fake")
    files = [
        {
            "id": "one",
            "name": "Physics Notes extra",
            "webViewLink": "https://docs.google.com/document/d/one",
        },
        {
            "id": "two",
            "name": "Physics Notes",
            "webViewLink": "https://docs.google.com/document/d/two",
        },
    ]
    result = google_apps.resolve("docs", "physics notes", opener=_fake_drive(files))
    assert result["status"] == "resolved"
    assert result["url"].endswith("/two")


def test_incomplete_lookup_cannot_claim_unique_exact_name(monkeypatch):
    import io

    monkeypatch.setattr(google_apps.google_api, "account_email", lambda _: "")
    monkeypatch.setattr(google_apps.google_api, "access_token", lambda **_: "fake")
    files = [
        {
            "id": "one",
            "name": "Physics Notes",
            "webViewLink": "https://docs.google.com/document/d/one",
        }
    ]
    for extra in [{"nextPageToken": "more"}, {"incompleteSearch": True}]:

        def opener(req, timeout=None):
            return io.BytesIO(json.dumps({"files": files, **extra}).encode())

        result = google_apps.resolve("docs", "Physics Notes", opener=opener)
        assert result["status"] == "needs_choice"
        assert result["incomplete_search"] is True
        assert "url" not in result


def test_supplied_mp4_normalizes_ordinary_whitespace_preserving_title(monkeypatch):
    monkeypatch.setattr(google_apps.google_api, "account_email", lambda _: "")
    monkeypatch.setattr(google_apps.google_api, "access_token", lambda **_: "fake")
    supplied = "Starly The Attack Titan Vs Armoured Titan Sylem Remake Clips.mp4"
    actual = "Starly  The Attack Titan Vs Armoured Titan  Sylem Remake Clips.mp4"
    files = [
        {
            "id": "one",
            "name": actual,
            "webViewLink": "https://drive.google.com/file/d/one/view",
        }
    ]
    for wanted in [supplied, " \t" + supplied.lower() + "\r\n"]:
        result = google_apps.resolve("drive", wanted, opener=_fake_drive(files))
        assert result["status"] == "resolved"
        assert result["title"] == actual
        assert result["url"].endswith("/one/view")


def test_normalized_equal_names_remain_ambiguous(monkeypatch):
    monkeypatch.setattr(google_apps.google_api, "account_email", lambda _: "")
    monkeypatch.setattr(google_apps.google_api, "access_token", lambda **_: "fake")
    files = [
        {
            "id": "one",
            "name": "Physics  Notes",
            "webViewLink": "https://docs.google.com/document/d/one",
        },
        {
            "id": "two",
            "name": " physics\tNOTES ",
            "webViewLink": "https://docs.google.com/document/d/two",
        },
    ]
    result = google_apps.resolve("docs", "Physics Notes", opener=_fake_drive(files))
    assert result["status"] == "needs_choice"
    assert "url" not in result
    assert [c["title"] for c in result["candidates"]] == [f["name"] for f in files]


def test_normalized_unique_result_requires_complete_search(monkeypatch):
    import io

    monkeypatch.setattr(google_apps.google_api, "account_email", lambda _: "")
    monkeypatch.setattr(google_apps.google_api, "access_token", lambda **_: "fake")
    files = [
        {
            "id": "one",
            "name": "Physics  Notes",
            "webViewLink": "https://docs.google.com/document/d/one",
        }
    ]
    for extra in [{"nextPageToken": "more"}, {"incompleteSearch": True}]:

        def opener(req, timeout=None):
            return io.BytesIO(json.dumps({"files": files, **extra}).encode())

        result = google_apps.resolve("docs", "Physics Notes", opener=opener)
        assert result["status"] == "needs_choice"
        assert result["incomplete_search"] is True
        assert "url" not in result


def test_whitespace_matching_preserves_words_punctuation_and_nonordinary_spaces(
    monkeypatch,
):
    monkeypatch.setattr(google_apps.google_api, "account_email", lambda _: "")
    monkeypatch.setattr(google_apps.google_api, "access_token", lambda **_: "fake")
    for actual in ["Physics-Notes", "Physics Notes extra", "Physics\u00a0Notes"]:
        files = [
            {
                "id": "one",
                "name": actual,
                "webViewLink": "https://docs.google.com/document/d/one",
            }
        ]
        result = google_apps.resolve("docs", "Physics Notes", opener=_fake_drive(files))
        assert result["status"] != "resolved"
