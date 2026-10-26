from test_audio_ctl import FakeSink

import bridge
from action_outcome import ActionOutcome, parse_outcome


def test_bridge_settings_use_readback_and_preserve_five_step(monkeypatch):
    sink = FakeSink(pct=40)

    def run(argv, timeout=10):
        p = sink.run(argv)
        return p.returncode, p.stdout, p.stderr

    monkeypatch.setattr(bridge, "_run", run)
    monkeypatch.setattr(bridge, "_which", sink.which)
    result = bridge.run_phone_tool("volume_up", {})
    assert parse_outcome(result).verified and sink.pct == 45


def test_voice_reply_template_does_not_override_uncertainty(monkeypatch):
    outcome = ActionOutcome(
        "setting.volume",
        "failed",
        "started",
        "mismatched",
        "audio_service",
        evidence={"expected_pct": 40, "actual_pct": 70},
    )
    monkeypatch.setattr(bridge, "_run_voice_action", lambda *a: (500, outcome.result()))
    spoken = []
    monkeypatch.setattr(bridge, "_speak_quietly", lambda s: (spoken.append(s) or "", 0))
    _, payload = bridge._voice_tool_reply(
        ("volume_set", {"level": 40}, "Done!"), "volume 40", False
    )
    assert payload["reply"] == outcome.say and spoken == [outcome.say]
    assert payload["action"]["outcome"] == outcome.to_dict()
