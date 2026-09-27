"""Second wake phrase: "daddy" via segmenter + tiny Whisper."""

import numpy as np
import pytest

import keyword_spot as K


@pytest.mark.parametrize(
    "text",
    ["Wake up, Daddy's home.", "Daddy", "hey daddy", "Wake up, that is home.",
     "Dad's home!", "DADDY'S HOME"],
)
def test_trigger_texts(text):
    assert K.has_daddy(text)


@pytest.mark.parametrize(
    "text",
    ["I bought a new caddy", "What time is it?", "Did he go home?", "that is home",
     "the dad joke", "", "paddy field"],
)
def test_non_triggers(text):
    assert not K.has_daddy(text)


def _tone(seconds, amp=4000):
    t = np.arange(int(seconds * K.RATE)) / K.RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.int16)


def _run(seg, pcm):
    out = []
    for i in range(0, len(pcm), K.FRAME):
        out += seg.feed(pcm[i : i + K.FRAME])
    return out


def test_segmenter_cuts_one_utterance():
    quiet = np.random.default_rng(0).integers(-50, 50, K.RATE).astype(np.int16)
    pcm = np.concatenate([quiet, _tone(1.2), np.zeros(K.RATE, np.int16)])
    segs = _run(K.Segmenter(), pcm)
    assert len(segs) == 1
    assert 1.2 <= segs[0].size / K.RATE <= 1.2 + K.HANG_S + 0.1


def test_segmenter_drops_clicks_and_caps_long_speech():
    click = np.concatenate([_tone(0.1), np.zeros(K.RATE, np.int16)])
    assert _run(K.Segmenter(), click) == []
    long = np.concatenate([_tone(7.0), np.zeros(K.RATE, np.int16)])
    segs = _run(K.Segmenter(), long)
    assert segs and all(s.size <= (K.MAX_SEG_S + 0.1) * K.RATE for s in segs)


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("JARVIS_DADDY_WAKE", "0")
    assert not K.enabled()
    monkeypatch.setenv("JARVIS_DADDY_WAKE", "1")
    assert K.enabled()


def test_worker_down_is_empty_not_crash():
    w = K.WhisperWorker(python="/nonexistent/python")
    assert w.transcribe(np.zeros(1600, np.int16)) == ""
