"""Silent-call announce: wake-client command, token, agent join, speak() routing."""

import json
import os
import socket
import threading
from types import SimpleNamespace

import agent
import speak
import wake_client


def test_extract_announce_text() -> None:
    assert wake_client.extract_announce_text({"announce": "  Hi   Sir "}) == "Hi Sir"
    assert wake_client.extract_announce_text({"announce": ""}) is None
    assert wake_client.extract_announce_text({"announce": 5}) is None
    assert wake_client.extract_announce_text({"talk": True}) is None
    assert wake_client.extract_announce_text(
        {"announce": "x" * (wake_client.TALK_TEXT_MAX + 1)}
    ) is None


def test_token_carries_announce_attribute() -> None:
    import jwt

    def attrs(**kw):
        tok = wake_client.mint_summon_token(
            url="ws://x", api_key="k" * 8, api_secret="s" * 32, room="r",
            agent_name="a", **kw,
        )
        return jwt.decode(tok, options={"verify_signature": False}).get("attributes", {})

    assert attrs(reason="announce", announce="Mail from Sam")["jarvis.announce"] == "Mail from Sam"
    assert "jarvis.announce" not in attrs()


def _room(attrs: dict):
    p = SimpleNamespace(identity="jarvis-master", attributes=attrs)
    return SimpleNamespace(remote_participants={"p": p})


def test_agent_reads_announce_from_room() -> None:
    assert agent._wake_announce(_room({"jarvis.announce": " Three  new mails. "})) == "Three new mails."
    assert agent._wake_announce(_room({"jarvis.wake": "wake"})) == ""
    assert agent._wake_announce(SimpleNamespace(remote_participants={})) == ""


def _serve(tmp_path, reply: dict, seen: list):
    path = tmp_path / "wake.sock"
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(1)

    def run() -> None:
        conn, _ = srv.accept()
        with conn:
            seen.append(json.loads(conn.recv(4096).decode()))
            conn.sendall(json.dumps(reply).encode())
        srv.close()

    threading.Thread(target=run, daemon=True).start()


def test_announce_via_call_true_when_accepted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    seen: list = []
    _serve(tmp_path, {"ok": True, "announce": "queued"}, seen)
    assert speak.announce_via_call("Urgent mail from Sam") is True
    assert seen == [{"announce": "Urgent mail from Sam"}]


def test_announce_via_call_false_when_refused_or_down(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert speak.announce_via_call("x") is False  # no socket at all
    _serve(tmp_path, {"ok": False, "error": "busy"}, [])
    assert speak.announce_via_call("x") is False


def test_speak_uses_call_then_falls_back(monkeypatch) -> None:
    ran: list = []
    monkeypatch.setattr(speak, "wake_status", lambda: {})
    monkeypatch.setattr(speak, "_run", lambda *a: ran.append(a))
    monkeypatch.delenv("JARVIS_ANNOUNCE_CALL", raising=False)

    toasts: list = []
    monkeypatch.setattr(speak, "_notify", lambda *a: toasts.append(a))
    monkeypatch.setattr(speak, "announce_via_call", lambda t: True)
    assert speak.speak("Hello Sir", source="t1", min_gap_s=0)
    import time

    time.sleep(0.1)
    assert ran == []  # spoken inside the call, no local voice
    assert len(toasts) == 1  # visible receipt

    monkeypatch.setattr(speak, "announce_via_call", lambda t: False)
    assert speak.speak("Hello Sir", source="t2", min_gap_s=0)
    import time

    time.sleep(0.1)
    assert len(ran) == 1 and ran[0][2] is True  # local voice fallback


def test_kill_switch_and_quiet_states_skip_the_call(monkeypatch) -> None:
    called: list = []
    monkeypatch.setattr(speak, "announce_via_call", lambda t: called.append(t) or True)
    monkeypatch.setattr(speak, "_run", lambda *a: None)
    monkeypatch.setenv("JARVIS_ANNOUNCE_CALL", "0")
    monkeypatch.setattr(speak, "wake_status", lambda: {})
    speak.speak("a", source="k1", min_gap_s=0)
    monkeypatch.delenv("JARVIS_ANNOUNCE_CALL")
    monkeypatch.setattr(speak, "wake_status", lambda: {"muted": True})
    speak.speak("b", source="k2", min_gap_s=0)
    monkeypatch.setattr(speak, "wake_status", lambda: {"in_call": True})
    speak.speak("c", source="k3", min_gap_s=0)
    assert called == []
