import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _glb(path: Path, names: list[str], emissive: str | None = None) -> None:
    j = {
        "asset": {"version": "2.0"},
        "materials": [{"name": "m"}, {"name": "e", "emissiveFactor": [1, 1, 1]}],
        "meshes": [
            {"primitives": [{"material": 1 if n == emissive else 0}]} for n in names
        ],
        "nodes": [{"name": n, "mesh": i} for i, n in enumerate(names)],
    }
    js = json.dumps(j).encode()
    js += b" " * (-len(js) % 4)
    path.write_bytes(
        struct.pack("<III", 0x46546C67, 2, 20 + len(js))
        + struct.pack("<II", len(js), 0x4E4F534A)
        + js
    )


def test_roles_and_voice(tmp_path, monkeypatch):
    import model3d
    import model3d_parts as mp

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(model3d, "ROOT", tmp_path)
    slug = "car"
    (tmp_path / slug).mkdir()
    _glb(
        tmp_path / slug / f"{slug}.glb",
        [
            "body",
            "door_left",
            "door_right",
            "wheel_front_left",
            "headlight_left",
            "glowthing",
        ],
        "glowthing",
    )
    model3d.set_state(slug=slug, open=True)
    roles = {p["id"]: p["role"] for p in mp.parts_of(tmp_path / slug / f"{slug}.glb")}
    assert roles["door_left"] == "hinge" and roles["wheel_front_left"] == "spin"
    assert roles["headlight_left"] == "light" and roles["glowthing"] == "light"

    assert "Lights off" in model3d.part_command("turn the headlights off")
    assert model3d.state_payload()["fx"]["headlight_left"]["on"] is False
    assert "Opening" in model3d.part_command("open the left door")
    fx = model3d.state_payload()["fx"]
    assert fx["door_left"]["open"] == 1.0 and "door_right" not in fx
    assert "Spinning" in model3d.part_command("spin the wheels")
    assert "I can control" in model3d.part_command("what can I control")
