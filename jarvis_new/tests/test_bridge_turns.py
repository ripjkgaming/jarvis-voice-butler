"""Turn ids, idempotency, cancel, ordering and speaker owner for the bridge."""

import sys
import threading

import pytest

sys.path.insert(0, "src")

import bridge


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for d in (bridge._IDEM, bridge._CANCELLED, bridge._CLIENT_HIGH, bridge._SPEAKER):
        d.clear()
    monkeypatch.setattr(bridge, "_effective_guest", lambda *a: False)
    monkeypatch.setattr(bridge, "_gemini_reply", lambda *a, **k: ("hello", None))
    monkeypatch.setattr(bridge, "_match_voice_tool", lambda *a: None)
    import watch_control

    monkeypatch.setattr(watch_control.controls, "handle", lambda *a, **k: None)
    yield
    for d in (bridge._IDEM, bridge._CANCELLED, bridge._CLIENT_HIGH, bridge._SPEAKER):
        d.clear()


def _action(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, "_match_voice_tool", lambda *a: ("lock", {}, "Done."))

    def run(tool, args):
        calls.append(tool)
        return 200, {"ok": True}

    monkeypatch.setattr(bridge, "_run_voice_action", run)
    return calls


def test_no_fields_unchanged():
    code, res = bridge.handle_chat({"text": "hi"})
    assert code == 200 and res["ok"] is True and res["reply"] == "hello"
    assert "turn_id" not in res


def test_turn_id_echoed_including_errors():
    code, res = bridge.handle_chat({"text": "hi", "turn_id": 5})
    assert res["turn_id"] == 5
    code, res = bridge.handle_chat({"text": "", "turn_id": "abc"})
    assert code == 400 and res["turn_id"] == "abc"


def test_idempotent_retry_does_not_rerun_action(monkeypatch):
    calls = _action(monkeypatch)
    body = {"text": "lock it", "idem_key": "k1"}
    first = bridge.handle_chat(dict(body))
    second = bridge.handle_chat(dict(body))
    assert calls == ["lock"]
    assert second[1]["reply"] == first[1]["reply"] and second[1]["idempotent"]


def test_idempotent_retry_waits_for_inflight(monkeypatch):
    calls = []
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(bridge, "_match_voice_tool", lambda *a: ("lock", {}, "Done."))

    def run(tool, args):
        calls.append(tool)
        started.set()
        release.wait(5)
        return 200, {"ok": True}

    monkeypatch.setattr(bridge, "_run_voice_action", run)
    out = []
    body = {"text": "lock", "idem_key": "k2"}
    t = threading.Thread(target=lambda: out.append(bridge.handle_chat(dict(body))))
    t.start()
    assert started.wait(5)
    t2 = threading.Thread(target=lambda: out.append(bridge.handle_chat(dict(body))))
    t2.start()
    release.set()
    t.join(5)
    t2.join(5)
    assert calls == ["lock"] and len(out) == 2
    assert all(o[1]["reply"] == "Done." for o in out)


def test_cancel_before_action(monkeypatch):
    calls = _action(monkeypatch)
    assert bridge.cancel_turn(7)
    code, res = bridge.handle_chat({"text": "lock", "turn_id": 7})
    assert calls == [] and res["cancelled"] is True and res["turn_id"] == 7


def test_cancel_during_action_runs_to_completion(monkeypatch):
    monkeypatch.setattr(bridge, "_match_voice_tool", lambda *a: ("lock", {}, "Done."))
    ran = []

    def run(tool, args):
        bridge.cancel_turn(9)
        ran.append(tool)
        return 200, {"ok": True}

    monkeypatch.setattr(bridge, "_run_voice_action", run)
    code, res = bridge.handle_chat({"text": "lock", "turn_id": 9})
    assert ran == ["lock"] and res["cancelled"] is True and res["reply"] == ""


def test_cancel_between_gemini_attempts(monkeypatch):
    monkeypatch.undo()
    bridge.cancel_turn(3)
    bridge._TURN_CTX.turn_key = "default:3"
    try:
        assert bridge._turn_cancelled()
    finally:
        bridge._TURN_CTX.turn_key = None
    assert not bridge._turn_cancelled()


def test_stale_turn_skipped(monkeypatch):
    calls = _action(monkeypatch)
    bridge.handle_chat({"text": "lock", "turn_id": 10, "client_id": "p"})
    code, res = bridge.handle_chat({"text": "lock", "turn_id": 9, "client_id": "p"})
    assert res["stale"] is True and calls == ["lock"]
    code, res = bridge.handle_chat({"text": "lock", "turn_id": 9, "client_id": "q"})
    assert "stale" not in res


def test_voice_refused_while_phone_active(monkeypatch):
    monkeypatch.setattr(bridge, "_wake_rpc", lambda p: pytest.fail("spoke"))
    bridge.speaker_mark("phone")
    code, res = bridge.handle_chat({"text": "hi", "voice": True})
    assert res["spoke_on"] == "none" and res["reason"] == "phone_call_active"
    assert res["reply"] == "hello"


def test_voice_marks_desktop(monkeypatch):
    monkeypatch.setattr(bridge, "_wake_rpc", lambda p: {"ok": True})
    code, res = bridge.handle_chat({"text": "hi", "voice": True})
    assert res["spoke_on"] == "desktop" and bridge.speaker_active("desktop")
    bridge.speaker_release("desktop")
    assert not bridge.speaker_active("desktop")
