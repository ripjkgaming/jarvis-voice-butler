"""Hardware speaker routing behind the AEC filter, without real audio changes."""

import subprocess

import audio_output


def fake_audio(default="jarvis_aec_speaker", fail=None):
    calls = []

    def run(argv, **options):
        calls.append(argv)
        out = ""
        if argv[1] == "get-default-sink":
            out = default
        elif argv[1:4] == ["list", "short", "modules"]:
            out = "13\tmodule-echo-cancel\tsink_name=jarvis_aec_speaker sink_master=alsa_speaker\t\n"
        elif argv[1] == "get-sink-volume":
            out = "front-left: 21627 / 33% / -28 dB"
        elif argv[1] == "get-sink-mute":
            out = "Mute: yes"
        return subprocess.CompletedProcess(argv, int(argv[1] == fail), out, "")

    return run, calls


def test_read_hardware_instead_of_virtual_gain():
    run, calls = fake_audio()
    result = audio_output.run(
        ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], runner=run
    )
    assert result.stdout == "Volume: 0.33 [MUTED]\n"
    assert ["pactl", "get-sink-volume", "alsa_speaker"] in calls


def test_write_hardware_and_remove_independent_filter_attenuation():
    run, calls = fake_audio()
    result = audio_output.run(
        ["wpctl", "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", "0.45"], runner=run
    )
    assert result.returncode == 0
    assert calls[-3:] == [
        ["pactl", "set-sink-volume", "alsa_speaker", "45%"],
        ["pactl", "set-sink-volume", "jarvis_aec_speaker", "100%"],
        ["pactl", "set-sink-mute", "jarvis_aec_speaker", "0"],
    ]


def test_mute_pactl_targets_same_hardware():
    run, calls = fake_audio()
    audio_output.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"], runner=run)
    assert calls[2] == ["pactl", "set-sink-mute", "alsa_speaker", "1"]


def test_headphone_selection_uses_normal_default():
    run, calls = fake_audio(default="bluetooth_headphones")
    command = ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]
    audio_output.run(command, runner=run)
    assert calls == [["pactl", "get-default-sink"], command]


def test_failed_hardware_write_leaves_filter_alone():
    run, calls = fake_audio(fail="set-sink-volume")
    result = audio_output.run(
        ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.45"], runner=run
    )
    assert result.returncode == 1
    assert len(calls) == 3


def test_failed_mute_read_is_not_reported_as_unmuted():
    run, _ = fake_audio(fail="get-sink-mute")
    result = audio_output.run(
        ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], runner=run
    )
    assert result.returncode != 0
