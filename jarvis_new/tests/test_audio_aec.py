"""Echo-cancel decision logic and command building (no real pactl)."""

import audio_aec

SPK = "alsa_output.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__Speaker__sink"
JBL = "alsa_output.usb-Harman_JBL_Tune_520C.analog-stereo"


def test_engages_on_builtin_speaker_only() -> None:
    assert audio_aec.should_engage(SPK)
    assert not audio_aec.should_engage(JBL)
    assert not audio_aec.should_engage("")


def test_env_modes(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_AEC", raising=False)
    assert audio_aec.mode() == "auto"
    monkeypatch.setenv("JARVIS_AEC", "0")
    assert audio_aec.mode() == "off"
    monkeypatch.setenv("JARVIS_AEC", "1")
    assert audio_aec.mode() == "on"
    assert audio_aec.should_engage(JBL, "on")
    assert not audio_aec.should_engage(SPK, "off")


def test_already_engaged_counts_as_speaker() -> None:
    assert audio_aec.should_engage(audio_aec.AEC_SPEAKER)


def test_load_args_use_webrtc_and_masters() -> None:
    args = audio_aec.build_load_args("mic-src", "spk-sink")
    assert args[:2] == ["load-module", "module-echo-cancel"]
    assert "aec_method=webrtc" in args
    assert "source_master=mic-src" in args and "sink_master=spk-sink" in args
    assert f"source_name={audio_aec.AEC_MIC}" in args


def test_engage_loads_then_sets_defaults(monkeypatch) -> None:
    calls: list[tuple] = []
    loaded = {"v": False}

    def fake(*a):
        calls.append(a)
        if a[:1] == ("get-default-sink",):
            return SPK
        if a[:1] == ("get-default-source",):
            return "alsa_input.pci-mic"
        if a[:3] == ("list", "short", "sources"):
            return audio_aec.AEC_MIC if loaded["v"] else "x"
        if a[:1] == ("load-module",):
            loaded["v"] = True
            return "1"
        return ""

    monkeypatch.delenv("JARVIS_AEC", raising=False)
    monkeypatch.setattr(audio_aec, "_pactl", fake)
    assert audio_aec.engage() == "engaged"
    assert ("set-default-sink", audio_aec.AEC_SPEAKER) in calls
    assert ("set-default-source", audio_aec.AEC_MIC) in calls
    assert sum(c[:1] == ("load-module",) for c in calls) == 1


def test_engage_noop_on_headset_and_never_raises(monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.delenv("JARVIS_AEC", raising=False)
    monkeypatch.setattr(
        audio_aec, "_pactl", lambda *a: calls.append(a) or JBL
    )
    assert audio_aec.engage().startswith("skipped")
    assert not any(c[:1] in (("load-module",), ("set-default-sink",)) for c in calls)

    def boom(*a):
        raise OSError("no pactl")

    monkeypatch.setattr(audio_aec, "_pactl", boom)
    assert audio_aec.engage().startswith("error")


SGET = """Simple mixer control 'Dmic0',0
  Capabilities: cvolume cswitch
  Front Left: Capture 68 [97%] [18.00dB] [on]
  Front Right: Capture 68 [97%] [18.00dB] [on]
"""


def test_parse_control_db() -> None:
    assert audio_aec.parse_control_db(SGET) == 18.0
    assert audio_aec.parse_control_db("no gain here") is None


def test_dmic_gain_env(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_DMIC_GAIN_DB", raising=False)
    assert audio_aec.dmic_gain_db() == 0.0
    monkeypatch.setenv("JARVIS_DMIC_GAIN_DB", "6")
    assert audio_aec.dmic_gain_db() == 6.0
    monkeypatch.setenv("JARVIS_DMIC_GAIN_DB", "off")
    assert audio_aec.dmic_gain_db() is None


def test_headroom_lowers_but_never_raises(monkeypatch) -> None:
    calls = []
    monkeypatch.delenv("JARVIS_DMIC_GAIN_DB", raising=False)
    monkeypatch.setattr(audio_aec, "_dmic_card", lambda: "1")

    def fake(*args):
        calls.append(args)
        return SGET if args[2] == "sget" else ""

    monkeypatch.setattr(audio_aec, "_amixer", fake)
    assert audio_aec.set_mic_headroom() == "lowered to 0 dB"
    assert calls[-1] == ("-c", "1", "sset", "Dmic0", "0dB")

    calls.clear()
    monkeypatch.setattr(audio_aec, "_amixer", lambda *a: SGET.replace("18.00", "-3.00"))
    assert audio_aec.set_mic_headroom() == "kept (-3 dB)"


def test_headroom_never_raises(monkeypatch) -> None:
    monkeypatch.setattr(audio_aec, "_dmic_card", lambda: (_ for _ in ()).throw(OSError("x")))
    assert audio_aec.set_mic_headroom().startswith("error")
