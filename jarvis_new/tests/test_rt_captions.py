import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np

from jarvis_rt.audio import AutoGain, rms
from jarvis_rt.io import TextOutput, TranscriptSynchronizer
from jarvis_rt.markup import strip_all_markup


class Collect(TextOutput):
    def __init__(self):
        super().__init__(label="c")
        self.parts, self.flushes = [], 0

    async def capture_text(self, text):
        self.parts.append(text)

    def flush(self):
        self.flushes += 1


def test_markup_strip():
    assert (
        strip_all_markup('Hello <emotion value="happy"/>world</emotion>')
        == "Hello world"
    )
    assert strip_all_markup("a < b and c > d") == "a < b and c > d"


def test_words_follow_audio_and_flush_releases_rest():
    async def run():
        sink = Collect()
        t = [0.0]
        sync = TranscriptSynchronizer(sink, clock=lambda: t[0])
        sync.push_text("one two three four five six ")
        await asyncio.sleep(0.12)
        assert sink.parts == []  # no audio played yet: nothing released
        sync.push_audio(b"\0" * 24000 * 2 * 2)  # 2 s of audio
        t[0] = 0.5
        await asyncio.sleep(0.12)
        early = "".join(sink.parts)
        await sync.flush()
        return early, "".join(sink.parts), sink.flushes

    early, full, flushes = asyncio.run(run())
    assert 0 < len(early.split()) < 6
    assert full == "one two three four five six " and flushes == 1


def test_reset_drops_unheard():
    async def run():
        sink = Collect()
        sync = TranscriptSynchronizer(sink)
        sync.push_text("never heard ")
        sync.reset()
        await asyncio.sleep(0.1)
        return sink.parts, sink.flushes

    assert asyncio.run(run()) == ([], 1)


def test_agc_lifts_quiet_speech_but_not_silence():
    quiet = (np.sin(np.arange(512) / 5) * 800).astype(np.int16).tobytes()
    agc = AutoGain()
    for _ in range(80):
        out = agc.process(quiet)
    assert rms(out) > rms(quiet) * 2 and agc.gain <= agc.max_gain
    silence = bytes(1024)
    assert AutoGain().process(silence) == silence
