"""Removed secondary wake never transcribes ambient input; all PCM is synthetic."""

import asyncio
import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest

import keyword_spot
import school
import wake_client


class StopListeningError(Exception):
    pass


def setup_loop(monkeypatch, *, school_mode=False, trigger=None):
    obj = wake_client.WakeClient.__new__(wake_client.WakeClient)
    obj._mic_lock = threading.Lock()
    obj._muted = False
    obj._mute_epoch = 0
    obj._in_call = False
    obj._listen_queue_live = True
    obj._threshold = 0.5
    obj._talk_event = threading.Event()
    transcriptions = []

    def transcribe(pcm):
        transcriptions.append(pcm.size)
        return "Hey Jarvis what time is it"

    obj._spotter = SimpleNamespace(transcribe=transcribe)

    class Model:
        calls = 0

        def predict(self, frame):
            self.calls += 1
            return {"hey_jarvis": float(trigger == "wake" and self.calls >= 13)}

    model = Model()

    class Input:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def __enter__(self):
            async def feed():
                for number in range(210):
                    if trigger == "ptt" and number == 32:
                        obj._talk_event.set()
                    raw = np.full(wake_client.BLOCKSIZE, 4000, np.int16).tobytes()
                    self.callback(raw, wake_client.BLOCKSIZE, None, None)
                    await asyncio.sleep(0)

            self.producer = asyncio.create_task(feed())
            return self

        def __exit__(self, *args):
            self.producer.cancel()

    monkeypatch.setitem(
        sys.modules, "sounddevice", SimpleNamespace(RawInputStream=Input)
    )
    monkeypatch.setattr(school, "is_school", lambda: school_mode)
    monkeypatch.setitem(
        sys.modules, "mic_clean", SimpleNamespace(make_cleaner=lambda block: None)
    )
    monkeypatch.setattr(wake_client, "summon_overlay", lambda: None)
    monkeypatch.setattr(wake_client, "mark_hud_waking", lambda: None)
    monkeypatch.setattr(wake_client, "hud_stage", lambda stage: None)
    return obj, model, transcriptions


@pytest.mark.parametrize("setting", ["0", "1"])
def test_legacy_environment_cannot_enable_secondary_wake(monkeypatch, setting):
    monkeypatch.setenv("JARVIS_DADDY_WAKE", setting)
    assert keyword_spot.enabled() is False


async def test_normal_input_never_instantiates_secondary_detector_or_transcribes(
    monkeypatch,
):
    monkeypatch.setenv("JARVIS_DADDY_WAKE", "1")
    obj, model, transcriptions = setup_loop(monkeypatch)

    def removed_detector(*args):
        pytest.fail("removed secondary detector must not allocate resources")

    monkeypatch.setattr(keyword_spot, "Segmenter", removed_detector)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(obj._listen_loop(model), 0.15)
    assert model.calls >= 70, "primary detector must still receive sustained input"
    assert transcriptions == []


@pytest.mark.parametrize("trigger", ["wake", "ptt"])
async def test_primary_wake_and_ptt_keep_the_call_path(monkeypatch, trigger):
    monkeypatch.setenv("JARVIS_DADDY_WAKE", "1")
    obj, model, transcriptions = setup_loop(monkeypatch, trigger=trigger)
    entries = []

    async def summon(**kwargs):
        entries.append(kwargs)
        raise StopListeningError

    monkeypatch.setattr(obj, "_summon_with_rewake", summon)
    with pytest.raises(StopListeningError):
        await asyncio.wait_for(obj._listen_loop(model), 1)
    assert entries[0]["preserve_join_audio"] is True
    assert entries[0]["preroll"]
    assert transcriptions == []


async def test_school_confirmation_keeps_shared_transcription(monkeypatch):
    obj, model, transcriptions = setup_loop(
        monkeypatch, school_mode=True, trigger="wake"
    )
    loop = asyncio.get_running_loop()

    def immediate_executor(_executor, function, *args):
        future = loop.create_future()
        future.set_result(function(*args))
        return future

    monkeypatch.setattr(loop, "run_in_executor", immediate_executor)

    async def record(*args):
        return [np.full(wake_client.BLOCKSIZE, 123, np.int16).tobytes()]

    seeds = []

    async def summon(**kwargs):
        seeds.append(obj._pending_text)
        raise StopListeningError

    monkeypatch.setattr(obj, "_record_question", record)
    monkeypatch.setattr(obj, "_summon_session", summon)
    with pytest.raises(StopListeningError):
        await asyncio.wait_for(obj._listen_loop(model), 1)
    assert len(transcriptions) >= 2, (
        "school warmup and confirmed question need the shared worker"
    )
    assert seeds == ["what time is it"]
