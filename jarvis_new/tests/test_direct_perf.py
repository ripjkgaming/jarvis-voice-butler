"""Direct-pipeline performance + sizing gate (Phase 5 slice 5.3).

No mic / server / keys: cached Piper fixtures (rendered once by the 5.4
harness into ``~/.cache/jarvis-regressions/``) through local
faster-whisper on CPU int8, plus a Silero VAD sensitivity sweep.

(a) ``test_first_transcript_and_turn_latency`` — cold start
    (model load + first transcription) and warm p50 turn latency over
    the 10 gate fixtures. Gate: p50 < 6s on CPU.
(b) ``test_size_matrix`` — tiny full + small subset against the base
    reference: confirms the Phase-5 benchmark table (tiny WER ~0.26,
    base/small WER ~0.07 with small ~3x slower) on the gate fixtures.
    The sizing recommendation next to JARVIS_WHISPER_MODEL follows this.
(c) ``test_vad_sensitivity_sweep`` — activation thresholds 0.3/0.5/0.7
    over speech+silence: defaults hold unless numbers say otherwise.

The default pipeline stays ``realtime`` — nothing here changes it.
Budget: ~2 min CPU offline (one load per size + short transcriptions).
"""

from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path

import numpy as np
import pytest
from livekit import rtc
from test_voice_fixtures import FIXTURES, fixture_slug, wer
from test_voice_regressions import EARS_IDX, cached_fixture_wav, expand_numbers

from local_stt import FasterWhisperSTT

FIRST_TRANSCRIPT_BUDGET_S = 120.0
P50_BUDGET_S = 6.0
SMALL_SUBSET = [1, 4, 6]  # time / twelve-percent / ada: names + numbers


def _needs_size(size: str) -> str | None:
    hub = (
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
        / f"models--Systran--faster-whisper-{size}"
    )
    if not hub.exists():
        return f"whisper {size} weights not cached (offline box?)"
    return None


def _needs_voice() -> str | None:
    from local_voice import DEFAULT_VOICE_PATH

    if not DEFAULT_VOICE_PATH.exists():
        return "voice model not downloaded"
    return None


def _fixture_pcm(idx: int) -> tuple[np.ndarray, dict]:
    fix = FIXTURES[idx]
    raw = cached_fixture_wav(fix["text"], fix["kind"])
    return np.frombuffer(raw[44:], dtype=np.int16), fix


async def _transcribe(stt: FasterWhisperSTT, pcm: np.ndarray) -> tuple[str, float]:
    frame = rtc.AudioFrame(
        data=pcm.tobytes(),
        sample_rate=16000,
        num_channels=1,
        samples_per_channel=len(pcm),
    )
    started = time.monotonic()
    event = await stt.recognize([frame])
    elapsed = time.monotonic() - started
    assert event.type.name == "FINAL_TRANSCRIPT"
    return event.alternatives[0].text, elapsed


async def test_first_transcript_and_turn_latency() -> None:
    """Cold start + warm p50 turn latency on the reference (base) ear."""
    skip = _needs_voice() or _needs_size("base")
    if skip is not None:
        pytest.skip(skip)
    stt = FasterWhisperSTT(model_size="base")  # fresh: load happens here
    pcm, fix = await asyncio.to_thread(_fixture_pcm, EARS_IDX[1])
    first_text, first_secs = await _transcribe(stt, pcm)
    print(f"\nfirst transcript ({fix['text']!r}): {first_secs:.1f}s -> {first_text!r}")
    assert first_secs < FIRST_TRANSCRIPT_BUDGET_S

    latencies: list[float] = []
    for idx in EARS_IDX:
        item_pcm, item_fix = await asyncio.to_thread(_fixture_pcm, idx)
        _hyp, secs = await _transcribe(stt, item_pcm)
        latencies.append(secs)
        print(f"  {fixture_slug(item_fix['text'], item_fix['kind'])}: {secs:.2f}s")
    p50 = statistics.median(latencies)
    print(f"base p50 turn latency: {p50:.2f}s (max {max(latencies):.2f}s)")
    assert p50 < P50_BUDGET_S, f"p50 {p50:.2f}s >= {P50_BUDGET_S}s"


async def test_size_matrix() -> None:
    """tiny/base/small on gate fixtures: accuracy knee + small parity."""
    skip = _needs_voice() or _needs_size("tiny") or _needs_size("base")
    if skip is not None:
        pytest.skip(skip)
    results: dict[str, list[float]] = {}
    for size in ("tiny", "base"):
        stt = FasterWhisperSTT(model_size=size)
        wers: list[float] = []
        started = time.monotonic()
        for idx in EARS_IDX:
            pcm, fix = await asyncio.to_thread(_fixture_pcm, idx)
            hyp, _ = await _transcribe(stt, pcm)
            wers.append(wer(expand_numbers(fix["text"]), expand_numbers(hyp)))
        results[size] = wers
        print(
            f"\n{size}: mean digit-blind WER {sum(wers) / len(wers):.3f}, "
            f"wall {time.monotonic() - started:.1f}s"
        )
    base_mean = sum(results["base"]) / len(results["base"])
    assert base_mean <= 0.2, f"base reference ear regressed: {base_mean:.3f}"

    small_skip = _needs_size("small")
    if small_skip is not None:
        pytest.skip(small_skip)
    stt = FasterWhisperSTT(model_size="small")
    for idx in SMALL_SUBSET:
        pcm, fix = await asyncio.to_thread(_fixture_pcm, idx)
        hyp, _ = await _transcribe(stt, pcm)
        score = wer(expand_numbers(fix["text"]), expand_numbers(hyp))
        base_score = results["base"][EARS_IDX.index(idx)]
        print(f"small {fix['text']!r}: WER {score:.2f} (base {base_score:.2f})")
        assert score == base_score, "small must match base per-fixture (parity)"


async def _vad_events(threshold: float, pcm: np.ndarray) -> list[str]:
    """Speech event types for PCM at one activation threshold."""
    from livekit.plugins import silero

    vad = silero.VAD.load(activation_threshold=threshold)
    stream = vad.stream()
    try:
        chunk = 512  # 32ms @ 16kHz
        for i in range(0, len(pcm), chunk):
            piece = pcm[i : i + chunk]
            if len(piece) < chunk:
                piece = np.pad(piece, (0, chunk - len(piece)))
            stream.push_frame(
                rtc.AudioFrame(
                    data=piece.astype(np.int16).tobytes(),
                    sample_rate=16000,
                    num_channels=1,
                    samples_per_channel=chunk,
                )
            )
        stream.end_input()
        kinds: list[str] = []
        async with asyncio.timeout(30):
            async for event in stream:
                kinds.append(event.type.name)
                if event.type.name == "END_OF_SPEECH":
                    break
        return kinds
    finally:
        await stream.aclose()


async def test_vad_sensitivity_sweep() -> None:
    """0.3/0.5/0.7 over speech+silence: defaults hold unless shown worse."""
    skip = _needs_voice()
    if skip is not None:
        pytest.skip(skip)
    pcm, _ = await asyncio.to_thread(_fixture_pcm, EARS_IDX[1])
    tailed = np.concatenate([pcm, np.zeros(16000, dtype=np.int16)])  # +1s silence
    silence = np.zeros(32000, dtype=np.int16)  # 2s pure silence
    rows: list[tuple[float, bool, bool]] = []
    for threshold in (0.3, 0.5, 0.7):
        speech_kinds = await _vad_events(threshold, tailed)
        silence_kinds = await _vad_events(threshold, silence)
        recalled = "START_OF_SPEECH" in speech_kinds
        false_fire = "START_OF_SPEECH" in silence_kinds
        rows.append((threshold, recalled, false_fire))
        print(f"threshold {threshold}: speech={recalled} silence_fire={false_fire}")
    default = next(r for r in rows if r[0] == 0.5)
    assert default[1], "default VAD must detect gate speech"
    assert not default[2], "default VAD must not fire on pure silence"
