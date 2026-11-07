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
















def test_buffer_passthrough():
    rt = cr.get_runtime()
    rt.buffer_input("x")
    assert rt.drain_input() == ["x"] and rt.drain_input() == []
