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
