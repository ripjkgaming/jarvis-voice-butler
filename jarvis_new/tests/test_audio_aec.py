"""Echo-cancel decision logic and command building (no real pactl)."""

import pytest

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
    monkeypatch.setattr(audio_aec, "_pactl", lambda *a: calls.append(a) or JBL)
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
    monkeypatch.setattr(
        audio_aec, "_dmic_card", lambda: (_ for _ in ()).throw(OSError("x"))
    )
    assert audio_aec.set_mic_headroom().startswith("error")


HDMI = "alsa_output.pci-0000_01_00.1.hdmi-stereo"
MIC = "alsa_input.pci-built-in.HiFi__Mic1__source"
OWNED_MODULE = (
    "42\tmodule-echo-cancel\t"
    f"source_name={audio_aec.AEC_MIC} sink_name={audio_aec.AEC_SPEAKER} "
    f"source_master={MIC} sink_master={SPK}\t"
)


def _stale_aec_server(monkeypatch, *, sink=HDMI, source=None, modules=None):
    state = {"sink": sink, "source": source or audio_aec.AEC_MIC}
    calls = []

    def fake(*args):
        calls.append(args)
        if args == ("get-default-sink",):
            return state["sink"]
        if args == ("get-default-source",):
            return state["source"]
        if args == ("list", "short", "modules"):
            return OWNED_MODULE if modules is None else modules
        if args == ("list", "short", "sources"):
            return f"1\t{MIC}\tpw\n2\t{audio_aec.AEC_MIC}\tpw"
        if args == ("list", "short", "sinks"):
            return f"3\t{SPK}\tpw\n4\t{HDMI}\tpw"
        if args[0] == "set-default-source":
            state["source"] = args[1]
        if args[0] == "set-default-sink":
            state["sink"] = args[1]
        return ""

    monkeypatch.delenv("JARVIS_AEC", raising=False)
    monkeypatch.setattr(audio_aec, "_pactl", fake)
    return state, calls


@pytest.mark.parametrize("cfg", ["auto", "off"])
def test_skipped_startup_releases_owned_aec_preserving_hdmi(monkeypatch, cfg):
    state, calls = _stale_aec_server(monkeypatch)
    monkeypatch.setenv("JARVIS_AEC", cfg)
    assert audio_aec.engage().startswith("skipped")
    assert state == {"sink": HDMI, "source": MIC}
    assert ("unload-module", "42") in calls
    assert calls.index(("set-default-source", MIC)) < calls.index(
        ("unload-module", "42")
    )
    assert not any(c[0] in ("load-module", "set-default-sink") for c in calls)


def test_off_restores_owned_defaults_before_unload(monkeypatch):
    state, calls = _stale_aec_server(monkeypatch, sink=audio_aec.AEC_SPEAKER)
    monkeypatch.setenv("JARVIS_AEC", "off")
    assert audio_aec.engage() == "skipped (off)"
    assert state == {"sink": SPK, "source": MIC}
    assert calls.index(("set-default-sink", SPK)) < calls.index(("unload-module", "42"))


def test_skip_preserves_user_selected_microphone(monkeypatch):
    state, calls = _stale_aec_server(monkeypatch, source="usb_mic")
    assert audio_aec.engage().startswith("skipped")
    assert state == {"sink": HDMI, "source": "usb_mic"}
    assert ("unload-module", "42") in calls
    assert not any(c[0].startswith("set-default-") for c in calls)


@pytest.mark.parametrize(
    "modules",
    [
        "",
        "7\tmodule-null-sink\t"
        + f"source_name={audio_aec.AEC_MIC} sink_name={audio_aec.AEC_SPEAKER}",
        OWNED_MODULE.replace("jarvis_aec_mic", "jarvis_aec_mic_other"),
        OWNED_MODULE.replace("jarvis_aec_speaker", "other_speaker"),
    ],
)
def test_skip_does_not_touch_unrelated_or_absent_modules(monkeypatch, modules):
    _state, calls = _stale_aec_server(monkeypatch, modules=modules)
    assert audio_aec.engage().startswith("skipped")
    assert not any(
        c[0] in ("unload-module", "set-default-source", "set-default-sink")
        for c in calls
    )


def test_forced_on_keeps_existing_aec(monkeypatch):
    state, calls = _stale_aec_server(monkeypatch)
    monkeypatch.setenv("JARVIS_AEC", "on")
    assert audio_aec.engage() == "engaged"
    assert state == {"sink": audio_aec.AEC_SPEAKER, "source": audio_aec.AEC_MIC}
    assert not any(c[0] == "unload-module" for c in calls)


def test_missing_master_keeps_aec_and_reports_failure(monkeypatch):
    _state, calls = _stale_aec_server(
        monkeypatch,
        modules=OWNED_MODULE.replace(f"source_master={MIC}", "source_master=missing"),
    )
    assert audio_aec.engage().startswith("error")
    assert not any(
        c[0] in ("unload-module", "set-default-source", "set-default-sink")
        for c in calls
    )


def test_missing_sink_master_validates_before_any_restore(monkeypatch):
    _state, calls = _stale_aec_server(
        monkeypatch,
        sink=audio_aec.AEC_SPEAKER,
        modules=OWNED_MODULE.replace(f"sink_master={SPK}", "sink_master=missing"),
    )
    monkeypatch.setenv("JARVIS_AEC", "off")
    assert audio_aec.engage().startswith("error")
    assert not any(
        c[0] in ("unload-module", "set-default-source", "set-default-sink")
        for c in calls
    )


def test_failed_source_restore_does_not_unload(monkeypatch):
    _state, calls = _stale_aec_server(monkeypatch)
    fake = audio_aec._pactl

    def fail_restore(*args):
        if args[0] == "set-default-source":
            raise OSError("source restore failed")
        return fake(*args)

    monkeypatch.setattr(audio_aec, "_pactl", fail_restore)
    assert audio_aec.engage().startswith("error")
    assert not any(c[0] == "unload-module" for c in calls)


def test_cleanup_failure_is_reported_without_raising(monkeypatch):
    _state, calls = _stale_aec_server(monkeypatch)
    fake = audio_aec._pactl

    def fail_unload(*args):
        if args[0] == "unload-module":
            raise OSError("unload failed")
        return fake(*args)

    monkeypatch.setattr(audio_aec, "_pactl", fail_unload)
    assert "unload failed" in audio_aec.engage()
    assert ("set-default-source", MIC) in calls
