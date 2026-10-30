"""LiveKit-free STT/TTS/VAD contracts in ``jarvis_rt`` (stt.py, tts.py, vad.py)."""

import asyncio

import numpy as np
import pytest

from jarvis_rt import tts as rt_tts
from jarvis_rt import vad as rt_vad
from jarvis_rt.stt import (
    STT,
    APIConnectOptions,
    APIError,
    AudioFrame,
    AudioResampler,
    Chan,
    RecognizeStream,
    SpeechData,
    SpeechEvent,
    SpeechEventType,
    STTCapabilities,
)
from jarvis_rt.tts import TTS, AudioEmitter, ChunkedStream, SynthesizeStream


def _tone(rate: int, seconds: float, amp: int = 10000, hz: float = 440.0) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * hz * t) * amp).astype(np.int16)


def _frames(pcm: np.ndarray, rate: int, size: int) -> list[AudioFrame]:
    return [
        AudioFrame(pcm[i : i + size].tobytes(), rate, 1)
        for i in range(0, len(pcm), size)
    ]


# --- shared audio primitives -------------------------------------------------


def test_audio_frame_accepts_bytes_and_arrays() -> None:
    samples = np.arange(480, dtype=np.int16)
    from_bytes = AudioFrame(samples.tobytes(), 16000, 1)
    from_array = AudioFrame(samples, 16000, 1)
    assert from_bytes.samples_per_channel == 480
    assert from_array.data.tobytes() == samples.tobytes()
    assert np.frombuffer(from_array.data, dtype=np.int16).tolist() == samples.tolist()


def test_audio_frame_rejects_zero_channels() -> None:
    with pytest.raises(ValueError):
        AudioFrame(b"\x00\x00", 16000, 0)


def test_resampler_downsample_length_and_tone_level() -> None:
    pcm = _tone(48000, 1.0)
    r = AudioResampler(48000, 16000)
    out = r.push(AudioFrame(pcm.tobytes(), 48000, 1)) + r.flush()
    y = np.concatenate([np.frombuffer(f.data, np.int16) for f in out])
    assert len(y) == 16000
    body = y[400:-400].astype(np.float64)
    expected_rms = 10000 / np.sqrt(2)
    assert np.sqrt(np.mean(body**2)) == pytest.approx(expected_rms, rel=0.03)


def test_resampler_non_integer_ratio_keeps_frequency() -> None:
    pcm = _tone(44100, 1.0, hz=1000.0)
    r = AudioResampler(44100, 16000)
    out = r.push(AudioFrame(pcm.tobytes(), 44100, 1)) + r.flush()
    y = np.concatenate([np.frombuffer(f.data, np.int16) for f in out]).astype(float)
    assert len(y) == 16000
    body = y[200:-200]
    crossings = np.count_nonzero(np.diff(np.signbit(body)))
    # A 1 kHz tone crosses zero twice per cycle.
    assert crossings == pytest.approx(2 * 1000 * len(body) / 16000, abs=4)


def test_resampler_chunked_equals_one_shot() -> None:
    pcm = _tone(44100, 0.7, hz=700.0)
    one = AudioResampler(44100, 16000)
    whole = one.push(AudioFrame(pcm.tobytes(), 44100, 1)) + one.flush()
    chunked = AudioResampler(44100, 16000)
    parts = []
    for i in range(0, len(pcm), 1000):
        chunk = pcm[i : i + 1000]
        parts += chunked.push(AudioFrame(chunk.tobytes(), 44100, 1))
    parts += chunked.flush()

    def cat(frames):
        return np.concatenate([np.frombuffer(f.data, np.int16) for f in frames])

    assert np.array_equal(cat(whole), cat(parts))


def test_resampler_rejects_format_change() -> None:
    r = AudioResampler(48000, 16000)
    with pytest.raises(ValueError):
        r.push(AudioFrame(np.zeros(160, np.int16), 16000, 1))


@pytest.mark.asyncio
async def test_chan_iterates_until_closed() -> None:
    ch: Chan[int] = Chan()
    ch.send_nowait(1)
    ch.send_nowait(2)
    ch.close()
    assert [x async for x in ch] == [1, 2]
    with pytest.raises(RuntimeError):
        ch.send_nowait(3)


# --- STT ---------------------------------------------------------------------


class _CountSTT(STT):
    def __init__(self, fail_times: int = 0, fail_cls=APIError) -> None:
        super().__init__(
            capabilities=STTCapabilities(streaming=False, interim_results=False)
        )
        self.calls = 0
        self._fail_times = fail_times
        self._fail_cls = fail_cls

    async def _recognize_impl(self, buffer, *, language="en", conn_options=None):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._fail_cls("flaky")
        frames = buffer if isinstance(buffer, list) else [buffer]
        text = f"{len(frames)} frames"
        return SpeechEvent(
            type=SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[SpeechData(language="en", text=text)],
        )


@pytest.mark.asyncio
async def test_stt_recognize_returns_event_and_labels() -> None:
    stt = _CountSTT()
    frame = AudioFrame(np.zeros(160, np.int16), 16000, 1)
    event = await stt.recognize([frame, frame])
    assert event.type is SpeechEventType.FINAL_TRANSCRIPT
    assert event.alternatives[0].text == "2 frames"
    assert stt.capabilities.streaming is False
    assert stt.label.endswith("_CountSTT")


@pytest.mark.asyncio
async def test_stt_retries_api_errors_then_succeeds() -> None:
    stt = _CountSTT(fail_times=2)
    frame = AudioFrame(np.zeros(160, np.int16), 16000, 1)
    opts = APIConnectOptions(max_retry=3, retry_interval=0.0)
    event = await stt.recognize(frame, conn_options=opts)
    assert stt.calls == 3
    assert event.alternatives[0].text == "1 frames"


@pytest.mark.asyncio
async def test_stt_gives_up_after_max_retry() -> None:
    stt = _CountSTT(fail_times=10)
    frame = AudioFrame(np.zeros(160, np.int16), 16000, 1)
    with pytest.raises(APIError):
        await stt.recognize(
            frame, conn_options=APIConnectOptions(max_retry=1, retry_interval=0.0)
        )
    assert stt.calls == 2


@pytest.mark.asyncio
async def test_stt_non_api_error_is_not_retried() -> None:
    stt = _CountSTT(fail_times=5, fail_cls=RuntimeError)
    frame = AudioFrame(np.zeros(160, np.int16), 16000, 1)
    with pytest.raises(RuntimeError):
        await stt.recognize(
            frame, conn_options=APIConnectOptions(max_retry=3, retry_interval=0.0)
        )
    assert stt.calls == 1


def test_stt_stream_is_unsupported_by_default() -> None:
    with pytest.raises(NotImplementedError):
        _CountSTT().stream()


class _SegmentRecognizer(RecognizeStream):
    """Emits one event per flushed segment, plus a tail event if audio is left unflushed."""

    async def _run(self) -> None:
        count = 0
        async for item in self._input_ch:
            if isinstance(item, RecognizeStream._FlushSentinel):
                self._event_ch.send_nowait(_event(f"seg:{count}"))
                count = 0
            else:
                count += 1
                self.rates_seen = item.sample_rate
        if count:
            self._event_ch.send_nowait(_event(f"tail:{count}"))


def _event(text: str) -> SpeechEvent:
    return SpeechEvent(
        type=SpeechEventType.FINAL_TRANSCRIPT,
        alternatives=[SpeechData(language="en", text=text)],
    )


@pytest.mark.asyncio
async def test_recognize_stream_segments_and_end_input() -> None:
    stream = _SegmentRecognizer(stt=_CountSTT())
    frame = AudioFrame(np.zeros(160, np.int16), 16000, 1)
    stream.push_frame(frame)
    stream.push_frame(frame)
    stream.flush()
    stream.push_frame(frame)
    stream.end_input()
    texts = [ev.alternatives[0].text async for ev in stream]
    assert texts == ["seg:2", "seg:1"]  # end_input() flushes the open segment
    with pytest.raises(RuntimeError):
        stream.push_frame(frame)


@pytest.mark.asyncio
async def test_recognize_stream_resamples_to_requested_rate() -> None:
    stream = _SegmentRecognizer(stt=_CountSTT(), sample_rate=16000)
    stream.push_frame(AudioFrame(np.zeros(4800, np.int16), 48000, 1))
    stream.end_input()
    [ev async for ev in stream]
    assert stream.rates_seen == 16000
    await stream.aclose()


# --- TTS ---------------------------------------------------------------------


class _ToneChunked(ChunkedStream):
    async def _run(self, output_emitter: AudioEmitter) -> None:
        output_emitter.initialize(
            request_id="req", sample_rate=16000, num_channels=1, mime_type="audio/pcm"
        )
        for sentence in self._input_text.split("|"):
            if sentence.strip():
                output_emitter.push(np.full(1600, 5, np.int16).tobytes())
        output_emitter.flush()


class _ToneStream(SynthesizeStream):
    async def _run(self, output_emitter: AudioEmitter) -> None:
        output_emitter.initialize(
            request_id="req",
            sample_rate=16000,
            num_channels=1,
            mime_type="audio/pcm",
            stream=True,
        )
        buffer = ""
        async for item in self._input_ch:
            if isinstance(item, str):
                buffer += item
            elif isinstance(item, SynthesizeStream._FlushSentinel):
                if buffer.strip():
                    output_emitter.start_segment(segment_id=f"seg{len(buffer)}")
                    output_emitter.push(np.full(800, 3, np.int16).tobytes())
                    output_emitter.end_segment()
                buffer = ""


class _FakeTTS(TTS):
    def __init__(self) -> None:
        super().__init__(
            capabilities=rt_tts.TTSCapabilities(streaming=True),
            sample_rate=16000,
            num_channels=1,
        )

    def synthesize(self, text, *, conn_options=None):
        return _ToneChunked(tts=self, input_text=text)

    def stream(self, *, conn_options=None):
        return _ToneStream(tts=self)


@pytest.mark.asyncio
async def test_chunked_stream_emits_pcm_frames_and_collects() -> None:
    tts = _FakeTTS()
    stream = tts.synthesize("one|two")
    frames = [ev.frame async for ev in stream]
    assert len(frames) == 2
    assert all(f.sample_rate == 16000 and f.num_channels == 1 for f in frames)
    assert frames[0].samples_per_channel == 1600
    collected = await tts.synthesize("one").collect()
    assert collected.samples_per_channel == 1600


@pytest.mark.asyncio
async def test_chunked_stream_empty_text_pushes_nothing() -> None:
    frames = [ev async for ev in _FakeTTS().synthesize("   ")]
    assert frames == []


@pytest.mark.asyncio
async def test_chunked_stream_raises_when_text_renders_no_audio() -> None:
    class _Silent(ChunkedStream):
        async def _run(self, output_emitter: AudioEmitter) -> None:
            output_emitter.initialize(
                request_id="r", sample_rate=16000, num_channels=1, mime_type="audio/pcm"
            )

    class _SilentTTS(_FakeTTS):
        def synthesize(self, text, *, conn_options=None):
            return _Silent(tts=self, input_text=text)

    with pytest.raises(APIError):
        async for _ in _SilentTTS().synthesize("hello"):
            pass


@pytest.mark.asyncio
async def test_synthesize_stream_segments_follow_flushes() -> None:
    stream = _FakeTTS().stream()
    stream.push_text("Hello there.")
    stream.flush()
    stream.push_text("Second.")
    stream.end_input()
    segments = [ev.segment_id async for ev in stream]
    assert segments == ["seg12", "seg7"]
    with pytest.raises(RuntimeError):
        stream.push_text("late")


@pytest.mark.asyncio
async def test_synthesize_stream_aclose_cancels_blocked_run() -> None:
    class _Blocked(SynthesizeStream):
        async def _run(self, output_emitter: AudioEmitter) -> None:
            output_emitter.initialize(
                request_id="r", sample_rate=16000, num_channels=1, mime_type="audio/pcm"
            )
            await asyncio.Event().wait()

    class _BlockedTTS(_FakeTTS):
        def stream(self, *, conn_options=None):
            return _Blocked(tts=self)

    stream = _BlockedTTS().stream()
    consumer = asyncio.create_task(_drain(stream))
    await asyncio.sleep(0.01)
    await asyncio.wait_for(stream.aclose(), 1.0)
    await asyncio.wait_for(consumer, 1.0)


async def _drain(stream) -> None:
    async for _ in stream:
        pass


def test_audio_emitter_requires_initialize() -> None:
    emitter = AudioEmitter(dst=Chan())
    with pytest.raises(RuntimeError):
        emitter.push(b"\x00\x00")


# --- VAD ---------------------------------------------------------------------

_RATE = 16000
_WINDOW = 512
_SILENCE_S = 1.0
_TONE_S = 1.0
_TAIL_S = 1.5


def _speech_signal(rate: int) -> np.ndarray:
    tone = _tone(rate, _TONE_S, amp=10000, hz=300.0)
    return np.concatenate(
        [
            np.zeros(int(rate * _SILENCE_S), np.int16),
            tone,
            np.zeros(int(rate * _TAIL_S), np.int16),
        ]
    )


class _LoudnessSession:
    """Fake ONNX session: probability 0.9 for loud windows, 0.01 otherwise."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, _outputs, feed):
        self.calls += 1
        window = feed["input"][:, -_WINDOW:]
        rms = float(np.sqrt(np.mean(window.astype(np.float64) ** 2)))
        prob = 0.9 if rms > 0.05 else 0.01
        return np.array([[prob]], dtype=np.float32), feed["state"]


def _fake_vad(monkeypatch) -> rt_vad.SileroVAD:
    session = _LoudnessSession()
    monkeypatch.setattr(
        rt_vad, "new_inference_session", lambda force_cpu, onnx_file_path=None: session
    )
    vad = rt_vad.SileroVAD.load()
    vad._fake_session = session  # type: ignore[attr-defined]
    return vad


async def _run_vad(vad: rt_vad.SileroVAD, rate: int, frame_size: int) -> list:
    stream = vad.stream()
    for frame in _frames(_speech_signal(rate), rate, frame_size):
        stream.push_frame(frame)
    stream.end_input()
    events = [ev async for ev in stream]
    await stream.aclose()
    return events


@pytest.mark.asyncio
async def test_vad_fake_model_start_and_end_bracket_the_tone(monkeypatch) -> None:
    vad = _fake_vad(monkeypatch)
    events = await _run_vad(vad, _RATE, _WINDOW)
    starts = [e for e in events if e.type is rt_vad.VADEventType.START_OF_SPEECH]
    ends = [e for e in events if e.type is rt_vad.VADEventType.END_OF_SPEECH]
    assert len(starts) == 1 and len(ends) == 1
    tone_start, tone_end = _SILENCE_S, _SILENCE_S + _TONE_S
    assert tone_start <= starts[0].timestamp <= tone_start + 0.2
    assert ends[0].timestamp >= tone_end + 0.55 - 0.05
    assert ends[0].timestamp <= tone_end + 0.55 + 0.1
    assert starts[0].timestamp < ends[0].timestamp
    inference = [e for e in events if e.type is rt_vad.VADEventType.INFERENCE_DONE]
    assert len(inference) == vad._fake_session.calls
    assert all(e.frames and e.frames[0].sample_rate == _RATE for e in inference)


@pytest.mark.asyncio
async def test_vad_fake_model_resamples_48k_input(monkeypatch) -> None:
    vad = _fake_vad(monkeypatch)
    events = await _run_vad(vad, 48000, 1536)
    kinds = [e.type for e in events if e.type is not rt_vad.VADEventType.INFERENCE_DONE]
    assert kinds == [
        rt_vad.VADEventType.START_OF_SPEECH,
        rt_vad.VADEventType.END_OF_SPEECH,
    ]


@pytest.mark.asyncio
async def test_vad_fake_model_silence_never_starts(monkeypatch) -> None:
    vad = _fake_vad(monkeypatch)
    stream = vad.stream()
    stream.push_frame(AudioFrame(np.zeros(_RATE * 2, np.int16), _RATE, 1))
    stream.end_input()
    events = [ev async for ev in stream]
    assert not [e for e in events if e.type is rt_vad.VADEventType.START_OF_SPEECH]


def test_vad_load_validates_sample_rate_and_deactivation(monkeypatch) -> None:
    monkeypatch.setattr(
        rt_vad,
        "new_inference_session",
        lambda force_cpu, onnx_file_path=None: _LoudnessSession(),
    )
    with pytest.raises(ValueError):
        rt_vad.SileroVAD.load(sample_rate=44100)
    with pytest.raises(ValueError):
        rt_vad.SileroVAD.load(deactivation_threshold=0.0)


def test_vad_default_thresholds_match_agent_settings(monkeypatch) -> None:
    monkeypatch.setattr(
        rt_vad,
        "new_inference_session",
        lambda force_cpu, onnx_file_path=None: _LoudnessSession(),
    )
    vad = rt_vad.SileroVAD.load()
    assert vad._opts.min_speech_duration == pytest.approx(0.05)
    assert vad._opts.min_silence_duration == pytest.approx(0.55)
    assert vad._opts.activation_threshold == pytest.approx(0.5)
    assert vad._opts.deactivation_threshold == pytest.approx(0.35)


def test_bundled_model_file_is_present() -> None:
    assert rt_vad.DEFAULT_MODEL_PATH.is_file()
    assert rt_vad.DEFAULT_MODEL_PATH.stat().st_size > 1_000_000


@pytest.mark.asyncio
async def test_real_silero_model_reports_silence_as_non_speech() -> None:
    vad = rt_vad.SileroVAD.load()
    stream = vad.stream()
    stream.push_frame(AudioFrame(np.zeros(_RATE * 2, np.int16), _RATE, 1))
    stream.end_input()
    events = [ev async for ev in stream]
    probs = [
        e.probability for e in events if e.type is rt_vad.VADEventType.INFERENCE_DONE
    ]
    assert len(probs) > 50
    assert all(0.0 <= p <= 1.0 for p in probs)
    assert max(probs) < 0.5
    assert not [e for e in events if e.type is rt_vad.VADEventType.START_OF_SPEECH]
