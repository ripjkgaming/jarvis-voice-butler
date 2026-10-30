import datetime as dt
from zoneinfo import ZoneInfo

from phone_calls import CallQueue, quiet


def ts(day, clock):
    return (
        dt.datetime.fromisoformat(f"2026-10-{day}T{clock}:00")
        .replace(tzinfo=ZoneInfo("Asia/Singapore"))
        .timestamp()
    )


def test_boundaries():
    assert not quiet(ts("05", "07:59"))
    assert quiet(ts("05", "08:00"))
    assert quiet(ts("05", "15:29"))
    assert not quiet(ts("05", "15:30"))
    assert not quiet(ts("04", "10:00"))


def test_suppression_expiry_dedupe_and_boundary(tmp_path):
    q = CallQueue(tmp_path / "calls.db")
    n = ts("05", "07:59") + 50
    q.configure(True, True, True, n)
    assert q.offer("calendar", "event", "Useful reminder", n)
    assert not q.offer("calendar", "event", "Duplicate", n)
    assert q.pending(n)["invitation"]
    assert q.pending(ts("05", "08:00"))["invitation"] is None
    assert q.pending(ts("05", "15:30"))["invitation"] is None
    q.configure(True, True, True, ts("05", "15:30"))
    assert not q.offer("update", "urgent", "Urgent", ts("05", "08:00"))
    assert not q.offer("update", "urgent", "Urgent", ts("05", "15:30"))


def test_accept_once_and_offline_no_queue(tmp_path):
    q = CallQueue(tmp_path / "calls.db")
    n = ts("04", "10:00")
    assert not q.offer("update", "offline", "No dump", n)
    q.configure(True, True, True, n)
    assert q.offer("update", "fresh", "Ready", n)
    i = q.pending(n)["invitation"]
    assert q.claim(i["id"], "accept", n)
    assert not q.claim(i["id"], "accept", n)
    assert q.offer("update", "next", "Spacing removed", n + 30)
    q.configure(False, True, True, n + 31)
    assert q.pending(n + 31)["invitation"] is None


def test_expired_and_declined(tmp_path):
    q = CallQueue(tmp_path / "calls.db")
    n = ts("04", "10:00")
    q.configure(True, True, True, n)
    assert q.offer("update", "one", "Ready", n)
    i = q.pending(n)["invitation"]
    assert q.claim(i["id"], "decline", n)
    assert not q.claim(i["id"], "accept", n)
    n += 901
    q.configure(True, True, True, n)
    assert q.offer("update", "two", "Ready", n)
    i = q.pending(n)["invitation"]
    assert not q.claim(i["id"], "accept", n + 46)


def test_restart_daily_cap_and_no_urgent_bypass(tmp_path):
    path = tmp_path / "calls.db"
    q = CallQueue(path)
    n = ts("04", "10:00")
    for k in range(3):
        now = n + k * 901
        q.configure(True, True, True, now)
        assert q.offer("update", str(k), "Finished task", now)
        assert q.claim(q.pending(now)["invitation"]["id"], "decline", now)
    q = CallQueue(path)
    q.configure(True, True, True, n + 3000)
    assert not q.offer("update", "cap", "Fourth", n + 3000)
    q.configure(True, False, True, n + 4000)
    assert not q.offer("calendar", "disabled", "Reminder", n + 4000)


def test_server_owned_opener_once_and_quiet_rejected(tmp_path, monkeypatch):
    import phone_calls

    q = CallQueue(tmp_path / "calls.db")
    monkeypatch.setattr(phone_calls, "queue", lambda: q)
    n = ts("04", "10:00")
    q.configure(True, True, True, n)
    assert q.offer("update", "fresh", "Job completed, Sir.", n)
    i = q.pending(n)["invitation"]
    assert q.claim(i["id"], "accept", n)
    assert phone_calls.consume_announcement(i["id"], "untrusted", n) == ""
    assert (
        phone_calls.consume_announcement(i["id"], "jarvis-phone-" + i["id"], n)
        == "Job completed, Sir."
    )
    assert phone_calls.consume_announcement(i["id"], "jarvis-phone-" + i["id"], n) == ""
    n = ts("05", "07:59") + 50
    q.configure(True, True, True, n)
    q.offer("calendar", "boundary", "School reminder", n)
    i = q.pending(n)["invitation"]
    assert q.claim(i["id"], "accept", n)
    assert q.accepted(i["id"], ts("05", "08:00")) is None


def test_stale_cannot_mint_or_announce(tmp_path, monkeypatch):
    import pytest

    import phone_calls

    q = CallQueue(tmp_path / "calls.db")
    monkeypatch.setattr(phone_calls, "queue", lambda: q)
    n = ts("04", "10:00")
    q.configure(True, True, True, n)
    q.offer("update", "fresh", "Job completed", n)
    i = q.pending(n)["invitation"]
    q.claim(i["id"], "accept", n)
    monkeypatch.setattr(phone_calls.time, "time", lambda: n + 46)
    with pytest.raises(RuntimeError):
        phone_calls.mint_phone_token(i["id"])
    assert phone_calls.consume_announcement(i["id"], "jarvis-phone-" + i["id"]) == ""


def test_signed_phone_token_and_manual_unchanged(tmp_path, monkeypatch):
    import pytest

    import mint_token
    import phone_calls
    import phone_session
    from jarvis_rt.transport.tokens import (
        MemoryNonceLedger,
        ReplayedTokenError,
        load_secret,
        verify_call_token,
    )

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis-home"))
    q = CallQueue(tmp_path / "calls.db")
    monkeypatch.setattr(phone_calls, "queue", lambda: q)
    n = ts("04", "10:00")
    monkeypatch.setattr(phone_calls.time, "time", lambda: n)
    q.configure(True, True, True, n)
    q.offer("update", "mint", "Build completed", n)
    i = q.pending(n)["invitation"]
    q.claim(i["id"], "accept", n)

    def claims(o):
        return verify_call_token(o["participantToken"], load_secret(), now=n)

    incoming = phone_calls.mint_phone_token(i["id"])
    p = claims(incoming)
    assert p.identity == phone_session.identity_for(incoming["roomName"])
    assert p.room == incoming["roomName"]
    assert p.mode == "speaker"
    assert p.expires - p.issued == 900
    assert set(p.attrs) == {
        "jarvis.phone_call",
        "jarvis.phone_owner",
        "jarvis.phone_sig",
    }
    assert p.attrs["jarvis.phone_call"] == i["id"]
    assert p.attrs["jarvis.phone_owner"] == incoming["roomName"]
    assert (
        phone_session.verify(p.attrs["jarvis.phone_sig"], incoming["roomName"])
        == p.identity
    )
    ledger = MemoryNonceLedger()
    verify_call_token(incoming["participantToken"], load_secret(), now=n, ledger=ledger)
    with pytest.raises(ReplayedTokenError):
        verify_call_token(
            incoming["participantToken"], load_secret(), now=n, ledger=ledger
        )
    normal = claims(mint_token.mint_token_payload())
    assert normal.identity.startswith("voice_assistant_user_")
    assert "jarvis.phone_call" not in normal.attrs


def test_http_calls_auth_old_clients_and_guest(tmp_path, monkeypatch):
    import json
    import threading
    import urllib.error
    import urllib.request

    import bridge
    import phone_calls
    import watch_mode

    q = CallQueue(tmp_path / "http.db")
    monkeypatch.setattr(phone_calls, "queue", lambda: q)
    monkeypatch.setattr(watch_mode, "guest_required", lambda: False)
    server = bridge.create_server(host="127.0.0.1", port=0, token="test-only")
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def req(path, body=None, token="test-only"):
        r = urllib.request.Request(
            base + path,
            data=json.dumps(body).encode() if body else None,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(r, timeout=3) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    try:
        assert req("/phone/v1/calls", token="wrong")[0] == 401
        assert req("/phone/v1/capabilities")[1]["features"]["incoming_calls"]
        assert req("/phone/v1/calls")[1]["invitation"] is None
        assert (
            req(
                "/phone/v1/calls",
                {
                    "api_version": 1,
                    "action": "configure",
                    "enabled": True,
                    "calendar": True,
                    "updates": True,
                    "guest": True,
                },
            )[0]
            == 403
        )
        assert (
            req(
                "/phone/v1/calls",
                {"api_version": 1, "action": "accept", "id": "unknown"},
            )[0]
            == 409
        )
        assert (
            req(
                "/phone/v1/calls",
                {
                    "api_version": 1,
                    "action": "configure",
                    "enabled": False,
                    "calendar": True,
                    "updates": True,
                },
            )[0]
            == 200
        )
    finally:
        server.shutdown()
        server.server_close()
        t.join()


def test_diagnostic_get_does_not_keep_phone_online(tmp_path):
    q = CallQueue(tmp_path / "read.db")
    n = ts("04", "10:00")
    q.configure(True, True, True, n)
    q.pending(n + 59)
    q.pending(n + 61)
    assert not q.offer("update", "offline-after-read", "Read is not a listener", n + 61)


def test_monitor_diagnostics_and_readiness_do_not_queue_rings(tmp_path):
    q = CallQueue(tmp_path / "monitor.db")
    n = ts("04", "10:00")
    q.monitor(
        True,
        True,
        True,
        {
            "client_time_ms": int(n * 1000),
            "notifications": False,
            "channel_importance": 4,
            "status": "notifications_disabled",
        },
        n,
    )
    assert not q.offer("update", "disabled", "No notifications", n)
    q.monitor(
        True,
        True,
        True,
        {
            "client_time_ms": int((n + 60) * 1000),
            "notifications": True,
            "channel_importance": 4,
            "status": "monitoring",
            "secret": "MUST NOT SAVE",
        },
        n + 60,
    )
    d = q.pending(n + 60)["monitor"]
    assert "secret" not in d
    assert d["status"] == "monitoring"
    assert q.offer("update", "ready", "Ready listener", n + 60)
    q.monitor(
        True,
        True,
        True,
        {
            "client_time_ms": int((n + 120) * 1000),
            "notifications": True,
            "channel_importance": 0,
            "status": "channel_disabled",
        },
        n + 120,
    )
    assert q.pending(n + 120)["invitation"] is None


def test_removed_spacing_still_keeps_dedupe_daily_cap_and_school_gate(tmp_path):
    q = CallQueue(tmp_path / "no-spacing.db")
    n = ts("04", "10:00")
    q.configure(True, True, True, n)
    for k in range(3):
        assert q.offer("update", "call" + str(k), "A distinct useful update", n + k)
        i = q.pending(n + k)["invitation"]
        assert q.claim(i["id"], "decline", n + k)
    assert not q.offer("update", "fourth", "Daily cap still applies", n + 4)
    assert not q.offer("update", "call0", "Duplicate still blocked", n + 5)
    n = ts("05", "08:00")
    q.configure(True, True, True, n)
    assert not q.offer("update", "school", "School still protected", n)


def test_weekly_checkin_occurrence_and_no_offline_catchup(tmp_path):
    q = CallQueue(tmp_path / "schedule.db")

    def at(s):
        return (
            dt.datetime.fromisoformat(s)
            .replace(tzinfo=ZoneInfo("Asia/Singapore"))
            .timestamp()
        )

    n = at("2026-10-07T19:05:00")
    q.configure(True, True, True, n)
    q.poll_schedule(n)
    invite = q.pending(n)["invitation"]
    assert invite and invite["kind"] == "checkin"
    assert invite["expires"] <= n + 45
    q.claim(invite["id"], "decline", n + 1)
    q.poll_schedule(n + 2)
    assert q.pending(n + 2)["invitation"] is None
    assert q.schedule(n)["next_at"].startswith("2026-10-14T19:05:00")
    q.set_schedule(False)
    q.configure(True, True, True, n + 7 * 86400)
    q.poll_schedule(n + 7 * 86400)
    assert q.pending(n + 7 * 86400)["invitation"] is None
    q.set_schedule(True)
    missed = at("2026-10-21T19:05:00")
    q.configure(False, True, True, missed)
    q.poll_schedule(missed)
    q.configure(True, True, True, missed + 20)
    q.poll_schedule(missed + 20)
    assert q.pending(missed + 20)["invitation"] is None
    assert q.poll_schedule(missed + 60) is False


def test_schedule_first_due_late_window_and_atomic_claim(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    q = CallQueue(tmp_path / "atomic-schedule.db")
    n = dt.datetime(2026, 10, 7, 19, 5, tzinfo=ZoneInfo("Asia/Singapore")).timestamp()
    q.configure(True, False, False, n)
    assert not q.poll_schedule(n - 1)
    assert not q.poll_schedule(n - 7 * 86400)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(q.poll_schedule, [n + 30] * 4))
    assert sum(claimed) == 1
    invite = q.pending(n + 30)["invitation"]
    assert invite["expires"] == n + 45
    assert q.pending(n + 45)["invitation"] is None
    assert not q.poll_schedule(n + 45)
    q2 = CallQueue(tmp_path / "late.db")
    q2.configure(True, True, True, n + 46)
    assert not q2.poll_schedule(n + 46)
    assert q2.pending(n + 46)["invitation"] is None


def test_bedtime_hook_uses_scheduler_wrapper_and_preserves_weekly_result(monkeypatch):
    import sys
    from types import SimpleNamespace

    import phone_calls

    calls = []
    q = SimpleNamespace(
        poll_schedule=lambda now: calls.append(("weekly", now)) or "weekly-result"
    )
    monkeypatch.setattr(phone_calls, "queue", lambda: q)

    def bedtime(queue, now):
        assert queue is q
        calls.append(("bedtime", now))
        raise RuntimeError("isolated Calendar failure")

    monkeypatch.setitem(
        sys.modules, "bedtime_calls", SimpleNamespace(poll_schedule=bedtime)
    )
    assert phone_calls.poll_schedule(123) == "weekly-result"
    assert calls == [("weekly", 123), ("bedtime", 123)]
