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


def test_live_caption_roundtrip(tmp_path, monkeypatch) -> None:
    """Agent writes the word-synced line; the bridge reads it back."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from bridge import read_live_caption
    from hud_events import live_caption

    assert read_live_caption() is None
    assert live_caption("7", "Good  day,")
    got = read_live_caption()
    assert got["id"] == "7" and got["text"] == "Good day," and got["done"] is False
    live_caption("7", "Good day, Sir.", done=True)
    assert read_live_caption()["done"] is True


def test_live_caption_stale_or_corrupt_is_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from bridge import read_live_caption
    from hud_events import live_caption

    live_caption("1", "old")
    assert read_live_caption(max_age_s=-1) is None
    (tmp_path / "caption_live.json").write_text("{nope")
    assert read_live_caption() is None


def test_jarvis_tool_runs_mirror_to_activity(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    import activity
    from hud_events import jarvis_activity_id, mirror_to_activity

    assert jarvis_activity_id("open_app") == "jarvis-open-app"
    mirror_to_activity("open_app", "tool_start")
    item = activity.get("jarvis-open-app")
    assert item["status"] == "running" and item["source"] == "jarvis"
    mirror_to_activity("open_app", "tool_finish", "error")
    assert activity.get("jarvis-open-app")["status"] == "failed"


def test_bridge_activity_route_lists_items(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    import activity
    from bridge import read_activity

    assert read_activity() == []
    activity.start("download", "ubuntu.iso", item_id="download-abc")
    assert [i["id"] for i in read_activity()] == ["download-abc"]
