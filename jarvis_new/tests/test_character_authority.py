"""Bridge is the character authority; other processes mirror its state file."""

import contextlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, "src")

import character_runtime as cr
import switch_conductor as sc


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_BRIDGE_TOKEN", "tok")
    cr.reset_runtime()
    sc.reset_conductor()
    yield
    cr.reset_runtime()
    sc.reset_conductor()


def _state():
    return json.loads(cr.state_path().read_text())


def test_boot_resets_stale_file_and_switch_writes():
    cr.state_path().write_text(
        json.dumps({"active": "ultron", "target": None, "phase": "idle", "char_turn": 3})
    )
    cr.set_authority(True)
    assert _state()["active"] == "jarvis"
    res = cr.get_runtime().switch("ultron", "ui")
    assert res["switched"]
    st = _state()
    assert st["target"] == "ultron" and st["phase"] != "idle" and "ts" in st


def test_mirror_reads_authority_state():
    rt = cr.get_runtime()
    cr.set_authority(False)
    assert rt.active_id() == "jarvis"  # missing file
    cr.state_path().write_text("{garbage")
    rt._mirror_checked = 0
    assert rt.active_id() == "jarvis"  # corrupt
    cr.state_path().write_text(
        json.dumps({"active": "ultron", "target": None, "phase": "idle", "char_turn": 2})
    )
    rt._mirror_checked = 0
    assert rt.active_id() == "ultron"
    assert rt.snapshot()["char_turn"] == 2


class _Srv:
    def __init__(self, reply):
        outer = self
        outer.hits = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                outer.hits.append(
                    (self.path, self.headers.get("Authorization"), json.loads(self.rfile.read(n)))
                )
                body = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


def test_mirror_forwards_to_bridge_without_conductor(monkeypatch):
    srv = _Srv({"ok": True, "handled": True, "switched": True, "target": "ultron",
                "reply": "hi", "character": {}})
    monkeypatch.setenv("JARVIS_BRIDGE_PORT", str(srv.port))
    cr.set_authority(False)
    rt = cr.get_runtime()
    res = rt.handle_text("switch to ultron", "voice")
    srv.srv.shutdown()
    assert res["switched"] and res["target"] == "ultron" and "ok" not in res
    assert srv.hits == [("/character/switch", "Bearer tok", {"target": "ultron", "source": "voice"})]
    assert sc.start_switch(res, "voice") is False
    assert sc._conductor is None


def test_mirror_bridge_unreachable(monkeypatch):
    monkeypatch.setenv("JARVIS_BRIDGE_PORT", "1")
    cr.set_authority(False)
    res = cr.get_runtime().switch("ultron", "voice")
    assert res == {"handled": True, "switched": False,
                   "reply": "I could not reach the control bridge"}
    assert cr.get_runtime().active_id() == "jarvis"


def test_chat_answered_by_new_character_after_midswitch(monkeypatch):
    import bridge

    cr.set_authority(True)
    rt = cr.get_runtime()
    res = rt.switch("ultron", "ui")
    turn = res["char_turn"]
    seen = {}

    def fake(prompt, guest=False):
        seen["prompt"] = prompt
        seen["active"] = rt.snapshot()["active"]
        return "ok", None

    monkeypatch.setattr(bridge, "_gemini_reply", fake)
    monkeypatch.setattr(bridge, "_character_gate", lambda *a, **k: None)
    monkeypatch.setattr(bridge, "_match_voice_tool", lambda *a, **k: None)

    def finish():
        time.sleep(0.2)
        for ph in (cr.Phase.EXITING, cr.Phase.SIGNATURE, cr.Phase.ENTERING, cr.Phase.SETTLING):
            with contextlib.suppress(Exception):
                rt.set_phase(ph)
        rt.complete(turn)

    threading.Thread(target=finish, daemon=True).start()
    code, out = bridge._handle_chat({"text": "hello there"})
    assert code == 200
    assert out["speaker"] == "ultron" and seen["active"] == "ultron"
