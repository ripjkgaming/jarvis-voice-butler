import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_view_command_state(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    import model3d

    assert model3d.view_command("stop") == "Holding."
    assert model3d.get_state()["rotate"] is False
    model3d.view_command("faster")
    assert model3d.get_state()["speed"] > 1
    model3d.view_command("reverse")
    assert model3d.get_state()["dir"] == -1
    model3d.view_command("reset")
    assert model3d.get_state()["dir"] == 1


def test_compound_edit_goes_to_codex(monkeypatch):
    import model3d

    # part-level / compound edits must not be handled by the fast path
    for t in ("make the shade red and 30% bigger", "add a second arm"):
        assert model3d.fast_edit(t, "x") is None
