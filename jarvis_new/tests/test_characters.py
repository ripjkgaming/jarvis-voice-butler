from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from src import characters as C  # noqa: N812

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


def test_load_real():
    chars = C.load_all()
    assert set(chars) == {"jarvis", "ultron", "friday"}
    assert chars["ultron"].signature == "violent_settle"
    assert set(C.REQUIRED_LINES) <= set(chars["friday"].lines)


def test_fourth_character_zero_code(cdir):
    def e(d):
        d["id"] = "vision"
        d["aliases"] = ["vision", "vishon"]
        d["wake"]["names"] = ["vision"]
    _variant(cdir, "vision", e)
    chars = C.load_all(cdir)
    assert "vision" in chars
    assert C.resolve_name("vishon", chars) == "vision"


def test_skips_underscore_files(cdir):
    (cdir / "_junk.yaml").write_text("not: [valid")
    assert "_junk" not in C.load_all(cdir)


def test_missing_line_kind(cdir):
    _variant(cdir, "bad", lambda d: (d.__setitem__("id", "bad"), d["persona"]["lines"].pop("rollback")))
    with pytest.raises(C.CharacterError, match=r"bad\.yaml.*rollback"):
        C.load_all(cdir)


def test_missing_key(cdir):
    _variant(cdir, "bad", lambda d: (d.__setitem__("id", "bad"), d.pop("address")))
    with pytest.raises(C.CharacterError, match="address"):
        C.load_all(cdir)


def test_token_set_mismatch(cdir):
    _variant(cdir, "bad", lambda d: (d.__setitem__("id", "bad"), d["theme"]["tokens"].pop("warn")))
    with pytest.raises(C.CharacterError, match="warn"):
        C.load_all(cdir)


def test_bad_color(cdir):
    _variant(cdir, "bad", lambda d: (d.__setitem__("id", "bad"), d["theme"]["tokens"].__setitem__("accent", "blue")))
    with pytest.raises(C.CharacterError, match="accent"):
        C.load_all(cdir)


def test_unknown_signature(cdir):
    _variant(cdir, "bad", lambda d: (d.__setitem__("id", "bad"), d["transition"].__setitem__("signature", "nope")))
    with pytest.raises(C.CharacterError, match="nope"):
        C.load_all(cdir)


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


def test_resolve_name():
    chars = C.load_all()
    assert C.resolve_name("Ultron", chars) == "ultron"
    assert C.resolve_name("  JERVIS ", chars) == "jarvis"
    assert C.resolve_name("fryday", chars) == "friday"
    assert C.resolve_name("ultrin", chars) == "ultron"
    assert C.resolve_name("banana", chars) is None
    assert C.resolve_name("", chars) is None


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


def test_shipped_layouts_and_particles_valid():
    chars = C.load_all()
    lay = C.motion()["layouts"]
    assert {c.layout for c in chars.values()} == {"hud_round", "hud_angular", "hud_glass"}
    assert set(lay) >= {c.layout for c in chars.values()}
    for c in chars.values():
        for key, (lo, hi) in C.PARTICLE_TOKENS.items():
            assert lo <= c.theme_tokens[key] <= hi
    # Ultron sparse embers, Friday soft motes, Jarvis the dense reference.
    t = {k: v.theme_tokens["particle_count"] for k, v in chars.items()}
    assert t["ultron"] < t["friday"] < t["jarvis"]


def test_unknown_layout_rejected(cdir):
    _variant(cdir, "bad", lambda d: (d.__setitem__("id", "bad"), d["theme"].__setitem__("layout", "hud_nope")))
    with pytest.raises(C.CharacterError, match="hud_nope"):
        C.load_all(cdir)


def test_layout_columns_must_sum_to_100(cdir):
    _edit_motion(cdir, lambda d: d["layouts"]["hud_angular"].__setitem__("columns", [10, 50, 30]))
    with pytest.raises(C.CharacterError, match="hud_angular.columns"):
        C.load_all(cdir)


def test_layout_reactor_scale_bounds(cdir):
    _edit_motion(cdir, lambda d: d["layouts"]["hud_glass"].__setitem__("reactor_scale", 3))
    with pytest.raises(C.CharacterError, match="reactor_scale"):
        C.load_all(cdir)


def test_particle_token_range(cdir):
    _variant(cdir, "bad", lambda d: (d.__setitem__("id", "bad"), d["theme"]["tokens"].__setitem__("particle_count", 9)))
    with pytest.raises(C.CharacterError, match="particle_count"):
        C.load_all(cdir)


def test_glow_budget_cap(cdir):
    _edit_motion(cdir, lambda d: d["glow"].__setitem__("budget_ms", 5))
    with pytest.raises(C.CharacterError, match="budget_ms"):
        C.load_all(cdir)
