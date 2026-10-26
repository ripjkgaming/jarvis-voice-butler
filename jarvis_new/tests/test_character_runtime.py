"""CharacterRuntime: boot, switching, retarget, prewarm, listeners."""

import json
import sys

import pytest

sys.path.insert(0, "src")

import character_runtime as cr
from switch_state import Phase


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    cr.reset_runtime()
    yield
    cr.reset_runtime()


def test_boot_jarvis_despite_hint(tmp_path):
    (tmp_path / "character.json").write_text(json.dumps({"recent": "ultron"}))
    assert cr.get_runtime().active_id() == "jarvis"
    assert cr.get_runtime() is cr.get_runtime()


def test_switch_command_and_non_command():
    rt = cr.get_runtime()
    assert rt.handle_text("tell me about ultron") is None
    res = rt.handle_text("switch to ultron")
    assert res["switched"] and res["target"] == "ultron" and res["from"] == "jarvis"
    assert res["char_turn"] == 1 and res["reply"] and res["exit_line"]
    snap = rt.snapshot()
    assert snap["phase"] == "preparing" and snap["target"] == "ultron"
    assert snap["active"] == "jarvis" and "ultron" in snap["characters"]


def test_unknown_lists_names():
    res = cr.get_runtime().handle_text("switch to zorblax")
    assert res["handled"] and not res["switched"]
    assert "ULTRON" in res["reply"].upper() and "JARVIS" in res["reply"].upper()


def test_already_active_no_bump():
    rt = cr.get_runtime()
    res = rt.handle_text("switch to jarvis")
    assert res["handled"] and not res["switched"] and res["reply"]
    assert rt.snapshot()["char_turn"] == 0


def test_retarget_bumps_turn():
    rt = cr.get_runtime()
    a = rt.switch("ultron", "ui")
    b = rt.switch("friday", "ui")
    assert b["char_turn"] == a["char_turn"] + 1 and b["target"] == "friday"


def test_listener_phase_and_complete():
    rt = cr.get_runtime()
    seen = []
    rt.subscribe(seen.append)
    rt.subscribe(lambda s: 1 / 0)  # bad listener must not break others
    turn = rt.switch("ultron", "ui")["char_turn"]
    for ph in (Phase.EXITING, Phase.SIGNATURE, Phase.ENTERING, Phase.SETTLING):
        rt.set_phase(ph)
    assert rt.complete(turn)
    assert rt.active_id() == "ultron"
    assert [s["phase"] for s in seen][-1] == "idle" and len(seen) == 6


def test_prewarm_failure_rolls_back_and_hooks_run():
    rt = cr.get_runtime()
    rt.register_prewarm(lambda ch: (_ for _ in ()).throw(RuntimeError("boom")))
    turn = rt.switch("ultron", "ui")["char_turn"]
    rt.run_prewarm(turn).join(2)
    assert rt.snapshot()["phase"] == "rollback"


def test_prewarm_default_ok():
    rt = cr.get_runtime()
    turn = rt.switch("ultron", "ui")["char_turn"]
    rt.run_prewarm(turn).join(2)
    assert rt.machine.ready_gate(turn)


def test_buffer_passthrough():
    rt = cr.get_runtime()
    rt.buffer_input("x")
    assert rt.drain_input() == ["x"] and rt.drain_input() == []
