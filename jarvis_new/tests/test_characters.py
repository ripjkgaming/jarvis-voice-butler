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
    assert d["theme_tokens"]["accent"] == "#5fe3ff"
    assert d["wake_names"] == ["jarvis"]


def test_line_format():
    j = C.load_all()["jarvis"]
    assert j.lines["unknown_name"][0].format(names="x")
    out = C.line(j, "unknown_name", names="Ultron, Friday")
    assert "Ultron, Friday" in out
    assert C.line(j, "greet") in j.lines["greet"]
