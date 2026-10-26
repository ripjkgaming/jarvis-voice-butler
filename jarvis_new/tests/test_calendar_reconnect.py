"""Offline regression tests: no browser consent, network, or real credentials."""

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

import google_api
from proactive.sources import calendar_source as cal

spec = importlib.util.spec_from_file_location(
    "calendar_auth_script", Path(__file__).parents[1] / "scripts/google_auth.py"
)
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)

OLD_SCOPES = google_api.SCOPES[:5]


@pytest.fixture
def personal(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    saved = {
        "refresh_token": "fake-old",
        "client_id": "fake-id",
        "client_secret": "fake-secret",
        "token_uri": google_api.TOKEN_URI,
        "scopes": OLD_SCOPES,
    }
    google_api.token_path().write_text(json.dumps(saved))
    (tmp_path / "google_accounts.json").write_text(
        json.dumps({"personal": "owner@example.test", "school": "student@example.test"})
    )
    return saved


def test_reconnect_preserves_existing_scopes_and_identity(personal):
    profile = auth.personal_reconnect_profile()
    assert profile["scopes"] == OLD_SCOPES
    assert profile["email"] == "owner@example.test"
    assert profile["saved"]["client_id"] == "fake-id"
    assert "https://www.googleapis.com/auth/gmail.send" not in profile["scopes"]


@pytest.mark.parametrize(
    "scopes",
    [None, [], ["openid"], ["https://www.googleapis.com/auth/calendar.events", 3]],
)
def test_missing_or_ambiguous_scopes_fail_closed(personal, scopes):
    personal["scopes"] = scopes
    google_api.token_path().write_text(json.dumps(personal))
    with pytest.raises(SystemExit):
        auth.personal_reconnect_profile()


def test_missing_identity_fails_closed(personal):
    google_api._accounts_path().write_text("{}")
    with pytest.raises(SystemExit):
        auth.personal_reconnect_profile()


def test_reconnect_rejects_wrong_account_or_changed_scopes_before_write(personal):
    profile = auth.personal_reconnect_profile()
    old = google_api.token_path().read_bytes()
    for scopes, email in [
        (OLD_SCOPES, "wrong@example.test"),
        (google_api.SCOPES, "owner@example.test"),
        (None, "owner@example.test"),
    ]:
        with pytest.raises(SystemExit):
            auth.validate_personal_grant(
                profile,
                {
                    "refresh_token": "fake-new",
                    "access_token": "fake-access",
                    "scope": " ".join(scopes) if scopes else None,
                },
                email,
            )
        assert google_api.token_path().read_bytes() == old
    auth.validate_personal_grant(
        profile,
        {
            "refresh_token": "fake-new",
            "access_token": "fake-access",
            "scope": " ".join(OLD_SCOPES),
        },
        "owner@example.test",
    )


def test_http_diagnostic_contains_only_allowlisted_code(personal):
    def failed(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url,
            400,
            "secret reason",
            {},
            io.BytesIO(
                json.dumps(
                    {"error": "invalid_grant", "error_description": "SECRET BODY"}
                ).encode()
            ),
        )

    with pytest.raises(google_api.GoogleError) as got:
        google_api.access_token(opener=failed)
    assert got.value.http_status == 400
    assert got.value.oauth_error == "invalid_grant"
    assert "http=400 oauth=invalid_grant" in str(got.value)
    assert "SECRET" not in str(got.value)


def test_untrusted_oauth_code_not_logged(personal):
    def failed(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, 503, "secret", {}, io.BytesIO(b'{"error":"secret_token"}')
        )

    with pytest.raises(google_api.GoogleError) as got:
        google_api.access_token(opener=failed)
    assert got.value.oauth_error is None
    assert "secret" not in str(got.value)
    assert "expired" not in str(got.value)


@pytest.fixture
def source(personal, monkeypatch):
    monkeypatch.setattr(cal, "_cache", {"at": 0, "events": [], "identity": None})
    monkeypatch.setattr(
        cal, "_failure", {"identity": None, "retry_at": 0, "attempts": 0, "code": None}
    )
    calls, logs = [], []
    monkeypatch.setattr(cal, "_log", lambda msg: logs.append(msg))

    def failed(*args, **kwargs):
        calls.append(1)
        raise google_api.GoogleError(
            "auth failed", http_status=400, oauth_error="invalid_grant"
        )

    monkeypatch.setattr(google_api, "calendar_list", failed)
    return calls, logs


def test_auth_backoff_and_credential_replacement_recovery(source, monkeypatch):
    calls, logs = source
    with pytest.raises(google_api.GoogleError):
        cal.poll(1000)
    assert cal.poll(1030) == []
    assert cal.poll(1899) == []
    assert len(calls) == 1
    assert cal.poll(1900) == []
    assert len(calls) == 2
    assert len([x for x in logs if "backoff" in x]) == 1
    replacement = google_api.token_path().with_suffix(".new")
    replacement.write_text('{"refresh_token":"fake-replaced"}')
    replacement.replace(google_api.token_path())
    monkeypatch.setattr(google_api, "calendar_list", lambda *a, **k: [])
    assert cal.poll(1930) == []
    assert any("recovered" in x for x in logs)
    assert cal._failure["attempts"] == 0


def test_transient_backoff_is_capped(source, monkeypatch):
    calls, _logs = source

    def failed(*a, **k):
        calls.append(1)
        raise google_api.GoogleError("temporary", http_status=503)

    monkeypatch.setattr(google_api, "calendar_list", failed)
    now = 1000
    delays = []
    for _ in range(8):
        if not calls:
            with pytest.raises(google_api.GoogleError):
                cal.poll(now)
        else:
            assert cal.poll(now) == []
        delays.append(cal._failure["retry_at"] - now)
        assert cal.poll(now + 1) == []
        now = cal._failure["retry_at"]
    assert delays == [60, 120, 240, 480, 900, 900, 900, 900]


def test_failed_calendar_does_not_block_other_sources_or_phone_scheduler(
    source, monkeypatch, tmp_path
):
    from proactive import engine

    monkeypatch.setattr(engine, "state_path", lambda: tmp_path / "engine.json")
    other = []
    eng = engine.ProactiveEngine(
        sources=[cal.poll, lambda now: other.append(now) or []], log=lambda *a: None
    )
    eng.run_once(1000)
    eng.run_once(1030)
    assert other == [1000, 1030]
    # Drive two real loop iterations with fake threading and a fake call scheduler.
    import threading

    import phone_calls

    polls = []

    class Stop:
        def __init__(self):
            self.count = 0

        def wait(self, delay):
            if delay == 45:
                return False
            self.count += 1
            return self.count >= 2

        def is_set(self):
            return self.count >= 2

    class Thread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(threading, "Event", Stop)
    monkeypatch.setattr(threading, "Thread", Thread)
    monkeypatch.setattr(phone_calls, "poll_schedule", lambda: polls.append(1))
    monkeypatch.setattr(engine, "ProactiveEngine", lambda: eng)
    monkeypatch.setattr(engine.time, "time", lambda: 1030)
    engine.start_thread()
    assert polls == [1, 1]


def test_atomic_replacement_preserves_other_accounts(personal):
    school_path = google_api.token_path("school")
    school_path.write_text("fake-school-credentials")
    gmail_path = google_api.jarvis_home() / "fake-gmail-token.json"
    gmail_path.write_text("fake-gmail-credentials")
    saved = dict(personal, refresh_token="fake-new")
    auth._write_token(google_api.token_path(), saved)
    assert json.loads(google_api.token_path().read_text()) == saved
    assert google_api.token_path().stat().st_mode & 0o777 == 0o600
    assert school_path.read_text() == "fake-school-credentials"
    assert gmail_path.read_text() == "fake-gmail-credentials"


def test_connection_changed_during_consent_is_preserved(personal):
    profile = auth.personal_reconnect_profile()
    changed = dict(personal, refresh_token="concurrently-replaced")
    google_api.token_path().write_text(json.dumps(changed))
    with pytest.raises(SystemExit, match="changed during consent"):
        auth.validate_personal_grant(
            profile,
            {
                "access_token": "fake",
                "refresh_token": "fake-new",
                "scope": " ".join(OLD_SCOPES),
            },
            "owner@example.test",
        )
    assert json.loads(google_api.token_path().read_text()) == changed
