import json

from hud_events import (
    intent_echo_payload,
    tool_finish_payload,
    tool_start_payload,
)


def test_tool_start_payload_shape():
    p = json.loads(tool_start_payload("nmap_scan"))
    assert p["kind"] == "tool_start"
    assert p["tool"] == "nmap_scan"
    assert "scanning" in p["label"]
    assert "ts" in p


def test_tool_finish_payload_ok_flag():
    p = json.loads(tool_finish_payload("nmap_scan", ok=False))
    assert p["kind"] == "tool_finish"
    assert p["ok"] is False


def test_intent_echo_format():
    p = json.loads(intent_echo_payload("crimson", "hot rod red"))
    assert p["kind"] == "intent_echo"
    assert p["echo"] == "hot rod red → crimson"


def test_caption_round_trip(monkeypatch, tmp_path):
    import hud_events

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert hud_events.read_captions() == []
    assert hud_events.caption("sir", "  hello   there ") is True
    assert hud_events.caption("jarvis", "At once, Sir.") is True
    assert hud_events.caption("sir", "   ") is False
    lines = hud_events.read_captions()
    assert [(c["role"], c["text"]) for c in lines] == [
        ("sir", "hello there"),
        ("jarvis", "At once, Sir."),
    ]
    assert all(isinstance(c["ts"], int) for c in lines)


def test_captions_skips_garbage(tmp_path, monkeypatch):
    import hud_events

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "captions.log").write_text("not-a-caption\n12\tsir\thello\n")
    lines = hud_events.read_captions()
    assert [(c["role"], c["text"]) for c in lines] == [("sir", "hello")]


def test_partial_caption_fires_on_growth_or_time() -> None:
    from hud_events import partial_changed

    assert partial_changed("", 0.0, "hey Jarvis what", 1.0) is True
    assert partial_changed("hey", 0.0, "hey", 5.0) is False
    assert partial_changed("", 0.0, "", 5.0) is False
    assert partial_changed("hey Jarvis", 0.0, "hey Jarvis what", 0.5) is False
    assert partial_changed("hey Jarvis", 0.0, "hey Jarvis what", 2.0) is True
