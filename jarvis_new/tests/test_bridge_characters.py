"""Bridge character endpoints, chat switching, captions compat."""

import json
import sys
import urllib.request

import pytest

sys.path.insert(0, "src")

import bridge
import character_runtime as cr
import hud_events


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    cr.reset_runtime()
    for d in (bridge._IDEM, bridge._CANCELLED, bridge._CLIENT_HIGH, bridge._SPEAKER):
        d.clear()
    guest = {"v": False}
    monkeypatch.setattr(bridge, "_effective_guest", lambda body=None: guest["v"] or bool((body or {}).get("guest")))
    monkeypatch.setattr(bridge, "_gemini_reply", lambda *a, **k: ("hello", None))
    monkeypatch.setattr(bridge, "_match_voice_tool", lambda *a: None)
    import watch_control

    monkeypatch.setattr(watch_control.controls, "handle", lambda *a, **k: None)
    yield guest
    cr.reset_runtime()


def _call(server, method, path, payload=None):
    port = server.server_address[1]
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except Exception as exc:
        return getattr(exc, "code", 0), json.loads(exc.read().decode() or "{}")


def test_chat_switch_and_no_false_positive():
    _, res = bridge.handle_chat({"text": "tell me about ultron"})
    assert res["reply"] == "hello" and res["speaker"] == "jarvis" and res["char_turn"] == 0
    _, res = bridge.handle_chat({"text": "switch to ultron"})
    assert res["switched"] and res["target"] == "ultron" and res["character"]["target"] == "ultron"


def test_chat_ignores_client_character_and_accepts_3_item_history():
    code, res = bridge.handle_chat(
        {"text": "hi", "character": "ultron", "history": [["user", "a"], ["jarvis", "b", "jarvis"]]}
    )
    assert code == 200 and res["speaker"] == "jarvis"


def test_chat_unknown_and_already_active():
    _, res = bridge.handle_chat({"text": "switch to zorblax"})
    assert not res["switched"] and "ULTRON" in res["reply"].upper()
    _, res = bridge.handle_chat({"text": "switch to jarvis"})
    assert not res["switched"]
    assert cr.get_runtime().snapshot()["char_turn"] == 0


def test_guest_chat_not_switched(_env):
    _env["v"] = True
    _, res = bridge.handle_chat({"text": "switch to ultron"})
    assert "switched" not in res
    assert cr.get_runtime().snapshot()["char_turn"] == 0


def test_characters_payload_no_prompt_and_endpoints():
    server, _ = bridge._run_in_thread()
    try:
        code, res = _call(server, "GET", "/characters")
        assert code == 200 and res["active"] == "jarvis" and "motion" in res
        assert {"jarvis", "ultron", "friday"} <= set(res["characters"])
        assert all("prompt" not in c for c in res["characters"].values())
        code, snap = _call(server, "GET", "/character")
        assert snap["phase"] == "idle"
        code, out = _call(server, "POST", "/character/switch", {"target": "ultron", "source": "ui"})
        assert code == 200 and out["switched"] and out["char_turn"] == 1
        code, out = _call(server, "POST", "/character/switch", {"target": "friday"})
        assert out["char_turn"] == 2
        code, out = _call(server, "POST", "/character/switch", {"target": "ultron", "guest": True})
        assert code == 403
        code, room = _call(server, "GET", "/room")
        assert room["character"]["char_turn"] == 2
    finally:
        server.shutdown()


def test_captions_two_and_three_columns(tmp_path):
    (tmp_path / "captions.log").write_text(
        "100\tsir\told line\n101\tjarvis\tnew line\tultron\n"
    )
    for reader in (bridge.read_captions, hud_events.read_captions):
        got = reader(10)
        assert got[0]["speaker"] == "jarvis" and got[0]["text"] == "old line"
        assert got[1]["speaker"] == "ultron" and got[1]["role"] == "jarvis"


def test_caption_writes_speaker(tmp_path):
    cr.get_runtime()
    assert hud_events.caption("jarvis", "hello")
    cols = (tmp_path / "captions.log").read_text().splitlines()[-1].split("\t")
    assert len(cols) == 4 and cols[3] == "jarvis"
