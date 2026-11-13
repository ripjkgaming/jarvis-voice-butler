import numpy as np

import passive_commands as pc
from intent import hud_commands as hc


def _tone(sec, amp):
    return (np.random.default_rng(0).standard_normal(int(16000 * sec)) * amp).astype(
        np.int16
    )


def _run(slicer, audio):
    out = []
    for i in range(0, len(audio), 512):
        r = slicer.feed(audio[i : i + 512])
        if r is not None:
            out.append(r)
    return out


def test_short_utterance_is_emitted():
    audio = np.concatenate([_tone(1.0, 20), _tone(1.0, 3000), _tone(1.2, 20)])
    got = _run(pc.UtteranceSlicer(), audio)
    assert len(got) == 1 and 0.9 < len(got[0]) / 16000 < 2.5


def test_long_speech_and_blips_are_dropped():
    long_ = np.concatenate([_tone(0.5, 20), _tone(5.0, 3000), _tone(1.2, 20)])
    assert _run(pc.UtteranceSlicer(), long_) == []
    blip = np.concatenate([_tone(0.5, 20), _tone(0.06, 3000), _tone(1.2, 20)])
    assert _run(pc.UtteranceSlicer(), blip) == []


def test_resolve_grammar_then_remote_with_gate():
    seen = []
    remote = lambda t: seen.append(t) or "hide"  # noqa: E731
    assert pc.resolve("Jarvis hide", remote) == "hide"
    assert seen == []  # grammar hit, Needle never asked
    assert pc.resolve("could you make the thing go away please", remote) is None
    assert pc.resolve("kindly dismiss the hud for now", remote) == "hide"
    assert pc.resolve("what a lovely day", remote) is None
    assert seen == ["kindly dismiss the hud for now"]


def test_hud_mute_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert hc.hud_muted() is False
    assert hc.set_hud_muted(True) and hc.hud_muted() is True
    assert hc.set_hud_muted(False) and hc.hud_muted() is False


def test_fuzzy_fixes_whisper_misheard_single_words():
    # Real faster-whisper base.en output for Piper-spoken commands.
    for heard, cmd in [("Nute", "mute"), ("Top", "stop"), ("Hidal", "hide_all")]:
        assert pc.resolve(heard, lambda t: None) == cmd
    for noise in ["banana", "good morning", "what time is it", "the"]:
        assert pc.resolve(noise, lambda t: None) is None
