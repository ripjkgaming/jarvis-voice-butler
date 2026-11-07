from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

import characters as C  # noqa: N812

REPO = Path(__file__).resolve().parent.parent / "characters"


@pytest.fixture
def cdir(tmp_path: Path) -> Path:
    for f in REPO.glob("*.yaml"):
        shutil.copy(f, tmp_path / f.name)
    return tmp_path


def _variant(cdir: Path, name: str, edit) -> None:
    data = yaml.safe_load((cdir / "jarvis.yaml").read_text())
    edit(data)
    (cdir / f"{name}.yaml").write_text(yaml.safe_dump(data))






def test_skips_underscore_files(cdir):
    (cdir / "_junk.yaml").write_text("not: [valid")
    assert "_junk" not in C.load_all(cdir)












def test_motion_validation(cdir):
    m = C.motion()
    assert m["beat_ms"] <= 700
    data = yaml.safe_load((cdir / "_motion.yaml").read_text())
    data["beat_ms"] = 900
    (cdir / "_motion.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(C.CharacterError, match="beat_ms"):
        C.motion(cdir)
    data["beat_ms"] = 700
    data["phases"]["signature"] = [500, 9999]
    (cdir / "_motion.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(C.CharacterError, match="signature"):
        C.motion(cdir)


def test_boot_always_jarvis(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    C.write_recent("ultron")
    assert json.loads((tmp_path / "character.json").read_text()) == {"recent": "ultron"}
    assert C.read_recent() == "ultron"
    assert C.boot_character() == "jarvis"
    assert C.boot_character(hint="ultron") == "jarvis"


def test_read_recent_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert C.read_recent() is None




def test_public_dict_no_prompt():
    chars = C.load_all()
    d = C.public_dict(chars["jarvis"])
    json.dumps(d)
    assert "prompt" not in d
    assert chars["jarvis"].prompt.strip()[:20] not in json.dumps(d)
    assert d["theme_tokens"]["accent"] == "#a8ebff"
    assert d["wake_names"] == ["jarvis"]


def test_line_format():
    j = C.load_all()["jarvis"]
    assert j.lines["unknown_name"][0].format(names="x")
    out = C.line(j, "unknown_name", names="Ultron, Friday")
    assert "Ultron, Friday" in out
    assert C.line(j, "greet") in j.lines["greet"]


def _edit_motion(cdir: Path, edit) -> None:
    p = cdir / "_motion.yaml"
    d = yaml.safe_load(p.read_text())
    edit(d)
    p.write_text(yaml.safe_dump(d))






def test_layout_columns_must_sum_to_100(cdir):
    _edit_motion(cdir, lambda d: d["layouts"]["hud_angular"].__setitem__("columns", [10, 50, 30]))
    with pytest.raises(C.CharacterError, match="hud_angular.columns"):
        C.load_all(cdir)


def test_layout_reactor_scale_bounds(cdir):
    _edit_motion(cdir, lambda d: d["layouts"]["hud_glass"].__setitem__("reactor_scale", 3))
    with pytest.raises(C.CharacterError, match="reactor_scale"):
        C.load_all(cdir)




def test_glow_budget_cap(cdir):
    _edit_motion(cdir, lambda d: d["glow"].__setitem__("budget_ms", 5))
    with pytest.raises(C.CharacterError, match="budget_ms"):
        C.load_all(cdir)
