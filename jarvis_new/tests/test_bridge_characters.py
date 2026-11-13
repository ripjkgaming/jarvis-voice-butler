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
    monkeypatch.setattr(
        bridge,
        "_effective_guest",
        lambda body=None: guest["v"] or bool((body or {}).get("guest")),
    )
    monkeypatch.setattr(bridge, "_gemini_reply", lambda *a, **k: ("hello", None))
    monkeypatch.setattr(bridge, "_match_voice_tool", lambda *a: None)
    import watch_control

    monkeypatch.setattr(watch_control.controls, "handle", lambda *a, **k: None)
    yield guest
    cr.reset_runtime()


def _call(server, method, path, payload=None):
    port = server.server_address[1]
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method
    )
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except Exception as exc:
        return getattr(exc, "code", 0), json.loads(exc.read().decode() or "{}")


def test_chat_ignores_client_character_and_accepts_3_item_history():
    code, res = bridge.handle_chat(
        {
            "text": "hi",
            "character": "ultron",
            "history": [["user", "a"], ["jarvis", "b", "jarvis"]],
        }
    )
    assert code == 200 and res["speaker"] == "jarvis"


def test_guest_chat_not_switched(_env):
    _env["v"] = True
    _, res = bridge.handle_chat({"text": "switch to ultron"})
    assert "switched" not in res
    assert cr.get_runtime().snapshot()["char_turn"] == 0


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
