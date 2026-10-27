"""Wake client character_prewarm, idle warm, per-voice locks, jarvis never fx'd."""

import sys
import threading
import time
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, "src")

import character_voice as cv


def ch(cid, model=None):
    return SimpleNamespace(id=cid, voice={"model": model or f"m-{cid}", "pitch_semitones": -2})


class FakeBank(cv.VoiceBank):
    def __init__(self):
        super().__init__()
        self.warmed = []

    def prewarm(self, char):
        self.warmed.append(char.id)
        return True


def test_idle_warm_skips_jarvis_and_warms_others():
    bank = FakeBank()
    t = cv.start_idle_warm(
        [ch("jarvis"), ch("ultron"), ch("friday")], lambda: False,
        bank=bank, sleep=lambda s: None, background=False,
    )
    t.join(2)
    assert bank.warmed == ["ultron", "friday"]


def test_idle_warm_skipped_when_call_active():
    bank = FakeBank()
    t = cv.start_idle_warm(
        [ch("ultron")], lambda: True, bank=bank, sleep=lambda s: None, background=False
    )
    t.join(2)
    assert bank.warmed == []


def test_idle_warm_env_off(monkeypatch):
    monkeypatch.setenv("JARVIS_CHARACTER_IDLE_WARM", "0")
    assert cv.start_idle_warm([ch("ultron")], lambda: False, bank=FakeBank()) is None


def test_wake_client_idle_busy_in_call():
    import wake_client

    wc = wake_client.WakeClient.__new__(wake_client.WakeClient)
    wc._mic_lock = threading.Lock()
    wc._muted, wc._threshold, wc._in_call = False, 0.5, True
    assert wc._idle_warm_busy() is True
    wc._in_call = False
    assert wc._idle_warm_busy() is False


def test_wake_client_character_prewarm(monkeypatch):
    import wake_client

    seen = []
    monkeypatch.setattr(cv, "prewarm", lambda c: seen.append(c.id) or True)
    wc = wake_client.WakeClient.__new__(wake_client.WakeClient)
    reply = wc._character_prewarm("ultron")
    assert reply["ok"] is True and isinstance(reply["ms"], int) and seen == ["ultron"]
    assert wc._character_prewarm("nobody")["ok"] is False
    assert wc._character_prewarm(5)["ok"] is False
    monkeypatch.setattr(cv, "prewarm", lambda c: False)
    assert wc._character_prewarm("ultron")["ok"] is False


def test_distinct_voices_do_not_serialize(monkeypatch):
    bank = cv.VoiceBank()
    gate = threading.Event()
    entered = []

    def fake_render(voice, text, length_scale=1.0):
        entered.append(voice)
        if voice == "slow":
            gate.wait(2)
        return np.zeros(100, dtype=np.int16).tobytes()

    monkeypatch.setattr(cv, "render_sentence_pcm", fake_render)
    monkeypatch.setattr(cv, "load_piper_voice", lambda p: p.stem)
    monkeypatch.setattr(bank, "sample_rate", lambda c: 22050)
    slow, fast = ch("ultron", "slow"), ch("friday", "fast")
    th = threading.Thread(target=bank.render_pcm, args=("a", slow))
    th.start()
    time.sleep(0.05)
    t = time.monotonic()
    bank.render_pcm("b", fast)
    assert time.monotonic() - t < 0.5 and "fast" in entered
    gate.set()
    th.join(2)


def test_jarvis_never_goes_through_fx(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("fx used for jarvis")

    monkeypatch.setattr(cv.voice_fx, "pitch_shift", boom)
    monkeypatch.setattr(cv.voice_fx, "apply_effects", boom)
    pcm = np.ones(50, dtype=np.int16)
    jarvis = SimpleNamespace(id="jarvis", voice={"pitch_semitones": -5, "effects": ["x"]})
    assert cv.apply_fx(pcm, 22050, jarvis) is pcm
