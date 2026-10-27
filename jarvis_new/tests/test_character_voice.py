"""Hermetic tests for character_voice and wake_client character_say."""

import asyncio
import types

import numpy as np
import pytest

import audio_shared
import character_voice as cv
import wake_client


def ch(cid, **voice):
    base = {"model": "m-" + cid, "pitch_semitones": 0, "length_scale": 1.0,
            "effects": [], "whoosh": "holo"}
    base.update(voice)
    return types.SimpleNamespace(id=cid, voice=base)


JARVIS = ch("jarvis", length_scale=0.85)
ULTRON = ch("ultron", pitch_semitones=-3, effects=["ringmod_light"], whoosh="glitch")


class FakeVoice:
    def __init__(self):
        self.config = types.SimpleNamespace(sample_rate=22050)

    def synthesize(self, text, syn_config=None):
        data = (np.sin(np.arange(2000) / 20) * 8000).astype(np.int16).tobytes()
        yield types.SimpleNamespace(audio_int16_bytes=data)


@pytest.fixture
def bank(monkeypatch, tmp_path):
    loads = []

    def fake_load(path):
        loads.append(path)
        if not path.exists():
            raise RuntimeError("missing")
        return FakeVoice()

    monkeypatch.setattr(cv, "load_piper_voice", fake_load)
    monkeypatch.setattr(cv, "VOICE_DIR", tmp_path)
    monkeypatch.setattr(cv, "resolve_voice_path", lambda e=None: tmp_path / "alan.onnx")
    (tmp_path / "alan.onnx").write_bytes(b"x")
    (tmp_path / "m-ultron.onnx").write_bytes(b"x")
    calls = []

    def fake_render(voice, text, *, length_scale=1.0, **kw):
        calls.append(length_scale)
        return (np.sin(np.arange(4000) / 20) * 8000).astype(np.int16).tobytes()

    monkeypatch.setattr(cv, "render_sentence_pcm", fake_render)
    b = cv.VoiceBank()
    b.loads, b.calls = loads, calls
    return b


def test_bank_caches_voice(bank):
    bank.render_pcm("hi", ULTRON)
    bank.render_pcm("again", ULTRON)
    assert len(bank.loads) == 1


def test_prewarm_false_on_missing_model(bank):
    assert bank.prewarm(ch("friday", model="nope")) is False
    assert bank.prewarm(ULTRON) is True


def test_jarvis_no_fx_and_legacy_scale(bank, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("fx must not run for jarvis")

    monkeypatch.setattr(cv.voice_fx, "pitch_shift", boom)
    monkeypatch.setattr(cv.voice_fx, "apply_effects", boom)
    pcm, sr = bank.render_pcm("hi", JARVIS)
    assert sr == 22050 and pcm.dtype == np.int16
    assert bank.calls == [audio_shared.resolve_length_scale(None)]
    raw = (np.sin(np.arange(4000) / 20) * 8000).astype(np.int16)
    assert np.array_equal(pcm, raw)


def test_fx_called_for_ultron(bank, monkeypatch):
    seen = []
    monkeypatch.setattr(cv.voice_fx, "pitch_shift", lambda p, sr, s: seen.append(("p", s)) or p)
    monkeypatch.setattr(cv.voice_fx, "apply_effects", lambda p, sr, e: seen.append(("e", e)) or p)
    bank.render_pcm("hi", ULTRON)
    assert seen == [("p", -3.0), ("e", ["ringmod_light"])]
    assert bank.calls == [1.0]


def test_whoosh_pcm(bank):
    w = bank.whoosh_pcm(ULTRON, 22050, 400)
    assert w.dtype == np.int16 and len(w) == int(22050 * 0.4)


def test_make_session_tts_jarvis_is_plain_piper_path(bank):
    rt = types.SimpleNamespace(active=lambda: JARVIS)
    t = cv.make_session_tts(runtime=rt, bank=bank)
    assert t._target() is t
    rt2 = types.SimpleNamespace(active=lambda: ULTRON)
    t2 = cv.make_session_tts(runtime=rt2, bank=bank)
    child = t2._target()
    assert child is not t2 and child is t2._target()
    raw = child._render_sentence("hello")
    assert raw and len(raw) % 2 == 0


# ---------------- wake_client character_say ----------------
class FakePlayback:
    active = 0
    max_active = 0
    writes = []  # noqa: RUF012
    cancels = 0
    streams = 0

    def __init__(self, *a, **k):
        FakePlayback.streams += 1
        self._closed = False

    def cancel(self, *a):
        FakePlayback.cancels += 1

    async def write(self, samples, sr, turn_id=None):
        FakePlayback.active += 1
        FakePlayback.max_active = max(FakePlayback.max_active, FakePlayback.active)
        await asyncio.sleep(0)
        FakePlayback.writes.append((len(samples), turn_id))
        FakePlayback.active -= 1

    async def aclose(self):
        self._closed = True


@pytest.fixture
def client(monkeypatch):
    import audio_playback
    import character_runtime

    FakePlayback.active = FakePlayback.max_active = FakePlayback.cancels = FakePlayback.streams = 0
    FakePlayback.writes = []
    monkeypatch.setattr(audio_playback, "AudioPlayback", FakePlayback)
    rt = types.SimpleNamespace(chars={"ultron": ULTRON, "jarvis": JARVIS})
    monkeypatch.setattr(character_runtime, "get_runtime", lambda: rt)
    pcm = (np.sin(np.arange(22050) / 10) * 30000).astype(np.int16)
    monkeypatch.setattr(cv, "render_pcm", lambda t, c: (pcm.copy(), 22050))
    monkeypatch.setattr(cv, "whoosh_pcm", lambda c, sr, d=800: np.full(sr, 32000, np.int16))
    c = wake_client.WakeClient.__new__(wake_client.WakeClient)
    yield c
    lp = getattr(c, "_say_loop", None)
    if lp:
        lp.call_soon_threadsafe(lp.stop)


def wait_writes(n, t=3.0):
    import time

    end = time.time() + t
    while time.time() < end and len(FakePlayback.writes) < n:
        time.sleep(0.01)


def test_character_say_mixes_whoosh_peak_safe(client, monkeypatch):
    got = {}
    real = wake_client.prepare_character_pcm
    monkeypatch.setattr(
        wake_client, "prepare_character_pcm",
        lambda p, sr, ms: got.setdefault("pcm", real(p, sr, ms)),
    )
    r = client._character_say({"text": "hello", "character": "ultron", "whoosh": True})
    assert r["ok"] and "ms" in r
    wait_writes(1)
    pcm = got["pcm"]
    assert pcm.dtype == np.int16 and int(np.abs(pcm.astype(int)).max()) <= 32767
    assert pcm[0] == 0  # faded in, no click


def test_voice_output_off_skips(client, monkeypatch):
    monkeypatch.setenv("JARVIS_VOICE_OUTPUT", "0")
    monkeypatch.setattr(cv, "render_pcm", lambda *a: pytest.fail("rendered"))
    r = client._character_say({"text": "x", "character": "ultron"})
    assert r == {"ok": True, "skipped": "voice_output_off"}


def test_voice_output_env_default_on(client, monkeypatch):
    monkeypatch.delenv("JARVIS_VOICE_OUTPUT", raising=False)
    assert client._voice_output_on() is True


def test_bad_requests(client):
    assert client._character_say("x")["ok"] is False
    assert client._character_say({"text": "", "character": "ultron"})["ok"] is False
    assert client._character_say({"text": "hi", "character": "nobody"})["ok"] is False


def test_crossfade_single_writer_no_overlap(client):
    client._character_say({"text": "one", "character": "ultron"})
    client._character_say({"text": "two", "character": "jarvis", "interrupt": True})
    wait_writes(2)
    import time
    time.sleep(0.3)
    assert FakePlayback.max_active == 1
    assert FakePlayback.streams == 1  # one playback, never a second stream
    assert FakePlayback.cancels >= 1


def test_play_agent_fade_noop_for_jarvis_only(monkeypatch):
    import sys

    monkeypatch.delitem(sys.modules, "character_runtime", raising=False)
    assert wake_client._character_changed() is False
