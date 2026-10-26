"""Bridge turn cancellation, request deduplication and output arbitration."""

import concurrent.futures
import sys
import threading

import pytest

sys.path.insert(0, "src")
import bridge


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    for name in ("_IDEM", "_CANCELLED", "_CLIENT_HIGH", "_SPEAKER"):
        monkeypatch.setattr(bridge, name, {})


def request(turn=1, client="phone", **fields):
    return dict(turn_id=turn, client_id=client, text="hello", **fields)


def test_concurrent_retry_executes_side_effect_once():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def action(body):
        calls.append(body)
        entered.set()
        assert release.wait(2)
        return 200, {"ok": True, "reply": "sent", "action": {"ok": True}}

    with concurrent.futures.ThreadPoolExecutor() as pool:
        first = pool.submit(bridge._with_turn, request(idem_key="same"), "chat", action)
        assert entered.wait(1)
        second = pool.submit(
            bridge._with_turn, request(idem_key="same"), "chat", action
        )
        bridge.cancel_turn(1, "phone")
        release.set()
        a, b = first.result(), second.result()
    assert len(calls) == 1
    for code, result in (a, b):
        assert code == 200
        assert result["cancelled"] and result["reply"] == ""
        assert result["action"] == {"ok": True}


def test_cancel_before_start_prevents_action():
    bridge.cancel_turn(1, "phone")

    def forbidden(_):
        pytest.fail("cancelled action ran")

    assert bridge._with_turn(request(), "chat", forbidden)[1]["cancelled"]


def test_cancel_is_scoped_to_client():
    bridge.cancel_turn(1, "other")
    assert (
        bridge._with_turn(request(), "chat", lambda _: (200, {"reply": "ok"}))[1][
            "reply"
        ]
        == "ok"
    )


def test_turn_id_alone_deduplicates_and_key_conflict_rejects():
    calls = []

    def run(_):
        calls.append(1)
        return 200, {"reply": "ok"}

    bridge._with_turn(request(), "chat", run)
    bridge._with_turn(request(), "chat", run)
    assert len(calls) == 1
    assert bridge._with_turn(request(text2="changed"), "chat", run)[0] == 409


def test_completed_reply_retry_is_suppressed_after_newer_turn():
    def run(_):
        return (200, {"reply": "hello", "audio_b64": "old"})

    bridge._with_turn(request(), "chat", run)
    bridge._with_turn(request(2), "chat", run)
    result = bridge._with_turn(request(), "chat", run)[1]
    assert result["reply"] == "" and result["audio_b64"] == ""


def test_atomic_speaker_claim():
    barrier = threading.Barrier(2)

    def claim(surface):
        barrier.wait()
        return bridge.speaker_claim(surface)

    with concurrent.futures.ThreadPoolExecutor() as pool:
        desktop = pool.submit(claim, "desktop")
        phone = pool.submit(claim, "phone")
        assert sum((desktop.result(), phone.result())) == 1
    winner = "phone" if bridge.speaker_active("phone") else "desktop"
    other = "desktop" if winner == "phone" else "phone"
    bridge.speaker_release(winner)
    assert bridge.speaker_claim(other)


def test_same_turn_cannot_rerun_with_changed_idempotency_key():
    calls = []

    def run(_):
        calls.append(1)
        return 200, {"reply": "ok"}

    bridge._with_turn(request(idem_key="a"), "chat", run)
    bridge._with_turn(request(idem_key="b"), "chat", run)
    assert calls == [1]
    assert bridge._with_turn(request(2, idem_key="a"), "chat", run)[0] == 409


def test_summon_ids_do_not_mark_chat_requests_stale():
    def run(_):
        return (200, {"reply": "ok"})

    bridge._with_turn(request(999999), "summon", run)
    assert bridge._with_turn(request(), "chat", run)[1]["reply"] == "ok"


def test_cancel_of_launched_voice_is_forwarded_only_for_matching_client(monkeypatch):
    monkeypatch.setattr(bridge, "_LAUNCHED", {})
    messages = []

    def rpc(payload):
        messages.append(payload)
        return {"ok": True}

    monkeypatch.setattr(bridge, "_wake_rpc", rpc)
    bridge.handle_summon(request(42))
    assert messages[0] == {
        "talk": True,
        "text": "hello",
        "turn_id": 42,
        "client_id": "phone",
    }
    bridge.cancel_turn(42, "other")
    assert len(messages) == 1
    bridge.cancel_turn(42, "phone")
    assert messages[-1] == {
        "cancel_external": True,
        "turn_id": 42,
        "client_id": "phone",
    }


def test_cancel_during_launch_is_forwarded_after_launch_race(monkeypatch):
    monkeypatch.setattr(bridge, "_LAUNCHED", {})
    messages = []

    def rpc(payload):
        messages.append(payload)
        if payload.get("talk"):
            bridge.cancel_turn(42, "phone")
        return {"ok": True}

    monkeypatch.setattr(bridge, "_wake_rpc", rpc)
    result = bridge.handle_summon(request(42))[1]
    assert result["cancelled"]
    assert messages[-1]["cancel_external"]
