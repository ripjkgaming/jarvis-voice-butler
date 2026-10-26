import json
import sys

sys.path.insert(0, "src")
import wake_events


def test_encode_event_character_optional():
    base = json.loads(wake_events.encode_event("wake", 1.0))
    assert base == {"event": "wake", "ts": 1.0}
    got = json.loads(wake_events.encode_event("wake", 1.0, "ultron", 3))
    assert got["character"] == "ultron" and got["char_turn"] == 3
