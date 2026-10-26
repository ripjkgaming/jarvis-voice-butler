from test_audio_ctl import FakeSink

from action_outcome import parse_outcome
from system import audio_ctl


def test_wrong_changed_level_is_failure():
    sink = FakeSink(pct=10)

    def run(argv, **kwargs):
        out = sink.run(argv, **kwargs)
        if argv[:2] == ["wpctl", "set-volume"]:
            sink.pct = 70
        return out

    out = parse_outcome(audio_ctl.volume_result("set", 40, run=run, which=sink.which))
    assert out.state == "failed" and out.check == "mismatched"
    assert not out.retry_safe


def test_known_setting_and_step():
    sink = FakeSink(pct=40)
    out = parse_outcome(
        audio_ctl.volume_result("up", run=sink.run, which=sink.which, step=5)
    )
    assert out.verified and sink.pct == 45


def test_unmute_failure_cannot_pass_volume_proof():
    sink = FakeSink(pct=40, muted=True)

    def run(argv, **kwargs):
        if argv[:2] == ["wpctl", "set-mute"]:
            return sink.run(["unknown"], **kwargs)
        return sink.run(argv, **kwargs)

    out = parse_outcome(audio_ctl.volume_result("set", 50, run=run, which=sink.which))
    assert not out.verified


def test_mute_read_unavailable_is_not_false():
    import subprocess

    def run(argv, **kwargs):
        if argv[:2] == ["pactl", "get-sink-volume"]:
            return subprocess.CompletedProcess(argv, 0, "40%", "")
        if argv[:2] == ["pactl", "set-sink-mute"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "")

    out = parse_outcome(
        audio_ctl.volume_result("unmute", run=run, which=lambda name: None)
    )
    assert not out.verified and out.check == "unavailable"


def test_revocation_after_dispatch_never_retries():
    sink = FakeSink(pct=40)
    calls = []

    def authorize():
        if sink.pct == 50:
            raise PermissionError("revoked")

    def run(argv, **kwargs):
        if argv[:2] == ["wpctl", "set-volume"]:
            calls.append(argv)
        return sink.run(argv, **kwargs)

    out = parse_outcome(
        audio_ctl.volume_result("up", run=run, which=sink.which, authorize=authorize)
    )
    assert out.state == "dispatched_unverified" and not out.retry_safe
    assert len(calls) == 1


def test_tuple_api_keeps_receipt():
    sink = FakeSink()
    result = audio_ctl.volume("set", 40, run=sink.run, which=sink.which)
    ok, say = result
    assert ok and say == "Volume 40 percent."
    assert parse_outcome(result.result).verified
